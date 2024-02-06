#pragma once
#include <sdkconfig.h>

namespace app::control 
{

/// Initializes control system state (motor & lights).
void init();

/// Runs control system update tick, incl. safety stop checks 
/// (like in case of timeout, for no control request in some time).
void tick();

/// Marks current control state as fresh (prevents timeout for safety stop).
void refresh();

/// Enum type used to specify motor among them all.
enum class Motor {
	Left,
	Right,
	_Count,
};

void setMainLight(bool on);
bool getMainLight();

void setOtherLight(bool on);
bool getOtherLight();

void setMotor(Motor which, float duty);
float getMotor(Motor which);

}
