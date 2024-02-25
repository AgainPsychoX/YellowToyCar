import os
import argparse
from benedict import benedict
from time import time, strftime
import cv2
import requests
import numpy as np

window_name = 'YellowToyCar Stream'

DEFAULT_IP = '192.168.4.1' # default for ESP32 esp-idf

PIXFORMAT_GRAYSCALE = 3
PIXFORMAT_JPEG      = 4

JPEG_SOI_MARKER = b'\xff\xd8'
JPEG_EOI_MARKER = b'\xff\xd9'

def check_window_is_closed(window_name):
	try:
		cv2.pollKey() # required on some backends to update window state by running queued up events handling
		return cv2.getWindowProperty(window_name, 0) < 0
	except Exception: # might throw null pointer exception if window already closed
		return True

def generate_frame_filename_for_saving(index):
	timestamp = strftime("%Y%m%d_%H%M%S")
	return f'{index:0>4}_{timestamp}'

def handle_mjpeg_stream(args, config):
	# Some code adapter from https://stackoverflow.com/questions/21702477/how-to-parse-mjpeg-http-stream-from-ip-camera
	# Other solution like `cv2.VideoCapture(stream_url)` couldn't be used, as it fails to work here.
	start = time()
	total_frames = 0
	request = requests.get(f'http://{args.ip}:81/stream', stream=True)
	if request.status_code == 200:
		buffer = bytes()
		for chunk in request.iter_content(chunk_size=4096):
			buffer += chunk
			a = buffer.find(JPEG_SOI_MARKER)
			if a == -1:
				continue
			b = buffer.find(JPEG_EOI_MARKER, a)
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

			if args.save:
				filename = generate_frame_filename_for_saving(total_frames) + '.jpg'
				cv2.imwrite(os.path.join(args.save, filename), image)

			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {request.status_code}')

def handle_jpeg_frame(args, config):
	request = requests.get(f'http://{args.ip}/capture')
	if request.status_code == 200:
		image = cv2.imdecode(np.frombuffer(request.content, dtype=np.uint8), cv2.IMREAD_COLOR)
		cv2.imshow(window_name, image)

		if args.save:
			cv2.imwrite(args.save, image)

		while True:
			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {request.status_code}')

def main():
	parser = argparse.ArgumentParser(description='''This script allows to retrieve camera frames from the car.''')
	parser.add_argument('--config-file', metavar='PATH', help='JSON file to be used as config. If not provided, will be fetched from the device.', required=False)
	parser.add_argument('--ip', '--address', help='IP of the device. Defaults to the one from the config file or 192.168.4.1.', required=False)
	parser.add_argument('--save', metavar='PATH', help='If set, specifies path to file (or folder) for the frame (or stream) to be saved.', required=False)
	parser.add_argument('--frame', help='If set, only saves/views single frame.', required=False, action='store_true')
	args = parser.parse_args()

	if args.save:
		args.save = os.path.normpath(args.save)
		if args.frame:
			if os.path.exists(args.save):
				print('Error: File exists on specified path, cannot save.')
				return
		else:
			if os.path.isfile(args.save):
				print('Error: File exists on specified path, cannot save.')
				return
			os.makedirs(args.save, exist_ok=True)
			if len(os.listdir(args.save)) != 0:
				print('Error: Directory is not empty')
				return

	if args.config_file:
		config = benedict(args.config_file, format='json')
		if config.get('network.ap.ip'):
			args.ip = config['network.ap.ip']
			print(f'Using IP from AP configuration: {args.ip}')
		elif config.get('network.sta.ip'):
			args.ip = config['network.sta.ip']
			print(f'Using IP from STA configuration: {args.ip}')
		else:
			args.ip = DEFAULT_IP
			print(f'No config with IP provided, falling back to using default IP: {args.ip}')
	else:
		if not args.ip:
			args.ip = DEFAULT_IP
			print(f'No config nor IP provided, falling back to using default IP: {args.ip}')

		print('Fetching the config from the device')
		config = benedict(f'http://{args.ip}/config', format='json', requests_options={'timeout': 5})

	pixformat = int(config['camera.pixformat'])
	if args.frame:
		if pixformat == PIXFORMAT_JPEG:
			handle_jpeg_frame(args, config)
		else:
			print('Unsupported pixel format')
	else: # stream
		if pixformat == PIXFORMAT_JPEG:
			handle_mjpeg_stream(args, config)
		else:
			print('Unsupported pixel format')

if __name__ == '__main__':
	main()
