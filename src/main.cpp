#include <sdkconfig.h>
#include <esp_log.h>
#include <nvs_flash.h>
#include <esp_sntp.h>
#include "common.hpp"

#include "hal.hpp"
#include "control.hpp"
namespace app::nvs {
	inline void init()
	{
		esp_err_t ret = nvs_flash_init();
		if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
			ESP_ERROR_CHECK(nvs_flash_erase());
			ret = nvs_flash_init();
		}
		ESP_ERROR_CHECK(ret);
	}
}
namespace app::network { // from network.cpp
	void init(void);
}
namespace app::camera { // from camera.cpp
	void init(void);
}
namespace app::http { // from http.cpp
	void init(void);
}
namespace app::time {
	inline void init()
	{
		esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
		esp_sntp_setservername(0, "pl.pool.ntp.org");
		esp_sntp_init();

		// TODO: allow changing NTP server & timezone
		setenv("TZ", "CET-1CEST,M3.5.0,M10.5.0/3", 1); // Hardcoded for Europe/Warsaw
		tzset();
	}
}
namespace app::udp {
	void init();
	void listen();
}

using namespace app;

static const char* TAG = "main";

extern "C" void app_main(void)
{
	ESP_LOGI(TAG, "Hello!");

	////////////////////////////////////////

	hal::init();
	nvs::init();
	network::init();
	camera::init();
	control::init();
	http::init();
	time::init();

	////////////////////////////////////////

	udp::init();
	for (;;) {
		udp::listen();
		if (errno) udp::init();
		delay(1);
		control::tick();
	}
}
