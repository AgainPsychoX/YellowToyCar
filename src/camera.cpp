#include <sdkconfig.h>
#include <cstring>
#include <cctype>
#include <esp_err.h>
#include <esp_log.h>
#include <esp_camera.h>
#include <jsmn.h>
#include <to_string.hpp>
#include "common.hpp"

#define CAMERA_NVS_NAMESPACE "network"

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
	
	if (esp_camera_load_from_nvs(CAMERA_NVS_NAMESPACE) != ESP_OK) {
		/* Fallback to some default settings */
	}
}

inline bool has_simple_value(const jsmntok_t* token) {
	if (token->type == JSMN_UNDEFINED) return false;
	if (token->type == JSMN_OBJECT) return false;
	if (token->type == JSMN_ARRAY) return false;
	return true;
}

static const char* TAG_CONFIG_CAMERA = "config-camera";

pixformat_t parse_pixformat(const char* str)
{
	if (strcasecmp(str, "PIXFORMAT_")) {
		str += sizeof("PIXFORMAT_");
	}
	switch (fnv1a32i(str)) {
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_RGB565)>.data()):      case fnv1a32i("RGB565"):    return PIXFORMAT_RGB565;    // 2BPP/RGB565
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_YUV422)>.data()):      case fnv1a32i("YUV422"):    return PIXFORMAT_YUV422;    // 2BPP/YUV422
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_YUV420)>.data()):      case fnv1a32i("YUV420"):    return PIXFORMAT_YUV420;    // 1.5BPP/YUV420
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_GRAYSCALE)>.data()):   case fnv1a32i("GRAYSCALE"): return PIXFORMAT_GRAYSCALE; // 1BPP/GRAYSCALE
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_JPEG)>.data()):        case fnv1a32i("JPEG"):      return PIXFORMAT_JPEG;      // JPEG/COMPRESSED
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_RGB888)>.data()):      case fnv1a32i("RGB888"):    return PIXFORMAT_RGB888;    // 3BPP/RGB888
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_RAW)>.data()):         case fnv1a32i("RAW"):       return PIXFORMAT_RAW;       // RAW
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_RGB444)>.data()):      case fnv1a32i("RGB444"):    return PIXFORMAT_RGB444;    // 3BP2P/RGB444
		case fnv1a32i(to_string<static_cast<uint32_t>(PIXFORMAT_RGB555)>.data()):      case fnv1a32i("RGB555"):    return PIXFORMAT_RGB555;    // 3BP2P/RGB555
		default: return static_cast<pixformat_t>(-1); // No invalid value in enum, so artificial value used here.
	}
}

framesize_t parse_framesize(const char* str)
{
	if (strcasecmp(str, "FRAMESIZE_")) {
		str += sizeof("FRAMESIZE_");
	}
	switch (fnv1a32i(str)) {
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_96X96)>.data()):   case fnv1a32i("96x96"):                             return FRAMESIZE_96X96;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_QQVGA)>.data()):   case fnv1a32i("160x120"):   case fnv1a32i("QQVGA"): return FRAMESIZE_QQVGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_QCIF)>.data()):    case fnv1a32i("176x144"):   case fnv1a32i("QCIF"):  return FRAMESIZE_QCIF;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_HQVGA)>.data()):   case fnv1a32i("240x176"):   case fnv1a32i("HQVGA"): return FRAMESIZE_HQVGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_240X240)>.data()): case fnv1a32i("240x240"):                           return FRAMESIZE_240X240;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_QVGA)>.data()):    case fnv1a32i("320x240"):   case fnv1a32i("QVGA"):  return FRAMESIZE_QVGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_CIF)>.data()):     case fnv1a32i("400x296"):   case fnv1a32i("CIF"):   return FRAMESIZE_CIF; // Native for OV2640
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_HVGA)>.data()):    case fnv1a32i("480x320"):   case fnv1a32i("HVGA"):  return FRAMESIZE_HVGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_VGA)>.data()):     case fnv1a32i("640x480"):   case fnv1a32i("VGA"):   return FRAMESIZE_VGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_SVGA)>.data()):    case fnv1a32i("800x600"):   case fnv1a32i("SVGA"):  return FRAMESIZE_SVGA; // Native for OV2640
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_XGA)>.data()):     case fnv1a32i("1024x768"):  case fnv1a32i("XGA"):   return FRAMESIZE_XGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_HD)>.data()):      case fnv1a32i("1280x720"):  case fnv1a32i("HD"):    return FRAMESIZE_HD;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_SXGA)>.data()):    case fnv1a32i("1280x1024"): case fnv1a32i("SXGA"):  return FRAMESIZE_SXGA;
		case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_UXGA)>.data()):    case fnv1a32i("1600x1200"): case fnv1a32i("UXGA"):  return FRAMESIZE_UXGA; // Native for OV2640
		/* Unsupported by OV2640 */
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_FHD)>.data()):     case fnv1a32i("1920x1080"): case fnv1a32i("FHD"):   return FRAMESIZE_FHD;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_P_HD)>.data()):    case fnv1a32i("720x1280"):  case fnv1a32i("P_HD"):  return FRAMESIZE_P_HD;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_P_3MP)>.data()):   case fnv1a32i("864x1536"):  case fnv1a32i("P_3MP"): return FRAMESIZE_P_3MP;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_QXGA)>.data()):    case fnv1a32i("2048x1536"): case fnv1a32i("QXGA"):  return FRAMESIZE_QXGA;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_QHD)>.data()):     case fnv1a32i("2560x1440"): case fnv1a32i("QHD"):   return FRAMESIZE_QHD;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_WQXGA)>.data()):   case fnv1a32i("2560x1600"): case fnv1a32i("WQXGA"): return FRAMESIZE_WQXGA;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_P_FHD)>.data()):   case fnv1a32i("1080x1920"): case fnv1a32i("P_FHD"): return FRAMESIZE_P_FHD;
		// case fnv1a32i(to_string<static_cast<uint32_t>(FRAMESIZE_QSXGA)>.data()):   case fnv1a32i("2560x1920"): case fnv1a32i("QSXGA"): return FRAMESIZE_QSXGA;
		default: return FRAMESIZE_INVALID;
	}
}

/// @brief Applies (and/or reads current) JSON configuration for camera.
/// @param[in] input Buffer with JSON data that was parsed into JSON into tokens.
///		Note: Passed non-const to allow in-place strings manipulation.
/// @param[in] first_token First JSMN JSON token related to the config to be parsed.
/// @param[out] output Optional buffer for writing JSON with current configuration.
/// @param[in] output_length Length of output buffer.
/// @param[out] output_return Used to return number of bytes that would be written 
/// 	to the output, or negative for error. Basically `printf`-like return.
/// @return 
esp_err_t config_camera(
	char* input, jsmntok_t* first_token,
	char* output, size_t output_length, int* output_return
) {
	if (unlikely(first_token->type != JSMN_OBJECT))
		return ESP_FAIL;

	sensor_t* sensor = esp_camera_sensor_get();
	if (unlikely(!sensor)) {
		ESP_LOGE(TAG_CONFIG_CAMERA, "Failed to get camera handle to set config");
		return ESP_FAIL;
	}

	if (input) {
		for (size_t i = 0; i < first_token->size; i += 2) {
			auto* key_token   = first_token + i + 1;
			auto* value_token = first_token + i + 2;
			printf("JSON parsing in config_network: key='%*s'\n", key_token->end - key_token->start, input + key_token->start);
			if (unlikely(!has_simple_value(value_token)))
				return ESP_FAIL;
			switch (fnv1a32(input + key_token->start, input + key_token->end)) {
				case fnv1a32("framesize"):
					input[value_token->end] = 0;
					sensor->set_framesize(sensor, parse_framesize(input + value_token->start));
					break;
				case fnv1a32("pixformat"):
					input[value_token->end] = 0;
					sensor->set_pixformat(sensor, parse_pixformat(input + value_token->start));
					break;
				case fnv1a32("quality"): /* for JPEG compression */
					sensor->set_quality(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("bpc"):
					sensor->set_bpc(sensor, parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("wpc"):
					sensor->set_wpc(sensor, parseBooleanFast(input + value_token->start));
					break;

				case fnv1a32("hmirror"):
					sensor->set_hmirror(sensor, parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("vflip"):
					sensor->set_vflip(sensor, parseBooleanFast(input + value_token->start));
					break;

				case fnv1a32("contrast"):
					sensor->set_contrast(sensor, atoi(input + value_token->start));
					break;
				case fnv1a32("brightness"):
					sensor->set_brightness(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("sharpness"): 
					// TODO: not supported by original library
					sensor->set_sharpness(sensor, atoi(input + value_token->start));
					break;
				case fnv1a32("denoise"):
					// TODO: not supported by original library
					sensor->set_denoise(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("gain_ceiling"): {
					// Clamp value here, because - unlike other params - the library doesn't do that,
					// expecting users to use values from enum to prevent invalid state...
					int value = atoi(input + value_token->start);
					if (value < 0) value = 0; else if (value > 6) value = 6;
					sensor->set_gainceiling(sensor, static_cast<gainceiling_t>(value));
					break;
				}
				case fnv1a32("agc"):
					sensor->set_gain_ctrl(sensor, parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("agc_gain"):
					sensor->set_agc_gain(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("aec"):
					sensor->set_exposure_ctrl(sensor, parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("night"):
				case fnv1a32("aec2"): // night mode of automatic gain control
					sensor->set_aec2(sensor, parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("ae_level"): 
					sensor->set_ae_level(sensor, atoi(input + value_token->start));
					break;
				case fnv1a32("exposure"): {
					input[value_token->end] = 0;
					char* p = input + value_token->start;
					if (*p == 'a') { // auto mode
						sensor->set_exposure_ctrl(sensor, true);
						while (*++p)
							if (std::isdigit(*p) || *p == '-')
								break;
						sensor->set_ae_level(sensor, atoi(p));
						break;
					}
					sensor->set_exposure_ctrl(sensor, false);
					[[fallthrough]];
				}
				case fnv1a32("aec_value"):
					sensor->set_aec_value(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("awb"):
					sensor->set_whitebal(sensor, parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("awb_gain"):
					sensor->set_awb_gain(sensor, atoi(input + value_token->start));
					break;
				case fnv1a32("wb_mode"):
					sensor->set_wb_mode(sensor, atoi(input + value_token->start));
					break;
				case fnv1a32("dcw"): // advanced auto white balance 
					sensor->set_dcw(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("raw_gma"):
					sensor->set_raw_gma(sensor, atoi(input + value_token->start));
					break;
				case fnv1a32("lenc"):
					sensor->set_lenc(sensor, atoi(input + value_token->start));
					break;

				case fnv1a32("special"):
				case fnv1a32("special_effect"):
					sensor->set_special_effect(sensor, atoi(input + value_token->start));
					break;

				default:
					ESP_LOGV(TAG_CONFIG_CAMERA, "Unknown field '%.*s' for camera config JSON object, ignoring.", 
						key_token->end - key_token->start, input + key_token->start);
					break;
			}
		}

		// TODO: report invalid parameters somehow (i.e. out of bounds contrast/brightness values, invalid framesize etc.)
	}

	if (output) {
		*output_return = snprintf(
			output, output_length,
			"{"
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
			"}",
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
			sensor->status.special_effect
		);
	}

	esp_camera_save_to_nvs(CAMERA_NVS_NAMESPACE);

	return ESP_OK;
}
