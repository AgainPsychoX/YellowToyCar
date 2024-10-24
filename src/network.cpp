#include <sdkconfig.h>
#include <cstring>
#include <esp_err.h>
#include <esp_log.h>
#include <nvs_handle.hpp>
#include <esp_netif.h>
#include <esp_wifi.h>
#include <freertos/timers.h>
#include <jsmn.h>
#include "utils.hpp"

#define FORCE_WIFI_DEFAULTS 0

#ifndef FORCE_DUMP_NETWORK_CONFIG
#	define FORCE_DUMP_NETWORK_CONFIG 0
#endif
#ifndef FORCE_WIFI_DEFAULTS
#	define FORCE_WIFI_DEFAULTS 0
#endif

namespace app::control { // from control.cpp
	extern uptime_t lastControlTime;
	extern uptime_t controlTimeout;
}

namespace app::network
{

esp_err_t config(char* input, jsmntok_t* root, char* output, size_t output_length, int* output_return);

////////////////////////////////////////////////////////////////////////////////
// Utils

#ifndef DEFAULT_AP_CHANNEL
#define DEFAULT_AP_CHANNEL  1
#endif
#ifndef DEFAULT_SSID
#define DEFAULT_SSID        "YellowToyCar"
#endif
#ifndef DEFAULT_PASSWORD
#define DEFAULT_PASSWORD    "AAaa11!!"
#endif

#define NVS_NETWORK_NAMESPACE "network"

esp_netif_t* ap_netif  = nullptr;
esp_netif_t* sta_netif = nullptr;

static_assert(sizeof(esp_ip4_addr_t) == sizeof(uint32_t), "Assuming `esp_ip4_addr_t` is 32 bits value.");
/// Load IP info from NVS for given interface 
esp_err_t load_ip_info_from_nvs(nvs::NVSHandle& nvs_handle, wifi_interface_t interface, esp_netif_ip_info_t& ip_info)
{
	ESP_ERROR_CHECK_RETURN(nvs_handle.get_item(interface == WIFI_IF_AP ? "ap.ip"   : "sta.ip",   reinterpret_cast<uint32_t&>(ip_info.ip)));
	ESP_ERROR_CHECK_RETURN(nvs_handle.get_item(interface == WIFI_IF_AP ? "ap.gw"   : "sta.gw",   reinterpret_cast<uint32_t&>(ip_info.gw)));
	ESP_ERROR_CHECK_RETURN(nvs_handle.get_item(interface == WIFI_IF_AP ? "ap.mask" : "sta.mask", reinterpret_cast<uint32_t&>(ip_info.netmask)));
	return ESP_OK;
}
/// Save IP info to NVS for given interface 
esp_err_t save_ip_info_to_nvs(nvs::NVSHandle& nvs_handle, wifi_interface_t interface, const esp_netif_ip_info_t& ip_info)
{
	ESP_ERROR_CHECK_RETURN(nvs_handle.set_item(interface == WIFI_IF_AP ? "ap.ip"   : "sta.ip",   reinterpret_cast<const uint32_t&>(ip_info.ip)));
	ESP_ERROR_CHECK_RETURN(nvs_handle.set_item(interface == WIFI_IF_AP ? "ap.gw"   : "sta.gw",   reinterpret_cast<const uint32_t&>(ip_info.gw)));
	ESP_ERROR_CHECK_RETURN(nvs_handle.set_item(interface == WIFI_IF_AP ? "ap.mask" : "sta.mask", reinterpret_cast<const uint32_t&>(ip_info.netmask)));
	return ESP_OK;
}
/// Get IP info for given interface (if initialized), optionally falling back to reading from NVS.
esp_err_t get_ip_info(wifi_interface_t interface, esp_netif_ip_info_t& ip_info, nvs::NVSHandle* nvs_handle)
{
	esp_netif_t* netif = interface == WIFI_IF_AP ? ap_netif : sta_netif;
	if (!netif && nvs_handle) return load_ip_info_from_nvs(*nvs_handle, interface, ip_info);
	return esp_netif_get_ip_info(netif, &ip_info);
}

static_assert(sizeof(wifi_mode_t) == sizeof(uint32_t), "Assuming `wifi_mode_t` is 32 bits value.");
inline esp_err_t load_wifi_mode_from_nvs(nvs::NVSHandle& nvs_handle, wifi_mode_t& mode)
{
	return nvs_handle.get_item("wifi_mode", reinterpret_cast<uint32_t&>(mode));
}
inline esp_err_t save_wifi_mode_to_nvs(nvs::NVSHandle& nvs_handle, wifi_mode_t mode)
{
	return nvs_handle.set_item("wifi_mode", static_cast<uint32_t>(mode));
}

////////////////////////////////////////////////////////////////////////////////
// Fallback

static const char* TAG_FALLBACK = "ap-fallback";

constexpr uptime_t reconnectMinimalDelay = 100'000;
constexpr uptime_t reconnectDelayWhenNoStations = 5'000'000;  // Delay, in microseconds, necessary to allow new stations to connect properly.
constexpr uptime_t reconnectDelayWhenStationsConnected = 60'000'000; // Delay, in microseconds, for reconnect attempts when there are stations connected.
constexpr bool reconnectWhenStationsConnected = true; // Whenever we want want to try reconnect even while there are stations connected.
constexpr bool reconnectWhenBeingControlled = false;

uptime_t fallbackTimeout = 10'000'000;  // Microseconds after which we start AP if we can't connect with STA. Configurable by config.
uptime_t disconnectedTimestamp = 0;     // Timestamp when our device (station) lost connection to AP, or 0 if connected, or in AP only mode.
uptime_t nextReconnectTimestamp;        // Timestamp for next reconnect attempt, only valid when `disconnectedTimestamp` is not 0.

TimerHandle_t reconnectTimer = nullptr;

void scheduleDelayedReconnectAsStation();

esp_err_t connectAsStation()
{
	const auto now = esp_timer_get_time();
	const bool isControlled = now - control::lastControlTime < control::controlTimeout;
	if (!isControlled || reconnectWhenBeingControlled) {
		if (isControlled && reconnectWhenBeingControlled) {
			ESP_LOGW(TAG_FALLBACK, "Connecting while still being controlled");
		}
		else {
			ESP_LOGD(TAG_FALLBACK, "Connecting");
		}
		esp_err_t err = ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_connect());
		if (err != ESP_OK) {
			scheduleDelayedReconnectAsStation();
		}
		return err;
	}
	ESP_LOGD(TAG_FALLBACK, "Cannot try connecting right now, delaying");
	scheduleDelayedReconnectAsStation();
	return ESP_ERR_INVALID_STATE;
}

void scheduleDelayedReconnectAsStation(TickType_t ticks)
{
	xTimerReset(reconnectTimer, 0);
	xTimerChangePeriod(reconnectTimer, ticks, 0);
	// TODO: rethink error handling for very unlikely stuff, maybe ASSERT & ASSERT_WITHOUT_ABORT
}

void scheduleDelayedReconnectAsStation()
{
	xTimerStop(reconnectTimer, 0);

	wifi_mode_t mode;
	esp_wifi_get_mode(&mode);
	if (mode == WIFI_MODE_APSTA) {
		wifi_sta_list_t sta_list;
		if (esp_err_t err = esp_wifi_ap_get_sta_list(&sta_list); err != ESP_OK) {
			ESP_ERROR_CHECK_WITHOUT_ABORT(err);
			sta_list.num = 0;
		}
		if (sta_list.num > 0) {
			if (reconnectWhenStationsConnected) {
				ESP_LOGV(TAG_FALLBACK, "Reconnect retry scheduled, with %u stations connected to AP", sta_list.num);
				scheduleDelayedReconnectAsStation(reconnectDelayWhenStationsConnected / 1000 / portTICK_PERIOD_MS);
				return;
			}
			else {
				ESP_LOGV(TAG_FALLBACK, "Waiting for %u client stations disconnect events", sta_list.num);
				return; // wait for the disconnect events
			}
		}
		else /* sta_list.num == 0 */ {
			ESP_LOGV(TAG_FALLBACK, "Reconnect retry scheduled, since no stations connected to AP");
			scheduleDelayedReconnectAsStation(reconnectDelayWhenNoStations / 1000 / portTICK_PERIOD_MS);
			return;
		}
	}
	else if (fallbackTimeout) {
		const auto timeSinceDisconnect = esp_timer_get_time() - disconnectedTimestamp;
		if (timeSinceDisconnect >= fallbackTimeout) {
			ESP_LOGI(TAG_FALLBACK, "Cannot reconnect as STA, falling back to AP...");

			if (!ap_netif) ap_netif = esp_netif_create_default_wifi_ap();
			// IP not set, using the default one (192.168.4.1)

			esp_wifi_set_mode(WIFI_MODE_APSTA);
			esp_netif_dhcps_start(ap_netif);

			scheduleDelayedReconnectAsStation(reconnectDelayWhenNoStations / 1000 / portTICK_PERIOD_MS);
			return;
		}
		const auto remainingTime = fallbackTimeout - timeSinceDisconnect;
		ESP_LOGV(TAG_FALLBACK, "Reconnect retry scheduled - fallback to AP in %" PRIi64 "us", remainingTime);
	}
	else {
		ESP_LOGV(TAG_FALLBACK, "Reconnect retry scheduled - fallback not configured");
	}
	scheduleDelayedReconnectAsStation(reconnectMinimalDelay / 1000 / portTICK_PERIOD_MS);
}

/// Event handler for WIFI_EVENT_STA_DISCONNECTED, called when our STA disconnects from AP, but also on connect failure.
/// See https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/wifi.html#wifi-event-sta-disconnected
void handle_sta_disconnected(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data)
{
	const auto& eventData = *static_cast<const wifi_event_sta_disconnected_t*>(event_data);
	if (disconnectedTimestamp) {
		ESP_LOGD(TAG_FALLBACK, "Failed to connect as station! reason=%u rssi=%d", eventData.reason, eventData.rssi);
	}
	else /* disconnectedTimestamp == 0, was connected, but not anymore */ {
		ESP_LOGD(TAG_FALLBACK, "Disconnected! reason=%u rssi=%d", eventData.reason, eventData.rssi);
		disconnectedTimestamp = esp_timer_get_time();
	}
	scheduleDelayedReconnectAsStation();
}

void handle_ap_stadisconnect(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data)
{
	// If some station disconnect from our AP, it might be good time to reconnect as STA
	if (disconnectedTimestamp) {
		scheduleDelayedReconnectAsStation();
	}
}

esp_event_handler_instance_t ehi_sta_disconnect;
esp_event_handler_instance_t ehi_ap_stadisconnect;

esp_err_t registerDisconnectEventHandlers() {
	ESP_LOGV(TAG_FALLBACK, "Registering disconnect event handlers");
	if (esp_err_t err = esp_event_handler_instance_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, handle_sta_disconnected, nullptr, &ehi_sta_disconnect); err != ESP_OK) return err;
	if (esp_err_t err = esp_event_handler_instance_register(WIFI_EVENT, WIFI_EVENT_AP_STADISCONNECTED, handle_ap_stadisconnect, nullptr, &ehi_ap_stadisconnect); err != ESP_OK) return err;
	// TODO: add WIFI_EVENT_AP_STACONNECTED to delay reconnecting as STA
	return ESP_OK;
}
esp_err_t unregisterDisconnectEventHandlers() {
	ESP_LOGV(TAG_FALLBACK, "Unregistering disconnect event handlers");
	if (esp_err_t err = esp_event_handler_instance_unregister(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, ehi_sta_disconnect); err != ESP_OK) return err;
	if (esp_err_t err = esp_event_handler_instance_unregister(WIFI_EVENT, WIFI_EVENT_AP_STADISCONNECTED, ehi_ap_stadisconnect); err != ESP_OK) return err;
	return ESP_OK;
}

////////////////////////////////////////////////////////////////////////////////
// Initialization

static const char* TAG_INIT_NETWORK = "init-network";

void init()
{
	esp_err_t nvs_result;
	std::shared_ptr<nvs::NVSHandle> nvs_handle = nvs::open_nvs_handle(NVS_NETWORK_NAMESPACE, NVS_READWRITE, &nvs_result);
	ESP_ERROR_CHECK(nvs_result);

	ESP_ERROR_CHECK(esp_netif_init());
	ESP_ERROR_CHECK(esp_event_loop_create_default());

	wifi_init_config_t wifi_init_config = WIFI_INIT_CONFIG_DEFAULT();
	ESP_ERROR_CHECK(esp_wifi_init(&wifi_init_config));

	esp_netif_ip_info_t ap_ip_info;
	if (unlikely(load_ip_info_from_nvs(*nvs_handle, WIFI_IF_AP, ap_ip_info) != ESP_OK)) {
		ESP_LOGD(TAG_INIT_NETWORK, "Missing IP info for %s interface, using defaults", "AP");
		esp_netif_t* ap_netif = esp_netif_create_default_wifi_ap();
		esp_netif_get_ip_info(ap_netif, &ap_ip_info);
		save_ip_info_to_nvs(*nvs_handle, WIFI_IF_AP, ap_ip_info);
		esp_netif_destroy_default_wifi(ap_netif);
	}

	esp_netif_ip_info_t sta_ip_info;
	if (unlikely(load_ip_info_from_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info) != ESP_OK)) {
		ESP_LOGD(TAG_INIT_NETWORK, "Missing IP info for %s interface, using defaults", "STA");
		// Note: DHCP client is used on default, so use preset to avoid uninitialized garbage.
		// esp_netif_t* sta_netif = esp_netif_create_default_wifi_sta();
		// esp_netif_get_ip_info(sta_netif, &ap_ip_info);
		// save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info);
		// esp_netif_destroy_default_wifi(sta_netif);
		esp_netif_ip_info_t preset = {
			.ip = {0},
			.netmask = {ESP_IP4TOADDR(255, 255, 255, 0)},
			.gw = {0},
		};
		save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, preset);
	}

	wifi_mode_t mode;
	if (likely(load_wifi_mode_from_nvs(*nvs_handle, mode)) == ESP_OK && !FORCE_WIFI_DEFAULTS) {
		// Start networking as configured (in NVS). 
		// Note that some stuff (like SSIDs & passwords) are persisted by Wi-Fi stack internally.

		const bool use_ap  = mode == WIFI_MODE_AP  || mode == WIFI_MODE_APSTA;
		const bool use_sta = mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA;

		if (use_ap) {
			ap_netif = esp_netif_create_default_wifi_ap();
			esp_netif_set_ip_info(ap_netif, &ap_ip_info);
		}
		if (use_sta) {
			sta_netif = esp_netif_create_default_wifi_sta();
			esp_netif_set_ip_info(sta_netif, &sta_ip_info);

			bool sta_static = false;
			ESP_IGNORE_ERROR(nvs_handle->get_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)));

			if (sta_static) esp_netif_dhcpc_stop(sta_netif);
		}

		ESP_ERROR_CHECK(esp_wifi_set_mode(mode));

		if (esp_log_level_get(TAG_INIT_NETWORK) >= ESP_LOG_DEBUG || FORCE_DUMP_NETWORK_CONFIG) {
			char buffer[1024];
			int ret;
			config(nullptr, nullptr, buffer, sizeof(buffer), &ret);
			ESP_LOGD(TAG_INIT_NETWORK, "Networking config dump: %.*s", 1024 - 1, buffer);
		}
	}
	else {
		ap_netif = esp_netif_create_default_wifi_ap();
		esp_wifi_restore();
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
		std::strncpy(reinterpret_cast<char*>(wifi_config.ap.ssid),     DEFAULT_SSID,     sizeof(wifi_config_t::ap.ssid));
		std::strncpy(reinterpret_cast<char*>(wifi_config.ap.password), DEFAULT_PASSWORD, sizeof(wifi_config_t::ap.password));
		ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
		ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));

		ESP_LOGW(TAG_INIT_NETWORK, "Missing data! Defaulting to AP with SSID: '%s' and PSK: '%s'", DEFAULT_SSID, DEFAULT_PASSWORD);
	}

	ESP_IGNORE_ERROR(nvs_handle->get_item("fallback", reinterpret_cast<uint64_t&>(fallbackTimeout)));

	ESP_ERROR_CHECK(esp_event_handler_instance_register(
		WIFI_EVENT, WIFI_EVENT_STA_START, 
		[] (void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
			ESP_LOGV(TAG_INIT_NETWORK, "Station started, trying to connect");
			connectAsStation();
		}, 
		nullptr, nullptr
	));

	ESP_ERROR_CHECK(esp_event_handler_instance_register(
		WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, 
		[] (void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
			disconnectedTimestamp = 0;
			xTimerStop(reconnectTimer, 0);
		}, 
		nullptr, nullptr
	));

	ESP_ERROR_CHECK(registerDisconnectEventHandlers());

	reconnectTimer = xTimerCreate(
		"wifi-reconnect", // name
		reconnectDelayWhenNoStations / 1000 / portTICK_PERIOD_MS, // period in ticks
		pdFALSE, // auto-reload
		nullptr, // ??? static_cast<void*>(0)
		[] (TimerHandle_t) { connectAsStation(); } // callback
	);

	ESP_ERROR_CHECK(esp_wifi_start());
}

////////////////////////////////////////////////////////////////////////////////
// Configuration

static const char* TAG_CONFIG_NETWORK = "config-network";

const char* wifi_mode_to_cstr(wifi_mode_t mode)
{
	switch (mode) {
		case WIFI_MODE_STA:     return "sta";
		case WIFI_MODE_AP:      return "ap";
		case WIFI_MODE_APSTA:   return "apsta";
		default:                return NULL;
	}
}

struct wifi_common_config_t
{
	char ssid[32];
	char password[64];
};

inline bool has_simple_value(const jsmntok_t* token)
{
	if (token->type == JSMN_UNDEFINED) return false;
	if (token->type == JSMN_OBJECT) return false;
	if (token->type == JSMN_ARRAY) return false;
	return true;
}

esp_err_t config__common_keys(
	char* input, uint32_t key_hash, jsmntok_t* value_token,
	wifi_common_config_t& wifi_config, esp_netif_ip_info_t& ip_info
) {
	const size_t value_length = value_token->end - value_token->start;
	switch (key_hash) {
		case fnv1a32("ip"): {
			input[value_token->end] = 0;
			if (esp_netif_str_to_ip4(input + value_token->start, &ip_info.ip) != ESP_OK)
				return ESP_FAIL;
			break;
		}
		case fnv1a32("gateway"):
		case fnv1a32("gw"): {
			input[value_token->end] = 0;
			if (esp_netif_str_to_ip4(input + value_token->start, &ip_info.gw) != ESP_OK)
				return ESP_FAIL;
			break;
		}
		case fnv1a32("mask"):
		case fnv1a32("netmask"): {
			input[value_token->end] = 0;
			if (std::strchr(input + value_token->start, '.') == nullptr) {
				const uint8_t maskLength = std::atoi(input + value_token->start);
				if (maskLength > 30) 
					return ESP_FAIL;
				ip_info.netmask.addr = hton(~0u << (32 - maskLength));
				ESP_LOGV(TAG_CONFIG_NETWORK, "Setting mask as length %u. Resulting address: " IPSTR, 
					maskLength, IP2STR(&ip_info.netmask));
			}
			else {
				if (esp_netif_str_to_ip4(input + value_token->start, &ip_info.netmask) != ESP_OK)
					return ESP_FAIL;
			}
			break;
		}
		case fnv1a32("ssid"): {
			if (value_length > sizeof(wifi_config.ssid)) return ESP_FAIL;
			std::strncpy(wifi_config.ssid, input + value_token->start, value_length);
			wifi_config.ssid[value_length] = '\0';
			break;
		}
		case fnv1a32("psk"):
		case fnv1a32("password"): {
			if (value_token->type == JSMN_STRING && value_length != 0) {
				if (value_length > sizeof(wifi_config.password) - 1) return ESP_FAIL;
				std::strncpy(wifi_config.password, input + value_token->start, value_length);
				wifi_config.password[value_length] = '\0';
			}
			else {
				std::memset(wifi_config.password, 0, sizeof(wifi_config.password));
			}
			break;
		}
	}
	return ESP_OK;
}

inline esp_err_t config__ap(
	char* input, jsmntok_t* root,
	wifi_ap_config_t& wifi_config, esp_netif_ip_info_t& ip_info
) {
	if (unlikely(root->type != JSMN_OBJECT))
		return ESP_FAIL;
	if (unlikely(root->size < 1)) 
		return ESP_FAIL;
	for (jsmntok_t* token = root + 1;;) {
		auto* key_token   = token;
		auto* value_token = token + 1;
		ESP_LOGV(TAG_CONFIG_NETWORK, "key='%.*s' value='%.*s'", 
			key_token->end - key_token->start, input + key_token->start,
			value_token->end - value_token->start, input + value_token->start
		);
		if (unlikely(!has_simple_value(value_token)))
			return ESP_FAIL;
		const size_t value_length = value_token->end - value_token->start;
		const auto key_hash = fnv1a32(input + key_token->start, input + key_token->end);
		if (config__common_keys(input, key_hash, value_token, reinterpret_cast<wifi_common_config_t&>(wifi_config), ip_info) != ESP_OK)
			return ESP_FAIL;
		switch (key_hash) {
			case fnv1a32("ssid"): {
				wifi_config.ssid_len = value_length;
				break;
			}
			case fnv1a32("psk"):
			case fnv1a32("password"): {
				if (value_token->type == JSMN_STRING && value_length != 0) {
					wifi_config.authmode = WIFI_AUTH_WPA2_PSK;
				}
				else {
					wifi_config.authmode = WIFI_AUTH_OPEN;
				}
				break;
			}
			case fnv1a32("channel"): {
				wifi_config.channel = std::atoi(input + value_token->start);
				break;
			}
			case fnv1a32("hidden"): {
				wifi_config.ssid_hidden = parseBooleanFast(input + value_token->start);
				break;
			}
		}

		// Skip primitive pair (key & value)
		token += 2;
		if (root->end < token->end)
			break;
	}

	return ESP_OK;
}

inline esp_err_t config__sta(
	char* input, jsmntok_t* root,
	wifi_sta_config_t& wifi_config, esp_netif_ip_info_t& ip_info, bool& static_ip
) {
	if (unlikely(root->type != JSMN_OBJECT))
		return ESP_FAIL;
	if (unlikely(root->size < 1)) 
		return ESP_FAIL;
	for (jsmntok_t* token = root + 1;;) {
		auto* key_token   = token;
		auto* value_token = token + 1;
		ESP_LOGV(TAG_CONFIG_NETWORK, "key='%.*s' value='%.*s'", 
			key_token->end - key_token->start, input + key_token->start,
			value_token->end - value_token->start, input + value_token->start
		);
		if (unlikely(!has_simple_value(value_token)))
			return ESP_FAIL;
		const size_t value_length = value_token->end - value_token->start;
		const auto key_hash = fnv1a32(input + key_token->start, input + key_token->end);
		if (config__common_keys(input, key_hash, value_token, reinterpret_cast<wifi_common_config_t&>(wifi_config), ip_info) != ESP_OK)
			return ESP_FAIL;
		switch (key_hash) {
			case fnv1a32("static"): {
				static_ip = parseBooleanFast(input + value_token->start);
				break;
			}
			case fnv1a32("psk"):
			case fnv1a32("password"): {
				wifi_config.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
				if (value_token->type == JSMN_STRING && value_length != 0) {
					wifi_config.threshold.authmode = WIFI_AUTH_WPA2_PSK;
				}
				else {
					wifi_config.threshold.authmode = WIFI_AUTH_OPEN;
				}
				break;
			}
		}

		// Skip primitive pair (key & value)
		token += 2;
		if (root->end < token->end)
			break;
	}

	return ESP_OK;
}

/// @brief Applies (and/or reads current) JSON configuration for networking.
/// @param[in] input Buffer with JSON data that was parsed into JSON into tokens.
///		Note: Passed non-const to allow in-place strings manipulation.
/// @param[in] root JSMN JSON object token related to the config to be parsed.
/// @param[out] output Optional buffer for writing JSON with current configuration.
/// @param[in] output_length Length of output buffer.
/// @param[out] output_return Used to return number of bytes that would be written 
/// 	to the output, or negative for error. Basically `printf`-like return.
/// @return 
esp_err_t config(
	char* input, jsmntok_t* root,
	char* output, size_t output_length, int* output_return
) {
	esp_err_t nvs_result;
	std::shared_ptr<nvs::NVSHandle> nvs_handle = nvs::open_nvs_handle(NVS_NETWORK_NAMESPACE, NVS_READWRITE, &nvs_result);
	ESP_ERROR_CHECK(nvs_result);

	wifi_ap_config_t ap_config = {};
	wifi_sta_config_t sta_config = {};
	esp_wifi_get_config(WIFI_IF_AP,  reinterpret_cast<wifi_config_t*>(&ap_config));
	esp_wifi_get_config(WIFI_IF_STA, reinterpret_cast<wifi_config_t*>(&sta_config));

	esp_netif_ip_info_t ap_ip_info;
	esp_netif_ip_info_t sta_ip_info;
	get_ip_info(WIFI_IF_AP,  ap_ip_info,  nvs_handle.get());
	get_ip_info(WIFI_IF_STA, sta_ip_info, nvs_handle.get());

	wifi_mode_t mode = WIFI_MODE_AP;
	bool sta_static = false;
	ESP_IGNORE_ERROR(load_wifi_mode_from_nvs(*nvs_handle, mode));
	ESP_IGNORE_ERROR(nvs_handle->get_item("fallback", reinterpret_cast<uint64_t&>(fallbackTimeout)));
	ESP_IGNORE_ERROR(nvs_handle->get_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)));

	if (input) {
		if (unlikely(root->type != JSMN_OBJECT))
			return ESP_FAIL;
		if (unlikely(root->size < 1)) 
			return ESP_FAIL;
		for (jsmntok_t* token = root + 1;;) {
			auto* key_token   = token;
			auto* value_token = token + 1;
			ESP_LOGV(TAG_CONFIG_NETWORK, "key='%.*s' value='%.*s'", 
				key_token->end - key_token->start, input + key_token->start,
				value_token->end - value_token->start, input + value_token->start
			);

			// Dispatch based on type and key
			const auto key_hash = fnv1a32(input + key_token->start, input + key_token->end);
			if (value_token->type == JSMN_OBJECT) {
				ESP_LOGV(TAG_CONFIG_NETWORK, "type=object size=%zu", value_token->size);
				switch (key_hash) {
					case fnv1a32("ap"): {
						if (config__ap(input, value_token, ap_config, ap_ip_info) != ESP_OK)
							return ESP_FAIL;
						break;
					}
					case fnv1a32("sta"): {
						if (config__sta(input, value_token, sta_config, sta_ip_info, sta_static) != ESP_OK)
							return ESP_FAIL;
						break;
					}
					default:
						ESP_LOGD(TAG_CONFIG_NETWORK, "Unknown field '%.*s', ignoring.", 
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
					case fnv1a32("mode"): {
						const uint32_t value_hash = fnv1a32(input + value_token->start, input + value_token->end);
						switch (value_hash) {
							case fnv1a32("sta"):   mode = WIFI_MODE_STA; break;
							case fnv1a32("ap"):    mode = WIFI_MODE_AP; break;
							case fnv1a32("nat"):
							case fnv1a32("apsta"): mode = WIFI_MODE_APSTA; break;
							default:
								return ESP_FAIL;
						}
						break;
					}
					case fnv1a32("fallback"): {
						fallbackTimeout = std::atoi(input + value_token->start) * 1000;
						if (fallbackTimeout && fallbackTimeout < reconnectDelayWhenNoStations) {
							ESP_LOGD(TAG_CONFIG_NETWORK, "Fallback timeout clamped to minimal value.");
							fallbackTimeout = reconnectDelayWhenNoStations;
						}
						break;
					}
					default:
						ESP_LOGD(TAG_CONFIG_NETWORK, "Unknown field '%.*s', ignoring.", 
							key_token->end - key_token->start, input + key_token->start);
						break;
				}

				// Skip primitive pair (key & value)
				token += 2;
				if (root->end < token->end)
					goto done;
			}
		}
		done:

		ESP_ERROR_CHECK_RETURN(save_ip_info_to_nvs(*nvs_handle, WIFI_IF_AP,  ap_ip_info));
		ESP_ERROR_CHECK_RETURN(save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info));
		ESP_ERROR_CHECK_RETURN(save_wifi_mode_to_nvs(*nvs_handle, mode));
		ESP_ERROR_CHECK_RETURN(nvs_handle->set_item("fallback", reinterpret_cast<uint64_t&>(fallbackTimeout)));
		ESP_ERROR_CHECK_RETURN(nvs_handle->set_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)));
		ESP_ERROR_CHECK_RETURN(nvs_handle->commit());

		const bool use_ap  = mode == WIFI_MODE_AP  || mode == WIFI_MODE_APSTA;
		const bool use_sta = mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA;

		// WiFi AP/STA specific config will persisted by WiFi component (`esp_wifi_set_config`)

		// Stop reconnecting behaviour
		unregisterDisconnectEventHandlers();
		disconnectedTimestamp = 0;
		xTimerStop(reconnectTimer, 0);

		esp_wifi_disconnect();
		esp_wifi_stop();

		if (!ap_netif)  ap_netif  = esp_netif_create_default_wifi_ap();
		if (!sta_netif) sta_netif = esp_netif_create_default_wifi_sta();

		esp_netif_dhcps_stop(ap_netif);
		esp_netif_dhcpc_stop(sta_netif);

		ESP_ERROR_CHECK_RETURN(esp_netif_set_ip_info(ap_netif, &ap_ip_info));
		ESP_ERROR_CHECK_RETURN(esp_netif_set_ip_info(sta_netif, &sta_ip_info));

		// FIXME: need to update DHCP server addresses (incl. leases) if AP address was changed

		esp_wifi_set_mode(WIFI_MODE_APSTA); // to allow setting config without error
		ESP_ERROR_CHECK_RETURN(esp_wifi_set_config(WIFI_IF_AP,  reinterpret_cast<wifi_config_t*>(&ap_config)));
		ESP_ERROR_CHECK_RETURN(esp_wifi_set_config(WIFI_IF_STA, reinterpret_cast<wifi_config_t*>(&sta_config)));

		esp_wifi_set_mode(mode);

		if (use_ap) esp_netif_dhcps_start(ap_netif);
		if (use_sta && !sta_static) esp_netif_dhcpc_start(sta_netif);

		ESP_ERROR_CHECK_RETURN(registerDisconnectEventHandlers());

		esp_wifi_start();
		// esp_wifi_connect(); on WIFI_EVENT_STA_START event

		if (mode == WIFI_MODE_APSTA) {
			// TODO: NAT
		}

		// TODO: is restart necessary to apply the changes?
		// TODO: use separate one-time task to apply all those changes, to allow for outputting response

		ESP_LOGI(TAG_CONFIG_NETWORK, "Network config applied");
		return ESP_OK;
	}

	if (output_return) {
		*output_return = std::snprintf(
			output, output_length,
			"{"
				"\"mode\":\"%s\","
				"\"fallback\":%u,"
				"\"sta\":{"
					"\"ssid\":\"%.32s\","
					"\"psk\":\"%.64s\","
					"\"ip\":\"" IPSTR "\","
					"\"mask\":%u,"
					"\"gateway\":\"" IPSTR "\","
					"\"static\":%c"
				"},"
				"\"ap\":{"
					"\"ssid\":\"%.32s\","
					"\"psk\":\"%.64s\","
					"\"ip\":\"" IPSTR "\","
					"\"mask\":%u,"
					"\"gateway\":\"" IPSTR "\","
					"\"channel\":%u,"
					"\"hidden\":%c"
				"}"
			"}",
			wifi_mode_to_cstr(mode),
			static_cast<unsigned int>(fallbackTimeout / 1000),
			/* network.sta */
			sta_config.ssid,
			sta_config.password,
			IP2STR(&sta_ip_info.ip),
			numberOfSetBits(sta_ip_info.netmask.addr),
			IP2STR(&sta_ip_info.gw),
			'0' + sta_static,
			/* network.ap */
			ap_config.ssid,
			ap_config.password,
			IP2STR(&ap_ip_info.ip),
			numberOfSetBits(ap_ip_info.netmask.addr),
			IP2STR(&ap_ip_info.gw),
			ap_config.channel,
			'0' + ap_config.ssid_hidden
		);
	}

	return ESP_OK;
}

////////////////////////////////////////////////////////////////////////////////

}
