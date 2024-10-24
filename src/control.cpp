#include <sdkconfig.h>
#include <cstdio>
#include <esp_log.h>
#include <jsmn.h>
#include "control.hpp"
#include "hal.hpp"

namespace app::control
{

////////////////////////////////////////////////////////////////////////////////
// Control with state management

void init()
{
	setMotor(Motor::Left, 0);
	setMotor(Motor::Right, 0);
	setMainLight(false);
	setOtherLight(false);
}

constexpr uptime_t initialControlTimeout = 2'000'000; // us
uptime_t lastControlTime = -initialControlTimeout; // us (negative to make initial state valid as not yet controlled)
uptime_t controlTimeout = initialControlTimeout; // us

uptime_t mainLightControlTimeout = 30'000'000; // us

void tick()
{
	uptime_t timeSinceControl = esp_timer_get_time() - lastControlTime;
	if (timeSinceControl > controlTimeout) {
		setMotor(Motor::Left, 0);
		setMotor(Motor::Right, 0);
		delay(1);
		if (timeSinceControl > mainLightControlTimeout) {
			setMainLight(false);
			setOtherLight(false);
		}
	}
}

void refresh()
{
	lastControlTime = esp_timer_get_time();
}

// TODO: allow configure control timeout?
// TODO: calibration values for motors?

////////////////////////////////////////
// Lights

bool mainLightState;
void setMainLight(bool on)
{
	hal::setMainLight(on);
	mainLightState = on;
}
bool getMainLight()
{
	return mainLightState;
}

bool otherLightState;
void setOtherLight(bool on)
{
	hal::setOtherLight(on);
	otherLightState = on;
}
bool getOtherLight()
{
	return otherLightState;
}

////////////////////////////////////////
// Motors

float lastMotorDuty[static_cast<uint8_t>(Motor::_Count)];

// No translation for motor enums (aside from casting) is required between
// `control` and `hal` modules because it just so happens next MCPWM timers
// are in right order.
static_assert(static_cast<int>(control::Motor::Left)  == static_cast<int>(hal::Motor::Left));
static_assert(static_cast<int>(control::Motor::Right) == static_cast<int>(hal::Motor::Right));

void setMotor(Motor which, float duty)
{
	hal::setMotor(static_cast<hal::Motor>(which), duty);
	lastMotorDuty[static_cast<uint8_t>(which)] = duty;
}

float getMotor(Motor which)
{
	return lastMotorDuty[static_cast<uint8_t>(which)];
}

////////////////////////////////////////////////////////////////////////////////
// Configuration

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
					control::setMainLight(parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("otherLight"):
					control::setOtherLight(parseBooleanFast(input + value_token->start));
					break;
				case fnv1a32("left"):
					control::setOtherLight(std::atoi(input + value_token->start));
					break;
				case fnv1a32("right"):
					control::setOtherLight(std::atoi(input + value_token->start));
					break;
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

		// Control object existing, even empty, marks the control state as fresh
		control::refresh();
	}

	if (output_return) {
		*output_return = std::snprintf(
			output, output_length,
			"{"
				"\"mainLight\":%u,"
				"\"otherLight\":%u,"
				"\"left\":%.1f,"
				"\"right\":%.1f"
			"}",
			getMainLight(),
			getOtherLight(),
			getMotor(Motor::Left),
			getMotor(Motor::Right)
		);
	}

	return ESP_OK;
}

}
