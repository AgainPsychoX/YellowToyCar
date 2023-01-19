#include <sdkconfig.h>
#include <string_view>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

/// Checks error and returns from current function with the same error.
#define ESP_ERROR_CHECK_RETURN(x) do {                                         \
		esp_err_t err_rc_ = (x);                                               \
		if (unlikely(err_rc_ != ESP_OK)) return err_rc_;                       \
	} while(0)

/// Used to indicate where error checking is deliberately omitted.
#define ESP_IGNORE_ERROR(x) (x)

// Util for delay in miliseconds
inline void delay(const TickType_t millis)
{
	vTaskDelay(millis / portTICK_PERIOD_MS);
}

/// constexpr version of `tolower`
constexpr char tolower(const char c) {
    return (c < 'A' || 'Z' < c) ? c : c + ('a' - 'A');
}

// FNV1a 32 hashing
constexpr uint32_t fnv1a32(const char* s) {
	uint32_t hash = 2166136261u;
	while (*s) {
		hash ^= *s++;
		hash *= 16777619u;
	}
	return hash;
}
constexpr uint32_t fnv1a32(const char* s, size_t count) {
	uint32_t hash = 2166136261u;
	while (count--) {
		hash ^= *s++;
		hash *= 16777619u;
	}
	return hash;
}
template<typename TIterator>
constexpr uint32_t fnv1a32(TIterator s, TIterator e)
{
	uint32_t hash = 2166136261u;
	while (s != e) {
		hash ^= *s++;
		hash *= 16777619u;
	}
	return hash;
}

constexpr uint32_t fnv1a32i(const char* s) {
	uint32_t hash = 2166136261u;
	while (*s) {
		hash ^= tolower(*s++);
		hash *= 16777619u;
	}
	return hash;
}
constexpr uint32_t fnv1a32i(const char* s, size_t count) {
	uint32_t hash = 2166136261u;
	while (count--) {
		hash ^= tolower(*s++);
		hash *= 16777619u;
	}
	return hash;
}
template<typename TIterator>
constexpr uint32_t fnv1a32i(const TIterator s, const TIterator e)
{
	uint32_t hash = 2166136261u;
	while (s != e) {
		hash ^= tolower(*s++);
		hash *= 16777619u;
	}
	return hash;
}

/// Parses boolean-like string.
constexpr bool parseBooleanFast(const char* str) {
	//return str[0] == '1' || str[0] == 't' || str[0] == 'T' || str[0] == 'y' || str[0] == 'Y';
	return !(str[0] == '0' || str[0] == 'f' || str[0] == 'F' || str[0] == 'n' || str[0] == 'N');
}

// Taken from great article at https://stackoverflow.com/a/109025/4880243
constexpr uint8_t numberOfSetBits(uint32_t i)
{
	i = i - ((i >> 1) & 0x55555555);
	i = (i & 0x33333333) + ((i >> 2) & 0x33333333);
	i = (i + (i >> 4)) & 0x0F0F0F0F;
	return (i * 0x01010101) >> 24;
}
