#include <sdkconfig.h>
#include "hal.hpp"

namespace app::hal 
{

const char* TAG = "hal";

void setMotor(Motor which, float duty) {
	const auto timer = static_cast<mcpwm_timer_t>(which);
	if (duty > 0) {
		// Forward
		mcpwm_set_signal_low(MCPWM_UNIT_0, timer, MCPWM_GEN_B);
		mcpwm_set_duty(MCPWM_UNIT_0, timer, MCPWM_GEN_A, duty);
		mcpwm_set_duty_type(MCPWM_UNIT_0, timer, MCPWM_GEN_A, MCPWM_DUTY_MODE_0);
	}
	else {
		// Backwards
		mcpwm_set_signal_low(MCPWM_UNIT_0, timer, MCPWM_GEN_A);
		mcpwm_set_duty(MCPWM_UNIT_0, timer, MCPWM_GEN_B, -duty);
		mcpwm_set_duty_type(MCPWM_UNIT_0, timer, MCPWM_GEN_B, MCPWM_DUTY_MODE_0);
	}
	ESP_LOGD(TAG, "Motor %s set to %.2f%%", which == Motor::Left ? "LEFT" : "RIGHT", duty);
}

void init()
{
	// Initializing MCPWM (motors control over pulse-width-modulation)
	// See https://docs.espressif.com/projects/esp-idf/en/v4.4.3/esp32/api-reference/peripherals/mcpwm.html
	mcpwm_gpio_init(MCPWM_UNIT_0, MCPWM0A, GPIO_MOTORS_LEFT_FORWARD);
	mcpwm_gpio_init(MCPWM_UNIT_0, MCPWM0B, GPIO_MOTORS_LEFT_BACKWARD);
	mcpwm_gpio_init(MCPWM_UNIT_0, MCPWM1A, GPIO_MOTORS_RIGHT_FORWARD);
	mcpwm_gpio_init(MCPWM_UNIT_0, MCPWM1B, GPIO_MOTORS_RIGHT_BACKWARD);

	mcpwm_config_t pwm_config_left = {
		.frequency = MOTORS_FREQUENCY,
		.cmpr_a = 0,
		.cmpr_b = 0,
		.duty_mode = MCPWM_DUTY_MODE_0,
		.counter_mode = MCPWM_UP_COUNTER,
	};
	mcpwm_init(MCPWM_UNIT_0, MCPWM_TIMER_0, &pwm_config_left);
	mcpwm_config_t pwm_config_right = {
		.frequency = MOTORS_FREQUENCY,
		.cmpr_a = 0,
		.cmpr_b = 0,
		.duty_mode = MCPWM_DUTY_MODE_0,
		.counter_mode = MCPWM_UP_COUNTER,
	};
	mcpwm_init(MCPWM_UNIT_0, MCPWM_TIMER_1, &pwm_config_right);

	// Initialize lights 
	gpio_set_direction(GPIO_MAIN_LIGHT,  GPIO_MODE_OUTPUT);
	gpio_set_direction(GPIO_OTHER_LIGHT, GPIO_MODE_OUTPUT);
	hal::setMainLight(false);
	hal::setOtherLight(false);
}

}
