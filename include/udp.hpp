#pragma once
#include <sdkconfig.h>
#include "common.hpp"

#define UDP_PORT 83
#define UDP_TIMEOUT 10'000 // us, or 0 to use non-blocking mode

namespace app::udp
{

extern const char* TAG;

enum class PacketType : uint8_t {
	ShortControl = 1,
	LongControl = 2,
};

struct ShortControlPacket {
	PacketType type;
	union {
		uint8_t flags;
		struct {
			bool mainLight      : 1;
			bool otherLight     : 1;
			uint8_t _reserved   : 4;
			bool leftBackward   : 1;
			bool rightBackward  : 1;
		};
	};
	uint8_t leftDuty;
	uint8_t rightDuty;
};
struct LongControlPacket {
	PacketType type;
	union {
		uint8_t flags;
		struct {
			bool mainLight      : 1;
			bool otherLight     : 1;
			uint8_t _reserved   : 6;
		};
	};
	uint16_t smoothingTime; // ms
	float targetLeftDuty; // 63.8f == 62.8%
	float targetRightDuty;
};

constexpr size_t maxPacketLength = 16;
union UnknownPacket {
	char buffer[maxPacketLength];
	struct {
		PacketType type;
	};
	ShortControlPacket asShortControl;
	LongControlPacket asLongControl;
};
static_assert(sizeof(UnknownPacket) == maxPacketLength);

void destroy();
void init();
void listen();

}
