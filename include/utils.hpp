#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Util for delay in miliseconds
inline void delay(const TickType_t millis)
{
	vTaskDelay(millis / portTICK_PERIOD_MS);
}
