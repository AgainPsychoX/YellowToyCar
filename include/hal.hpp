#pragma once
#include <sdkconfig.h>
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

void setMainLight(bool on);

void setOtherLight(bool on);

/// Enum type used to specify motor among them all.
/// Underlying values of the enum are also ID of associated MCPWM timer.
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
