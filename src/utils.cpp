#include "utils.hpp"
#include <cctype>
#include <esp_log.h>

void dumpMemoryToLog(const char* tag, const void* pointer, size_t length)
{
	const char* message = static_cast<const char*>(pointer);
	char buffer[80]; // (16*3 + 2 + 16 + 1) < 80
	for (size_t i = 0; i < length; i += 16) {
		char* position = buffer;

		// Print bytes as hex
		for (size_t j = 0; j < 16; j++) {
			position += sprintf(position, "%02hhX ", message[i + j]);
			if (i + j + 1 >= length) {
				while (++j < 16) {
					position[0] = position[1] = position[2] = ' ';
					position += 3;
				}
				break;
			}
		}

		*position++ = '|';
		*position++ = ' ';

		// Print bytes as printable characters (if possible)
		for (size_t j = 0; j < 16; j++) {
			char c = i + j >= length ? '.' // out of length
				: isprint(message[i + j]) ? message[i + j] // printable
				: '.'; // non-printable
			position += sprintf(position, "%c", c);
		}

		ESP_LOGV(tag, "%s", buffer);
	}
	// TODO: add offsets/headers on left & top? maybe addresses instead offset?
}
