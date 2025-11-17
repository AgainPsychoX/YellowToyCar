#pragma once
#include <sdkconfig.h>
#include <esp_camera.h>
#include "common.hpp"

namespace app::camera 
{

/// Helper class to manage safe access to camera frame buffer
class FrameBufferGuard : SemaphoreGuard 
{
protected:
	camera_fb_t* fb;

	FrameBufferGuard(SemaphoreGuard&& sg, camera_fb_t* fb)
		: SemaphoreGuard(std::move(sg)), fb(fb)
	{}

public:
	FrameBufferGuard(FrameBufferGuard&& o) 
		: SemaphoreGuard(std::move(o)), fb(std::exchange(o.fb, nullptr))
	{}

	~FrameBufferGuard();

	operator bool() const
	{
		return fb != nullptr && SemaphoreGuard::operator bool();
	}

	operator camera_fb_t*() const { return fb; }
	camera_fb_t& operator*() const { return *fb; }
	camera_fb_t* operator->() const { return fb; }

	static FrameBufferGuard take(TickType_t blockTime = portMAX_DELAY);
};

/// Initializes camera system
esp_err_t init();

}
