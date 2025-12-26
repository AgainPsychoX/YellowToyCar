#!/usr/bin/env python3
"""
Convert Label Studio video annotations (videorectangle) into YOLO .txt files.

Creates one `.txt` per image in `--image-dir` into `--output-dir` containing
lines in the YOLO format:

	<class_id> <x_center> <y_center> <width> <height>

Coordinates are normalized 0..1 (fractions of image width/height). The script
reads the Label Studio JSON export, finds `--task-id`, interpolates frame
keyframes and writes annotation files. Images are never copied or modified.
"""

from __future__ import annotations
import argparse
import json
import os
import shutil
from typing import Dict, List, Tuple
import cv2
import yaml

# Leave the debugging code here for easy enabling when needed
# import debugpy
# debugpy.listen(5678) # default
# print("Waiting for debugger attach")
# debugpy.wait_for_client()

def _hex_to_bgr(hex_color: str):
	"""Convert '#RRGGBB' or 'RRGGBB' to OpenCV BGR tuple."""
	s = hex_color.strip()
	if s.startswith('#'):
		s = s[1:]
	if len(s) != 6 or any(c not in '0123456789abcdefABCDEF' for c in s):
		raise ValueError(f"Invalid hex color: {hex_color}")
	r = int(s[0:2], 16)
	g = int(s[2:4], 16)
	b = int(s[4:6], 16)
	return (b, g, r)


def parse_label_colors_arg(s: str) -> Dict[str, Tuple[int, int, int]]:
	"""Parse inline 'label:rrggbb,label2:rrggbb' into dict label->BGR."""
	out: Dict[str, Tuple[int, int, int]] = {}
	if not s:
		return out
	for pair in s.replace(';', ',').split(','):
		pair = pair.strip()
		if not pair:
			continue
		if ':' not in pair:
			print(f"Warning: skipping invalid label-color pair '{pair}'")
			continue
		label, color = pair.split(':', 1)
		label = label.strip().lower()
		color = color.strip()
		try:
			out[label] = _hex_to_bgr(color)
		except ValueError as e:
			print(f"Warning: {e}. Skipping '{label}'.")
	return out


def _collect_tracks(task_data: dict) -> List[dict]:
	tracks = []
	annotations = task_data.get('annotations', [])
	if not annotations:
		return tracks
	for ann in annotations[0].get('result', []):
		if ann.get('type') == 'videorectangle':
			val = ann.get('value', {})
			seq = val.get('sequence', [])
			labels = val.get('labels', [])
			if seq and labels:
				tracks.append({'sequence': seq, 'labels': labels})
	return tracks


def build_frame_bboxes(tracks: List[dict]) -> Dict[int, List[Tuple[List[str], float, float, float, float]]]:
	"""
	Return mapping frame_num (1-based) -> list of (labels, x%, y%, w%, h%).

	For each keyframe:
	- If disabled: add as a single frame (no interpolation)
	- If enabled: interpolate to next keyframe (enabled or disabled)
	- Last enabled keyframe with no successor: add as single frame
	"""
	frame_bboxes: Dict[int, List[Tuple[List[str], float, float, float, float]]] = {}

	for track in tracks:
		seq = sorted(track['sequence'], key=lambda s: s.get('frame', 0))
		labels = track['labels']

		for i, kf in enumerate(seq):
			if not kf.get('enabled', False):
				# Disabled keyframe: just add it as a single frame
				frame_num = kf['frame'] + 1
				frame_bboxes.setdefault(frame_num, []).append((labels, kf['x'], kf['y'], kf['width'], kf['height']))
			else:
				# Enabled keyframe: interpolate to next keyframe if it exists
				if i + 1 < len(seq):
					kf2 = seq[i + 1]
					a_frame = kf['frame']
					b_frame = kf2['frame']
					for f in range(a_frame, b_frame):
						t = (f - a_frame) / (b_frame - a_frame) if b_frame > a_frame else 0.0
						x = kf['x'] + (kf2['x'] - kf['x']) * t
						y = kf['y'] + (kf2['y'] - kf['y']) * t
						w = kf['width'] + (kf2['width'] - kf['width']) * t
						h = kf['height'] + (kf2['height'] - kf['height']) * t
						frame_bboxes.setdefault(f + 1, []).append((labels, x, y, w, h))
				else:
					# Last keyframe: just add it
					frame_num = kf['frame'] + 1
					frame_bboxes.setdefault(frame_num, []).append((labels, kf['x'], kf['y'], kf['width'], kf['height']))

	return frame_bboxes


def parse_image_frame_number(filename: str) -> int | None:
	try:
		prefix = filename.split('_', 1)[0]
		return int(prefix)
	except Exception:
		return None


def write_yolo_txt_annotation(
		bboxes: List[Tuple[List[str], float, float, float, float]], 
		name_to_id: Dict[str, int], output_dir: str, image_name: str, skip_empty: bool = False) -> bool:
	"""
	Write YOLO annotation file for one image.

	Converts bboxes (x%, y%, w%, h%) to YOLO format (normalized center/width/height).
	Returns True if written, False if skipped (empty annotation and skip_empty=True).
	"""
	out_lines: List[str] = []

	for labels, x_pct, y_pct, w_pct, h_pct in bboxes:
		# x_pct,y_pct are top-left in percentage; convert to YOLO normalized center/width/height
		cx = (x_pct + w_pct / 2.0) / 100.0
		cy = (y_pct + h_pct / 2.0) / 100.0
		nw = max(0.0, min(1.0, w_pct / 100.0))
		nh = max(0.0, min(1.0, h_pct / 100.0))
		# For each label in the list, write a separate YOLO object line
		for lbl in labels:
			if lbl not in name_to_id:
				raise ValueError(f"label '{lbl}' not found in class names")
			idx = name_to_id[lbl]
			out_lines.append(f"{idx} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

	out_path = os.path.join(output_dir, os.path.splitext(image_name)[0] + '.txt')
	if out_lines:
		with open(out_path, 'w', encoding='utf-8') as f:
			f.write('\n'.join(out_lines) + '\n')
		return True
	else:
		if not skip_empty:
			# create an empty annotation file (default behavior)
			open(out_path, 'w', encoding='utf-8').close()
		return False


def write_debug_image(
		img, bboxes: List[Tuple[List[str], float, float, float, float]], image_name: str, output_dir: str, 
		label_color_map: Dict[str, Tuple[int, int, int]] | None = None, default_color=(0,0,255)) -> None:
	"""Draw bounding boxes on image and write to output directory.

	If no bboxes, image is copied unmodified. Labels are appended to filename.
	"""
	img_height, img_width = img.shape[:2]

	if not bboxes:
		# No annotations; copy image as-is
		output_path = os.path.join(output_dir, image_name)
		cv2.imwrite(output_path, img)
		return

	label_names = []
	for labels, x_pct, y_pct, w_pct, h_pct in bboxes:
		# labels is a list; join into single label token
		label_name = '-'.join(labels)
		label_names.append(label_name)

		x = int(x_pct * img_width / 100.0)
		y = int(y_pct * img_height / 100.0)
		w = int(w_pct * img_width / 100.0)
		h = int(h_pct * img_height / 100.0)
		start_point = (x, y)
		end_point = (x + w, y + h)
		# choose color: try first label then joined label, fall back to default
		color_bgr = default_color
		if label_color_map:
			lookup_keys = [l.strip().lower() for l in labels if l] + [label_name.lower()]
			for k in lookup_keys:
				if k in label_color_map:
					color_bgr = label_color_map[k]
					break
		thickness = 2
		cv2.rectangle(img, start_point, end_point, color_bgr, thickness)
		font = cv2.FONT_HERSHEY_SIMPLEX
		font_scale = 0.5
		text_position = (start_point[0], max(0, start_point[1] - 10))
		cv2.putText(img, label_name, text_position, font, font_scale, color_bgr, thickness)

	# Create output filename with appended labels (unique set)
	unique_labels = sorted(set(label_names))
	labels_suffix = '_'.join(unique_labels)
	name, ext = os.path.splitext(image_name)
	output_name = f"{name}_{labels_suffix}{ext}"
	output_path = os.path.join(output_dir, output_name)
	cv2.imwrite(output_path, img)


def prepare_output_dir(path: str, overwrite: bool) -> None:
	"""Ensure output dir exists and is empty; clear when overwrite is set."""
	if not os.path.exists(path):
		os.makedirs(path)
		return

	contents = os.listdir(path)
	if not contents:
		return

	if not overwrite:
		print(f"Error: output directory '{path}' is not empty. Use --overwrite to clear it.")
		raise SystemExit(1)

	try:
		shutil.rmtree(path)
		os.makedirs(path)
	except Exception as e:
		print(f"Error: failed to clear directory '{path}': {e}")
		raise SystemExit(1)


def collect_image_files(image_dir: str, exts: Tuple[str, ...]) -> Dict[int, str]:
	"""
	Return mapping frame_num -> image filename (leaf name).
	If multiple images share the same frame number, the last encountered will be used.
	"""
	mapping: Dict[int, str] = {}
	for name in os.listdir(image_dir):
		if not name.lower().endswith(exts):
			continue
		fn = parse_image_frame_number(name)
		if fn is None:
			continue
		mapping[fn] = name
	return mapping


def read_class_names_from_file(path: str) -> Dict[str, int]:
	"""
	Read class names from a YOLO-style YAML file.

	Expects a YAML file containing a top-level `names` mapping (index->name) or
	a list of names. PyYAML is required (imported at module level).

	Returns a dict mapping class name -> class id (index).
	"""
	with open(path, 'r', encoding='utf-8') as f:
		data = yaml.safe_load(f)
	if not isinstance(data, dict) or 'names' not in data:
		raise ValueError("YAML class file must contain top-level 'names' mapping or list")

	names_section = data['names']
	name_to_id: Dict[str, int] = {}

	if isinstance(names_section, dict):
		# mapping from index->name; sort by index to preserve order
		items = sorted(names_section.items(), key=lambda kv: int(kv[0]))
		for idx, name in items:
			name_to_id[str(name)] = int(idx)
	elif isinstance(names_section, list):
		for idx, name in enumerate(names_section):
			name_to_id[str(name)] = idx
	else:
		raise ValueError("YAML 'names' must be either a mapping or a list")

	return name_to_id


def main() -> None:
	parser = argparse.ArgumentParser(description='Prepare YOLO .txt annotations from Label Studio video annotations.')
	parser.add_argument('--json-file', required=True, 
		help='Label Studio JSON export file.')
	parser.add_argument('--task-id', type=int, required=True, 
		help='Task ID to process.')
	parser.add_argument('--image-dir', required=True, 
		help='Directory containing image frames.')
	parser.add_argument('--output-dir', required=True, 
		help='Directory where YOLO .txt files will be written.')
	parser.add_argument('--class-names-file', required=True, 
		help='Path to YAML class names file (YOLO dataset style with top-level `names`).')
	parser.add_argument('--skip-empty', action='store_true', 
		help="Do not write empty .txt files for images with no annotations (default: write empty files).")
	parser.add_argument('--exts', default='png,jpg,jpeg', 
		help='Comma-separated image extensions to consider.')
	parser.add_argument('--debug-by-drawing', default=None, 
		help='Path to output directory where annotated images will be written for debug purposes.')
	parser.add_argument('--label-colors', type=str, default=None, 
		help="Inline mapping 'label:rrggbb,label2:rrggbb' to control bounding box colors in debug images.")
	parser.add_argument('--overwrite', action='store_true',
		help='Clear output directories if they are not empty before writing.')
	args = parser.parse_args()

	name_to_id = read_class_names_from_file(args.class_names_file)

	# Prepare debug drawing helpers if enabled
	label_color_map = {}
	if args.debug_by_drawing:
		if args.label_colors:
			try:
				label_color_map = parse_label_colors_arg(args.label_colors)
			except Exception as e:
				print(f"Error: failed parsing --label-colors: {e}; aborting.")
				raise SystemExit(1)

	exts = tuple('.' + e.lower().lstrip('.') for e in args.exts.split(','))

	with open(args.json_file, 'r', encoding='utf-8') as f:
		data = json.load(f)

	task_data = None
	for task in data:
		if task.get('id') == args.task_id:
			task_data = task
			break
	if not task_data:
		print(f"Error: Task with ID {args.task_id} not found in the JSON file.")
		raise SystemExit(1)

	tracks = _collect_tracks(task_data)
	if not tracks:
		print(f"Error: No videorectangle tracks found for task {args.task_id}.")
		raise SystemExit(1)

	frame_bboxes = build_frame_bboxes(tracks)

	image_map = collect_image_files(args.image_dir, exts)

	# Gather all labels used (each label is a string). We'll expand multi-label boxes as multiple objects.
	labels_set = set()
	for bboxes in frame_bboxes.values():
		for labels, *_ in bboxes:
			for lbl in labels:
				labels_set.add(lbl)

	# Check for labels present in annotations but missing from the provided class names
	unknown = sorted(set(labels_set) - set(name_to_id.keys()))
	if unknown:
		print(f"Error: some labels in annotations are not present in {args.class_names_file}: {unknown}")
		print("Update the class names file to include these labels and re-run.")
		raise SystemExit(1)

	# Prepare output directories
	prepare_output_dir(args.output_dir, args.overwrite)
	if args.debug_by_drawing:
		prepare_output_dir(args.debug_by_drawing, args.overwrite)

	# Track per-label how many images contain that label (each image counted once per label)
	label_image_counts: Dict[str, int] = {}

	# Process each image file in a single pass: write YOLO .txt + optionally draw debug image
	processed = 0  # count of successfully read images
	written = 0
	processed_with_annotations = 0
	for frame_num, image_name in image_map.items():
		image_path = os.path.join(args.image_dir, image_name)
		img = cv2.imread(image_path)
		if img is None:
			print(f"Warning: Could not read {image_path}; skipping")
			continue
		processed += 1

		bboxes = frame_bboxes.get(frame_num, [])
		labels_in_image = set()
		for labels, *_ in bboxes:
			for lbl in labels:
				labels_in_image.add(lbl)

		# Write YOLO annotation file
		try:
			was_written = write_yolo_txt_annotation(bboxes, name_to_id, args.output_dir, image_name, skip_empty=args.skip_empty)
			if was_written:
				written += 1
		except ValueError as e:
			print(f"Error: {e} — aborting")
			raise SystemExit(1)

		# Update per-label image counts
		if labels_in_image:
			processed_with_annotations += 1
			for lbl in labels_in_image:
				label_image_counts[lbl] = label_image_counts.get(lbl, 0) + 1

		# Optionally draw debug image in the same pass
		if args.debug_by_drawing:
			try:
				write_debug_image(img, bboxes, image_name, args.debug_by_drawing, label_color_map)
			except Exception as e:
				print(f"Warning: failed to write debug image for {image_name}: {e}")

	# Print summary
	total_images = processed
	print(f"Processed {total_images} readable images; wrote {written} annotation files to {args.output_dir}")	
	print(f"Images with at least one annotation: {processed_with_annotations} ({(processed_with_annotations / total_images * 100) if total_images else 0:.2f}%)")
	if label_image_counts:
		print('\nPer-label image counts:')
		# Build a table sorted by descending count
		for lbl, cnt in sorted(label_image_counts.items(), key=lambda kv: kv[1], reverse=True):
			pct = (cnt / total_images * 100) if total_images else 0.0
			print(f"  {lbl}: {cnt} images ({pct:.2f}%)")


if __name__ == '__main__':
	main()
