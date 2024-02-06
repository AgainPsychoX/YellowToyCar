#include <sdkconfig.h>
#include <cstdio>
#include <esp_log.h>
#include <jsmn.h>
#include "common.hpp"

#include "hal.hpp"
namespace app {
	extern uint64_t lastControlTime; // TODO: move it to control.cpp from main?
}

namespace app::control
{

inline bool has_simple_value(const jsmntok_t* token)
{
	if (token->type == JSMN_UNDEFINED) return false;
	if (token->type == JSMN_OBJECT) return false;
	if (token->type == JSMN_ARRAY) return false;
	return true;
}

static const char* TAG_CONFIG_CONTROL = "config-control";

/// @brief Applies (and/or reads current) JSON configuration and status for controls.
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
	if (input) {
		if (unlikely(root->type != JSMN_OBJECT))
			return ESP_FAIL;
		if (unlikely(root->size < 1)) 
			return ESP_FAIL;
		for (jsmntok_t* token = root + 1;;) {
			auto* key_token   = token;
			auto* value_token = token + 1;
			ESP_LOGV(TAG_CONFIG_CONTROL, "key='%.*s' value='%.*s'", 
				key_token->end - key_token->start, input + key_token->start,
				value_token->end - value_token->start, input + value_token->start
			);

			if (unlikely(!has_simple_value(value_token)))
				return ESP_FAIL;
			const size_t value_length = value_token->end - value_token->start;
			switch (fnv1a32(input + key_token->start, input + key_token->end)) {
				case fnv1a32("mainLight"):
					hal::setMainLight(parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("otherLight"):
					hal::setOtherLight(parseBooleanFast(input + value_token->start));
					break;
					// TODO: allow control motors?
					// hal::setMotor(hal::Motor::Left,  toFloatMotorDuty(v.leftDuty, v.leftBackward));
					// hal::setMotor(hal::Motor::Right, toFloatMotorDuty(v.rightDuty, v.rightBackward));
					// lastControlTime = esp_timer_get_time();

					// TODO: allow set control timeout?
					// TODO: calibration values for motors?
				default:
					ESP_LOGD(TAG_CONFIG_CONTROL, "Unknown field '%.*s', ignoring.", 
						key_token->end - key_token->start, input + key_token->start);
					break;
			}

			// Skip primitive pair (key & value)
			token += 2;
			if (root->end < token->end)
				goto done;
		}
		done: /* semicolon for empty statement */ ;
	}

	if (output_return) {
		*output_return = std::snprintf(
			output, output_length,
			"{"
				"\"mainLight\":%u,"
				"\"otherLight\":%u"
			"}",
			0, 0 // TODO: return current control state? would require storing it...?
		);
	}

	return ESP_OK;
}

}
