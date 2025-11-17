import os
import shutil
import argparse
from benedict import benedict
from collections import deque
from time import time, strftime
import cv2
import requests
import numpy as np

window_name = 'YellowToyCar Stream'

DEFAULT_IP = '192.168.4.1' # default for ESP32 esp-idf

PIXFORMAT_RGB565    = 0
PIXFORMAT_YUV422    = 1
PIXFORMAT_GRAYSCALE = 3
PIXFORMAT_JPEG      = 4

JPEG_SOI_MARKER = b'\xff\xd8'
JPEG_EOI_MARKER = b'\xff\xd9'

FRAMESIZE_TO_DIMS = [
	(96, 96),      # 0: FRAMESIZE_96X96
	(160, 120),    # 1: FRAMESIZE_QQVGA
	(128, 128),    # 2: FRAMESIZE_128X128
	(176, 144),    # 3: FRAMESIZE_QCIF
	(240, 176),    # 4: FRAMESIZE_HQVGA
	(240, 240),    # 5: FRAMESIZE_240X240
	(320, 240),    # 6: FRAMESIZE_QVGA
	(400, 296),    # 7: FRAMESIZE_CIF
	(480, 320),    # 8: FRAMESIZE_HVGA
	(640, 480),    # 9: FRAMESIZE_VGA
	(800, 600),    # 10: FRAMESIZE_SVGA
	(1024, 768),   # 11: FRAMESIZE_XGA
	(1280, 720),   # 12: FRAMESIZE_HD
	(1280, 1024),  # 13: FRAMESIZE_SXGA
	(1600, 1200),  # 14: FRAMESIZE_UXGA
]

def check_window_is_closed(window_name):
	try:
		cv2.pollKey() # required on some backends to update window state by running queued up events handling
		return cv2.getWindowProperty(window_name, 0) < 0
	except Exception: # might throw null pointer exception if window already closed
		return True

def generate_frame_filename_for_saving(index):
	timestamp = strftime("%Y%m%d_%H%M%S")
	return f'{index:0>4}_{timestamp}'

def _escape_non_printable_ascii(s: str) -> str:
	"""Replaces non-printable ASCII characters with their escape sequences."""
	return ''.join(c if 32 <= ord(c) <= 126 else repr(c)[1:-1] for c in s)

def print_unexpected(data: bytes):
	"""
	Prints data from the stream. If data is ASCII, prints it as a string.
	If it contains non-ASCII characters, prints a hex dump, truncating
	if it is too long.
	"""
	try:
		# Check if the data can be fully decoded as ASCII
		decoded_str = data.decode('ascii')
		if not decoded_str.strip(): # Still check if it's effectively empty after stripping
			return # Don't print if it's only whitespace or empty
		print(f"  ASCII: '{_escape_non_printable_ascii(decoded_str)}'")
	except UnicodeDecodeError:
		# If not, print a hex dump
		if len(data) <= 16:
			hex_line = ' '.join(f'{b:02x}' for b in data)
			ascii_line = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data)
			print(f"  HEX:   {hex_line}")
			print(f"  ASCII: {ascii_line}")
		else:
			def print_hex_chunk(offset, chunk_data):
				hex_part = ' '.join(f'{b:02x}' for b in chunk_data)
				ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk_data)
				print(f"{offset:02x} | {hex_part:<47} | {ascii_part}")

			# Pretty print in a hex dump table format
			header_hex = ' '.join(f'{i:02x}' for i in range(16))
			print(f"   | {header_hex} | 0123456789ABCDEF (ASCII)")
			print(f"-- | {'-' * (16 * 3 - 1)} | {'-' * 16}")

			if len(data) > 128: # 8 lines * 16 bytes/line
				# Print first 4 lines
				for i in range(0, 64, 16):
					print_hex_chunk(i, data[i:i+16])
				print("...| ... | ...")
				# Print last 4 lines
				start_of_last_part = len(data) - 64
				for i in range(0, 64, 16):
					offset = start_of_last_part + i
					print_hex_chunk(offset, data[offset:offset+16])
			else:
				for i in range(0, len(data), 16):
					print_hex_chunk(i, data[i:i+16])

################################################################################

def handle_mjpeg_stream(args, config):
	# Some code adapted from https://stackoverflow.com/questions/21702477/how-to-parse-mjpeg-http-stream-from-ip-camera
	# Other solution like `cv2.VideoCapture(stream_url)` couldn't be used, as it fails to work here.
	start = time()
	total_frames = 0
	saved_frames = 0
	total_bytes = 0
	last_frame_points = deque()
	response = requests.get(f'http://{args.ip}:81/stream', stream=True)
	if response.status_code == 200:
		buffer = bytes()
		for chunk in response.iter_content(chunk_size=4096):
			total_bytes += len(chunk)
			buffer += chunk
			a = buffer.find(JPEG_SOI_MARKER)
			if a == -1:
				continue
			b = buffer.find(JPEG_EOI_MARKER, a)
			if b == -1:
				continue

			jpg = buffer[a:b+2]
			buffer = buffer[b+2:]

			now = time()
			last_frame_points.append((now, total_bytes))
			while len(last_frame_points) > 1 and last_frame_points[0][0] < (now - args.fps_window): # keep at least 2
				last_frame_points.popleft()
			if len(last_frame_points) > 1:
				time_diff = last_frame_points[-1][0] - last_frame_points[0][0]
				fps = len(last_frame_points) / time_diff
				bps = (last_frame_points[-1][1] - last_frame_points[0][1]) / time_diff
			else: # first frame
				fps = 0
				bps = total_bytes
			total_frames += 1
			from_start = now - start
			print(f'{from_start:.3f}s: frame #{total_frames}\tFPS: {fps:.2f}\tKB: {len(jpg) / 1024:.3f}\tKB/s: {bps / 1024:.3f}')

			image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
			height, width, channels = image.shape
			if width * args.scale >= 120:
				cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
				cv2.resizeWindow(window_name, width * args.scale, height * args.scale)
			if args.always_on_top:
				cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
			cv2.imshow(window_name, image)

			if args.save:
				saved_fps = saved_frames / from_start
				if not args.save_fps or saved_fps < args.save_fps:
					filename = generate_frame_filename_for_saving(total_frames) + '.jpg'
					cv2.imwrite(os.path.join(args.save, filename), image)
					saved_frames += 1

			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {response.status_code}')

def handle_jpeg_frame(args, config):
	response = requests.get(f'http://{args.ip}/capture')
	if response.status_code == 200:
		image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
		height, width, channels = image.shape
		if width * args.scale >= 120:
			cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
			cv2.resizeWindow(window_name, width * args.scale, height * args.scale)
		if args.always_on_top:
			cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)

		cv2.imshow(window_name, image)

		if args.save:
			cv2.imwrite(args.save, image)

		while True:
			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {response.status_code}')

################################################################################

def decode_static_size_frame(data, width, height, pixformat):
	if pixformat == PIXFORMAT_GRAYSCALE:
		return np.frombuffer(data, np.uint8).reshape(height, width)
	elif pixformat == PIXFORMAT_YUV422:
		# TODO: use  cv2.cvtColor(..., cv2.COLOR_...) instead
		# Read as YUV bytes
		yuv = np.frombuffer(data, dtype=np.uint8)
		y0 = yuv[0::4].astype(np.float32)
		u  = yuv[1::4].astype(np.float32) - 128
		y1 = yuv[2::4].astype(np.float32)
		v  = yuv[3::4].astype(np.float32) - 128
		# Convert to RGB
		r0 = y0 + 1.402 * v
		g0 = y0 - 0.344136 * u - 0.714136 * v
		b0 = y0 + 1.772 * u
		r1 = y1 + 1.402 * v
		g1 = y1 - 0.344136 * u - 0.714136 * v
		b1 = y1 + 1.772 * u
		# Interleave and reshape
		r = np.empty((r0.size + r1.size,), dtype=np.uint8)
		g = np.empty((g0.size + g1.size,), dtype=np.uint8)
		b = np.empty((b0.size + b1.size,), dtype=np.uint8)
		r[0::2] = np.clip(r0, 0, 255)
		r[1::2] = np.clip(r1, 0, 255)
		g[0::2] = np.clip(g0, 0, 255)
		g[1::2] = np.clip(g1, 0, 255)
		b[0::2] = np.clip(b0, 0, 255)
		b[1::2] = np.clip(b1, 0, 255)
		r = r.reshape((height, width))
		g = g.reshape((height, width))
		b = b.reshape((height, width))
		# Repack as BGR888
		return np.dstack((r, g, b)) #.astype(np.uint8)
	elif pixformat == PIXFORMAT_RGB565:
		rgb = np.frombuffer(data, dtype='>u2').reshape(height, width)
		# Decode from RGB565 to 8 bit R, G & B
		r = ((rgb & 0b1111100000000000) >> 11) * 255 // 0b011111
		g = ((rgb & 0b0000011111100000) >>  5) * 255 // 0b111111
		b = ((rgb & 0b0000000000011111) >>  0) * 255 // 0b011111
		# Repack as BGR888
		return np.dstack((b, g, r)).astype(np.uint8)

def handle_static_size_stream(args, config, pixformat):
	# TODO: This doesn't support changing framesize during the stream;
	#	It would require server to include width & height before the pixels data
	width, height = FRAMESIZE_TO_DIMS[int(config['camera.framesize'])]
	if width * args.scale >= 120:
		cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
		cv2.resizeWindow(window_name, width * args.scale, height * args.scale)
	if args.always_on_top:
		cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)

	if pixformat == PIXFORMAT_RGB565 or pixformat == PIXFORMAT_YUV422:
		chunk_size = width * height * 2
	elif pixformat == PIXFORMAT_GRAYSCALE:
		chunk_size = width * height
	else:
		raise ValueError(f'Error: Unsupported pixformat={pixformat}')

	start = time()
	total_frames = 0
	saved_frames = 0
	total_bytes = 0
	last_frame_points = deque()
	response = requests.get(f'http://{args.ip}:81/stream', stream=True)
	if response.status_code == 200:
		for chunk in response.iter_content(chunk_size=chunk_size):
			total_bytes += len(chunk)

			if args.verbose:
				print(f"Unexpected chunk with size {len(chunk)} (expected {chunk_size}):")
				print_unexpected(chunk)
			# Discard stream part & boundary markers by assuming they are different chunks
			if len(chunk) != chunk_size:
				continue

			now = time()
			last_frame_points.append((now, total_bytes))
			while len(last_frame_points) > 1 and last_frame_points[0][0] < (now - args.fps_window): # keep at least 2
				last_frame_points.popleft()
			if len(last_frame_points) > 1:
				time_diff = last_frame_points[-1][0] - last_frame_points[0][0]
				fps = len(last_frame_points) / time_diff
				bps = (last_frame_points[-1][1] - last_frame_points[0][1]) / time_diff
			else: # first frame
				fps = 0
				bps = total_bytes
			total_frames += 1
			from_start = now - start
			print(f'{from_start:.3f}s: frame #{total_frames}\tFPS: {fps:.2f}\tKB/s: {bps / 1024:.3f}')

			cv2.imshow(window_name, decode_static_size_frame(chunk, width, height, pixformat))

			if args.save:
				saved_fps = saved_frames / from_start
				if not args.save_fps or saved_fps < args.save_fps:
					filename = generate_frame_filename_for_saving(total_frames) + '.bin'
					with open(os.path.join(args.save, filename), 'wb') as file:
						file.write(chunk)
					saved_frames += 1

			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {response.status_code}')

def handle_static_size_frame(args, config, pixformat):
	width, height = FRAMESIZE_TO_DIMS[int(config['camera.framesize'])]
	if width * args.scale >= 120:
		cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
		cv2.resizeWindow(window_name, width * args.scale, height * args.scale)
	if args.always_on_top:
		cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)

	response = requests.get(f'http://{args.ip}/capture')
	if response.status_code == 200:
		if response.headers['Content-Type'] == 'image/bmp':
			cv2.imshow(window_name, cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR))
		else: # maybe binary
			cv2.imshow(window_name, decode_static_size_frame(response.content, width, height, pixformat))

		if args.save:
			with open(args.save, 'wb') as file:
				file.write(response.content)
				print(f'Raw frame saved to {args.save}')

		while True:
			esc_or_q_pressed = cv2.pollKey() in [27, ord('q')]
			if check_window_is_closed(window_name) or esc_or_q_pressed:
				break
	else:
		print(f'Error: Received unexpected status code {response.status_code}')

################################################################################

def fps_type(x):
	try:
		x = float(x)
	except ValueError:
		raise argparse.ArgumentTypeError(f'{x} not a floating-point literal')
	if x < 1:
		raise argparse.ArgumentTypeError(f'{x} must be at least 1')
	return x

def main():
	parser = argparse.ArgumentParser(description='''This script allows to retrieve camera frames from the car.''')
	parser.add_argument('--config-file', metavar='PATH', help='JSON file to be used as config. If not provided, will be fetched from the device.', required=False)
	parser.add_argument('--ip', '--address', help=f'IP of the device. Defaults to the one from the config file or {DEFAULT_IP}.', required=False)
	parser.add_argument('--stream', help=argparse.SUPPRESS, required=False, action='store_true') # allow '--stream' just because I want to
	parser.add_argument('--frame', help='If set, only retrieves single frame.', required=False, action='store_true')
	parser.add_argument('--scale', help='Scale factor for displaying the received image (not saving).', required=False, type=int, default=1)
	parser.add_argument('--save', metavar='PATH', help='If set, specifies path to file (or folder) for the frame (or stream) to be saved.', required=False)
	parser.add_argument('--save-fps', metavar='FPS', help='If set, limits number of frames being saved.', required=False, type=fps_type)
	parser.add_argument('--overwrite', help='Allow overwriting existing files (warning: might remove files!)', required=False, action='store_true')
	parser.add_argument('--always-on-top', help='If set, the window will be always on top.', required=False, action='store_true')
	parser.add_argument('--fps-window', help='How much seconds behind to be used to calculate average FPS.', required=False, type=float, default=10)
	parser.add_argument('--verbose', help='Print unexpected data from the stream.', required=False, action='store_true')
	args = parser.parse_args()

	if args.save:
		args.save = os.path.normpath(args.save)
		if args.frame:
			if not args.overwrite and os.path.exists(args.save):
				print('Error: File exists on specified path, cannot save.')
				return
		else:
			# TODO: overwrite for streaming?
			if os.path.isfile(args.save):
				if args.overwrite:
					print(f'Overwriting existing file: {args.save}')
					os.remove(args.save)
				else:
					print('Error: File exists on specified path, cannot save.')
					return
			os.makedirs(args.save, exist_ok=True)
			if len(os.listdir(args.save)) != 0:
				if args.overwrite:
					print(f'Removing existing directory: {args.save}')
					shutil.rmtree(args.save)
					os.makedirs(args.save, exist_ok=True)
				else:
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

	try:
		pixformat = int(config['camera.pixformat'])

		# Sometimes, after crash, framesize is equal 25 - idk why...
		framesize = int(config['camera.framesize'])
		if framesize >= len(FRAMESIZE_TO_DIMS):
			print(f"Warning: framesize={framesize} is out of range. Assuming largest framesize={len(FRAMESIZE_TO_DIMS) - 1} aka {FRAMESIZE_TO_DIMS[-1]}")
			config['camera.framesize'] = len(FRAMESIZE_TO_DIMS) - 1

		if args.frame:
			if pixformat == PIXFORMAT_JPEG:
				handle_jpeg_frame(args, config)
			else:
				handle_static_size_frame(args, config, pixformat)
		else: # stream
			if pixformat == PIXFORMAT_JPEG:
				handle_mjpeg_stream(args, config)
			else:
				handle_static_size_stream(args, config, pixformat)
	except KeyboardInterrupt:
		print('Interrupted')
		pass

if __name__ == '__main__':
	main()
