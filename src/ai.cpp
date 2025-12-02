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
		/*lazy_load*/ true); // TODO: fix crash when false; I think it happens most of the time because main task (where init happens) has not enough stack
	hand_gesture_recognizer = new HandGestureRecognizer(HandGestureCls::MOBILENETV2_0_5_S8_V1);
	ESP_LOGI(TAG, "Initialized");
}

std::string recognize_gesture_to_json(const camera_fb_t& fb)
{
	if (fb.format != PIXFORMAT_JPEG) {
		return R"({"error":"Using format other than JPEG is not implemented for now"}])";
	}

	dl::image::jpeg_img_t img_jpeg = { .data = fb.buf, .data_len = fb.len };
	auto img_rgb = dl::image::sw_decode_jpeg(img_jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
	if (img_rgb.data == nullptr) {
		return R"({"error":"Failed to decode JPEG"}])";
	}

	ESP_LOGI(TAG, "Running recognizer");
	auto detection_results = hand_detect->run(img_rgb);
	auto gesture_results = hand_gesture_recognizer->recognize(img_rgb, detection_results);
	if (gesture_results.empty()) {
		heap_caps_free(img_rgb.data);
		return R"({"hands":[]}])";
	}
	
	// assert(detection_results.size() == gesture_results.size()); // TODO: but is it really?
	if (detection_results.size() != gesture_results.size()) {
		ESP_LOGW(TAG, "results counts mismatch: %zu != %zu", detection_results.size() != gesture_results.size());
	}

	std::string output;
	output.resize(512); // TODO: BTW, stupid thing, it zero-fills always anyway... makes me a bit tilted there is no `set_len` like in Rust. C++ 20 solved with with `std::format_to`. Also, it crashes on OOM and there is nothing one can do about it. C++ exceptions SUCK big time. If only there was `try_reserve` like in Rust... ffs..
	char* position = output.data();
	size_t remaining = output.size();
	// TODO: multiple results support
	const auto& d = detection_results.front();
	const auto& g = gesture_results.front();
	// TODO: understand box score and category more
	int ret = snprintf(position, remaining, R"({"hands":[{"box":[%d,%d,%d,%d],"box_score":%.2f,"box_category":%d,"gesture_category":"%s","gesture_score":%.2f}]})",
		d.box[0], d.box[1], d.box[2], d.box[3], d.score, d.category, g.cat_name, g.score);
	if (unlikely(ret < 0)) {
		// TODO: rewrite error handling to avoid repeating code, maybe smart ptr
		heap_caps_free(img_rgb.data);
		return R"({"error":"Failed to prepare response"}])";
	}
	position += ret;
	remaining -= ret;
	output.resize(position - output.data());

	heap_caps_free(img_rgb.data); //?
	return output;
}

}
