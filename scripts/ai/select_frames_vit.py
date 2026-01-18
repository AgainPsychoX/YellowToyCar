#!/usr/bin/env python3
"""
ViT-based Frame Selection for Object State Changes

CLI tool that selects a compact subset of frames using ViT embeddings and
percentile-based change metrics. Now shares embedding generation and caching
logic with frame_selection_core.
"""

import argparse
import csv
import os
import shutil
from pathlib import Path

import numpy as np

from utils import prepare_output_dir
from frame_selection_core import (
	DEFAULT_MODEL,
	EPSILON,
	SelectionParams,
	TransformConfig,
	EmbeddingConfig,
	collect_frames,
	create_frame_selection_data,
	farthest_point_sampling,
	load_or_compute_embeddings,
	compute_cache_key,
)


def parse_args() -> argparse.Namespace:
	"""Parse command line arguments."""
	parser = argparse.ArgumentParser(
		description='Select representative frames using ViT-based change detection',
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog="""
Examples:
  # Basic usage
  python select_frames_vit.py --input-dir ./captures/session1 --output-dir ./selected

  # Adjust selection parameters
  python select_frames_vit.py -i ./captures -o ./selected \\
	  --concentration-percentile 85 --min-spacing 10 --target-count 100

  # Use specific model and batch size
  python select_frames_vit.py -i ./captures -o ./selected \\
	  --model vit_small_patch16_224.dino --batch-size 16
		"""
	)

	parser.add_argument('-i', '--input-dir', type=str, required=True,
		help='Directory containing input JPEG frames')
	parser.add_argument('-o', '--output-dir', type=str, required=True,
		help='Directory where selected frames will be copied')
	parser.add_argument('--model', type=str, default=DEFAULT_MODEL,
		help=f'ViT model to use (default: {DEFAULT_MODEL})')
	parser.add_argument('--batch-size', type=int, default=8,
		help='Batch size for processing frames (default: 8)')
	parser.add_argument('--concentration-percentile', type=float, default=90.0,
		help='Percentile threshold for concentration metric (default: 90.0)')
	parser.add_argument('--total-change-percentile', type=float, default=60.0,
		help='Percentile threshold for total change metric (default: 60.0)')
	parser.add_argument('--entropy-percentile', type=float, default=40.0,
		help='Percentile threshold for entropy metric (lower is more focused, default: 40.0)')
	parser.add_argument('--temporal-window', type=int, default=5,
		help='Number of frames for temporal smoothing (default: 5)')
	parser.add_argument('--min-spacing', type=int, default=5,
		help='Minimum spacing between selected frames (default: 5)')
	parser.add_argument('--target-count', type=int, default=None,
		help='Target number of frames to select (optional, overrides percentiles)')
	parser.add_argument('--diversity-sampling', action='store_true',
		help='Apply diversity pruning using farthest-point sampling')
	parser.add_argument('--no-l2-normalize', action='store_true',
		help='Skip L2 normalization of patch embeddings')
	parser.add_argument('--overwrite', nargs='*', default=None, metavar='PATTERN',
		help=('Allow overwriting files in non-empty output directory. '
			'Without patterns, uses defaults: *.png *.jpg *.jpeg *.webp. '
			'With patterns, only deletes matching files (e.g., --overwrite "*.log" "*.txt")'))
	parser.add_argument('--cache-dir', type=str, default=None,
		help='Directory for caching embeddings (default: <input-dir>/.embedding_cache)')
	parser.add_argument('--force', action='store_true',
		help='Force recompute embeddings (ignore cache)')
	parser.add_argument('--transform', type=str, default='crop', choices=['crop', 'pad', 'scale'],
		help='Image transform mode: crop (resize + crop, may lose edges), '
			'pad (resize + pad, preserves all data), scale (stretch to fit, may distort). Default: crop')
	parser.add_argument('--align', type=str, default='center', choices=['center', 'top', 'bottom', 'left', 'right'],
		help='Alignment for crop/pad along the adjusted axis. '
			'top/bottom for portrait, left/right for landscape. Default: center')
	parser.add_argument('--save-metrics', action='store_true',
		help='Write frame metrics to CSV file in output directory')
	parser.add_argument('--no-clear-others', action='store_true',
		help='Do not clear other cache files when saving embeddings (keep multiple caches)')

	return parser.parse_args()


def main():
	args = parse_args()

	input_dir = Path(args.input_dir)
	output_dir = Path(args.output_dir)

	if not input_dir.exists():
		print(f"Error: Input directory does not exist: {input_dir}")
		raise SystemExit(1)

	if args.overwrite is not None and len(args.overwrite) == 0:
		args.overwrite = ['*.png', '*.jpg', '*.jpeg', '*.webp']
	try:
		prepare_output_dir(str(output_dir), args.overwrite)
	except Exception as e:
		print(f"Error: {e}")
		raise SystemExit(1)

	print(f"\nCollecting frames from: {input_dir}")
	frames = collect_frames(input_dir)
	if len(frames) < 2:
		print(f"Error: Need at least 2 frames, found {len(frames)}")
		raise SystemExit(1)

	print(f"Found {len(frames)} frames")

	normalize = not args.no_l2_normalize
	transform_config = TransformConfig.from_strings(args.transform, args.align)
	cache_dir = Path(args.cache_dir) if args.cache_dir else (input_dir / '.embedding_cache')
	embedding_config = EmbeddingConfig(
		model=args.model,
		normalize=normalize,
		transform=transform_config,
	)
	cache_key = compute_cache_key(embedding_config, frames)

	print(f"\nTransform: {transform_config.mode.value} (align: {transform_config.alignment.value})")
	print(f"Checking embedding cache (key: {cache_key})...")

	def _progress(done: int, total: int):
		print(f"  Processed {done}/{total} frames", end='\r', flush=True)

	embeddings, from_cache = load_or_compute_embeddings(
		cache_dir=cache_dir,
		frames=frames,
		config=embedding_config,
		force=args.force,
		batch_size=args.batch_size,
		progress=_progress,
		clear_others=not args.no_clear_others,
	)

	if from_cache:
		print(f"\nLoaded cached embeddings: {embeddings.shape}")
	else:
		print(f"\nComputed embeddings: {embeddings.shape}")
	params = SelectionParams(
		concentration_percentile=args.concentration_percentile,
		total_change_percentile=args.total_change_percentile,
		entropy_percentile=args.entropy_percentile,
		temporal_window=args.temporal_window,
		min_spacing=args.min_spacing)
	print("\nComputing patch-level changes...")
	print(f"\nApplying temporal smoothing (window={params.temporal_window})...")
	data = create_frame_selection_data(frames, embeddings, params.temporal_window)

	print(f"  Total change: mean={data.total_change.mean():.4f}, std={data.total_change.std():.4f}")
	print(f"  Concentration: mean={data.concentration.mean():.4f}, std={data.concentration.std():.4f}")
	print(f"  Entropy: mean={data.entropy.mean():.4f}, std={data.entropy.std():.4f}")

	if args.target_count is None:
		# Use specified percentiles to select frames
		conc_thresh, total_thresh, entropy_thresh = data.get_percentile_thresholds(
			params.concentration_percentile,
			params.total_change_percentile,
			params.entropy_percentile)
		print(f"\nThresholds:")
		print(f"  Concentration: {conc_thresh:.4f} ({params.concentration_percentile}th percentile)")
		print(f"  Total change: {total_thresh:.4f} ({params.total_change_percentile}th percentile)")
		print(f"  Entropy: {entropy_thresh:.4f} ({params.entropy_percentile}th percentile)")

		print("\nSelecting candidate frames...")
		candidate_indices = data.select_frames(
			params.concentration_percentile,
			params.total_change_percentile,
			params.entropy_percentile,
			params.min_spacing)
	else:
		print(f"\nAuto-calibrating thresholds for target count: {args.target_count}")
		best_candidates = []
		for percentile in range(95, 0, -5):
			candidates = data.select_frames(
				percentile,
				params.total_change_percentile,
				params.entropy_percentile,
				params.min_spacing)
			best_candidates = candidates
			if len(candidates) <= args.target_count:
				break

		candidate_indices = best_candidates
		print(f"Selected {len(candidate_indices)} candidates (target: {args.target_count})")

	print(f"Selected {len(candidate_indices)} candidate frames")

	if len(candidate_indices) == 0:
		print("Warning: No frames met the selection criteria")
		print("Try adjusting thresholds or reducing percentiles")
		raise SystemExit(0)

	if args.diversity_sampling and len(candidate_indices) > 1:
		print("\nApplying diversity pruning...")
		candidate_embeddings = []
		for idx in candidate_indices:
			frame_embedding = embeddings[idx].mean(dim=0)
			candidate_embeddings.append(frame_embedding.numpy())

		candidate_embeddings = np.stack(candidate_embeddings)
		candidate_embeddings = candidate_embeddings / (
			np.linalg.norm(candidate_embeddings, axis=1, keepdims=True) + EPSILON
		)

		if args.target_count is not None:
			n_keep = min(args.target_count, len(candidate_indices))
		else:
			n_keep = max(len(candidate_indices) // 2, 1)

		fps_indices = farthest_point_sampling(candidate_embeddings, n_keep)
		final_indices = [candidate_indices[i] for i in fps_indices]
		print(f"Pruned to {len(final_indices)} diverse frames")
	else:
		final_indices = candidate_indices

	print(f"\nCopying {len(final_indices)} frames to: {output_dir}")
	for idx in final_indices:
		path = frames[idx]
		dest_path = output_dir / path.name
		shutil.copy2(path, dest_path)

	if args.save_metrics:
		csv_path = output_dir / 'frame_metrics.csv'
		print(f"\nSaving metrics to: {csv_path}")

		with open(csv_path, 'w', newline='') as f:
			writer = csv.writer(f)
			writer.writerow([
				'frame_index',
				'frame_number',
				'filename',
				'total_change',
				'concentration',
				'entropy',
				'selected'
			])

			for i, path in enumerate(frames):
				if i == 0:
					tc, conc, ent = None, None, None
				else:
					tc = float(data.total_change[i - 1])
					conc = float(data.concentration[i - 1])
					ent = float(data.entropy[i - 1])

				selected = 'yes' if i in final_indices else 'no'
				writer.writerow([i, i + 1, path.name, tc, conc, ent, selected])

	print("\n" + "=" * 60)
	print("SUMMARY")
	print("=" * 60)
	print(f"Input frames: {len(frames)}")
	print(f"Selected frames: {len(final_indices)} ({100 * len(final_indices) / len(frames):.1f}%)")
	print(f"Output directory: {output_dir}")
	if args.save_metrics:
		print(f"Metrics CSV: {output_dir / 'frame_metrics.csv'}")
	if len(final_indices) > 0:
		selected_frame_numbers = [int(i + 1) for i in final_indices]
		print(f"\nSelected frame numbers: {selected_frame_numbers[:10]}", end='')
		if len(selected_frame_numbers) > 10:
			print(f" ... {selected_frame_numbers[-3:]}")
		else:
			print()

	print("\nDone!")


if __name__ == '__main__':
	main()
