import time
from datetime import datetime
import os
import itertools
import argparse
import requests
import csv
import sys
import subprocess

class CameraConfigEmptyError(RuntimeError): ... # might indicate sensor crash

# Resolutions to test
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
ALL_RESOLUTION_INDICES = range(len(FRAMESIZE_TO_DIMS))

# Pixel formats to test
PIXFORMAT = {
	"RGB565":    0,
	"GRAYSCALE": 3,
	"JPEG":      4,
}
PIXFORMAT_TO_STR = {v: k for k, v in PIXFORMAT.items()}

class TsvNoQuoteDialect(csv.Dialect):
	delimiter = '\t'
	quoting = csv.QUOTE_NONE # to make JSON easier to copy
	escapechar = '\\'
	lineterminator = '\n'
csv.register_dialect('tsv-noquote', TsvNoQuoteDialect)

# Data from OV2640 datasheet for different modes/resolutions
# tP = PCLK period
TIMING_DATA = {
	"UXGA": {"tLINE": 1922, "VSYNC_INTERVAL_LINES": 1248}, # 1600x1200
	"SVGA": {"tLINE": 1190, "VSYNC_INTERVAL_LINES": 672},  # 800x600
	"CIF":  {"tLINE": 595,  "VSYNC_INTERVAL_LINES": 336},  # 400x296 (approx 352x288)
}

def get_resolution_mode(width, height):
	if width > 800 or height > 600:
		return "UXGA"
	if width > 400 or height > 296: # Datasheet CIF is ~400x296
		return "SVGA"
	return "CIF"

def calculate_expected_fps(pclk_mhz, width, height):
	if pclk_mhz <= 0:
		return 0

	mode = get_resolution_mode(width, height)
	timing = TIMING_DATA.get(mode)
	if not timing:
		return 0
	t_p_ns = (1 / pclk_mhz) * 1000
	t_frame_ns = timing["tLINE"] * timing["VSYNC_INTERVAL_LINES"] * t_p_ns
	if t_frame_ns == 0:
		return float('inf')

	return 1e9 / t_frame_ns

def calculate_expected_pclk_mhz(xclk, doubler, clk_div, dvp_div):
	pclk_divisor = clk_div

	# This is an assumption: that DVP divider also affects PCLK speed.
	# In many cases it might just gate the output without changing frequency.
	# For this test, we'll assume it divides the clock.
	dvp_auto = dvp_div == 0
	if dvp_auto:
		# Experimentally, it feels like auto often falls back to 2
		pclk_divisor *= 2
	else:
		pclk_divisor *= dvp_div

	return (xclk * (2 if doubler else 1)) / pclk_divisor

def get_log_fieldnames():
	return [
		"timestamp", "format", "resolution", "xclk", "doubler",
		"clk_div", "dvp_div", "pclk_mhz", "expected_fps", "actual_fps",
		"kbs", "status", "notes"
	]

def write_log_header(log_file):
	if not os.path.exists(log_file):
		with open(log_file, "w", newline="") as f:
			csv.DictWriter(f, fieldnames=get_log_fieldnames(), dialect='tsv-noquote').writeheader()

def log_result(result, log_file):
	with open(log_file, "a", newline="") as f:
		csv.DictWriter(f, fieldnames=get_log_fieldnames(), dialect='tsv-noquote').writerow(result)

def ping_device(ip: str) -> bool:
	"""Pings a device once to see if it is reachable."""
	count_param = '-n' if sys.platform.lower() == 'win32' else '-c'
	timeout_value = '1000' if sys.platform.lower() == 'win32' else '1'
	ping_command = ['ping', count_param, '1', '-w', timeout_value, ip]
	ping_process = subprocess.run(ping_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	return ping_process.returncode == 0

def wait_for_device(ip: str, retries: int = 10, progress_print = None) -> bool:
	"""Waits for a device to become reachable via ping."""
	for i in range(retries):
		if progress_print and i > 0:
			print(progress_print, end="", flush=True)
		if ping_device(ip):
			return True
	return False

def restart_device(ip: str, wait_icmp: int = 10, wait_extra: float = 2, progress_print = None):
	response = requests.post(f"http://{ip}/config", json={"restart": True}, timeout=2)
	response.raise_for_status()
	time.sleep(1) # avoid successful ping while restart request is being processed

	if not wait_for_device(ip, retries=wait_icmp, progress_print=progress_print):
		return False

	time.sleep(wait_extra)
	return True

def measure_fps_from_stream(stream_url, pixformat, width, height, capture_duration):
	frames_captured = 0
	total_bytes = 0

	response = requests.get(stream_url, stream=True, timeout=5)
	response.raise_for_status()

	start_time = time.time()

	if pixformat == PIXFORMAT["JPEG"]:
		# Handle MJPEG stream by finding SOI and EOI markers
		JPEG_SOI_MARKER = b'\xff\xd8'
		JPEG_EOI_MARKER = b'\xff\xd9'
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
			buffer = buffer[b+2:]

			frames_captured += 1
			if time.time() - start_time >= capture_duration:
				break
	else:
		# Handle fixed-size formats (RGB565, GRAYSCALE)
		if pixformat == PIXFORMAT["RGB565"]:
			frame_size = width * height * 2
		elif pixformat == PIXFORMAT["GRAYSCALE"]:
			frame_size = width * height
		else:
			raise ValueError(f"Unsupported streaming format: {pixformat}")

		# Read chunks of the expected frame size, discarding smaller chunks (boundaries)
		for chunk in response.iter_content(chunk_size=frame_size):
			total_bytes += len(chunk)
			if len(chunk) != frame_size:
				continue # likely multipart boundary, ignore

			frames_captured += 1
			if time.time() - start_time >= capture_duration:
				break

	end_time = time.time()
	duration = end_time - start_time

	actual_fps = frames_captured / duration if duration > 0 else float('inf')
	kbs = (total_bytes / duration / 1024) if duration > 0 else 0

	time.sleep(0.5) # short sleep to make sure the stream is closed
	return actual_fps, kbs

def load_cached_tests(log_file):
	"""Loads previously run test configurations from the log file to avoid re-running them."""
	if not os.path.exists(log_file):
		return {}

	cached_tests = {} # Key -> list of rows
	with open(log_file, "r", newline="") as f:
		# Let DictReader use the first row as header
		reader = csv.DictReader(f, dialect='tsv-noquote')
		for row in reader:
			try:
				# Create a unique key for each test configuration
				key = (
					row['format'],
					row['resolution'],
					int(float(row['xclk'])), # Handle float format like '16.00'
					row['doubler'].lower() == 'true',
					int(row['clk_div']),
					int(row['dvp_div'])
				)
				if key not in cached_tests:
					cached_tests[key] = []
				cached_tests[key].append(row)
			except (KeyError, ValueError) as e:
				print(f"Warning: Skipping malformed row in cache file: {row} ({e})")
	return cached_tests

def get_expected_fps_for_combination(combination):
	"""Calculate the expected FPS for a given test combination tuple."""
	(pix_str, res, xclk, doubler, clk_div, dvp_div) = combination
	width, height = FRAMESIZE_TO_DIMS[res]
	expected_pclk_mhz = calculate_expected_pclk_mhz(xclk, doubler, clk_div, dvp_div)
	return calculate_expected_fps(expected_pclk_mhz, width, height)

def run_test(args):
	"""Main function to run all test combinations."""
	write_log_header(args.log_file)
	
	cached_tests = {}
	if not args.no_cache:
		cached_tests = load_cached_tests(args.log_file)
		if cached_tests:
			print(f"Loaded {len(cached_tests)} previously completed tests from {args.log_file}.")
	
	# Generate all combinations for the run
	all_combinations = list(itertools.product(args.pixformat, args.resolution, args.xclk, args.doubler, args.clk_div, args.dvp_div))

	# Sort combinations by expected FPS in descending order to prioritize high-performance tests
	test_combinations = sorted(all_combinations, key=get_expected_fps_for_combination, reverse=True)

	total_tests = len(test_combinations)

	# Check if the camera config is empty, which might indicate a sensor crash.
	# If so, try to restart the device.
	try:
		config_response = requests.get(f"http://{args.ip}/config", timeout=2)
		config_response.raise_for_status()
		current_config = config_response.json()
		if not current_config.get("camera"):
			print("Camera config is empty, likely sensor crash.")

			# Try restarting the device
			print("Restarting device...", end='', flush=True)
			if not restart_device(args.ip, progress_print='.'):
				print("Device did not come back online after restart.")
				return
			print() # new line

			# Check config again after restart
			config_response = requests.get(f"http://{args.ip}/config", timeout=2)
			config_response.raise_for_status()
			if not config_response.json().get("camera"):
				print("Camera config still empty after restart. Try hardware restart!")
				return False
	except requests.exceptions.RequestException:
		print("Device did not respond to GET /config properly")
		return False

	# Get initial uptime
	try:
		print(f"Connecting to device at {args.ip} to get initial status...")
		status_response = requests.get(f"http://{args.ip}/status", timeout=2)
		status_response.raise_for_status()
		last_known_uptime = status_response.json().get("uptime", 0)
		print(f"Initial device uptime: {last_known_uptime / 1000000:.3f}s")
	except requests.exceptions.RequestException as e:
		print(f"Error getting initial status: {e}.")
		return False

	print(f"Starting camera performance testing with {total_tests} combinations.")
	print(f"Target device IP: {args.ip}")

	for i, (pix_str, res, xclk, doubler, clk_div, dvp_div) in enumerate(test_combinations):
		pix = PIXFORMAT[pix_str]
		dvp_auto = dvp_div == 0

		width, height = FRAMESIZE_TO_DIMS[res]
		pix_str = PIXFORMAT_TO_STR[pix]
		res_str = f"{width}x{height}"

		# Calculate expected FPS for the current combination
		expected_pclk_mhz = calculate_expected_pclk_mhz(xclk, doubler, clk_div, dvp_div)
		expected_fps = calculate_expected_fps(expected_pclk_mhz, width, height)

		config_str = f"{res_str:<9} {pix_str} XCLK={xclk}MHz x2={doubler:0} div={clk_div} DVP_div={dvp_div if not dvp_auto else 'auto':<4} -> Expected FPS: {expected_fps:.2f})"
		print(f"[{i+1:4d}/{total_tests}] {config_str} ... ", end='', flush=True)

		# Caching check
		test_key = (pix_str, res_str, xclk, doubler, clk_div, dvp_div)
		if test_key in cached_tests:
			last_result = cached_tests[test_key][-1]
			is_failed = last_result['status'].startswith('FAIL')
			# Skip if not retrying fails, or if retrying fails and the test was not a failure.
			if not (args.retry_fails and is_failed):
				# if last_result['status'].startswith('OK'):
				# 	print(f"[CACHED] [{last_result['status']}] Actual FPS: {last_result['actual_fps']}, KB/s: {last_result['kbs']}")
				# else:
				# 	print(f"[CACHED] [{last_result['status']}]")
				continue

		result = {
			"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
			"format": pix_str,
			"resolution": res_str,
			"xclk": xclk,
			"doubler": doubler,
			"clk_div": clk_div,
			"dvp_div": dvp_div,
			"pclk_mhz": expected_pclk_mhz,
			"expected_fps": expected_fps,
			"actual_fps": 0.0,
			"kbs": 0.0,
			"status": "", # starts with `OK: `, `FAIL: ` or `FATAL: ` (non-recoverable)
			"notes": ""
		}

		try:
			# --- Set Custom Registers ---
			# CLKRC: Clock control
			# Bit 7: Doubler, Bits 5:0: Divider-1
			clkrc_val = (0b10000000 if doubler else 0) | (clk_div - 1)

			# R_DVP_SP: DVP output speed control
			# Bit 7: Auto mode, Bits 6:0: Divider
			dvp_sp_val = (0b10000000 if dvp_auto else 0) | (8 if dvp_auto else dvp_div)

			# --- Configure Camera via HTTP POST ---
			config_payload = {
				"camera": {
					"framesize": res,
					"pixformat": pix,
					"xclk": xclk,
					"clkrc": clkrc_val,
					"r_dvp_sp": dvp_sp_val,
				}
			}
			if pix == PIXFORMAT["JPEG"]:
				config_payload["camera"]["quality"] = 12

			response = requests.post(f"http://{args.ip}/config", json=config_payload, timeout=2)
			response.raise_for_status() # Will raise an exception for 4xx/5xx status

			# --- Verify Configuration ---
			# Wait a moment for settings to apply, especially if a re-init is triggered.
			time.sleep(1)

			verify_response = requests.get(f"http://{args.ip}/config", timeout=2)
			verify_response.raise_for_status()
			current_config = verify_response.json()

			# Check if camera config is empty, which might indicate the sensor crash
			if not current_config.get("camera"):
				raise CameraConfigEmptyError

			# Check if the key values match what we set.
			# Note: The device might not support reading back all values,
			# so we only check the ones we know are readable from camera.cpp.
			applied_fs = current_config.get("camera", {}).get("framesize")
			applied_pix = current_config.get("camera", {}).get("pixformat")
			applied_clkrc = current_config.get("camera", {}).get("clkrc")
			applied_dvp_sp = current_config.get("camera", {}).get("r_dvp_sp")

			mismatches = []
			if applied_fs != res:
				mismatches.append(f"framesize (sent {res}, got {applied_fs})")
			if applied_pix != pix:
				mismatches.append(f"pixformat (sent {pix}, got {applied_pix})")
			if applied_clkrc != clkrc_val:
				mismatches.append(f"clkrc (sent {clkrc_val}, got {applied_clkrc})")
			if applied_dvp_sp != dvp_sp_val:
				mismatches.append(f"r_dvp_sp (sent {dvp_sp_val}, got {applied_dvp_sp})")
			if mismatches:
				raise RuntimeError(f"Config mismatch! Issues: {', '.join(mismatches)}")

			# --- Measure Actual FPS from Stream ---
			result["actual_fps"], result["kbs"] = measure_fps_from_stream(f"http://{args.ip}:81/stream", pix, width, height, args.duration)
			result["status"] = "OK"

			print(f"[OK] Actual FPS: {result['actual_fps']:.2f}, KB/s: {result['kbs']:.2f}")

		except KeyboardInterrupt:
			result["status"] = "INTERRUPTED"
			print("Aborting test run due to keyboard interrupt.")
			return True

		except Exception as e:
			error_msg = str(e)

			# Check if it's a requests exception and has a request object to add more context
			if isinstance(e, requests.exceptions.RequestException) and e.request:
				req = e.request
				data_info = ""
				if req.body:
					try:
						data_info = req.body.decode('utf-8')
					except UnicodeDecodeError:
						data_info = f"{req.body!r}"
				error_msg = f"{e} ({req.method} {req.url} with data: {data_info})"

			result["status"] = f"FAIL: {error_msg}"
			print(f"[FAIL] {error_msg} ", end='', flush=True)

			try:
				# After a failure, check if the device is still responsive.
				if not wait_for_device(args.ip):
					extra = "Device did not respond to ICMP ping after error."
					result["status"] = result["status"].replace("FAIL: ", "FATAL: ", 1) + f" - {extra}"
					print(f"[FATAL] {extra}", end='', flush=True)
					return False
				time.sleep(1) # small delay to allow the HTTP server to get up

				# Check for crash by comparing uptime
				try:
					status_response = requests.get(f"http://{args.ip}/status", timeout=2)
					status_response.raise_for_status()
					current_uptime = status_response.json().get("uptime", 0)
					if current_uptime < last_known_uptime:
						extra = "Device crashed and rebooted."
						result["status"] += f" - {extra}"
						print(f"- {extra}", end='', flush=True)
					last_known_uptime = current_uptime # Update uptime for next check
				except requests.exceptions.RequestException:
					extra = "Device did not respond to GET /status after error."
					result["status"] = result["status"].replace("FAIL: ", "FATAL: ", 1) + f" - {extra}"
					print(f"[FATAL] {extra}", end='', flush=True)
					return False
				
				# Check if the camera config is empty, which might indicate a sensor crash.
				# If so, try to restart the device.
				try:
					config_response = requests.get(f"http://{args.ip}/config", timeout=2)
					config_response.raise_for_status()
					current_config = config_response.json()
					if not current_config.get("camera"):
						extra = "Camera config is empty, likely sensor crash."
						result["status"] = result["status"].replace("FAIL: ", "FATAL: ", 1) + f" - {extra}"
						print(f"- {extra}", end='', flush=True)

						# Try restarting the device
						print(" Restarting device...", end='', flush=True)
						if not restart_device(args.ip, progress_print='.'):
							extra = "Device did not come back online after restart."
							result["status"] += f" - {extra}"
							print(f"[FATAL] {extra}", end='', flush=True)
							return False

						# After restart, update last_known_uptime
						status_response = requests.get(f"http://{args.ip}/status", timeout=2)
						status_response.raise_for_status()
						last_known_uptime = status_response.json().get("uptime", 0)

						# Check config again after restart
						config_response = requests.get(f"http://{args.ip}/config", timeout=2)
						config_response.raise_for_status()
						if not config_response.json().get("camera"):
							extra = "Camera config still empty after restart."
							result["status"] += f" - {extra}"
							print(f"[FATAL] {extra}", end='', flush=True)
							return False
				except requests.exceptions.RequestException:
					extra = "Device did not respond to GET /config after error."
					result["status"] = result["status"].replace("FAIL: ", "FATAL: ", 1) + f" - {extra}"
					print(f"[FATAL] {extra}", end='', flush=True)
					return False

				if args.stop_on_fail:
					print("\nAborting test run due to --stop-on-fail.", end='', flush=True)
					return True

			except KeyboardInterrupt:
				result["status"] += " (and interrupted)"
				print("\nAborting test (just after failure) run due to keyboard interrupt.", end='', flush=True)
				return True

			finally:
				print() # new line

		finally:
			if result["status"]:
				# Format floating point numbers for consistent output before logging.
				result['pclk_mhz'] = f"{result['pclk_mhz']:.2f}"
				result['expected_fps'] = f"{result['expected_fps']:.2f}"
				result['actual_fps'] = f"{result['actual_fps']:.2f}"
				result['kbs'] = f"{result['kbs']:.2f}"
				log_result(result, args.log_file)

	print("\nAll tests completed.")
	return True

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='Camera performance testing script.')
	parser.add_argument('--ip', help='IP address of the ESP32 camera device. (default: 192.168.4.1)', default='192.168.4.1')
	parser.add_argument('--log-file', help='Path to the TSV log file. (default: performance_log.tsv)', default='performance_log.tsv')
	parser.add_argument('--duration', help='Duration in seconds to capture the stream for FPS measurement. (default: 5)', type=int, default=5)
	parser.add_argument('--no-cache', help='Do not use cached results; re-run all specified tests.', action='store_true')
	parser.add_argument('--stop-on-fail', help='Stop the test run on the first failure.', action='store_true')
	parser.add_argument('--retry-fails', help='Only run tests that have a "FAIL" status in the log file.', action='store_true')

	# Arguments for each test parameter
	parser.add_argument('--pixformat', help='Pixel formats to test. (default: all)',
		nargs='+', choices=list(PIXFORMAT.keys()), default=list(PIXFORMAT.keys()))
	parser.add_argument('--resolution', help='Resolution indices to test. (default: all)',
		nargs='+', type=int, choices=range(len(FRAMESIZE_TO_DIMS)), metavar=f'[0-{len(FRAMESIZE_TO_DIMS)-1}]', default=range(len(FRAMESIZE_TO_DIMS)))
	parser.add_argument('--xclk', help='XCLK frequencies (MHz) to test. (default: [16, 20])',
		nargs='+', type=int, choices=[16, 20, 24], default=[16, 20])
	parser.add_argument('--doubler', help='Doubler modes (True/False) to test. (default: [False, True])',
		nargs='+', type=lambda x: x.lower() in ['true', '1', 't', 'y'], default=[False, True])
	parser.add_argument('--clk-div', help='Clock dividers to test. (default: [1, 2, 4])',
		nargs='+', type=int, choices=[1, 2, 4], default=[1, 2, 4])
	parser.add_argument('--dvp-div', help='DVP dividers to test. Use 0 for auto mode. (default: [0, 1, 2, 4, 8])',
		nargs='+', type=int, choices=[0, 1, 2, 4, 8], default=[0, 1, 2, 4, 8])

	args = parser.parse_args()
	if not run_test(args):
		print("Test run failed.")
