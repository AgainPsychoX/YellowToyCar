#pragma once
#include <sdkconfig.h>
#include "camera.hpp"

namespace app::ai
{

void init();
void recognize_gesture(const camera_fb_t& fb);

}
