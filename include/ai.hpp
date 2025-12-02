#pragma once
#include <sdkconfig.h>
#include "camera.hpp"

namespace app::ai
{

void init();

/// @brief Applies hand detection and gesture recognition and prints results as JSON.
/// @param fb The camera frame buffer to process.
/// @return A JSON string representing the detected gestures (which might be empty list) or error.
std::string recognize_gesture_to_json(const camera_fb_t &fb);

}
