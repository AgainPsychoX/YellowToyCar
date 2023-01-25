#include <sdkconfig.h>
#include <cstring>
#include <esp_log.h>
#include <lwip/sockets.h>
#include <lwip/def.h>
#include "udp.hpp"

#include "hal.hpp"
namespace app {
	extern uint64_t lastControlTime;
}

// Ugly way to force debug & verbose logs to appear, see README > Known issues.
#undef ESP_LOGD
#define ESP_LOGD(...) ESP_LOGI(__VA_ARGS__)

namespace app::udp
{

const char* TAG = "udp";

template <typename T>
constexpr inline 
std::enable_if_t<std::is_unsigned_v<T>, float> 
toFloatMotorDuty(T value, bool backwards)
{
	float a = value;
	return (backwards ? -a : a) / std::numeric_limits<T>::max();
}

void handlePacket(const UnknownPacket& packet)
{
	switch (packet.type) {
		case PacketType::ShortControl: {
			const auto& v = packet.asShortControl;
			ESP_LOGD(TAG, "ShortControlPacket: F:%02X L:%u R:%u ", 
				v.flags, v.leftDuty, v.rightDuty);
			hal::setMainLight(v.mainLight);
			hal::setOtherLight(v.otherLight);
			hal::setMotor(hal::Motor::Left,  toFloatMotorDuty(v.leftDuty, v.leftBackward));
			hal::setMotor(hal::Motor::Right, toFloatMotorDuty(v.rightDuty, v.rightBackward));
			lastControlTime = esp_timer_get_time();
			return;
		}
		case PacketType::LongControl: {
			const auto& v = packet.asLongControl;
			ESP_LOGD(TAG, "LongControlPacket: F:%02X T:%ums L:%.2f R:%.2f ", 
				v.flags, v.smoothingTime, v.targetLeftDuty, v.targetRightDuty);
			hal::setMainLight(v.mainLight);
			hal::setOtherLight(v.otherLight);
			hal::setMotor(hal::Motor::Left,  v.targetLeftDuty);
			hal::setMotor(hal::Motor::Right, v.targetRightDuty);
			// TODO: implement smooth phasing
			lastControlTime = esp_timer_get_time();
			return;
		}
	}
	ESP_LOGW(TAG, "Invalid packet!");
}

const struct sockaddr_in server_addr = {
	.sin_family = AF_INET,
	.sin_port = PP_HTONS(UDP_PORT),
	.sin_addr = {
		.s_addr = PP_HTONL(INADDR_ANY),
	}
};
static_assert(sizeof(sockaddr_in) == sizeof(sockaddr));

#if UDP_TIMEOUT > 0
const struct timeval timeout = {
	.tv_sec  = UDP_TIMEOUT / 1'000'000,
	.tv_usec = UDP_TIMEOUT % 1'000'000,
};
#endif

int sock = -1;

/// Shutdowns the UDP socket
void destroy()
{
	if (sock != -1) {
		ESP_LOGV(TAG, "Shutting down socket");
		shutdown(sock, 0);
		close(sock);
		sock = -1;
	}
}

/// Prepares for receiving UDP packets. 
/// Can be used after errors to reinitialize.
void init()
{
	int ret;

	// Make sure current socket (if any) is closed
	destroy();

	// Create a socket
	sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
	if (sock < 0) {
		ESP_LOGE(TAG, "Unable to create socket: errno %d", errno);
		return;
	}
	ESP_LOGV(TAG, "Socket created");

#if UDP_TIMEOUT > 0
	// Set timeout
	setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
#else
	// Set non-blocking
	fcntl(sock, F_SETFL, fcntl(sock, F_GETFL, 0) | O_NONBLOCK);
#endif

	// Try to reuse address & port
	ret = 1; // reuse the variable (well, before using it, so it's pre-use, isn't it?)
	setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &ret, sizeof(int));
	setsockopt(sock, SOL_SOCKET, SO_REUSEPORT, &ret, sizeof(int));

	// Bind the socket
	ret = bind(sock, reinterpret_cast<const sockaddr*>(&server_addr), sizeof(server_addr));
	if (ret < 0) {
		ESP_LOGE(TAG, "Socket unable to bind: errno %d", errno);
		destroy();
		return;
	}
	ESP_LOGV(TAG, "Socket bound, port %d", UDP_PORT);
}

/// Listens for incoming packet (until timeout)
void listen()
{
	ESP_LOGV(TAG, "Listening for UDP packet");
	int ret;
	struct sockaddr_in client_addr;
	UnknownPacket packet;
	std::memset(&packet, 0, sizeof(packet));
	socklen_t len;
	ret = recvfrom(sock, packet.buffer, maxPacketLength, 0, reinterpret_cast<sockaddr*>(&client_addr), &len);
	if (ret < 0) {
		if (errno == EAGAIN || errno == EWOULDBLOCK) {
			// Timeouts are expected, they stop the movement.
			errno = 0;
			return;
		}
		ESP_LOGW(TAG, "Failed to receive (not timeout), errno %d", errno);
		return;
	}
	const size_t bytesReceived = ret;

	ESP_LOGV(TAG, "Got packet! bytes received: %zu", bytesReceived);
	handlePacket(packet);
}

}
