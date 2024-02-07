from time import time
import cv2
import requests
import numpy as np

# Script adapts code from https://stackoverflow.com/questions/21702477/how-to-parse-mjpeg-http-stream-from-ip-camera
# Other solution like `cv2.VideoCapture(stream_url)` couldn't be used, as it fails to work here.

stream_url = 'http://192.168.4.1:81/stream'
window_name = 'YellowToyCar Stream'

def check_window_is_closed(window_name):
	try:
		cv2.pollKey() # required on some backends to update window state by running queued up events handling
		return cv2.getWindowProperty(window_name, 0) < 0
	except Exception: # might throw null pointer exception if window already closed
		return True

def main():
	start = time()
	total_frames = 0
	request = requests.get(stream_url, stream=True)
	if request.status_code == 200:
		buffer = bytes()
		for chunk in request.iter_content(chunk_size=4096):
			buffer += chunk
			a = buffer.find(b'\xff\xd8')
			if a == -1:
				continue
			b = buffer.find(b'\xff\xd9', a)
			if b == -1:
				continue

			jpg = buffer[a:b+2]
			buffer = buffer[b+2:]
			image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
			cv2.imshow(window_name, image)

			total_frames += 1
			from_start = time() - start
			fps = total_frames / from_start
			print(f'{from_start:.3f}s: frame #{total_frames}\tFPS: {fps}')

			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {request.status_code}')


if __name__ == '__main__':
	main()
