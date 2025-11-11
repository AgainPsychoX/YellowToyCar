#include <sdkconfig.h>
#include <memory>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <lwip/sockets.h>
#include <lwip/api.h>
#include <lwip/tcp.h>
#include <lwip/udp.h>
#include <esp_log.h>
#include "utils.hpp" // saturatedSubtract

// Including in extra extern C (despite one being inside) because lwip_socket_dbg_get_socket is outside the inner one
extern "C" {
#include <lwip/priv/sockets_priv.h>
}

#include <private/external/freertos_queue_priv.h>
#include <private/external/freertos_tasks_priv.h>

namespace app::debug
{

static const char *TAG = "socket_debug";

struct FreeRTOS_Deleter
{
	void operator()(void* p) { vPortFree(p); }
};

esp_err_t print_lwip_sockets_status(void)
{
	vTaskSuspendAll();

	// Prepare tasks statuses array
	UBaseType_t num_tasks = uxTaskGetNumberOfTasks();
	std::unique_ptr<TaskStatus_t, FreeRTOS_Deleter> pxTaskStatusArray(
		static_cast<TaskStatus_t*>(pvPortMalloc(num_tasks * sizeof(TaskStatus_t))));
	if (unlikely(pxTaskStatusArray == nullptr))
		return ESP_ERR_NO_MEM;
	num_tasks = uxTaskGetSystemState(pxTaskStatusArray.get(), num_tasks, NULL);

	// Prepare buffer for printing
	constexpr size_t bufferSize = NUM_SOCKETS * 128;
	std::unique_ptr<char, FreeRTOS_Deleter> buffer(
		static_cast<char*>(pvPortMalloc(bufferSize)));
	if (unlikely(buffer == nullptr))
		return ESP_ERR_NO_MEM;
	int ret;
	char* position = buffer.get();
	size_t remaining = bufferSize;
	
	int used_sockets = 0;
	for (int i = 0; i < NUM_SOCKETS; i++) {
		int fd = i + LWIP_SOCKET_OFFSET;
		struct lwip_sock* sock = ::lwip_socket_dbg_get_socket(fd);
		if (sock && sock->conn) {
			used_sockets++;
			const char *type = "?";
			if (sock->conn->type == NETCONN_TCP) { type = "TCP"; }
			else if (sock->conn->type == NETCONN_UDP) { type = "UDP"; } 
			else if (sock->conn->type == NETCONN_RAW) { type = "RAW"; }

			#define FOO_BAR_XYZ(MBOX, LIST, WHICH) do { \
				if (sock->conn->MBOX && sock->conn->MBOX->os_mbox) { \
					if (not listLIST_IS_EMPTY(&sock->conn->MBOX->os_mbox->LIST)) { \
						TCB_t* tcb = static_cast<TCB_t*>(listGET_OWNER_OF_HEAD_ENTRY(&sock->conn->MBOX->os_mbox->LIST)); \
						ret = snprintf(position, remaining, "%d'%s'", WHICH, tcb->pcTaskName); \
						if (unlikely(ret < 0)) break; \
						position += ret; \
						remaining = saturatedSubtract(remaining, ret); \
					} \
				} \
			} while (0)

			ret = snprintf(position, remaining, "\nfd=%d type=%s state=%d task=", fd, type, sock->conn->state);
			if (unlikely(ret < 0)) break;
			position += ret;
			remaining = saturatedSubtract(remaining, ret);

			FOO_BAR_XYZ(recvmbox, xTasksWaitingToReceive, 0);
			FOO_BAR_XYZ(recvmbox, xTasksWaitingToSend, 1);
			FOO_BAR_XYZ(acceptmbox, xTasksWaitingToReceive, 2);
			FOO_BAR_XYZ(acceptmbox, xTasksWaitingToSend, 3);

			char local_ip_str[16];
			char remote_ip_str[16];
			switch (sock->conn->type) {
				case NETCONN_TCP: 
					if (sock->conn->pcb.tcp) {
						struct tcp_pcb* pcb = sock->conn->pcb.tcp;
						ipaddr_ntoa_r(&pcb->local_ip, local_ip_str, sizeof(local_ip_str));
						ipaddr_ntoa_r(&pcb->remote_ip, remote_ip_str, sizeof(remote_ip_str));
						ret = snprintf(position, remaining, " TCP: state=%s local=%s:%d remote=%s:%d", 
							tcp_debug_state_str(pcb->state), local_ip_str, pcb->local_port, remote_ip_str, pcb->remote_port);
						position += ret;
						remaining = saturatedSubtract(remaining, ret);
					}
					break;
				case NETCONN_UDP:
					if (sock->conn->pcb.udp) {
						struct udp_pcb* pcb = sock->conn->pcb.udp;
						ipaddr_ntoa_r(&pcb->local_ip, local_ip_str, sizeof(local_ip_str));
						ipaddr_ntoa_r(&pcb->remote_ip, remote_ip_str, sizeof(remote_ip_str));
						ret = snprintf(position, remaining, " UDP: local=%s:%d remote=%s:%d", 
							local_ip_str, pcb->local_port, remote_ip_str, pcb->remote_port);
						position += ret;
						remaining = saturatedSubtract(remaining, ret);
					}
					break;
				default:
					break; // ignore unexpected
			}
		}
	}
	xTaskResumeAll();

	ESP_LOGI(TAG, 
		"--- LWIP Sockets Status (Total: %d) ---"
		"%s" // starts with new line, so it's fine here
		"\nUsed sockets: %d, Free sockets: %d", 
		NUM_SOCKETS, buffer.get(), used_sockets, NUM_SOCKETS - used_sockets);
	return ret > 0 ? ESP_OK : ESP_ERR_NOT_FINISHED;
}

static void socket_debug_task(void *pvParameters)
{
	(void)pvParameters;
	while (1) {
		print_lwip_sockets_status();
		vTaskDelay(pdMS_TO_TICKS(500));
	}
}

void init(void)
{
	xTaskCreate(socket_debug_task, "socket_debug_task", 4096, NULL, 5, NULL);
	ESP_LOGI(TAG, "Socket debug task started.");
}

}
