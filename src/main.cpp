#include <cstdio>
#include <cinttypes>
#include <cstring>
#include <sdkconfig.h>
#include <esp_err.h>
#include <esp_log.h>
#include <esp_event.h>
#include <esp_netif.h>
#include <nvs_flash.h>
#include <esp_wifi.h>
#include "common.hpp"

static const char* TAG = "main";

#ifndef DEFAULT_AP_CHANNEL
#define DEFAULT_AP_CHANNEL  1
#endif
#ifndef DEFAULT_SSID
#define DEFAULT_SSID        "YellowToyCar"
#endif
#ifndef DEFAULT_PASSWORD
#define DEFAULT_PASSWORD    "AAaa11!!"
#endif

extern "C" void app_main(void)
{
	ESP_LOGI(TAG, "Hello!");

	////////////////////////////////////////

	esp_err_t ret = nvs_flash_init();
	if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
		ESP_ERROR_CHECK(nvs_flash_erase());
		ret = nvs_flash_init();
	}
	ESP_ERROR_CHECK(ret);

	ESP_ERROR_CHECK(esp_netif_init());
	ESP_ERROR_CHECK(esp_event_loop_create_default());
	esp_netif_create_default_wifi_ap();

	wifi_init_config_t wifi_init_config = WIFI_INIT_CONFIG_DEFAULT();
	ESP_ERROR_CHECK(esp_wifi_init(&wifi_init_config));

	wifi_config_t wifi_config = {
		.ap = {
			// .ssid = DEFAULT_SSID,
			// .password = DEFAULT_PASSWORD,
			.ssid_len = sizeof(DEFAULT_SSID) - 1,
			.channel = DEFAULT_AP_CHANNEL,
			.authmode = sizeof(DEFAULT_PASSWORD) > 1 ? WIFI_AUTH_WPA_WPA2_PSK : WIFI_AUTH_OPEN,
			.ssid_hidden = 0,
			.max_connection = 2,
			.beacon_interval = 500,
		},
	};
	strncpy(reinterpret_cast<char*>(wifi_config.ap.ssid),     DEFAULT_SSID,     sizeof(wifi_config_t::ap.ssid));
	strncpy(reinterpret_cast<char*>(wifi_config.ap.password), DEFAULT_PASSWORD, sizeof(wifi_config_t::ap.password));
	ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
	ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
	ESP_ERROR_CHECK(esp_wifi_start());

	init_camera();
	init_httpd_main();
	init_httpd_stream();

	////////////////////////////////////////

	for (int i = 300; i >= 0; i--) {
		printf("Restarting in %d seconds...\n", i);
		delay(1000);
	}
	printf("Restarting now.\n");
	fflush(stdout);
	esp_restart();
}
