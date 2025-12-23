#!/usr/bin/env python3
"""Convert Label Studio video annotations (videorectangle) into YOLO .txt files.

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
from typing import Dict, List, Tuple
import cv2
import yaml


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
	"""Return mapping frame_num (1-based) -> list of (labels, x%, y%, w%, h%).

	The implementation follows the interpolation logic used by
	`draw_annotations.py` so frame indices match exactly.
	"""
	frame_bboxes: Dict[int, List[Tuple[List[str], float, float, float, float]]] = {}

	for track in tracks:
		seq = sorted(track['sequence'], key=lambda s: s.get('frame', 0))
		labels = track['labels']

		i = 0
		while i < len(seq):
			kf = seq[i]
			if not kf.get('enabled', False):
				i += 1
				continue

			j = i + 1
			found_enabled = False
			while j < len(seq):
				if seq[j].get('enabled', False):
					found_enabled = True
					break
				# Explicitly disabled keyframe: draw up to previous frame and advance
				if not seq[j].get('enabled', True):
					end_frame = seq[j]['frame'] - 1
					start_frame = kf['frame']
					for f in range(start_frame, end_frame + 1):
						frame_bboxes.setdefault(f + 1, []).append((labels, kf['x'], kf['y'], kf['width'], kf['height']))
					i = j + 1
					break
				j += 1

			# No further keyframes found
			if j >= len(seq) and not found_enabled:
				frame_num = kf['frame'] + 1
				frame_bboxes.setdefault(frame_num, []).append((labels, kf['x'], kf['y'], kf['width'], kf['height']))
				i += 1
				continue

			if found_enabled:
				kf2 = seq[j]
				a_frame = kf['frame']
				b_frame = kf2['frame']
				for f in range(a_frame, b_frame + 1):
					if b_frame == a_frame:
						t = 0.0
					else:
						t = (f - a_frame) / (b_frame - a_frame)
					x = kf['x'] + (kf2['x'] - kf['x']) * t
					y = kf['y'] + (kf2['y'] - kf['y']) * t
					w = kf['width'] + (kf2['width'] - kf['width']) * t
					h = kf['height'] + (kf2['height'] - kf['height']) * t
					frame_bboxes.setdefault(f + 1, []).append((labels, x, y, w, h))
				i = j

	return frame_bboxes


def parse_image_frame_number(filename: str) -> int | None:
	"""Extract frame number from filename using prefix before first '_'.

	Returns int frame number or None if it can't be parsed.
	"""
	try:
		prefix = filename.split('_', 1)[0]
		return int(prefix)
	except Exception:
		return None


def collect_image_files(image_dir: str, exts: Tuple[str, ...]) -> Dict[int, str]:
	"""Return mapping frame_num -> image filename (leaf name).

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


def read_class_names_from_file(path: str) -> List[str]:
	"""Read class names from a YOLO-style YAML file.

	Expects a YAML file containing a top-level `names` mapping (index->name) or
	a list of names. PyYAML is required (imported at module level).
	"""
	with open(path, 'r', encoding='utf-8') as f:
		data = yaml.safe_load(f)
	if not isinstance(data, dict) or 'names' not in data:
		raise ValueError("YAML class file must contain top-level 'names' mapping or list")

	names_section = data['names']
	if isinstance(names_section, dict):
		# mapping from index->name
		items = sorted(names_section.items(), key=lambda kv: int(kv[0]))
		return [str(v) for k, v in items]
	elif isinstance(names_section, list):
		return [str(x) for x in names_section]
	else:
		raise ValueError("YAML 'names' must be either a mapping or a list")


def main() -> None:
	parser = argparse.ArgumentParser(description='Prepare YOLO .txt annotations from Label Studio video annotations')
	parser.add_argument('--json-file', required=True, help='Label Studio JSON export file')
	parser.add_argument('--task-id', type=int, required=True, help='Task id to process')
	parser.add_argument('--image-dir', required=True, help='Directory containing image frames')
	parser.add_argument('--output-dir', required=True, help='Directory where YOLO .txt files will be written')
	parser.add_argument('--class-names-file', required=True, help='Path to YAML class names file (YOLO dataset style with top-level `names`). PyYAML is required.')
	parser.add_argument('--skip-empty', action='store_true', help="Do not write empty .txt files for images with no annotations (default: write empty files)")
	parser.add_argument('--exts', default='png,jpg,jpeg', help='Comma-separated image extensions to consider')
	args = parser.parse_args()

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
		raise SystemExit(2)

	tracks = _collect_tracks(task_data)
	if not tracks:
		print(f"Warning: No videorectangle tracks found for task {args.task_id}.")

	frame_bboxes = build_frame_bboxes(tracks)

	if not os.path.exists(args.output_dir):
		os.makedirs(args.output_dir)

	image_map = collect_image_files(args.image_dir, exts)

	# Gather all labels used (each label is a string). We'll expand multi-label boxes as multiple objects.
	labels_set = set()
	for bboxes in frame_bboxes.values():
		for labels, *_ in bboxes:
			for lbl in labels:
				labels_set.add(lbl)

	if not args.class_names_file:
		print("Error: --class-names-file is required. Provide a YAML file containing top-level 'names'.")
		raise SystemExit(2)

	# Read the class names YAML file
	class_names = read_class_names_from_file(args.class_names_file)

	# Check for labels present in annotations but missing from the provided class names
	unknown = sorted(set(labels_set) - set(class_names))
	if unknown:
		print(f"Error: some labels in annotations are not present in {args.class_names_file}: {unknown}")
		print("Update the class names file to include these labels and re-run.")
		raise SystemExit(2)

	name_to_id = {n: i for i, n in enumerate(class_names)}

	# We'll track per-label how many images contain that label (each image counted once per label)
	label_image_counts: Dict[str, int] = {}

	# Process each image file
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
		h, w = img.shape[:2]

		bboxes = frame_bboxes.get(frame_num, [])
		out_lines: List[str] = []
		labels_in_image = set()
		for labels, x_pct, y_pct, w_pct, h_pct in bboxes:
			# x_pct,y_pct are top-left in percentage; convert to YOLO normalized center/width/height
			cx = (x_pct + w_pct / 2.0) / 100.0
			cy = (y_pct + h_pct / 2.0) / 100.0
			nw = max(0.0, min(1.0, w_pct / 100.0))
			nh = max(0.0, min(1.0, h_pct / 100.0))
			# For each label in the list, write a separate YOLO object line
			for lbl in labels:
				labels_in_image.add(lbl)
				if lbl not in name_to_id:
					print(f"Error: label '{lbl}' not found in class names; this indicates a mismatch — aborting")
					raise SystemExit(2)
				idx = name_to_id[lbl]
				out_lines.append(f"{idx} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

		# Update per-label image counts (each label counted once per image)
		if labels_in_image:
			processed_with_annotations += 1
			for lbl in labels_in_image:
				label_image_counts[lbl] = label_image_counts.get(lbl, 0) + 1

		out_path = os.path.join(args.output_dir, os.path.splitext(image_name)[0] + '.txt')
		if out_lines:
			with open(out_path, 'w', encoding='utf-8') as f:
				f.write('\n'.join(out_lines) + '\n')
			written += 1
		else:
			if not args.skip_empty:
				# create an empty annotation file (default behavior)
				open(out_path, 'w', encoding='utf-8').close()


	# Print summary
	total_images = processed
	print(f"Processed {total_images} readable images; wrote {written} annotation files to {args.output_dir}")	
	print(f"Images with at least one annotation: {processed_with_annotations} ({(processed_with_annotations/total_images*100) if total_images else 0:.2f}%)")
	if label_image_counts:
		print('\nPer-label image counts:')
		# Build a table sorted by descending count
		for lbl, cnt in sorted(label_image_counts.items(), key=lambda kv: kv[1], reverse=True):
			pct = (cnt / total_images * 100) if total_images else 0.0
			print(f"  {lbl}: {cnt} images ({pct:.2f}%)")


if __name__ == '__main__':
	main()
