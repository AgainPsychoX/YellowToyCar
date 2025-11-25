import argparse
import os
import subprocess
import shlex
import sys
import csv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PIO_BUILD_DIR = os.path.join(PROJECT_ROOT, '.pio', 'build', 'esp32s3eye_debug')
ESP_DL_COMPONENT_DIR = os.path.join(PROJECT_ROOT, 'components', 'esp-dl', 'esp-dl')
PACK_ESPDL_MODELS_SCRIPT = os.path.join(ESP_DL_COMPONENT_DIR, 'fbs_loader', 'pack_espdl_models.py')
# TODO: figure out the paths more dynamically, allow for direct component and managed_components (maybe interpret idf_component.yml of the project?)
# TODO: try verify first instead always writing, or somehow otherwise remember if the model need to be uploaded; also, it takes few seconds to upload, no point wasting time
# TODO: integrate in PlatformIO as pre/post script (but see above first)

# OpenOCD configuration (based on how PlatformIO uses it)
OPENOCD_PACKAGE_DIR = os.path.expanduser("~/.platformio/packages/tool-openocd-esp32")
OPENOCD_SCRIPTS_DIR = os.path.join(OPENOCD_PACKAGE_DIR, "share/openocd/scripts")
OPENOCD_EXECUTABLE = os.path.join(OPENOCD_PACKAGE_DIR, "bin", "openocd.exe")
OPENOCD_CONFIG_FILES = [ "-f", "interface/esp_usb_jtag.cfg", "-f", "target/esp32s3.cfg" ]
OPENOCD_EXTRA_ARGS = ["-c", "adapter speed 5000"]

# Models to flash
MODELS_TO_FLASH = [
	{
		"name": "hand_detect",
		"partition": "hand_det",
		"source_models": ["components/esp-dl/models/hand_detect/models/s3/espdet_pico_224_224_hand.espdl"],
		"packed_output": "espdl_models/hand_detect.espdl",
	},
	{
		"name": "hand_gesture_recognition",
		"partition": "hand_gesture_cls",
		"source_models": ["components/esp-dl/models/hand_gesture_recognition/models/s3/mobilenetv2_0_5_128_128_gesture.espdl"],
		"packed_output": "espdl_models/hand_gesture_cls.espdl",
	},
]
# TODO: somehow not hardcode those paths?

def run_command(command, cwd=None, dry_run=False):
	"""Executes a command, streams its output in real-time, and exits on failure."""
	print(f"Executing: {shlex.join(command)}")
	if dry_run:
		print("(Dry run, not executing)")
		return
	try:
		subprocess.run(command, check=True, cwd=cwd)
	except (subprocess.CalledProcessError, FileNotFoundError) as e:
		print(f"\nError executing command: {e}", file=sys.stderr)
		sys.exit(1)

def _parse_size(value_str):
	"""Parses a size string (e.g., '2M', '600K', '0x1000') into an integer."""
	value_str = value_str.strip().upper()
	if value_str.endswith("K"):
		return int(value_str[:-1]) * 1024
	if value_str.endswith("M"):
		return int(value_str[:-1]) * 1024 * 1024
	if value_str.startswith("0X"):
		return int(value_str, 16)
	return int(value_str)

def get_partition_offsets():
	"""Reads the source partition CSV and calculates the offsets."""
	# Based on implementation in ~/.platformio/platforms/espressif32/builder/main.py

	partition_file = os.path.join(PROJECT_ROOT, "partitions.csv")
	if not os.path.exists(partition_file):
		print(f"Error: Partition file not found at '{partition_file}'", file=sys.stderr)
		sys.exit(1)

	offsets = {}
	# The first partition starts after the partition table (at 0x8000, size 0x1000)
	next_offset = 0x9000
	with open(partition_file, 'r', newline='') as f:
		reader = csv.reader(f)
		for row in reader:
			# Skip comments and empty lines
			if not row or not row[0] or row[0].strip().startswith('#'):
				continue
			
			tokens = [t.strip() for t in row]
			if len(tokens) < 5:
				continue

			name, p_type, subtype, offset_str, size_str = tokens[:5]

			# If offset is not specified, calculate it
			if not offset_str:
				# Align 'app' partitions to 64K, others to 4 bytes
				bound = 0x10000 if p_type in ("0", "app") else 4
				calculated_offset = (next_offset + bound - 1) & ~(bound - 1)
				offset = calculated_offset
			else:
				offset = _parse_size(offset_str)

			size = _parse_size(size_str)
			
			offsets[name] = hex(offset)

			# Update next_offset for the subsequent partition
			next_offset = offset + size

	return offsets

def pack_models(dry_run=False):
	"""Packs the models using the esp-dl script."""
	print("--- Step 1: Packing models ---")

	output_dir = os.path.join(PIO_BUILD_DIR, 'espdl_models')
	os.makedirs(output_dir, exist_ok=True)

	for model_info in MODELS_TO_FLASH:
		print(f"\nPacking model: {model_info['name']}")
		source_paths = [os.path.join(PROJECT_ROOT, p) for p in model_info['source_models']]
		output_path = os.path.join(PIO_BUILD_DIR, model_info['packed_output'])

		command = [
			sys.executable,
			PACK_ESPDL_MODELS_SCRIPT,
			'--model_path', *source_paths,
			'--out_file', output_path
		]
		run_command(command, dry_run=dry_run)
		if not dry_run:
			print(f"Successfully packed model to: {output_path}")

def flash_models(dry_run=False):
	"""Flashes the packed models using OpenOCD."""
	print("\n--- Step 2: Flashing models ---")

	partition_offsets = get_partition_offsets()

	openocd_cmd = [
		OPENOCD_EXECUTABLE,
		"-d2",
		"-s", OPENOCD_SCRIPTS_DIR,
		*OPENOCD_CONFIG_FILES,
		*OPENOCD_EXTRA_ARGS,
	]

	# Add program commands for each model
	for model_info in MODELS_TO_FLASH:
		packed_model_path = os.path.join(PIO_BUILD_DIR, model_info['packed_output'])
		if not os.path.exists(packed_model_path):
			print(f"Error: Packed model file not found: {packed_model_path}", file=sys.stderr)
			print("Please run the script without --skip-pack first.", file=sys.stderr)
			sys.exit(1)
		
		partition_name = model_info['partition']
		if partition_name not in partition_offsets:
			print(f"Error: Partition '{partition_name}' not found in partition table.", file=sys.stderr)
			sys.exit(1)
		partition_offset = partition_offsets[partition_name]

		flash_command = f'program_esp "{packed_model_path.replace(os.sep, "/")}" {partition_offset} verify' # forward slashes necessary!
		openocd_cmd.extend(["-c", flash_command])

	# Add final commands to reset the device and shutdown OpenOCD
	openocd_cmd.extend(["-c", "reset run", "-c", "shutdown"])

	run_command(openocd_cmd, dry_run=dry_run)
	if not dry_run:
		print("\nAll models flashed successfully!")

def main():
	parser = argparse.ArgumentParser(
		description="A workaround script for PlatformIO to pack and flash ESP-DL models using OpenOCD.",
		epilog="This is needed because PlatformIO does not respect custom flash targets form ESP-IDF.")
	parser.add_argument('--skip-pack', action='store_true',
		help="Skip the model packing step and only flash existing packed models.")
	parser.add_argument('--skip-flash', action='store_true',
		help="Skip the flashing step and only pack the models.")
	parser.add_argument('--dry-run', action='store_true',
		help="Print commands that would be executed, without running them.")
	args = parser.parse_args()

	if not args.skip_pack:
		pack_models(dry_run=args.dry_run)

	if not args.skip_flash:
		flash_models(dry_run=args.dry_run)

if __name__ == "__main__":
	main()
