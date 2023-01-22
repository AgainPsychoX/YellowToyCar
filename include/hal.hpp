#pragma once
#include <sdkconfig.h>
#include <esp_log.h>
#include <driver/gpio.h>
#include <driver/mcpwm.h>
#include "common.hpp"

#define MOTORS_FREQUENCY 100 // Hz
#define GPIO_MOTORS_RIGHT_FORWARD  GPIO_NUM_12
#define GPIO_MOTORS_RIGHT_BACKWARD GPIO_NUM_2
#define GPIO_MOTORS_LEFT_FORWARD   GPIO_NUM_15
#define GPIO_MOTORS_LEFT_BACKWARD  GPIO_NUM_14
#define GPIO_MAIN_LIGHT            GPIO_NUM_4  // External bright white
#define GPIO_OTHER_LIGHT           GPIO_NUM_33 // Internal red (pulled high)

namespace app::hal 
{

extern const char* TAG;

inline void setMainLight(bool on) {
	gpio_set_level(GPIO_MAIN_LIGHT, on);
	ESP_LOGD(TAG, "Main light %s", on ? "on" : "off");
}

inline void setOtherLight(bool on) {
	// Pulled high, so drive low to make it light
	gpio_set_level(GPIO_OTHER_LIGHT, !on);
	ESP_LOGD(TAG, "Other light %s", !on ? "on" : "off");
}

enum class Motor {
	Left  = MCPWM_TIMER_0,
	Right = MCPWM_TIMER_1
};

/// Sets selected motor to given duty cycle (12.3f = 12.3%). 
/// Use negative values to move backwards.
void setMotor(Motor which, float duty);

/// Initializes project custom hardware: motors and lights
void init();

}
