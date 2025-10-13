
#if defined(MODEL_ESP32CAM)
#	define CAM_PIN_PWDN     32
#	define CAM_PIN_RESET    -1
#	define CAM_PIN_XCLK     0
#	define CAM_PIN_SIOD     26
#	define CAM_PIN_SIOC     27
#	define CAM_PIN_D7       35
#	define CAM_PIN_D6       34
#	define CAM_PIN_D5       39
#	define CAM_PIN_D4       36
#	define CAM_PIN_D3       21
#	define CAM_PIN_D2       19
#	define CAM_PIN_D1       18
#	define CAM_PIN_D0       5
#	define CAM_PIN_VSYNC    25
#	define CAM_PIN_HREF     23
#	define CAM_PIN_PCLK     22
#elif defined(MODEL_ESP32S3EYE)
#	define CAM_PIN_PWDN     -1
#	define CAM_PIN_RESET    -1
#	define CAM_PIN_XCLK     15
#	define CAM_PIN_SIOD     4
#	define CAM_PIN_SIOC     5
#	define CAM_PIN_D7       11
#	define CAM_PIN_D6       9
#	define CAM_PIN_D5       8
#	define CAM_PIN_D4       10
#	define CAM_PIN_D3       12
#	define CAM_PIN_D2       18
#	define CAM_PIN_D1       17
#	define CAM_PIN_D0       16
#	define CAM_PIN_VSYNC    6
#	define CAM_PIN_HREF     7
#	define CAM_PIN_PCLK     13
#else
#	error "Unsupported model"
#endif
