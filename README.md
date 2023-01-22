
# Yellow Toy Car

This repository contains code, documentation and other stuff related to yellow toy car project I made.

<!-- TODO: One nice picture here -->





## Hardware

Hardware consist of:

* Microcontroller: [ESP32-Cam AI-Thinker development board](https://microcontrollerslab.com/esp32-cam-ai-thinker-pinout-gpio-pins-features-how-to-program/)
	* ESP32S chip
		* 2x 32-bit LX6 CPU; up to 240 MHz; 520 KB SRAM.
		* 802.11 b/g/n Wi-Fi and Bluetooth 4.2 BR/EDR with BLE
	* PSRAM on board, adding 4 MB.
	* OV2640 camera.
	* MicroSD card slot (unused, as GPIOs are used for motors and flash LED).
	* 2 LEDs: red internal pulled high, and bright white external, acting for camera flash.
* Motors driver: [L298N-based module](https://abc-rc.pl/product-pol-6196-Modul-sterownika-L298N-do-silnikow-DC-i-krokowych-Arduino.html?query_id=1), able to drive 2 DC motors.
* 4 motors, controlled in pairs, attached by gears to wheels.
* External antena for ESP32 Wi-Fi connectivity is used.
* Battery (3 cells of 4 V, total 12 V for main board, 8 V for motors used).
* Additional circuitry:
	* Voltage converter (down to 5V, red LED)
	* Voltage stabilizator (down to 3.3V required for ESP32, green LED).
	* Battery, motor drivers and programmer connectors.
	* Switch for programming mode (ON to program, OFF to execute). 
* Plastic grid and packaging.

<!-- TODO: Pictures here, in table -->





## Software

Software consist of:

+ Espressif IoT Development Framework (ESP-IDF) is used
+ Networking related code (<abbr title="access point">AP</abbr> or <abbr title="station">STA</abbr>)
+ Camera related code
+ JSON configuration interface functions
+ Main HTTP web server (port 80)
	+ Status JSON
	+ Configuration endpoint
	+ Basic (slow) controls
	+ Car camera frame capture
+ Stream HTTP web server (port 81)
	+ Camera stream only, since it's blocking multipart data stream.
	+ Separate server to allow concurrent requests for main server.
+ Simple HAL for the motors and the lights
+ UDP socket server for fast controls inputs (port 83)
	+ Used by external scripts, allowing to control from the computer.
	+ Used by dedicated mobile app (related project)



### Web API (HTTP)

* `/` or `/index` or `/index.html` → Website presented for user to control the car.

	<!-- TODO: Website screens here -->

* `/status` → Basic status, including time, lights & motors state and other diagnostic data.

	<details><summary>Details</summary><br/>

	```json
	{
		"uptime": 123456, // Microseconds passed from device boot.
		"time": "2023-01-12T23:49:03.348+0100", // Device time, synced using SNTP.
		"rssi": -67, // Signal strength of AP the device is connected to, or 0 if not connected.

		/* With `?details=1` querystring parameter, extended response is provided. */
		"stations": ["a1:b2:c3:d4:e5:f6"], // list of stations currently connected to our AP
	}
	```

	</details><br/>

* `/config` → Endpoint for requests to set configuration (JSON GET/POST API)

	<details><summary>Details</summary><br/>

	```json
	{
		/* Control & config for motors and lights */
		"control": {
			/* Other */
			"timeout": 2000, // Time in milliseconds counted from last control request/packet, after which movement should stop for safety reason
			/* Input values */
			"mainLight": 1,
			"otherLight": 1,
			"left": 12.3,  // The motors duty cycle are floats as percents,
			"right": 12.3, // i.e. 12.3 means 12.3% duty cycle.
			/* Calibration */
			"calibrate": {
				"left": 0.95, // Inputs will be multiplied by calibration values before outputting PWM signal.
				"right": 1.05,
				"frequency": 100, // Frequency to be used by PWMs
			}
		},
		/* Networking related. Some things are not implemented, including: DNS and DHCP leases */
		"network": {
			"mode": "ap", // for Access Point or "sta" for station mode, or "nat" (to make it work like router)
			"fallback": 10000, // duration after should fallback to hosting AP if cannot connect as station
			"dns1": "1.1.1.1",
			"dns2": "1.0.0.1",
			"sta": {
				"ssid": "YellowToyCar",
				"psk": "AAaa11!!",
				"static": 0, // 1 if static IP is to be used in STA mode
				"ip": "192.168.4.1",
				"mask": 24, // as number or IP
				"gateway": "192.168.4.1"
			},
			"ap": {
				"ssid": "YellowToyCar",
				"psk": "AAaa11!!",
				"channel": 0, // channel to use for AP, 0 for automatic
				"hidden": 0,
				"ip": "192.168.4.1",
				"mask": 24, // as number or IP
				"gateway": "192.168.4.1",
				"dhcp": {
					"enabled": 1,
					"lease": ["192.168.4.1", "192.168.4.20"],
				}
			},
			"sntp": {
				"pool": "pl.pool.ntp.org",
				"tz": "CET-1CEST,M3.5.0,M10.5.0/3",
				"interval": 3600000
			}
		},
		/* Camera settings. See this project or `esp32_camera` library sources for details. */
		"camera": {
			"framesize": 13,
			"pixformat": 4,
			"quality": 12,
			"bpc": 0,
			"wpc": 1,
			"hmirror": 0,
			"vflip": 0,
			"contrast": 0,
			"brightness": 0,
			"sharpness": 0,
			"denoise": 0,
			"gain_ceiling": 0,
			"agc": 1,
			"agc_gain": 0,
			"aec": 1,
			"aec2": 0,
			"ae_level": 0,
			"aec_value": 168,
			"awb": 1,
			"awb_gain": 1,
			"wb_mode": 0,
			"dcw": 1,
			"raw_gma": 1,
			"lenc": 1,
			"special": 0
		}
	}
	```
	Returns JSON of current configuration, if not changing anything. 

	* For AP mode, default IP/gateway should stay `192.168.4.1` for now, as DHCP settings are hardcoded to some default values.
	* DNS, SNTP and NAT settings are also not implemented yet.

	</details><br/>

* `/drive` → Basic controls endpoint, might be lagging as it's over HTTP, which uses TCP, which might retransmit old requests).

	<details><summary>Details</summary><br/>

	Querystring API:
	```c
	?mainLight=1    // Main light (external bright white LED)
	&otherLight=1   // Other light (internal small red LED)
	&left=255       // Left motor duty and direction (negative values for backward)
	&right=255      // Right motor duty and direction (negative values for backward)
	```
	Returns nothing.

	</details><br/>

* `/capture` → Frame capture from the car camera.

* `:81/stream` → Continuous frames stream from the car camera. Using sepa



### Fast controls API (UDP)

#### Short control packet

<table>
	<tbody>
		<tr>
			<th></th>
			<th><sub>Octet</sub></th>
			<th style="text-align:center"><sub>0</sub></th>
			<th style="text-align:center"><sub>1</sub></th>
			<th style="text-align:center"><sub>2</sub></th>
			<th style="text-align:center"><sub>3</sub></th>
		</tr>
		<tr>
			<th><sub>Octet</sub></th>
			<th><sub>Bits</sub></th>
			<th><i><sub>0 &nbsp; 1 &nbsp; 2 &nbsp; 3 &nbsp; 4 &nbsp; 5 &nbsp; 6 &nbsp; 7</sub></i></th>
			<th><i><sub>8 &nbsp; 9 &nbsp; 10 &nbsp; 11 &nbsp; 12 &nbsp; 13 &nbsp; 14 &nbsp; 15</sub></i></th>
			<th><i><sub>16 &nbsp; 17 &nbsp; 18 &nbsp; 19 &nbsp; 20 &nbsp; 21 &nbsp; 22 &nbsp; 23</sub></i></th>
			<th><i><sub>24 &nbsp; 25 &nbsp; 26 &nbsp; 27 &nbsp; 28 &nbsp; 29 &nbsp; 30 &nbsp; 31</sub></i></th>
		</tr>
		<tr>
			<td>0</td>
			<td>0</td>
			<td colspan="2">(UDP) Source port</td>
			<td colspan="2">(UDP) Destination port</td>
		</tr>
		<tr>
			<td>4</td>
			<td>32</td>
			<td colspan="2">(UDP) Length</td>
			<td colspan="2">(UDP) Checksum</td>
		</tr>
		<tr>
			<td>8</td>
			<td>64</td>
			<td colspan="1">Packet type <sup>(always 1)</sup></td>
			<td colspan="1">Flags <sup>(see table below)</sup></td>
			<td colspan="1">Left motor duty</td>
			<td colspan="1">Right motor duty</td>
		</tr>
	</tbody>
</table>

#### Flags

| Bit | Mask         | Description                                             |
|:---:|:-------------|:--------------------------------------------------------|
| 0   | `0b00000001` | Main light (external bright white LED)                  |
| 1   | `0b00000010` | Other light (internal small red LED)                    |
| 2   | `0b00000100` | Reserved                                                |
| 3   | `0b00001000` | Reserved                                                |
| 4   | `0b00010000` | Reserved                                                |
| 5   | `0b00100000` | Reserved                                                |
| 6   | `0b01000000` | Left motor direction                                    |
| 7   | `0b10000000` | Right motor direction                                   |

* For motor direction in the flags, cleared bit (`0`) means forward, set bit (`1`) means backward.

#### Long control packet

<table>
	<tbody>
		<tr>
			<th></th>
			<th><sub>Octet</sub></th>
			<th style="text-align:center"><sub>0</sub></th>
			<th style="text-align:center"><sub>1</sub></th>
			<th style="text-align:center"><sub>2</sub></th>
			<th style="text-align:center"><sub>3</sub></th>
		</tr>
		<tr>
			<th><sub>Octet</sub></th>
			<th><sub>Bits</sub></th>
			<th><i><sub>0 &nbsp; 1 &nbsp; 2 &nbsp; 3 &nbsp; 4 &nbsp; 5 &nbsp; 6 &nbsp; 7</sub></i></th>
			<th><i><sub>8 &nbsp; 9 &nbsp; 10 &nbsp; 11 &nbsp; 12 &nbsp; 13 &nbsp; 14 &nbsp; 15</sub></i></th>
			<th><i><sub>16 &nbsp; 17 &nbsp; 18 &nbsp; 19 &nbsp; 20 &nbsp; 21 &nbsp; 22 &nbsp; 23</sub></i></th>
			<th><i><sub>24 &nbsp; 25 &nbsp; 26 &nbsp; 27 &nbsp; 28 &nbsp; 29 &nbsp; 30 &nbsp; 31</sub></i></th>
		</tr>
		<tr>
			<td>0</td>
			<td>0</td>
			<td colspan="2">(UDP) Source port</td>
			<td colspan="2">(UDP) Destination port</td>
		</tr>
		<tr>
			<td>4</td>
			<td>32</td>
			<td colspan="2">(UDP) Length</td>
			<td colspan="2">(UDP) Checksum</td>
		</tr>
		<tr>
			<td>8</td>
			<td>64</td>
			<td colspan="1">Packet type: 2</td>
			<td colspan="1">Flags <sup>(see below)</sup></td>
			<td colspan="2">Time (in milliseconds) to smooth blend towards target motor values</td>
		</tr>
		<tr>
			<td>12</td>
			<td>96</td>
			<td colspan="4">Left motor duty, percent as float (i.e. <code>63.8f</code> equals to 63.3% duty cycle)</td>
		</tr>
		<tr>
			<td>16</td>
			<td>128</td>
			<td colspan="4">Right motor duty, percent as float (i.e. <code>63.8f</code> equals to 63.3% duty cycle)</td>
		</tr>
	</tbody>
</table>

* The flags in long control packet are the same as in the short, but without motor directions flags respected. 
* Use negative float numbers for moving backwards.



### Tasks

| Friendly name | Name     | Affinity | Priority | Source file | Description   |
|:--------------|:---------|:--------:|:--------:|:------------|:--------------|
| IPC tasks     | `ipcx`\* | All\*    | 0        | (internal)  | IPC tasks are used to implement the Inter-Processor Call feature.          |
| Main          | `main`   | CPU0     | 1        | `main.cpp`  | Initializes everything, starts other tasks, then carries background logic. |
| Camera stream | `httpd`  | CPU0     | 5        | `camera.cpp`
| LwIP          |          | ?
| WiFi          |          | CPU0
| Events        |          | ?
| Idle tasks    | `ipcx`\* | All\*    | 24       | (internal)  | Idle tasks created for (and pinned to) each CPU.

<small>\* - Some tasks work on multiple CPUs, as separate tasks.</small>





## Notes


### Known issues

* C/C++ compiler used is quite old and includes decade old known GCC bug related to `struct`s aggregate initializers. See [discussion here](https://stackoverflow.com/questions/70172941/c99-designator-member-outside-of-aggregate-initializer). As solution I found out its easiest to use `strncpy` which gets inlined/optimized away.
* [The PlatformIO docs about embedding files](https://docs.platformio.org/en/latest/platforms/espressif32.html#embedding-binary-data) suggest to use prefix `_binary_src_` while accessing the start/end labels of embedded data blocks (like in  `GENERATE_HTTPD_HANDLER_FOR_EMBEDDED_FILE` macro), its not true. The docs seems outdated or invalid in some areas, at least for `esp-idf`. However I found **solution**: Use both `board_build.embed_files` in `platformio.ini` and also `EMBED_FILES` in `CMakeLists.txt`. In code, use `_binary_`, without `src_` part.
* Code style is a bit mess, `snake_case` mixed with `camelCase` because we use C libraries from ESP-IDF and some parts use them a lot. It's even uglier to ride a single camel in the middle of snakes.
* There is [an issue with easy enabling `ESP_LOGV` and `ESP_LOGD` for single file](https://github.com/espressif/esp-idf/issues/8570), so I redefine those macros to `ESP_LOGI` as a workaround.
* ...



### To-do

+ Files:
	+ `main.cpp` externs everything, initializes everything, starts everything...
	+ `http.cpp` servers handlers (both main & streaming)
	+ `udp.cpp` whole UDP fast control server, externs state.
	+ `hal.cpp` abstraction over motors and lights.
+ Min-max tasks:
	+ CPU pins:
		+ One core for HTTP and trash tasks
		+ Other core for networking & fast control (UDP)
	+ Use mutex to lock frame buffer between capture and streaming.
	+ Trace tasks? `vTaskList`/`uxTaskGetSystemState`
+ Networking
	+ Configuration API
	+ Allow set static IP for station mode.
	+ Configuration UI
	+ Fallback timeout: Enter AP if couldn't connect as STA.
	+ Check periodicity for configured network while in soft AP (unless someone connected to soft AP).
	+ Detect connection dropped https://github.com/espressif/esp-idf/blob/master/examples/wifi/getting_started/softAP/main/softap_example_main.c#L33
	+ Optionally allow entering soft AP if lost and cannot find configured network, by duration setting.
	+ Allow set IP and DHCP settings for AP mode.
	+ Allow change DNS settings.
	+ Input sanitization, i.e. disallow using invalid IP addresses.
	+ Captive portal when in AP mode.
	+ Password protection (especially useful when connecting to open networks).
+ If password was to be implemented, don't forget to secure UDP server somehow.
+ Should there be status/echo packet types for UDP?
+ [SNTP time sync](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/system_time.html#sntp-time-synchronization)
	+ Make pool server and timezone configurable
+ Explore hidden features of the camera, see https://github.com/espressif/esp32-camera/issues/203
+ Isn't `COM8_AGC_EN` in the camera registers definitions off by 1? 
+ Detailed status output, including debug stuff
	+ Process list and stats.
	+ Memory heap usage & fragmentation.
	+ NVS dump. See https://github.com/AFontaine79/Espressif-NVS-Analyzer
	+ Networking stats.
+ Camera parameters are better described in [CircuitPython bindings docs for the esp32_camera library](https://docs.circuitpython.org/en/latest/shared-bindings/esp32_camera/index.html).
+ Create our own `Kconfig` file to keep optional features there, including some debugging. Also see https://esp32tutorials.com/esp32-static-fixed-ip-address-esp-idf/ 
+ You can use NAT?! 
	+ https://github.com/jonask1337/esp-idf-nat-example/blob/master/main/main.c 
	+ https://github.com/espressif/esp-lwip/blob/6132c9755a43d4e04de4457f1558ced415756e4d/src/core/ipv4/ip4_napt.c#L228
+ Use default C++ [`std::hash`](https://en.cppreference.com/w/cpp/utility/hash) (murmur most-likely, but might be more optimized than our `fnv1a32`)
+ Use `std::` over C stuff where possible, please?
+ Create fast and C++ `constexpr` string to IP 4 function
+ How do we nicely pass understandable error, i.e. from parsing config to response? https://github.com/TartanLlama/expected 👀
+ How does JSMN JSON handle escaping characters? Some strings like SSID/PSK might be invalid...
+ Allow some calibration for motors
+ Allow changing frequency for PWM signals for motors
+ Control LEDs with PWM?
+ Expose nice [console](https://docs.espressif.com/projects/esp-idf/en/v4.4.3/esp32/api-reference/system/console.html) over serial monitor
	+ Basic WiFi config
	+ Allow uploading JSON to change config?


