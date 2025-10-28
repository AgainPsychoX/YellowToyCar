#pragma once
#include <sdkconfig.h>
#include <string_view>
#include <utility>
#include <limits>
#include <type_traits>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <esp_timer.h>

////////////////////////////////////////////////////////////////////////////////
// Error handling

extern void _esp_error_check_failed_without_abort(esp_err_t rc, const char *file, int line, const char *function, const char *expression);

#ifdef NDEBUG
#	define ESP_ERROR_CHECK_RETURN(x)  ({ esp_err_t err_rc_ = (x); err_rc_; })
#	define ESP_ERROR_CHECK_OR_GOTO(x) ({ esp_err_t err_rc_ = (x); err_rc_; })
#elif defined(CONFIG_COMPILER_OPTIMIZATION_ASSERTIONS_SILENT)
/// Checks error and returns from current function with the same error.
#	define ESP_ERROR_CHECK_RETURN(x) ({                                        \
			esp_err_t err_rc_ = (x);                                           \
			if (unlikely(err_rc_ != ESP_OK)) {                                 \
				return err_rc_;                                                \
			}                                                                  \
			err_rc_;                                                           \
		})
/// Checks error and jump to label (goto) if error occurs.
#	define ESP_ERROR_CHECK_OR_GOTO(label, x) ({                                \
			esp_err_t err_rc_ = (x);                                           \
			if (unlikely(err_rc_ != ESP_OK)) {                                 \
				goto label;                                                    \
			}                                                                  \
			err_rc_;                                                           \
		})
#else
/// Checks error and returns from current function with the same error.
#	define ESP_ERROR_CHECK_RETURN(x) ({                                        \
			esp_err_t err_rc_ = (x);                                           \
			if (unlikely(err_rc_ != ESP_OK)) {                                 \
				_esp_error_check_failed_without_abort(                         \
					err_rc_, __FILE__, __LINE__, __ASSERT_FUNC, #x);           \
				return err_rc_;                                                \
			}                                                                  \
			err_rc_;                                                           \
		})
/// Checks error and jump to label (goto) if error occurs.
#	define ESP_ERROR_CHECK_OR_GOTO(label, x) ({                                \
			esp_err_t err_rc_ = (x);                                           \
			if (unlikely(err_rc_ != ESP_OK)) {                                 \
				_esp_error_check_failed_without_abort(                         \
					err_rc_, __FILE__, __LINE__, __ASSERT_FUNC, #x);           \
				goto label;                                                    \
			}                                                                  \
			err_rc_;                                                           \
		})
#endif //NDEBUG

/// Used to indicate where error checking is deliberately omitted.
#define ESP_IGNORE_ERROR(x) (x)

////////////////////////////////////////////////////////////////////////////////
// Other

/// Util for delay in miliseconds (`vTaskDelay` inside).
inline void delay(const TickType_t millis)
{
	vTaskDelay(millis / portTICK_PERIOD_MS);
}

/// Type returned from `esp_timer_get_time` function, which returns time in microseconds since boot.
using uptime_t = std::invoke_result_t<decltype(esp_timer_get_time)>;

////////////////////////////////////////////////////////////////////////////////
// Concurency

/// Helper class to automatically give up semaphore on end of scope.
class SemaphoreGuard
{
	SemaphoreHandle_t handle;

protected:
	SemaphoreGuard(SemaphoreHandle_t handle) 
		: handle(handle)
	{}

public:
	SemaphoreGuard(SemaphoreGuard&& o) 
		: handle(std::exchange(o.handle, nullptr))
	{}

	~SemaphoreGuard()
	{
		if (handle)
			xSemaphoreGive(handle);
	}

	operator bool() const
	{
		return handle != nullptr;
	}

	static SemaphoreGuard take(
		SemaphoreHandle_t handle, 
		TickType_t blockTime = portMAX_DELAY
	) {
		bool taken = xSemaphoreTake(handle, blockTime);
		return SemaphoreGuard { taken ? handle : nullptr };
	}
};

////////////////////////////////////////////////////////////////////////////////
// Parsing and processing

/// Compile-time version of `tolower`, without support for locales.
constexpr char tolower(const char c) {
	return (c < 'A' || 'Z' < c) ? c : c + ('a' - 'A');
}

/// Returns result of saturated subtraction. Example: `3 - 7 == 0`.
constexpr uint32_t saturatedSubtract(uint32_t x, uint32_t y)
{
	uint32_t res = x - y;
	res &= -(res <= x);
	return res;
}
static_assert(saturatedSubtract(3, 7) == 0);

/// Calculates FNV1a32 hash for given C-string.
constexpr uint32_t fnv1a32(const char* s) {
	uint32_t hash = 2166136261u;
	while (*s) {
		hash ^= *s++;
		hash *= 16777619u;
	}
	return hash;
}
/// Calculates FNV1a32 hash for given buffer. 
constexpr uint32_t fnv1a32(const char* s, size_t count) {
	uint32_t hash = 2166136261u;
	while (count--) {
		hash ^= *s++;
		hash *= 16777619u;
	}
	return hash;
}
/// Calculates FNV1a32 hash for iterable.
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
/// Calculates FNV1a32 hash for iterable.
template<typename TIterable>
constexpr inline uint32_t fnv1a32(const TIterable& c)
{
	return fnv1a32(std::cbegin(c), std::cend(c));
}

/// Calculates case-insensitive FNV1a32 hash for given C-string.
constexpr uint32_t fnv1a32i(const char* s) {
	uint32_t hash = 2166136261u;
	while (*s) {
		hash ^= tolower(*s++);
		hash *= 16777619u;
	}
	return hash;
}
/// Calculates case-insensitive FNV1a32 hash for given buffer. 
constexpr uint32_t fnv1a32i(const char* s, size_t count) {
	uint32_t hash = 2166136261u;
	while (count--) {
		hash ^= tolower(*s++);
		hash *= 16777619u;
	}
	return hash;
}
/// Calculates case-insensitive FNV1a32 hash for range, given start and end iterator.
template<typename TIterator>
constexpr uint32_t fnv1a32i(TIterator s, TIterator e)
{
	uint32_t hash = 2166136261u;
	while (s != e) {
		hash ^= tolower(*s++);
		hash *= 16777619u;
	}
	return hash;
}
/// Calculates case-insensitive FNV1a32 hash for iterable.
template<typename TIterable>
constexpr inline uint32_t fnv1a32i(const TIterable& c)
{
	return fnv1a32i(std::cbegin(c), std::cend(c));
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

template <typename T>
constexpr typename std::enable_if_t<sizeof(T) == 2, T>
hton(T value) noexcept
{
	return  ((value & 0x00FF) << 8)
	/***/ | ((value & 0xFF00) >> 8);
}

template <typename T>
constexpr typename std::enable_if_t<sizeof(T) == 4, T>
hton(T value) noexcept
{
	return  ((value & 0x000000FF) << 24)
	/***/ | ((value & 0x0000FF00) <<  8)
	/***/ | ((value & 0x00FF0000) >>  8)
	/***/ | ((value & 0xFF000000) >> 24);
}

template <typename T>
constexpr typename std::enable_if_t<sizeof(T) == 8, T>
hton(T value) noexcept
{
	return  ((value & 0xFF00000000000000ull) >> 56)
	/***/ | ((value & 0x00FF000000000000ull) >> 40)
	/***/ | ((value & 0x0000FF0000000000ull) >> 24)
	/***/ | ((value & 0x000000FF00000000ull) >>  8)
	/***/ | ((value & 0x00000000FF000000ull) <<  8)
	/***/ | ((value & 0x0000000000FF0000ull) << 24)
	/***/ | ((value & 0x000000000000FF00ull) << 40)
	/***/ | ((value & 0x00000000000000FFull) << 56);
}

////////////////////////////////////////////////////////////////////////////////
// Tools

void dumpMemoryToLog(const char* tag, const void* pointer, size_t length);
