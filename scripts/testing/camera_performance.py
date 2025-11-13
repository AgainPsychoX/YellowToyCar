import time
from datetime import datetime
import os
import itertools
import argparse
import requests
import sys
import subprocess

XCLK_FREQS = [16, 20]  # TODO: add 24 MHz later
CLOCK_DIVIDERS = [1, 2, 4] # (CLKRC[5:0]+1)
DOUBLER_MODES = [False, True]  # (CLKRC[7])
DVP_DIVIDERS = [1, 2, 4, 8] # (R_DVP_SP[6:0])
DVP_AUTO_MODES = [False, True] # auto DVP division mode (R_DVP_SP[7])

# Resolutions to test
FRAMESIZE_TO_DIMS = [
	(96, 96),      # 0: FRAMESIZE_96X96
	(160, 120),    # 1: FRAMESIZE_QQVGA
	(176, 144),    # 2: FRAMESIZE_QCIF
	(240, 176),    # 3: FRAMESIZE_HQVGA
	(240, 240),    # 4: FRAMESIZE_240X240
	(320, 240),    # 5: FRAMESIZE_QVGA
	(400, 296),    # 6: FRAMESIZE_CIF
	(480, 320),    # 7: FRAMESIZE_HVGA
	(640, 480),    # 8: FRAMESIZE_VGA
	(800, 600),    # 9: FRAMESIZE_SVGA
	(1024, 768),   # 10: FRAMESIZE_XGA
	(1280, 720),   # 11: FRAMESIZE_HD
	(1280, 1024),  # 12: FRAMESIZE_SXGA
	(1600, 1200),  # 13: FRAMESIZE_UXGA
]
ALL_RESOLUTION_INDICES = range(len(FRAMESIZE_TO_DIMS))

# Pixel formats to test
PIXFORMAT = {
	"RGB565":    0,
	"GRAYSCALE": 3,
	"JPEG":      4,
}
PIXFORMAT_TO_STR = {v: k for k, v in PIXFORMAT.items()}

CAPTURE_DURATION = 5 # seconds

LOG_FILE = "performance_log.md"

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

def write_log_header():
	if not os.path.exists(LOG_FILE):
		with open(LOG_FILE, "w") as f:
			f.write("# Camera Performance Test Log\n\n")
			f.write(
				"| Timestamp | Format | Resolution | XCLK (MHz) | Doubler | CLK Div | DVP Div | DVP Auto | PCLK (MHz) | Expected FPS | Actual FPS | KB/s | Status | Notes |\n"
			)
			f.write(
				"|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
			)

def log_result(result):
	with open(LOG_FILE, "a") as f:
		f.write(
			f"| {result['timestamp']} | {result['format']} | {result['resolution']} | "
			f"{result['xclk']} | {result['doubler']} | {result['clk_div']} | "
			f"{result['dvp_div']} | {result['dvp_auto']} | {result['pclk_mhz']:.2f} | "
			f"{result['expected_fps']:.2f} | {result['actual_fps']:.2f} | {result['kbs']:.2f} | {result['status']} |  |\n" # Notes empty, to be filled by human later on
		)

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

def run_test(ip_address):
	"""Main function to run all test combinations."""
	write_log_header()

	test_combinations = list(itertools.product(
		list(PIXFORMAT.values()),
		ALL_RESOLUTION_INDICES,
		XCLK_FREQS,
		DOUBLER_MODES,
		CLOCK_DIVIDERS,
		DVP_DIVIDERS,
		DVP_AUTO_MODES
	))

	total_tests = len(test_combinations)
	print(f"Starting camera performance test with {total_tests} combinations.")
	print(f"Target device IP: {ip_address}")

	for i, (pix, res, xclk, doubler, clk_div, dvp_div, dvp_auto) in enumerate(test_combinations):

		width, height = FRAMESIZE_TO_DIMS[res]
		pix_str = PIXFORMAT_TO_STR[pix]
		res_str = f"{width}x{height}"
		
		print(f"\n--- Test {i+1}/{total_tests} ---")
		print(f"Config: Res={res_str}, Fmt={pix_str}, XCLK={xclk}MHz, Doubler={doubler}, CLK_Div={clk_div}, DVP_Div={dvp_div}, DVP_Auto={dvp_auto}")

		result = {
			"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
			"format": pix_str,
			"resolution": res_str,
			"xclk": xclk,
			"doubler": doubler,
			"clk_div": clk_div,
			"dvp_div": dvp_div,
			"dvp_auto": dvp_auto,
			"pclk_mhz": 0,
			"expected_fps": 0,
			"actual_fps": 0.0,
			"kbs": 0.0,
			"status": "SKIPPED"
		}

		try:
			# --- Set Custom Registers ---
			# CLKRC: Clock control
			# Bit 7: Doubler, Bits 5:0: Divider-1
			clkrc_val = (0b10000000 if doubler else 0) | (clk_div - 1)

			# R_DVP_SP: DVP output speed control
			# Bit 7: Auto mode, Bits 6:0: Divider
			dvp_sp_val = (0b10000000 if dvp_auto else 0) | dvp_div

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

			response = requests.post(f"http://{ip_address}/config", json=config_payload, timeout=5)
			response.raise_for_status() # Will raise an exception for 4xx/5xx status

			# --- Verify Configuration ---
			# Wait a moment for settings to apply, especially if a re-init is triggered.
			time.sleep(1)

			print("Verifying applied configuration...")
			verify_response = requests.get(f"http://{ip_address}/config", timeout=5)
			verify_response.raise_for_status()
			current_config = verify_response.json()

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

			print("Configuration verified successfully.")
			

			# --- Calculations ---
			# PCLK = (XCLK * (2 if doubler else 1)) / CLK_DIV
			# The DVP divider seems to be for the output pins, not the internal PCLK.
			# If DVP Auto is on, the DVP divider is ignored.
			pclk_divisor = clk_div
			if not dvp_auto:
				# This is an assumption: that DVP divider also affects PCLK speed.
				# In many cases it might just gate the output without changing frequency.
				# For this test, we'll assume it divides the clock.
				pclk_divisor *= dvp_div

			result["pclk_mhz"] = (xclk * (2 if doubler else 1)) / pclk_divisor
			result["expected_fps"] = calculate_expected_fps(result["pclk_mhz"], width, height)

			# --- Measure Actual FPS from Stream ---
			print(f"Capturing frames for {CAPTURE_DURATION} seconds to measure FPS...")
			result["actual_fps"], result["kbs"] = measure_fps_from_stream(f"http://{ip_address}:81/stream", pix, width, height, CAPTURE_DURATION)
			result["status"] = "OK"
			
			print(f"Success! Actual FPS: {result['actual_fps']:.2f}, KB/s: {result['kbs']:.2f}")

		except Exception as e:
			print(f"Error during test: {e}")
			result["status"] = f"FAIL: {e}"

			# Try make sure we are still connected
			time.sleep(1)
			param_to_set_count = '-n' if sys.platform.lower() == 'win32' else '-c'
			command = ['ping', param_to_set_count, '1', ip_address]
			for _ in range(10): # try few times
				# Using subprocess to execute the command and hide output
				ping_process = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
				if ping_process.returncode == 0:
					break
			if ping_process.returncode != 0:
				print("Device did not respond to ICMP ping after error. Aborting test run.")
				return

		finally:
			log_result(result)

	print("\nAll tests completed.")
	print(f"Results saved to {LOG_FILE}")

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='Camera performance testing script.')
	parser.add_argument('--ip', default='192.168.4.1', help='IP address of the ESP32 camera device.')
	args = parser.parse_args()
	run_test(args.ip)
