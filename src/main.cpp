#include <sdkconfig.h>
#include <cstdio>
#include <cinttypes>
#include <cstring>
#include <esp_err.h>
#include <esp_log.h>
#include <esp_event.h>
#include <esp_netif.h>
#include <nvs_flash.h>
#include <esp_wifi.h>
#include <esp_sntp.h>
#include "common.hpp"

static const char* TAG = "main";

inline void init_nvs()
{
	esp_err_t ret = nvs_flash_init();
	if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
		ESP_ERROR_CHECK(nvs_flash_erase());
		ret = nvs_flash_init();
	}
	ESP_ERROR_CHECK(ret);
}

inline void init_time()
{
	sntp_setoperatingmode(SNTP_OPMODE_POLL);
	sntp_setservername(0, "pl.pool.ntp.org");
	sntp_init();

	// TODO: allow changing NTP server & timezone
	setenv("TZ", "CET-1CEST,M3.5.0,M10.5.0/3", 1); // Hardcoded for Europe/Warsaw
	tzset();
}

void init_network(void);        // from network.cpp
void init_camera(void);         // from camera.cpp
void init_httpd_main(void);     // from http.cpp
void init_httpd_stream(void);   // from http.cpp

extern "C" void app_main(void)
{
	ESP_LOGI(TAG, "Hello!");

	////////////////////////////////////////

	init_nvs();
	init_network();
	init_camera();
	init_httpd_main();
	init_httpd_stream();
	init_time();

	////////////////////////////////////////

	for (int i = 300; i >= 0; i--) {
		printf("Restarting in %d seconds...\n", i);
		delay(1000);
	}
	printf("Restarting now.\n");
	fflush(stdout);
	esp_restart();
}
