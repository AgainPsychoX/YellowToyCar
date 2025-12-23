import argparse
import json
import os
import cv2
import numpy as np

def _hex_to_bgr(hex_color):
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

def parse_label_colors_arg(s):
	"""Parse inline 'label:rrggbb,label2:rrggbb' into dict label->BGR."""
	out = {}
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

def draw_annotations(json_path, image_dir, output_dir, task_id, copy_unmodified=False, label_color_map=None, default_color=(0,0,255)):
	"""Draws bounding boxes and labels on images based on JSON annotations using OpenCV."""
	# Load the entire JSON file into memory
	with open(json_path, 'r') as f:
		data = json.load(f)

	# Find the requested task data (task_id from argument)
	task_data = None
	for task in data:
		if task.get('id') == task_id:
			task_data = task
			break
	if not task_data:
		print(f"Error: Task with ID {task_id} not found in the JSON file.")
		return

	annotations = task_data.get('annotations', [])
	if not annotations:
		print(f"Warning: No annotations found for task {task_id}.")
		return

	# Collect all videorectangle annotation tracks
	tracks = []
	for ann in annotations[0].get('result', []):
		if ann.get('type') == 'videorectangle':
			val = ann.get('value', {})
			seq = val.get('sequence', [])
			labels = val.get('labels', [])
			if seq and labels:
				tracks.append({
					'sequence': seq,
					'labels': labels
				})

	# Build a frame-indexed mapping of bboxes per frame
	# Label Studio uses 0-based frame indices; input files use 1-based frame numbering
	frame_bboxes = {}  # frame_number (1-based int) -> list of (label, x%, y%, w%, h%)

	for track in tracks:
		seq = sorted(track['sequence'], key=lambda s: s.get('frame', 0))
		labels = track['labels']

		# Iterate through sequence keyframes and interpolate between enabled keyframes
		i = 0
		while i < len(seq):
			kf = seq[i]
			if not kf.get('enabled', False):
				i += 1
				continue

			# Search for the next keyframe that is enabled or an explicit disable marker
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
						frame_bboxes.setdefault(f+1, []).append((labels, kf['x'], kf['y'], kf['width'], kf['height']))
					i = j + 1
					break
				j += 1

			# No further keyframes found
			if j >= len(seq) and not found_enabled:
				# Apply only the single keyframe position
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
					frame_bboxes.setdefault(f+1, []).append((labels, x, y, w, h))
				i = j

	# Create the output directory if it doesn't exist
	if not os.path.exists(output_dir):
		os.makedirs(output_dir)

	# Process each image file in the input directory
	for image_name in os.listdir(image_dir):
		if not image_name.lower().endswith(('.png', '.jpg', '.jpeg')):
			continue

		# Extract frame number from filename: assume it starts with 4-digit frame number
		try:
			frame_prefix = image_name.split('_', 1)[0]
			frame_num = int(frame_prefix)
		except Exception:
			print(f"Warning: Could not parse frame number from {image_name}. Skipping.")
			continue

		image_path = os.path.join(image_dir, image_name)

		try:
			img = cv2.imread(image_path)
			if img is None:
				print(f"Error: Could not read image {image_name}. Skipping.")
				continue

			img_height, img_width, _ = img.shape

			bboxes = frame_bboxes.get(frame_num, [])
			# If no bboxes, optionally copy file as-is (controlled by --copy-unmodified)
			if not bboxes:
				if not copy_unmodified:
					print(f"Skipping {image_name} (no annotations)")
					continue
				output_path = os.path.join(output_dir, image_name)
				cv2.imwrite(output_path, img)
				print(f"Copied unmodified {output_path}")
				continue
			# Draw bboxes (multiple labels possible)
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
			print(f"Processed and saved {output_path}")

		except Exception as e:
			print(f"Error processing {image_name}: {e}")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Draw bounding box annotations on images using OpenCV.")
	parser.add_argument("--json-file", required=True,
		help="Path to the Label Studio JSON export file.")
	parser.add_argument("--task-id", type=int, required=True,
		help="Task ID within the JSON to process (e.g., 17).")
	parser.add_argument("--image-dir", required=True,
		help="Path to the directory containing the images to annotate.")
	parser.add_argument("--output-dir", required=True,
		help="Path to the directory where annotated images will be saved.")
	parser.add_argument("--copy-unmodified", action="store_true",
		help="Copy images with no annotations to the output directory (default: don't copy).")
	parser.add_argument("--label-colors", type=str, default=None,
		help="Inline mapping 'label:rrggbb,label2:rrggbb' (hex only, # optional).")
	args = parser.parse_args()

	label_color_map = {}
	if args.label_colors:
		label_color_map = parse_label_colors_arg(args.label_colors)

	draw_annotations(
		args.json_file,
		args.image_dir,
		args.output_dir,
		task_id=args.task_id,
		copy_unmodified=args.copy_unmodified,
		label_color_map=label_color_map
	)
