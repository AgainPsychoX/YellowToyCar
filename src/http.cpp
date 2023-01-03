#include <sdkconfig.h>
#include <esp_err.h>
#include <esp_log.h>
#include <esp_http_server.h>
#include <esp_camera.h>
#include <esp_timer.h> // for testing
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

////////////////////////////////////////////////////////////////////////////////
// Camera

#include "camera_pins.hpp"

void init_camera(void)
{
	camera_config_t camera_config = {
		.pin_pwdn  = CAM_PIN_PWDN,
		.pin_reset = CAM_PIN_RESET,
		.pin_xclk = CAM_PIN_XCLK,
		.pin_sccb_sda = CAM_PIN_SIOD,
		.pin_sccb_scl = CAM_PIN_SIOC,
		.pin_d7 = CAM_PIN_D7,
		.pin_d6 = CAM_PIN_D6,
		.pin_d5 = CAM_PIN_D5,
		.pin_d4 = CAM_PIN_D4,
		.pin_d3 = CAM_PIN_D3,
		.pin_d2 = CAM_PIN_D2,
		.pin_d1 = CAM_PIN_D1,
		.pin_d0 = CAM_PIN_D0,
		.pin_vsync = CAM_PIN_VSYNC,
		.pin_href = CAM_PIN_HREF,
		.pin_pclk = CAM_PIN_PCLK,
		.xclk_freq_hz = 20000000,
		.ledc_timer = LEDC_TIMER_0,
		.ledc_channel = LEDC_CHANNEL_0,
		.pixel_format = PIXFORMAT_JPEG,
		.frame_size = FRAMESIZE_UXGA, // Max for OV2640
		.jpeg_quality = 12, // 0-63, lower number means higher quality
		.fb_count = 4,
	#ifdef BOARD_HAS_PSRAM
		.fb_location	= CAMERA_FB_IN_PSRAM,
	#else
		.fb_location	= CAMERA_FB_IN_DRAM, 
	#endif
		.grab_mode = CAMERA_GRAB_LATEST, 
	};
	ESP_ERROR_CHECK(esp_camera_init(&camera_config));
}

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

////////////////////////////////////////////////////////////////////////////////
// Main web server

static const char* TAG_HTTPD_MAIN = "httpd-main";

esp_err_t capture_handler(httpd_req_t* req)
{
	uint64_t start = esp_timer_get_time();
	auto [fb] = esp_camera_fb_guard();
	if (!fb) {
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

	ESP_LOGI(TAG_HTTPD_MAIN, "Starting main HTTP server on port: '%d'", config.server_port);
	ESP_ERROR_CHECK(httpd_start(&server, &config));

	httpd_register_uri_handler(server, {
		.uri      = "/",
		.method   = HTTP_GET,
		.handler  = embedded_index_html_gz_handler,
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
