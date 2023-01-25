#include <sdkconfig.h>
#include <cstring>
#include <esp_err.h>
#include <esp_log.h>
#include <nvs_handle.hpp>
#include <esp_netif.h>
#include <esp_wifi.h>
#include <jsmn.h>
#include "utils.hpp"

namespace app::network
{

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

////////////////////////////////////////////////////////////////////////////////
// Initialization

void init()
{
	esp_err_t nvs_result;
	std::shared_ptr<nvs::NVSHandle> nvs_handle = nvs::open_nvs_handle(NVS_NETWORK_NAMESPACE, NVS_READWRITE, &nvs_result);
	ESP_ERROR_CHECK(nvs_result);

	ESP_ERROR_CHECK(esp_netif_init());
	ESP_ERROR_CHECK(esp_event_loop_create_default());

	wifi_init_config_t wifi_init_config = WIFI_INIT_CONFIG_DEFAULT();
	ESP_ERROR_CHECK(esp_wifi_init(&wifi_init_config));

	/* use_config: */ {
		esp_netif_ip_info_t ap_ip_info;
		esp_netif_ip_info_t sta_ip_info;
		if (unlikely(load_ip_info_from_nvs(*nvs_handle, WIFI_IF_AP,  ap_ip_info)  != ESP_OK)) {
			ESP_LOGD(TAG_INIT_NETWORK, "Missing IP info for %s interface, using defaults", "AP");
			esp_netif_t* ap_netif = esp_netif_create_default_wifi_ap();
			esp_netif_get_ip_info(ap_netif, &ap_ip_info);
			save_ip_info_to_nvs(*nvs_handle, WIFI_IF_AP, ap_ip_info);
			esp_netif_destroy_default_wifi(ap_netif);
		}
		if (unlikely(load_ip_info_from_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info) != ESP_OK)) {
			ESP_LOGD(TAG_INIT_NETWORK, "Missing IP info for %s interface, using defaults", "STA");
			// Note: DHCP client is used on default, so use preset to avoid uninitialized garbage.
			// esp_netif_t* sta_netif = esp_netif_create_default_wifi_sta();
			// esp_netif_get_ip_info(sta_netif, &ap_ip_info);
			// save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info);
			// esp_netif_destroy_default_wifi(sta_netif);
			esp_netif_ip_info_t preset = {
				.ip = {0},
				.netmask = {PP_HTONL(0xFFFFFF00)},
				.gw = {0},
			};
			save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, preset);
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
		ESP_LOGW(TAG_INIT_NETWORK, "Missing data, falling back to default config");

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
		goto starting;
	}

	starting:
	ESP_ERROR_CHECK(esp_wifi_start());

	// TODO: create race between timeout fallback and connected event, if timed out -> stop STA, start AP
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
				// IP address
				if (esp_netif_str_to_ip4(input + value_token->start, &ip_info.netmask) != ESP_OK)
					return ESP_FAIL;
			}
			else {
				// Mask length
				const uint8_t maskLength = std::atoi(input + value_token->start);
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
				wifi_config.ssid_len = std::strlen(input + value_token->start);
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
		// const size_t value_length = value_token->end - value_token->start;
		const auto key_hash = fnv1a32(input + key_token->start, input + key_token->end);
		if (config__common_keys(input, key_hash, value_token, reinterpret_cast<wifi_common_config_t&>(wifi_config), ip_info) != ESP_OK)
			return ESP_FAIL;
		switch (key_hash) {
			case fnv1a32("static"): {
				static_ip = parseBooleanFast(input + value_token->start);
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
						fallback = std::atoi(input + value_token->start);
						if (fallback != 0 && fallback < 1000) {
							ESP_LOGD(TAG_CONFIG_NETWORK, "Fallback timeout clamped to minimal value of 1 second.");
							fallback = 1000;
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

		if (unlikely(save_ip_info_to_nvs(*nvs_handle, WIFI_IF_AP,  ap_ip_info)  != ESP_OK)) return ESP_FAIL;
		if (unlikely(save_ip_info_to_nvs(*nvs_handle, WIFI_IF_STA, sta_ip_info) != ESP_OK)) return ESP_FAIL;

		if (unlikely(nvs_handle->set_item("sta.static", reinterpret_cast<uint8_t&>(sta_static)) != ESP_OK)) return ESP_FAIL;
		if (unlikely(nvs_handle->set_item("wifi_mode",  reinterpret_cast<uint32_t&>(mode)) != ESP_OK)) return ESP_FAIL;
		if (unlikely(nvs_handle->set_item("fallback",   reinterpret_cast<uint8_t&>(sta_static)) != ESP_OK)) return ESP_FAIL;

		if (unlikely(nvs_handle->commit() != ESP_OK)) return ESP_FAIL;

		const bool is_ap  = mode == WIFI_MODE_AP  || mode == WIFI_MODE_APSTA;
		const bool is_sta = mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA;

		// WiFi AP/STA specific config are persisted by WiFi component.

		if (ap_netif)  esp_netif_dhcps_stop(ap_netif);
		if (sta_netif) esp_netif_dhcpc_stop(sta_netif);

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
			"}",
			wifi_mode_to_cstr(mode),
			fallback,
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
			'0' + ap_config.ssid_hidden
		);
	}

	return ESP_OK;
}

////////////////////////////////////////////////////////////////////////////////

}
