#include "ai.hpp"
#include <dl_image_jpeg.hpp>
#include <hand_detect.hpp>
#include <hand_gesture_recognition.hpp>
#include "camera.hpp"

extern const uint8_t gesture_jpg_start[] asm("_binary_gesture_jpg_start");
extern const uint8_t gesture_jpg_end[] asm("_binary_gesture_jpg_end");
const char *TAG = "hand_gesture_recognition";

namespace app::ai
{

static const char* TAG = "AI";

HandDetect* hand_detect;
HandGestureRecognizer* hand_gesture_recognizer;

void init()
{
	hand_detect = new HandDetect(
		/*model_type*/ static_cast<HandDetect::model_type_t>(CONFIG_DEFAULT_HAND_DETECT_MODEL),
		/*lazy_load*/ false);
	hand_gesture_recognizer = new HandGestureRecognizer(HandGestureCls::MOBILENETV2_0_5_S8_V1);
	ESP_LOGI(TAG, "Initialized");
}

void recognize_gesture(const camera_fb_t& fb)
{
	if (fb.format != PIXFORMAT_JPEG) {
		ESP_LOGE(TAG, "not implemented for now");
		return;
	}

	dl::image::jpeg_img_t gesture_jpeg = { .data = fb.buf, .data_len = fb.len };
	auto gesture = dl::image::sw_decode_jpeg(gesture_jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);

	ESP_LOGI(TAG, "Running recognizer");
	std::vector<dl::cls::result_t> results = hand_gesture_recognizer->recognize(gesture, hand_detect->run(gesture));
	
	ESP_LOGI(TAG, "Done");
	for (const auto &res : results) {
		ESP_LOGI(TAG, "category: %s, score: %f", res.cat_name, res.score);
	}

	heap_caps_free(gesture.data); //?
}

}
