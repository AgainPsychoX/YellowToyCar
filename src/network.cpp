#include <sdkconfig.h>
#include <cstring>
#include <esp_err.h>
#include <esp_log.h>
#include <nvs_handle.hpp>
#include <esp_netif.h>
#include <esp_wifi.h>
#include <jsmn.h>
#include "utils.hpp"

////////////////////////////////////////////////////////////////////////////////

#ifndef DEFAULT_AP_CHANNEL
#define DEFAULT_AP_CHANNEL  1
#endif
#ifndef DEFAULT_SSID
#define DEFAULT_SSID        "YellowToyCar"
#endif
#ifndef DEFAULT_PASSWORD
#define DEFAULT_PASSWORD    "AAaa11!!"
#endif

#define NETWORK_MISC_NVS_NAMESPACE "network"

#define ip4_addr_printf_unpack(ip) ip4_addr_get_byte(ip, 0), ip4_addr_get_byte(ip, 1), ip4_addr_get_byte(ip, 2), ip4_addr_get_byte(ip, 3)

static const char* TAG_INIT_NETWORK = "init-network";

esp_netif_t* ap_netif  = nullptr;
esp_netif_t* sta_netif = nullptr;

static_assert(sizeof(esp_ip4_addr_t) == sizeof(uint32_t), "Assuming `esp_ip4_addr_t` is 32 bits value.");
/// Load IP info from NVS for given interface 
esp_err_t load_ip_info_from_nvs(nvs::NVSHandle& nvs_handle, wifi_interface_t interface, esp_netif_ip_info_t& ip_info)
{
	esp_err_t err;
	if (unlikely(err = nvs_handle.get_item(interface == WIFI_IF_AP ? "ap.ip"   : "sta.ip",   reinterpret_cast<uint32_t&>(ip_info.ip))       != ESP_OK)) return err;
	if (unlikely(err = nvs_handle.get_item(interface == WIFI_IF_AP ? "ap.gw"   : "sta.gw",   reinterpret_cast<uint32_t&>(ip_info.gw))       != ESP_OK)) return err;
	if (unlikely(err = nvs_handle.get_item(interface == WIFI_IF_AP ? "ap.mask" : "sta.mask", reinterpret_cast<uint32_t&>(ip_info.netmask))  != ESP_OK)) return err;
	return ESP_OK;
}
/// Save IP info to NVS for given interface 
esp_err_t save_ip_info_to_nvs(nvs::NVSHandle& nvs_handle, wifi_interface_t interface, const esp_netif_ip_info_t& ip_info)
{
	esp_err_t err;
	if (unlikely(err = nvs_handle.set_item(interface == WIFI_IF_AP ? "ap.ip"   : "sta.ip",   reinterpret_cast<const uint32_t&>(ip_info.ip))       != ESP_OK)) return err;
	if (unlikely(err = nvs_handle.set_item(interface == WIFI_IF_AP ? "ap.gw"   : "sta.gw",   reinterpret_cast<const uint32_t&>(ip_info.gw))       != ESP_OK)) return err;
	if (unlikely(err = nvs_handle.set_item(interface == WIFI_IF_AP ? "ap.mask" : "sta.mask", reinterpret_cast<const uint32_t&>(ip_info.netmask))  != ESP_OK)) return err;
	return ESP_OK;
}
/// Get IP info for given interface (if initialized), optionally falling back to reading from NVS.
esp_err_t get_ip_info(wifi_interface_t interface, esp_netif_ip_info_t& ip_info, nvs::NVSHandle* nvs_handle)
{
	esp_netif_t* netif = interface == WIFI_IF_AP ? ap_netif : sta_netif;
	if (!netif && nvs_handle) return load_ip_info_from_nvs(*nvs_handle, interface, ip_info);
	return esp_netif_get_ip_info(netif, &ip_info);
}

void init_network()
{
	esp_err_t nvs_result;
	std::shared_ptr<nvs::NVSHandle> nvs_handle = nvs::open_nvs_handle(NETWORK_MISC_NVS_NAMESPACE, NVS_READWRITE, &nvs_result);
	ESP_ERROR_CHECK(nvs_result);

	ESP_ERROR_CHECK(esp_netif_init());
	ESP_ERROR_CHECK(esp_event_loop_create_default());

	wifi_init_config_t wifi_init_config = WIFI_INIT_CONFIG_DEFAULT();
	ESP_ERROR_CHECK(esp_wifi_init(&wifi_init_config));

	/* use_config: */ {
		esp_netif_ip_info_t ap_ip_info;
		esp_netif_ip_info_t sta_ip_info;
		if (unlikely(load_ip_info_from_nvs(*nvs_handle, WIFI_IF_AP,  ap_ip_info)  != ESP_OK)) {
			ESP_LOGV(TAG_INIT_NETWORK, "Missing IP info for %s interface, using defaults", "AP");
			esp_netif_t* ap_netif = esp_netif_create_default_wifi_ap();
			esp_netif_get_ip_info(ap_netif, &ap_ip_info);
			save_ip_info_to_nvs(*nvs_handle, WIFI_IF_AP, ap_ip_info);
			esp_netif_destroy_default_wifi(ap_netif);
		}
		if (unlikely(load_ip_info_from_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info) != ESP_OK)) {
			ESP_LOGV(TAG_INIT_NETWORK, "Missing IP info for %s interface, using defaults", "STA");
			esp_netif_t* sta_netif = esp_netif_create_default_wifi_sta();
			esp_netif_get_ip_info(sta_netif, &ap_ip_info);
			save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info);
			esp_netif_destroy_default_wifi(sta_netif);
		}

		wifi_mode_t mode;
		static_assert(sizeof(wifi_mode_t) == sizeof(uint32_t), "Assuming `wifi_mode_t` is 32 bits value.");
		if (unlikely(nvs_handle->get_item("wifi_mode", reinterpret_cast<uint32_t&>(mode)) != ESP_OK)) goto use_default;

		bool sta_static;
		static_assert(sizeof(bool) == sizeof(uint8_t), "Assuming `bool` is 8 bits value.");
		if (unlikely(nvs_handle->get_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)) != ESP_OK)) goto use_default;

		if (mode == WIFI_MODE_AP || mode == WIFI_MODE_APSTA) {
			ap_netif = esp_netif_create_default_wifi_ap();
			esp_netif_set_ip_info(ap_netif, &ap_ip_info);
		}
		if (mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA) {
			sta_netif = esp_netif_create_default_wifi_sta();
			esp_netif_set_ip_info(sta_netif, &sta_ip_info);
		}

		// WiFi config, both for AP and STA should be persisted by WiFi component already. 
		// If it fails, it will fallback to some defaults (other than in our fallback code below).
		// TODO: try detect wifi config fallbacks?

		ESP_ERROR_CHECK(esp_wifi_set_mode(mode));
		goto starting;
	}

	use_default: {
		ESP_LOGV(TAG_INIT_NETWORK, "Missing data, falling back to default config");

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
		strncpy(reinterpret_cast<char*>(wifi_config.ap.ssid),     DEFAULT_SSID,     sizeof(wifi_config_t::ap.ssid));
		strncpy(reinterpret_cast<char*>(wifi_config.ap.password), DEFAULT_PASSWORD, sizeof(wifi_config_t::ap.password));
		ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
		ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
		goto starting;
	}

	starting:
	ESP_ERROR_CHECK(esp_wifi_start());

	// TODO: create race between timeout fallback and connected event, if timed out -> stop STA, start AP
}

////////////////////////////////////////////////////////////////////////////////

static const char* TAG_CONFIG_NETWORK = "config-network";

struct wifi_common_config_t {
	char ssid[32];
	char password[64];
};

inline bool has_simple_value(const jsmntok_t* token) {
	if (token->type == JSMN_UNDEFINED) return false;
	if (token->type == JSMN_OBJECT) return false;
	if (token->type == JSMN_ARRAY) return false;
	return true;
}

esp_err_t config_network_common(
	char* buffer, jsmntok_t* first_token,
	wifi_common_config_t& wifi_config, esp_netif_ip_info_t& ip_info
) {
	if (unlikely(first_token->type != JSMN_OBJECT))
		return ESP_FAIL;

	for (size_t i = 0; i < first_token->size; i += 2) {
		auto* key_token   = first_token + i + 1;
		auto* value_token = first_token + i + 2;
		if (unlikely(!has_simple_value(value_token)))
			return ESP_FAIL;
		const auto value_length = value_token->end - value_token->start;
		switch (fnv1a32(buffer + key_token->start, buffer + key_token->end)) {
			case fnv1a32("ip"): {
				buffer[value_token->end] = 0;
				if (esp_netif_str_to_ip4(buffer + value_token->start, &ip_info.ip) != ESP_OK)
					return ESP_FAIL;
				break;
			}
			case fnv1a32("gateway"):
			case fnv1a32("gw"): {
				buffer[value_token->end] = 0;
				if (esp_netif_str_to_ip4(buffer + value_token->start, &ip_info.gw) != ESP_OK)
					return ESP_FAIL;
				break;
			}
			case fnv1a32("mask"):
			case fnv1a32("netmask"): {
				buffer[value_token->end] = 0;
				if (strchr(buffer + value_token->start, '.') == nullptr) {
					// IP address
					if (esp_netif_str_to_ip4(buffer + value_token->start, &ip_info.netmask) != ESP_OK)
						return ESP_FAIL;
				}
				else {
					// Mask length
					const uint8_t maskLength = atoi(buffer + value_token->start);
					uint8_t i = maskLength - 1;
					ip_info.netmask.addr = 1;
					while (i--) {
						ip_info.netmask.addr <<= 1;
						ip_info.netmask.addr |= 1;
					}
					ESP_LOGV(TAG_CONFIG_NETWORK, "Setting mask as length %u. Resulting address: %u.%u.%u.%u", 
						maskLength, ip4_addr_printf_unpack(&ip_info.netmask));
				}
				break;
			}
			case fnv1a32("ssid"): {
				if (value_length > sizeof(wifi_config.ssid)) return ESP_FAIL;
				strncpy(wifi_config.ssid, buffer + value_token->start, value_length);
				wifi_config.ssid[value_length] = '\0';
				break;
			}
			case fnv1a32("psk"):
			case fnv1a32("password"): {
				if (value_token->type == JSMN_STRING && value_length != 0) {
					if (value_length > sizeof(wifi_config.password) - 1) return ESP_FAIL;
					strncpy(wifi_config.password, buffer + value_token->start, value_length);
					wifi_config.password[value_length] = '\0';
				}
				else {
					memset(wifi_config.password, 0, sizeof(wifi_config.password));
				}
				break;
			}
			default:
				continue;
		}
	}

	return ESP_OK;
}

inline esp_err_t config_network_ap(
	char* buffer, jsmntok_t* first_token,
	wifi_ap_config_t& wifi_config, esp_netif_ip_info_t& ip_info
) {
	esp_err_t ret = config_network_common(buffer, first_token, reinterpret_cast<wifi_common_config_t&>(wifi_config), ip_info);
	if (ret != ESP_OK) return ret;

	for (size_t i = 0; i < first_token->size; i += 2) {
		auto* key_token   = first_token + i + 1;
		auto* value_token = first_token + i + 2;
		if (unlikely(!has_simple_value(value_token)))
			return ESP_FAIL;
		const auto value_length = value_token->end - value_token->start;
		switch (fnv1a32(buffer + key_token->start, buffer + key_token->end)) {
			case fnv1a32("ssid"): {
				wifi_config.ssid_len = strlen(buffer + value_token->start);
				break;
			}
			case fnv1a32("psk"):
			case fnv1a32("password"): {
				if (value_token->type == JSMN_STRING && value_length != 0) {
					wifi_config.authmode = WIFI_AUTH_WPA_WPA2_PSK;
				}
				else {
					wifi_config.authmode = WIFI_AUTH_OPEN;
				}
				break;
			}
			case fnv1a32("channel"): {
				wifi_config.channel = atoi(buffer + value_token->start);
				break;
			}
			case fnv1a32("hidden"): {
				wifi_config.ssid_hidden = parseBooleanFast(buffer + value_token->start);
				break;
			}
		}
	}

	return ESP_OK;
}

inline esp_err_t config_network_sta(
	char* buffer, jsmntok_t* first_token,
	wifi_sta_config_t& wifi_config, esp_netif_ip_info_t& ip_info, bool& static_ip
) {
	esp_err_t ret = config_network_common(buffer, first_token, reinterpret_cast<wifi_common_config_t&>(wifi_config), ip_info);
	if (ret != ESP_OK) return ret;

	for (size_t i = 0; i < first_token->size; i += 2) {
		auto* key_token   = first_token + i + 1;
		auto* value_token = first_token + i + 2;
		if (unlikely(!has_simple_value(value_token)))
			return ESP_FAIL;
		switch (fnv1a32(buffer + key_token->start, buffer + key_token->end)) {
			case fnv1a32("static"): {
				static_ip = parseBooleanFast(buffer + value_token->start);
				break;
			}
		}
	}

	return ESP_OK;
}

/// @brief Configures networking related stuff.
/// @param buffer Buffer with JSON data that was parsed into JSON into tokens.
///               Note: Passed non-const to allow in-place strings manipulation.
/// @param first_token First JSMN JSON token related to the config to be parsed.
/// @return 
esp_err_t config_network(char* buffer, jsmntok_t* first_token) {
	if (unlikely(first_token->type != JSMN_OBJECT))
		return ESP_FAIL;

	esp_err_t nvs_result;
	std::shared_ptr<nvs::NVSHandle> nvs_handle = nvs::open_nvs_handle(NETWORK_MISC_NVS_NAMESPACE, NVS_READWRITE, &nvs_result);
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

	for (size_t i = 0; i < first_token->size; i += 2) {
		auto* key_token   = first_token + i + 1;
		auto* value_token = first_token + i + 2;
		printf("JSON parsing in config_network: key='%*s'\n", key_token->end - key_token->start, buffer + key_token->start);

		const auto key_hash = fnv1a32(buffer + key_token->start, buffer + key_token->end);
		if (value_token->type == JSMN_OBJECT) {
			switch (key_hash) {
				case fnv1a32("ap"): {
					if (config_network_ap(buffer, value_token, ap_config, ap_ip_info) != ESP_OK)
						return ESP_FAIL;
					break;
				}
				case fnv1a32("sta"): {
					if (config_network_sta(buffer, value_token, sta_config, sta_ip_info, sta_static) != ESP_OK)
						return ESP_FAIL;
					break;
				}
				default:
					ESP_LOGV(TAG_CONFIG_NETWORK, "Unknown field '%.*s' for network config JSON object, ignoring.", 
						key_token->end - key_token->start, buffer + key_token->start);
					break;
			}
			i += value_token->size;
		}
		else {
			if (unlikely(!has_simple_value(value_token)))
				return ESP_FAIL;

			switch (key_hash) {
				case fnv1a32("mode"): {
					const uint32_t value_hash = fnv1a32(buffer + value_token->start, buffer + value_token->end);
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
					fallback = atoi(buffer + value_token->start);
					if (fallback != 0 && fallback < 1000) {
						ESP_LOGV(TAG_CONFIG_NETWORK, "Fallback timeout clamped to minimal value of 1 second.");
						fallback = 1000;
					}
					break;
				}
				case fnv1a32("gateway"): {
					buffer[value_token->end] = 0;
					if (esp_netif_str_to_ip4(buffer + value_token->start, &ap_ip_info.gw) != ESP_OK)
						return ESP_FAIL;
					sta_ip_info.gw = ap_ip_info.gw;
					break;
				}
				default:
					ESP_LOGV(TAG_CONFIG_NETWORK, "Unknown field '%.*s' for network config JSON object, ignoring.", 
						key_token->end - key_token->start, buffer + key_token->start);
					break;
			}
		}
	}

	if (unlikely(nvs_handle->set_item("ap.ip",    reinterpret_cast<uint32_t&>(ap_ip_info.ip))      != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("ap.gw",    reinterpret_cast<uint32_t&>(ap_ip_info.gw))      != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("ap.mask",  reinterpret_cast<uint32_t&>(ap_ip_info.netmask)) != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("sta.ip",   reinterpret_cast<uint32_t&>(ap_ip_info.ip))      != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("sta.gw",   reinterpret_cast<uint32_t&>(ap_ip_info.gw))      != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("sta.mask", reinterpret_cast<uint32_t&>(ap_ip_info.netmask)) != ESP_OK)) return ESP_FAIL;

	if (unlikely(nvs_handle->set_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)) != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("wifi_mode", reinterpret_cast<uint32_t&>(mode)) != ESP_OK)) return ESP_FAIL;
	if (unlikely(nvs_handle->set_item("fallback", reinterpret_cast<uint8_t&>(sta_static)) != ESP_OK)) return ESP_FAIL;

	if (unlikely(nvs_handle->commit() != ESP_OK)) return ESP_FAIL;

	const bool is_ap  = mode == WIFI_MODE_AP  || mode == WIFI_MODE_APSTA;
	const bool is_sta = mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA;

	// WiFi AP/STA specific config are persisted by WiFi component.

	esp_netif_dhcps_stop(ap_netif);
	esp_netif_dhcpc_stop(sta_netif);

	if (is_sta) esp_wifi_disconnect();

	// FIXME: need to update DHCP server addresses (incl. leases) if AP address was changed

	esp_wifi_set_mode(mode);

	esp_wifi_set_config(WIFI_IF_AP,  reinterpret_cast<wifi_config_t*>(&ap_config));
	esp_wifi_set_config(WIFI_IF_STA, reinterpret_cast<wifi_config_t*>(&sta_config));

	if (is_ap)  esp_netif_dhcps_start(ap_netif);
	if (is_sta) esp_netif_dhcpc_start(sta_netif);

	if (is_sta) esp_wifi_connect();

	if (mode == WIFI_MODE_APSTA) {
		// TODO: NAT
	}

	// TODO: is restart necessary to apply the changes?

	return ESP_OK;
}