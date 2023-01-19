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
		if (!*p) return;
		while (*p) {
			if (*p == '=') {
				keyEnd = p;
				break;
			}
			if (*p == '&') {
				keyEnd = p;
				valueEnd = keyEnd + 1; // will end up as 0 length value
				return;
			}
			p++;
		}
		while (*++p) {
			if (*p == '&') {
				break;
			}
		}
		valueEnd = p;
	}

public:
	QuerystringCrawlerIterator(const char* position)
		: keyStart(position), valueEnd(position)
	{
		forward();
	}

	QuerystringCrawlerIterator& operator++() noexcept {
		if (*valueEnd) {
			keyStart = valueEnd + 1;
			forward();
		}
		else {
			keyStart = valueEnd;
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
	std::string_view view;
public:
	using iterator = QuerystringCrawlerIterator;

	QuerystringCrawler(std::string_view&& view)
		: view(view)
	{}

	iterator begin() const noexcept {
		return iterator(this->view.data());
	}
	iterator end() const noexcept {
		return iterator(this->view.data() + this->view.length());
	}
};

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

	// Parse querystring
	// bool detailedMode = std::strstr(req->uri, "?detail") != nullptr;
	bool detailedMode = false;
	for (auto&& [key, value] : QuerystringCrawler(skipToQuerystring(req->uri).data())) {
		printf("status querystring key='%*s'\n", key.length(), key.data());
		switch (fnv1a32(key.begin(), key.end())) {
			case fnv1a32("details"): {
				detailedMode = true;
				break;
			}
		}
		// TODO: add optional parameters to include other data, i.e. connected clients to our AP
	}

	if (detailedMode) {
		wifi_sta_list_t sta_list;
		if (esp_err_t err = esp_wifi_ap_get_sta_list(&sta_list); err != ESP_OK) {
			ESP_ERROR_CHECK_WITHOUT_ABORT(err);
			sta_list.num = 0;
		}

		char* position = buffer;
		size_t remainingLength = bufferLength;
		ret = snprintf(
			position, remainingLength,
			"{"
				"\"time\":\"%s\","
				"\"rssi\":\"%d\","
				"\"uptime\":%llu,"
				"\"stations\":[",
			timeString,
			ap.rssi,
			esp_timer_get_time()
		);
		if (unlikely(ret < 0 || static_cast<size_t>(ret) >= remainingLength)) goto fail;
		position += ret;
		remainingLength -= ret;

		for (int i = 0; i < sta_list.num; i++) {
			const wifi_sta_info_t& sta_info = sta_list.sta[i];
			// TODO: look up for IP assigned by DHCP server
			ret = snprintf(
				position, remainingLength,
				"{\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\",\"rssi\":%d}%c",
				mac_addr_printf(sta_info.mac),
				sta_info.rssi,
				(i + 1 < sta_list.num) ? ',' : ' '
			);
			if (unlikely(ret < 0 || static_cast<size_t>(ret) >= remainingLength)) goto fail;
			position += ret;
			remainingLength -= ret;
		}

		ret = snprintf(position, remainingLength, "]}");
		if (unlikely(ret < 0 || static_cast<size_t>(ret) >= remainingLength)) goto fail;
	}
	else /* simple mode */ {
		ret = snprintf(
			buffer, bufferLength,
			"{"
				"\"time\":\"%s\","
				"\"rssi\":\"%d\","
				"\"uptime\":%llu"
			"}",
			timeString,
			ap.rssi,
			esp_timer_get_time()
		);
	}
	if (unlikely(ret < 0 || static_cast<size_t>(ret) >= bufferLength)) goto fail;
	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, buffer, ret);
	return ESP_OK;

	fail:
	httpd_resp_send_500(req);
	return ESP_FAIL;
}

esp_err_t config_network(char* buffer, jsmntok_t* first_token); // from network.cpp
esp_err_t config_camera(char* buffer, jsmntok_t* first_token); // from camera.cpp

const char* wifi_mode_to_cstr(wifi_mode_t mode)
{
	switch (mode) {
		case WIFI_MODE_STA:     return "sta";
		case WIFI_MODE_AP:      return "ap";
		case WIFI_MODE_APSTA:   return "apsta";
		default:                return NULL;
	}
}

/// Get IP info for given interface (if initialized), optionally falling back to reading from NVS.
esp_err_t get_ip_info(wifi_interface_t interface, esp_netif_ip_info_t& ip_info, nvs::NVSHandle* nvs_handle); // from network.cpp

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
		ret = jsmn_parse(&parser, buffer, bytes_received, tokens, max_tokens);
		if (ret <= 0) {
			if (ret == JSMN_ERROR_NOMEM)
				// TODO: Ask esp-idf to support "413 Payload Too Large" https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/413
				httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Payload Too Large");
			else
				httpd_resp_send_500(req);
			return ESP_FAIL;
		}
		const size_t tokens_count = ret;

		for (size_t i = 0; i < tokens_count; i += 2) {
			const auto* key_token   = tokens + i + 1;
			const auto* value_token = tokens + i + 2;
			printf("JSON parsing in config_handler: key='%*s'\n", key_token->end - key_token->start, buffer + key_token->start);
			switch (fnv1a32(buffer + key_token->start, buffer + key_token->end)) {
				case fnv1a32("network"): {
					if (config_network(buffer, tokens) != ESP_OK) {
						// TODO: how do we nicely pass understandable error? https://github.com/TartanLlama/expected 👀
						httpd_resp_send_500(req);
						return ESP_FAIL;
					}
					break;
				}
				case fnv1a32("camera"): {
					if (config_camera(buffer, tokens) != ESP_OK) {
						httpd_resp_send_500(req);
						return ESP_FAIL;
					}
					break;
				}
				case fnv1a32("control"): {
					// TODO: controls via config handler?
					break;
				}
				case fnv1a32("reset"): {
					if (parseBooleanFast(buffer + value_token->start)) {
						// TODO: reset safely
					}
					break;
				}
				default:
					ESP_LOGV(TAG_HTTPD_MAIN, "Unknown field '%.*s' on root level of config JSON, ignoring.", 
						key_token->end - key_token->start, buffer + key_token->start);
					break;
			}
		}
	}

	////////////////////////////////////////////////////////////////////////////////
	// Response with current configuration as JSON

	esp_err_t nvs_result;
	std::shared_ptr<nvs::NVSHandle> nvs_handle = nvs::open_nvs_handle("network", NVS_READWRITE, &nvs_result);
	if (unlikely(nvs_result != ESP_OK)) return nvs_result;

	wifi_ap_config_t ap_config;
	wifi_sta_config_t sta_config;
	esp_wifi_get_config(WIFI_IF_AP,  reinterpret_cast<wifi_config_t*>(&ap_config));
	esp_wifi_get_config(WIFI_IF_STA, reinterpret_cast<wifi_config_t*>(&sta_config));

	esp_netif_ip_info_t ap_ip_info;
	esp_netif_ip_info_t sta_ip_info;
	get_ip_info(WIFI_IF_AP,  ap_ip_info,  nvs_handle.get());
	get_ip_info(WIFI_IF_STA, sta_ip_info, nvs_handle.get());

	bool sta_static = false;
	wifi_mode_t mode = WIFI_MODE_AP;
	uint32_t fallback = 10000; // in milliseconds
	ESP_IGNORE_ERROR(nvs_handle->get_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)));
	ESP_IGNORE_ERROR(nvs_handle->get_item("wifi_mode",  reinterpret_cast<uint32_t&>(mode)));
	ESP_IGNORE_ERROR(nvs_handle->get_item("fallback",   reinterpret_cast<uint8_t&>(fallback)));

	sensor_t* sensor = esp_camera_sensor_get();
	if (unlikely(!sensor)) goto other_fail;

	ret = snprintf(
		buffer, bufferLength,
		"{"
			"\"network\":{"
				"\"mode\":\"%s\","
				"\"fallback\":%u,"
				"\"gateway\":\"%u.%u.%u.%u\","
				// "\"dns1\":\"%u.%u.%u.%u\","
				// "\"dns2\":\"%u.%u.%u.%u\""
				"\"sta\":{"
					"\"ssid\":\"%.32s\","
					"\"psk\":\"%.64s\","
					"\"ip\":\"%u.%u.%u.%u\","
					"\"mask\":%u,"
					"\"gateway\":\"%u.%u.%u.%u\","
					"\"static\":%c"
				"},"
				"\"ap\":{"
					"\"ssid\":\"%.32s\","
					"\"psk\":\"%.64s\","
					"\"ip\":\"%u.%u.%u.%u\","
					"\"mask\":%u,"
					"\"gateway\":\"%u.%u.%u.%u\","
					"\"channel\":%u,"
					"\"hidden\":%c"
				"}"
			"},"
			"\"camera\":{"
				"\"framesize\":%d,"
				"\"pixformat\":%d,"
				"\"quality\":%d,"
				"\"bpc\":%d,"
				"\"wpc\":%d,"
				"\"hmirror\":%d,"
				"\"vflip\":%d,"
				"\"contrast\":%d,"
				"\"brightness\":%d,"
				"\"sharpness\":%d,"
				"\"denoise\":%d,"
				"\"gain_ceiling\":%d,"
				"\"agc\":%d,"
				"\"agc_gain\":%d,"
				"\"aec\":%d,"
				"\"aec2\":%d,"
				"\"ae_level\":%d,"
				"\"aec_value\":%d,"
				"\"awb\":%d,"
				"\"awb_gain\":%d,"
				"\"wb_mode\":%d,"
				"\"dcw\":%d,"
				"\"raw_gma\":%d,"
				"\"lenc\":%d,"
				"\"special\":%d"
			"},"
			"\"uptime\":%llu"
		"}",
		/* network */
		wifi_mode_to_cstr(mode),
		fallback,
		ip4_addr_printf_unpack(&sta_ip_info.gw),
		/* network.sta */
		sta_config.ssid,
		sta_config.password,
		ip4_addr_printf_unpack(&sta_ip_info.ip),
		numberOfSetBits(sta_ip_info.netmask.addr),
		ip4_addr_printf_unpack(&sta_ip_info.gw),
		'0' + sta_static,
		/* network.ap */
		ap_config.ssid,
		ap_config.password,
		ip4_addr_printf_unpack(&ap_ip_info.ip),
		numberOfSetBits(ap_ip_info.netmask.addr),
		ip4_addr_printf_unpack(&ap_ip_info.gw),
		ap_config.channel,
		'0' + ap_config.ssid_hidden,
		/* camera */
		static_cast<uint8_t>(sensor->status.framesize),
		static_cast<uint8_t>(sensor->pixformat),
		sensor->status.quality,
		sensor->status.bpc,
		sensor->status.wpc,
		sensor->status.hmirror,
		sensor->status.vflip,
		sensor->status.contrast,
		sensor->status.brightness,
		sensor->status.sharpness,
		sensor->status.denoise,
		sensor->status.gainceiling,
		sensor->status.agc,
		sensor->status.agc_gain,
		sensor->status.aec,
		sensor->status.aec2,
		sensor->status.ae_level,
		sensor->status.aec_value,
		sensor->status.awb,
		sensor->status.awb_gain,
		sensor->status.wb_mode,
		sensor->status.dcw,
		sensor->status.raw_gma,
		sensor->status.lenc,
		sensor->status.special_effect,
		/* ... */
		esp_timer_get_time()
	);
	if (unlikely(ret < 0 || static_cast<size_t>(ret) >= bufferLength)) goto other_fail;
	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, buffer, ret);
	printf("uxTaskGetStackHighWaterMark(NULL) for config_handler: %u\n", uxTaskGetStackHighWaterMark(NULL));
	return ESP_OK;

	other_fail:
	httpd_resp_send_500(req);
	printf("uxTaskGetStackHighWaterMark(NULL) for config_handler: %u\n", uxTaskGetStackHighWaterMark(NULL));
	return ESP_FAIL;
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
