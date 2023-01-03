
# Yellow Toy Car

This repository contains code, documentation and other stuff related to yellow toy car project I made.

<!-- TODO: One nice picture here -->





## Hardware

Hardware consist of:

* Microcontroller: [ESP32-Cam AI-Thinker development board](https://microcontrollerslab.com/esp32-cam-ai-thinker-pinout-gpio-pins-features-how-to-program/) with OV2640 camera.
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

* Movement state output (using PWM) for motors 
* UDP socket server for fast controls inputs
	* Used by external scripts
	* Used by dedicated mobile app (related project)
* Main HTTP web server
	* Status JSON
	* Configuration endpoint
	* Basic controls
	* Car camera frame capture
* Stream HTTP web server (port 81)
	* Camera stream only, since it's blocking multipart data stream.
	* Separate server to allow concurrent requests for main server.



### Web API (HTTP)

* `/` or `/index` or `/index.html` → Website presented for user to control the car.

	<!-- TODO: Website screens here -->

* `/status` → Basic status, including time, lights & motors state and other diagnostic data.

	<details><summary>Example response</summary><br/>

	```json
	{
		"time": "",
		"millis": 12345,
	}
	```
	<!-- TODO: Include actual example -->

	</details><br/>

* `/config` → Endpoint for querystring requests to set configuration. 

	<details><summary>Details</summary><br/>

	```json
	{
		"time": "",
		"millis": 12345,
	}
	```
	Returns JSON of current configuration, if not changing anything.

	<!-- TODO: Include actual example -->

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
			<td colspan="2">(UDP) Lengtd</td>
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

* For motor direction, cleared bit means forward, set bit (`1`) means backward.




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





## To-do

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
	+ Enter soft AP if couldn't connect to configured network.
	+ Allow set IP and DHCP settings for AP mode.
	+ Check periodicity for configured network while in soft AP (unless someone connected to soft AP).
	+ Detect connection dropped https://github.com/espressif/esp-idf/blob/master/examples/wifi/getting_started/softAP/main/softap_example_main.c#L33
	+ Optionally allow entering soft AP if lost and cannot find configured network, by duration setting.
	+ Captive portal when in AP mode.
	+ Password protection (especially useful when connecting to open networks).
+ Explore hidden features of the camera, see https://github.com/espressif/esp32-camera/issues/203



