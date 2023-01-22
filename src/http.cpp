#include <sdkconfig.h>
#include <ctime>
#include <cstring>
#include <string_view>
#include <esp_err.h>
#include <esp_log.h>
#include <nvs_handle.hpp>
#include <esp_netif.h>
#include <esp_wifi.h>
#include <esp_http_server.h>
#include <esp_camera.h>
#include <esp_timer.h>
#include <jsmn.h>
#include "common.hpp"

namespace app::network { // from network.cpp
	esp_err_t config(char* input, jsmntok_t* root, char* output, size_t output_length, int* output_return); 
}
namespace app::camera { // from camera.cpp
	esp_err_t config(char* input, jsmntok_t* root, char* output, size_t output_length, int* output_return);
}

namespace app::http
{

////////////////////////////////////////////////////////////////////////////////
// Utils

inline esp_err_t httpd_register_uri_handler(httpd_handle_t handle, const httpd_uri_t& uri_handler)
{
	return httpd_register_uri_handler(handle, &uri_handler);
}

// Note: Additional `src_` prefix is necessary because of PlatformIO, see KNOWN_ISSUES.md
#define GENERATE_HTTPD_HANDLER_FOR_EMBEDDED_FILE_I(n, t, e)                    \
	esp_err_t embedded_##n##_handler(httpd_req_t* req)                         \
	{                                                                          \
		extern const unsigned char n##_start[] asm("_binary_" #n "_start");    \
		extern const unsigned char n##_end[]   asm("_binary_" #n "_end");      \
		const size_t n##_size = (n##_end - n##_start);                         \
		httpd_resp_set_type(req, t);                                           \
		if (sizeof(e) > 1) httpd_resp_set_hdr(req, "Content-Encoding", e);     \
		httpd_resp_send(req, (const char*)n##_start, n##_size);                \
		return ESP_OK;                                                         \
	}
#define GENERATE_HTTPD_HANDLER_FOR_EMBEDDED_FILE(snake_name, type, encoding) \
	GENERATE_HTTPD_HANDLER_FOR_EMBEDDED_FILE_I(snake_name, type, encoding)

/// Helper to avoid forgetting returning buffer
struct esp_camera_fb_guard
{
	camera_fb_t* fb;

	esp_camera_fb_guard() {
		fb = esp_camera_fb_get();
	}
	~esp_camera_fb_guard() {
		if (fb) esp_camera_fb_return(fb);
	}
};

#define ip4_addr_printf_unpack(ip) ip4_addr_get_byte(ip, 0), ip4_addr_get_byte(ip, 1), ip4_addr_get_byte(ip, 2), ip4_addr_get_byte(ip, 3)
#define mac_addr_printf(mac) mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]

std::string_view skipToQuerystring(std::string_view uri) {
	auto pos = uri.rfind('?');
	if (pos != std::string_view::npos) uri.remove_prefix(pos + 1);
	return uri;
}

class QuerystringCrawlerIterator
{
	const char* keyStart;
	const char* keyEnd;
	const char* valueEnd;

	void forward() noexcept {
		const char* p = keyStart;
		for(;;) {
			if (*p == '=') {
				keyEnd = p;
				while (*++p)
					if (*p == '&')
						break;
				valueEnd = p;
				return;
			}
			if (*p == '&' || !*p) {
				keyEnd = p;
				valueEnd = keyEnd + 1; // will end up as 0 length value
				return;
			}
			p++;
		}
	}

public:
	QuerystringCrawlerIterator(const char* position)
		: keyStart(position)
	{
		forward();
	}

	QuerystringCrawlerIterator& operator++() noexcept {
		if (*keyEnd && *valueEnd) {
			keyStart = valueEnd + 1;
			forward();
		}
		else {
			keyStart = *keyEnd ? valueEnd : keyEnd;
		}
		return *this;
	}
	
	std::pair<std::string_view, std::string_view> operator*() const noexcept {
		const auto valueStart = keyEnd + 1;
		return {
			{ keyStart, static_cast<size_t>(keyEnd - keyStart) }, 
			{ valueStart, static_cast<size_t>(valueEnd - valueStart) }
		};
	}

	bool operator!=(const QuerystringCrawlerIterator& other) const noexcept {
		return this->keyStart != other.keyStart;
	}
};

class QuerystringCrawler
{
	const std::string_view view;
public:
	using iterator = QuerystringCrawlerIterator;

	QuerystringCrawler(std::string_view&& view)
		: view(view)
	{}

	iterator begin() const noexcept {
		return iterator(std::cbegin(this->view));
	}
	iterator end() const noexcept {
		return iterator(std::cend(this->view));
	}
};

////////////////////////////////////////////////////////////////////////////////
// Configuration 

static const char* TAG_CONFIG_ROOT = "config-root";

inline bool has_simple_value(const jsmntok_t* token)
{
	if (token->type == JSMN_UNDEFINED) return false;
	if (token->type == JSMN_OBJECT) return false;
	if (token->type == JSMN_ARRAY) return false;
	return true;
}

/// @brief Applies (and/or reads current) JSON configuration for the whole app.
/// @param[in] input Buffer with JSON data that was parsed into JSON into tokens.
///		Note: Passed non-const to allow in-place strings manipulation.
/// @param[in] root JSMN JSON object token related to the config to be parsed.
/// @param[out] output Optional buffer for writing JSON with current configuration.
/// @param[in] output_length Length of output buffer.
/// @param[out] output_return Used to return number of bytes that would be written 
/// 	to the output, or negative for error. Basically `printf`-like return.
/// @return 
esp_err_t config_root(
	char* input, jsmntok_t* root,
	char* output, size_t output_length, int* output_return
) {
	if (input) {
		if (unlikely(root->type != JSMN_OBJECT))
			return ESP_FAIL;
		if (unlikely(root->size < 1)) 
			return ESP_FAIL;
		for (jsmntok_t* token = root + 1;;) {
			auto* key_token   = token;
			auto* value_token = token + 1;
			ESP_LOGV(TAG_CONFIG_ROOT, "key='%.*s' value='%.*s'", 
				key_token->end - key_token->start, input + key_token->start,
				value_token->end - value_token->start, input + value_token->start
			);

			// Dispatch based on type and key
			const auto key_hash = fnv1a32(input + key_token->start, input + key_token->end);
			if (value_token->type == JSMN_OBJECT) {
				ESP_LOGV(TAG_CONFIG_ROOT, "type=object size=%zu", value_token->size);
				switch (key_hash) {
					case fnv1a32("network"): {
						if (network::config(input, value_token, nullptr, 0, nullptr) != ESP_OK)
							return ESP_FAIL;
						break;
					}
					case fnv1a32("camera"): {
						if (camera::config(input, value_token, nullptr, 0, nullptr) != ESP_OK)
							return ESP_FAIL;
						break;
					}
					case fnv1a32("control"): {
						// TODO: controls via config handler?
						break;
					}
					default:
						ESP_LOGD(TAG_CONFIG_ROOT, "Unknown field '%.*s', ignoring.", 
							key_token->end - key_token->start, input + key_token->start);
						break;
				}

				// Skip object
				jsmntok_t* other = value_token + 1;
				while(1) {
					if (root->end < other->end)
						goto done;
					if (value_token->end < other->end) {
						break;
					}
					other++;
				}
				token = other;
			}
			else {
				if (unlikely(!has_simple_value(value_token)))
					return ESP_FAIL;

				switch (key_hash) {
					case fnv1a32("reset"): {
						if (parseBooleanFast(input + value_token->start)) {
							// TODO: reset safely
						}
						break;
					}
					default:
						ESP_LOGD(TAG_CONFIG_ROOT, "Unknown field '%.*s', ignoring.", 
							key_token->end - key_token->start, input + key_token->start);
						break;
				}

				// Skip primitive pair (key & value)
				token += 2;
				if (root->end < token->end)
					goto done;
			}
		}
		done: /* semicolon for empty statement */ ;
	}

	if (output_return) {
		int ret;
		char* position = output;
		size_t remaining = output_length;

		ret = snprintf(
			position, remaining,
			"{"
				"\"uptime\":%llu,"
				"\"network\":",
			esp_timer_get_time()
		);
		if (unlikely(ret < 0)) goto output_fail;
		position += ret;
		remaining = saturatedSubtract(remaining, ret);

		network::config(nullptr, nullptr, position, remaining, &ret);
		if (unlikely(ret < 0)) goto output_fail;
		position += ret;
		remaining = saturatedSubtract(remaining, ret);

		ret = snprintf(position, remaining, ",\"camera\":");
		if (unlikely(ret < 0)) goto output_fail;
		position += ret;
		remaining = saturatedSubtract(remaining, ret);

		camera::config(nullptr, nullptr, position, remaining, &ret);
		if (unlikely(ret < 0)) goto output_fail;
		position += ret;
		remaining = saturatedSubtract(remaining, ret);

		if (unlikely(remaining < 1)) 
			position++;
		else
			*position++ = '}';

		*output_return = position - output;
		return ESP_OK;

		output_fail:
		*output_return = -1;
		return ESP_FAIL;
	}

	return ESP_OK;
}

////////////////////////////////////////////////////////////////////////////////
// Main web server

static const char* TAG_HTTPD_MAIN = "httpd-main";

esp_err_t status_handler(httpd_req_t* req)
{
	int ret;
	char buffer[512];
	const size_t bufferLength = sizeof(buffer);

	char timeString[32];
	std::time_t time = std::time({});
	std::strftime(std::data(timeString), std::size(timeString), "%FT%T%z", std::gmtime(&time));

	wifi_mode_t mode;
	esp_wifi_get_mode(&mode);
	wifi_ap_record_t ap;
	if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
		ap.rssi = 0;
	}

	// For now, just use this simple version for detailed mode
	bool detailedMode = std::strstr(req->uri, "?detail") != nullptr;
	// Parse querystring
	// bool detailedMode = false;
	// for (auto&& [key, value] : QuerystringCrawler(skipToQuerystring(req->uri).data())) {
	// 	switch (fnv1a32(key.begin(), key.end())) {
	// 		case fnv1a32("details"): {
	// 			detailedMode = true;
	// 			break;
	// 		}
	// 	}
	// }

	size_t writtenLength;
	if (detailedMode) {
		wifi_sta_list_t sta_list;
		if (esp_err_t err = esp_wifi_ap_get_sta_list(&sta_list); err != ESP_OK) {
			ESP_ERROR_CHECK_WITHOUT_ABORT(err);
			sta_list.num = 0;
		}

		char* position = buffer;
		size_t remaining = bufferLength;

		ret = snprintf(
			position, remaining,
			"{"
				"\"uptime\":%llu,"
				"\"time\":\"%s\","
				"\"rssi\":%d,"
				"\"stations\":[",
			esp_timer_get_time(),
			timeString,
			ap.rssi
		);
		if (unlikely(ret < 0 || static_cast<size_t>(ret) >= remaining)) goto fail;
		position += ret;
		remaining -= ret;

		for (int i = 0; i < sta_list.num; i++) {
			const wifi_sta_info_t& sta_info = sta_list.sta[i];
			// TODO: look up for IP assigned by DHCP server
			ret = snprintf(
				position, remaining,
				"{\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\",\"rssi\":%d}%c",
				mac_addr_printf(sta_info.mac),
				sta_info.rssi,
				(i + 1 < sta_list.num) ? ',' : ' '
			);
			if (unlikely(ret < 0 || static_cast<size_t>(ret) >= remaining)) goto fail;
			position += ret;
			remaining -= ret;
		}

		ret = snprintf(position, remaining, "]}");
		if (unlikely(ret < 0 || static_cast<size_t>(ret) >= remaining)) goto fail;

		writtenLength = (position + ret) - buffer;
	}
	else /* simple mode */ {
		ret = snprintf(
			buffer, bufferLength,
			"{"
				"\"uptime\":%llu,"
				"\"time\":\"%s\","
				"\"rssi\":%d"
			"}",
			esp_timer_get_time(),
			timeString,
			ap.rssi
		);
		if (unlikely(ret < 0 || static_cast<size_t>(ret) >= bufferLength)) goto fail;

		writtenLength = ret;
	}
	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, buffer, writtenLength);
	return ESP_OK;

	fail:
	httpd_resp_send_500(req);
	return ESP_FAIL;
}

esp_err_t config_handler(httpd_req_t* req)
{
	int ret;
	char buffer[2048];
	const size_t bufferLength = sizeof(buffer);

	if (req->method == HTTP_POST || req->method == HTTP_PUT) {
		////////////////////////////////////////////////////////////////////////////////
		// Handle new configuration as JSON 

		ret = httpd_req_recv(req, buffer, bufferLength);
		if (ret <= 0) {
			if (ret == HTTPD_SOCK_ERR_TIMEOUT)
				httpd_resp_send_408(req);
			else
				httpd_resp_send_500(req);
			return ESP_FAIL;
		}
		const size_t bytes_received = ret;

		jsmn_parser parser;
		jsmntok_t tokens[128]; 
		const size_t max_tokens = sizeof(tokens) / sizeof(tokens[0]);
		jsmn_init(&parser);
		ret = jsmn_parse(&parser, buffer, bytes_received, tokens, max_tokens - 1);
		if (ret <= 0) {
			if (ret == JSMN_ERROR_NOMEM)
				// TODO: Ask esp-idf to support "413 Payload Too Large" https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/413
				httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Payload Too Large");
			else
				httpd_resp_send_500(req);
			return ESP_FAIL;
		}

		// Add guard token (useful for skipping objects in parsing)
		tokens[ret].end = std::numeric_limits<decltype(jsmntok_t::end)>::max();

		const size_t tokens_count = ret + 1;
		ESP_LOGV(TAG_HTTPD_MAIN, "config_handler! bytes_received=%zu tokens_count=%zu", bytes_received, tokens_count);

		if (config_root(buffer, tokens, nullptr, 0, nullptr) != ESP_OK) {
			httpd_resp_send_500(req);
			return ESP_FAIL;
		}
	}

	////////////////////////////////////////////////////////////////////////////////
	// Response with current configuration as JSON

	if (unlikely(
		config_root(nullptr, nullptr, buffer, bufferLength, &ret) != ESP_OK ||
	    ret < 0 || static_cast<size_t>(ret) >= bufferLength
	)) {
		httpd_resp_send_500(req);
		return ESP_FAIL;
	};
	const size_t bytes_written = ret;

	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, buffer, bytes_written);
	return ESP_OK;
}

esp_err_t capture_handler(httpd_req_t* req)
{
	uint64_t start = esp_timer_get_time();
	auto [fb] = esp_camera_fb_guard();
	if (unlikely(!fb)) {
		ESP_LOGE(TAG_HTTPD_MAIN, "Failed to get frame buffer of camera");
		httpd_resp_send_500(req);
		return ESP_FAIL;
	}
	uint64_t end = esp_timer_get_time();
	printf("Frame took %llu microseconds. Length: %u\n", end - start, fb->len);

	switch (fb->format) {
		case PIXFORMAT_JPEG: {
			httpd_resp_set_type(req, "image/jpeg");
			httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
			httpd_resp_send(req, (const char*)fb->buf, fb->len);
			return ESP_OK;
		}
		// TODO: return BMP if PIXFORMAT_RGB565, see https://en.wikipedia.org/wiki/BMP_file_format
		default: {
			ESP_LOGE(TAG_HTTPD_MAIN, "Camera frame with invalid format: %d ", fb->format);
			httpd_resp_send_500(req);
			return ESP_FAIL;
		}
	}
}

GENERATE_HTTPD_HANDLER_FOR_EMBEDDED_FILE(index_html_gz, "text/html", "gzip");

void init_httpd_main(void)
{
	httpd_handle_t server = NULL;
	httpd_config_t config = HTTPD_DEFAULT_CONFIG();
	config.server_port = 80;
	config.ctrl_port = 32080;
	config.core_id = 0;
	config.lru_purge_enable = true;
	config.stack_size = 8 * 1024;

	ESP_LOGI(TAG_HTTPD_MAIN, "Starting main HTTP server on port: '%d'", config.server_port);
	ESP_ERROR_CHECK(httpd_start(&server, &config));

	httpd_register_uri_handler(server, {
		.uri      = "/",
		.method   = HTTP_GET,
		.handler  = embedded_index_html_gz_handler,
		.user_ctx = nullptr,
	});
	httpd_register_uri_handler(server, {
		.uri      = "/status",
		.method   = HTTP_GET,
		.handler  = status_handler,
		.user_ctx = nullptr,
	});
	httpd_register_uri_handler(server, {
		.uri      = "/config",
		.method   = HTTP_GET,
		.handler  = config_handler,
		.user_ctx = nullptr,
	});
	httpd_register_uri_handler(server, {
		.uri      = "/config",
		.method   = HTTP_POST,
		.handler  = config_handler,
		.user_ctx = nullptr,
	});
	httpd_register_uri_handler(server, {
		.uri      = "/capture",
		.method   = HTTP_GET,
		.handler  = capture_handler,
		.user_ctx = nullptr,
	});
}

////////////////////////////////////////////////////////////////////////////////
// Stream web server

static const char* TAG_HTTPD_STREAM = "httpd-stream";

esp_err_t stream_handler(httpd_req_t* req)
{
	// TODO: stream_handler
	return ESP_FAIL;
}

void init_httpd_stream(void)
{
	httpd_handle_t server = NULL;
	httpd_config_t config = HTTPD_DEFAULT_CONFIG();
	config.server_port = 81;
	config.ctrl_port = 32081;
	config.core_id = 0;
	config.lru_purge_enable = true;
	config.max_uri_handlers = 1;

	ESP_LOGI(TAG_HTTPD_STREAM, "Starting stream HTTP server on port: '%d'", config.server_port);
	ESP_ERROR_CHECK(httpd_start(&server, &config));

	httpd_register_uri_handler(server, {
		.uri      = "/stream",
		.method   = HTTP_GET,
		.handler  = stream_handler,
		.user_ctx = nullptr,
	});
}

////////////////////////////////////////////////////////////////////////////////

void init(void)
{
	init_httpd_main();
	init_httpd_stream();
}

}
