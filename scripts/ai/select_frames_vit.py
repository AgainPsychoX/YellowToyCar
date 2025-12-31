#!/usr/bin/env python3
"""
ViT-based Frame Selection for Object State Changes

Automatically selects a compact subset of frames from sequential JPEG captures
that represent meaningful object state changes, while discarding background-driven
or redundant frames.

Architecture:
	Frames → Frozen ViT (DINO) → Patch embeddings → Patch-level change statistics
	→ Change concentration + persistence → Candidate selection → Diversity pruning
	→ Selected frames

Assumptions (Hard Constraints):
- Object is visually present in most frames
- Object occupies ≥ ~2 ViT patches at closest distance (~32x32 pixels for ViT-S/16)
- Background motion exists but is less temporally coherent than object motion
- No bounding boxes, no crops, no detectors available
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import List, Tuple, Optional, TYPE_CHECKING

import numpy as np

# Lazy imports for faster --help and cache-hit scenarios
# These modules are imported on-demand via _get_* functions
if TYPE_CHECKING:
	import torch
	from PIL import Image

_torch = None
_F = None
_transforms = None
_Image = None
_timm = None
_pairwise_distances = None


def _get_torch():
	"""Lazy import torch and related modules."""
	global _torch, _F
	if _torch is None:
		import torch
		import torch.nn.functional as F
		_torch = torch
		_F = F
	return _torch


def _get_torch_f():
	"""Get torch.nn.functional (requires _get_torch first)."""
	_get_torch()
	return _F


def _get_transforms():
	"""Lazy import torchvision.transforms."""
	global _transforms
	if _transforms is None:
		from torchvision import transforms
		_transforms = transforms
	return _transforms


def _get_pil():
	"""Lazy import PIL.Image."""
	global _Image
	if _Image is None:
		from PIL import Image
		_Image = Image
	return _Image


def _get_timm():
	"""Lazy import timm."""
	global _timm
	if _timm is None:
		import timm
		_timm = timm
	return _timm


def _get_pairwise_distances():
	"""Lazy import sklearn pairwise_distances."""
	global _pairwise_distances
	if _pairwise_distances is None:
		from sklearn.metrics import pairwise_distances
		_pairwise_distances = pairwise_distances
	return _pairwise_distances


# Constants
SUPPORTED_EXTENSIONS = ('.jpg', '.jpeg', '.png')
DEFAULT_MODEL = 'vit_small_patch16_224.dino'
IMAGE_SIZE = 224
EPSILON = 1e-8


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
	
	parser.add_argument('-i', '--input-dir', type=str, required=True, help='Directory containing input JPEG frames')
	parser.add_argument('-o', '--output-dir', type=str, required=True, help='Directory where selected frames will be copied')
	parser.add_argument('--model', type=str, default=DEFAULT_MODEL, help=f'ViT model to use (default: {DEFAULT_MODEL})')
	parser.add_argument('--batch-size', type=int, default=8, help='Batch size for processing frames (default: 8)')
	parser.add_argument('--concentration-percentile', type=float, default=90.0, help='Percentile threshold for concentration metric (default: 90.0)')
	parser.add_argument('--total-change-percentile', type=float, default=60.0, help='Percentile threshold for total change metric (default: 60.0)')
	parser.add_argument('--entropy-percentile', type=float, default=40.0, 
		help='Percentile threshold for entropy metric (lower is more focused, default: 40.0)')
	parser.add_argument('--temporal-window', type=int, default=5, help='Number of frames for temporal smoothing (default: 5)')
	parser.add_argument('--min-spacing', type=int, default=5, help='Minimum spacing between selected frames (default: 5)')
	parser.add_argument('--target-count', type=int, default=None, help='Target number of frames to select (optional, overrides percentiles)')
	parser.add_argument('--diversity-sampling', action='store_true', help='Apply diversity pruning using farthest-point sampling')
	parser.add_argument('--no-l2-normalize', action='store_true', help='Skip L2 normalization of patch embeddings')
	parser.add_argument('--overwrite', action='store_true', help='Overwrite output directory if it exists')
	parser.add_argument('--cache-dir', type=str, default=None, 
		help='Directory for caching embeddings (default: <input-dir>/.embedding_cache)')
	parser.add_argument('--force', action='store_true', help='Force recompute embeddings (ignore cache)')
	
	return parser.parse_args()


def parse_frame_number(filename: str) -> Optional[int]:
	"""
	Extract frame number from filename.
	
	Expected format: {frame_number}_{timestamp}.jpg
	Example: 0042_20231215_143045.jpg → 42
	"""
	try:
		prefix = filename.split('_', 1)[0]
		return int(prefix)
	except (ValueError, IndexError):
		return None


def collect_frames(input_dir: Path) -> List[Tuple[int, str, Path]]:
	"""
	Collect all valid image frames from input directory.
	
	Returns:
		List of (frame_number, filename, full_path) sorted by frame number
	"""
	frames = []
	
	for entry in input_dir.iterdir():
		if not entry.is_file():
			continue
		
		if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
			continue
		
		frame_num = parse_frame_number(entry.name)
		if frame_num is None:
			print(f"Warning: Skipping file with unparseable frame number: {entry.name}")
			continue
		
		frames.append((frame_num, entry.name, entry))
	
	# Sort by frame number
	frames.sort(key=lambda x: x[0])
	
	return frames


def compute_cache_key(
	model_name: str,
	frame_filenames: List[str],
	normalize: bool
) -> str:
	"""
	Compute a hash-based cache key for embeddings.
	
	Args:
		model_name: Name of the ViT model
		frame_filenames: List of input frame filenames (sorted)
		normalize: Whether L2 normalization is applied
	
	Returns:
		Cache key string (hex hash)
	"""
	hasher = hashlib.sha256()
	hasher.update(model_name.encode('utf-8'))
	hasher.update(b'\x00')  # separator
	hasher.update(str(normalize).encode('utf-8'))
	hasher.update(b'\x00')
	# Include sorted filenames to detect added/removed frames
	for fname in sorted(frame_filenames):
		hasher.update(fname.encode('utf-8'))
		hasher.update(b'\x00')
	return hasher.hexdigest()[:16]  # First 16 chars is enough


def get_cache_path(cache_dir: Path, cache_key: str) -> Tuple[Path, Path]:
	"""Get paths for cache tensor and metadata files."""
	tensor_path = cache_dir / f"embeddings_{cache_key}.pt"
	meta_path = cache_dir / f"embeddings_{cache_key}.json"
	return tensor_path, meta_path


def load_cached_embeddings(
	cache_dir: Path,
	cache_key: str,
	model_name: str,
	normalize: bool
) -> Optional["torch.Tensor"]:
	"""
	Load embeddings from cache if valid.
	
	Args:
		cache_dir: Cache directory
		cache_key: Cache key
		model_name: Expected model name
		normalize: Expected normalization setting
	
	Returns:
		Cached embeddings tensor or None if cache miss/invalid
	"""
	tensor_path, meta_path = get_cache_path(cache_dir, cache_key)
	
	if not tensor_path.exists() or not meta_path.exists():
		return None
	
	try:
		with open(meta_path, 'r') as f:
			meta = json.load(f)
		
		# Validate metadata
		if meta.get('model') != model_name:
			return None
		if meta.get('normalize') != normalize:
			return None
		if meta.get('cache_version') != 1:
			return None
		
		# Load tensor
		torch = _get_torch()
		embeddings = torch.load(tensor_path, map_location='cpu', weights_only=True)
		return embeddings
		
	except Exception as e:
		print(f"Warning: Failed to load cache: {e}")
		return None


def save_embeddings_cache(
	cache_dir: Path,
	cache_key: str,
	embeddings: "torch.Tensor",
	model_name: str,
	normalize: bool,
	frame_count: int
) -> None:
	"""
	Save embeddings to cache.
	
	Args:
		cache_dir: Cache directory
		cache_key: Cache key
		embeddings: Embeddings tensor to cache
		model_name: Model name for metadata
		normalize: Normalization setting for metadata
		frame_count: Number of frames for metadata
	"""
	cache_dir.mkdir(parents=True, exist_ok=True)
	tensor_path, meta_path = get_cache_path(cache_dir, cache_key)
	
	try:
		# Save tensor
		torch = _get_torch()
		torch.save(embeddings, tensor_path)
		
		# Save metadata
		meta = {
			'cache_version': 1,
			'model': model_name,
			'normalize': normalize,
			'frame_count': frame_count,
			'embedding_shape': list(embeddings.shape)
		}
		with open(meta_path, 'w') as f:
			json.dump(meta, f, indent=2)
		
		print(f"Cached embeddings to: {tensor_path}")
		
	except Exception as e:
		print(f"Warning: Failed to save cache: {e}")


def clear_cache_dir(cache_dir: Path) -> None:
	"""Remove all cache files from cache directory."""
	if not cache_dir.exists():
		return
	
	for f in cache_dir.glob("embeddings_*.pt"):
		f.unlink()
	for f in cache_dir.glob("embeddings_*.json"):
		f.unlink()
	print(f"Cleared cache directory: {cache_dir}")


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


def get_transform():
	"""Get image preprocessing transform."""
	transforms = _get_transforms()
	return transforms.Compose([
		transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
		transforms.CenterCrop(IMAGE_SIZE),
		transforms.ToTensor(),
		transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
	])


def load_model(model_name: str, device: "torch.device"):
	"""Load pretrained ViT model."""
	print(f"Loading model: {model_name}")
	timm = _get_timm()
	
	try:
		model = timm.create_model(model_name, pretrained=True)
	except Exception as e:
		print(f"Error loading model '{model_name}': {e}")
		print("\nAvailable DINO ViT models:")
		print("  - vit_small_patch16_224.dino")
		print("  - vit_base_patch16_224.dino")
		raise SystemExit(1)
	
	model = model.to(device)
	model.eval()
	
	# Verify model has patch embedding capability
	if not hasattr(model, 'forward_features'):
		print(f"Error: Model {model_name} does not expose patch embeddings")
		raise SystemExit(1)
	
	print(f"Model loaded successfully on {device}")
	return model


def extract_patch_embeddings(
	model,
	image_tensor: "torch.Tensor",
	normalize: bool = True
) -> "torch.Tensor":
	"""
	Extract patch embeddings from image.
	
	Args:
		model: ViT model
		image_tensor: Image tensor [B, 3, 224, 224]
		normalize: Whether to L2-normalize patch embeddings
	
	Returns:
		Patch embeddings [B, N_patches, D]
	"""
	torch = _get_torch()
	F = _get_torch_f()
	with torch.no_grad():
		# Get features - timm returns [B, N_tokens, D] where N_tokens = 1 + N_patches
		# First token is CLS, rest are patch tokens
		features = model.forward_features(image_tensor)
		
		# Drop CLS token, keep only patch tokens
		patch_tokens = features[:, 1:, :]  # [B, N_patches, D]
		
		if normalize:
			# L2 normalize each patch embedding
			patch_tokens = F.normalize(patch_tokens, p=2, dim=-1)
	
	return patch_tokens


def compute_patch_changes(
	embeddings: "torch.Tensor"
) -> "torch.Tensor":
	"""
	Compute per-patch L2 distance between consecutive frames.
	
	Args:
		embeddings: Patch embeddings [T, N_patches, D]
	
	Returns:
		Change magnitudes [T-1, N_patches]
	"""
	torch = _get_torch()
	# Compute differences between consecutive frames
	diffs = embeddings[1:] - embeddings[:-1]  # [T-1, N_patches, D]
	
	# L2 norm per patch
	changes = torch.norm(diffs, p=2, dim=-1)  # [T-1, N_patches]
	
	return changes


def compute_change_metrics(
	patch_changes: "torch.Tensor"
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
	"""
	Compute change concentration metrics.
	
	Args:
		patch_changes: Per-patch change magnitudes [T-1, N_patches]
	
	Returns:
		- total_change: Mean change across all patches [T-1]
		- concentration: Max/mean ratio [T-1]
		- entropy: Shannon entropy of normalized changes [T-1]
	"""
	changes_np = patch_changes.cpu().numpy()
	
	# Total change (mean across patches)
	total_change = changes_np.mean(axis=1)
	
	# Concentration ratio (max / mean)
	max_change = changes_np.max(axis=1)
	concentration = max_change / (total_change + EPSILON)
	
	# Entropy
	entropy = np.zeros(len(changes_np))
	for i, frame_changes in enumerate(changes_np):
		# Normalize to probability distribution
		p = frame_changes / (frame_changes.sum() + EPSILON)
		# Shannon entropy
		entropy[i] = -np.sum(p * np.log(p + EPSILON))
	
	return total_change, concentration, entropy


def temporal_smoothing(
	signal: np.ndarray,
	window: int
) -> np.ndarray:
	"""
	Apply moving average smoothing.
	
	Args:
		signal: Input signal [T]
		window: Window size for moving average
	
	Returns:
		Smoothed signal [T]
	"""
	if window <= 1:
		return signal
	
	# Pad signal at edges
	padded = np.pad(signal, (window // 2, window // 2), mode='edge')
	
	# Convolve with uniform kernel
	kernel = np.ones(window) / window
	smoothed = np.convolve(padded, kernel, mode='valid')
	
	# Ensure same length as input
	return smoothed[:len(signal)]


def select_candidate_frames(
	total_change: np.ndarray,
	concentration: np.ndarray,
	entropy: np.ndarray,
	concentration_threshold: float,
	total_change_threshold: float,
	entropy_threshold: float,
	min_spacing: int
) -> List[int]:
	"""
	Select candidate frames based on metric thresholds.
	
	Args:
		total_change: Total change metric [T-1]
		concentration: Concentration metric [T-1]
		entropy: Entropy metric [T-1]
		concentration_threshold: Minimum concentration value
		total_change_threshold: Minimum total change value
		entropy_threshold: Maximum entropy value (lower = more focused)
		min_spacing: Minimum frames between selections
	
	Returns:
		List of frame indices (0-indexed into original frame list)
	"""
	# Apply thresholds
	mask = (
		(concentration >= concentration_threshold) &
		(total_change >= total_change_threshold) &
		(entropy <= entropy_threshold)
	)
	
	candidate_indices = np.where(mask)[0]
	
	# Add 1 because change metrics start from frame 1 (comparing frame 0→1)
	candidate_indices = candidate_indices + 1
	
	if len(candidate_indices) == 0:
		return []
	
	# Keep only local maxima in concentration
	selected = []
	for idx in candidate_indices:
		# Check if this is a local maximum in concentration
		is_local_max = True
		
		# Look at neighbors within min_spacing
		for offset in range(-min_spacing, min_spacing + 1):
			if offset == 0:
				continue
			neighbor_idx = idx + offset
			# Check bounds (remembering concentration is [T-1] but idx is in [0, T))
			if 0 <= neighbor_idx - 1 < len(concentration):
				if concentration[idx - 1] < concentration[neighbor_idx - 1]:
					is_local_max = False
					break
		
		if is_local_max:
			selected.append(idx)
	
	# Enforce minimum spacing
	if len(selected) <= 1:
		return selected
	
	final_selected = [selected[0]]
	for idx in selected[1:]:
		if idx - final_selected[-1] >= min_spacing:
			final_selected.append(idx)
	
	return final_selected


def farthest_point_sampling(
	embeddings: np.ndarray,
	n_samples: int
) -> np.ndarray:
	"""
	Select diverse subset using farthest-point sampling.
	
	Args:
		embeddings: Embeddings [N, D]
		n_samples: Number of samples to select
	
	Returns:
		Indices of selected samples
	"""
	pairwise_distances = _get_pairwise_distances()
	n_points = len(embeddings)
	
	if n_samples >= n_points:
		return np.arange(n_points)
	
	# Start with random point
	selected = [np.random.randint(n_points)]
	
	# Compute all pairwise distances once
	distances = pairwise_distances(embeddings, embeddings, metric='euclidean')
	
	# Iteratively select farthest point
	for _ in range(n_samples - 1):
		# Distance to nearest selected point
		min_distances = distances[:, selected].min(axis=1)
		
		# Select point with maximum minimum distance
		next_point = min_distances.argmax()
		selected.append(next_point)
	
	return np.array(sorted(selected))


def main():
	args = parse_args()
	
	# Setup paths
	input_dir = Path(args.input_dir)
	output_dir = Path(args.output_dir)
	
	if not input_dir.exists():
		print(f"Error: Input directory does not exist: {input_dir}")
		raise SystemExit(1)
	
	prepare_output_dir(output_dir, args.overwrite)
	
	# Collect frames
	print(f"\nCollecting frames from: {input_dir}")
	frames = collect_frames(input_dir)
	
	if len(frames) < 2:
		print(f"Error: Need at least 2 frames, found {len(frames)}")
		raise SystemExit(1)
	
	print(f"Found {len(frames)} frames")
	print(f"Frame range: {frames[0][0]} to {frames[-1][0]}")
	
	# Setup cache first (before loading torch)
	normalize = not args.no_l2_normalize
	cache_dir = Path(args.cache_dir) if args.cache_dir else (input_dir / '.embedding_cache')
	
	# Compute cache key
	frame_filenames = [f[1] for f in frames]
	cache_key = compute_cache_key(args.model, frame_filenames, normalize)
	
	# Try to load from cache before importing heavy libraries
	all_embeddings = None
	if not args.force:
		print(f"\nChecking embedding cache (key: {cache_key})...")
		all_embeddings = load_cached_embeddings(cache_dir, cache_key, args.model, normalize)
		if all_embeddings is not None:
			print(f"Loaded cached embeddings: {all_embeddings.shape}")
	
	# Now import torch and setup device
	torch = _get_torch()
	device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
	print(f"\nUsing device: {device}")
	
	# Compute embeddings if not cached
	if all_embeddings is None:
		# Load model
		model = load_model(args.model, device)
		transform = get_transform()
		
		# Extract patch embeddings for all frames
		print("\nExtracting patch embeddings...")
		all_embeddings_list = []
		Image = _get_pil()
		
		batch_size = args.batch_size
		for i in range(0, len(frames), batch_size):
			batch_frames = frames[i:i + batch_size]
			batch_images = []
			
			for frame_num, filename, path in batch_frames:
				try:
					img = Image.open(path).convert('RGB')
					img_tensor = transform(img)
					batch_images.append(img_tensor)
				except Exception as e:
					print(f"Error loading {filename}: {e}")
					raise SystemExit(1)
			
			batch_tensor = torch.stack(batch_images).to(device)
			embeddings = extract_patch_embeddings(
				model,
				batch_tensor,
				normalize=normalize
			)
			all_embeddings_list.append(embeddings.cpu())
			
			print(f"  Processed {min(i + batch_size, len(frames))}/{len(frames)} frames", end='\r')
		
		print(f"  Processed {len(frames)}/{len(frames)} frames")
		
		# Concatenate all embeddings
		all_embeddings = torch.cat(all_embeddings_list, dim=0)  # [T, N_patches, D]
		print(f"Embeddings shape: {all_embeddings.shape}")
		
		# Save to cache
		save_embeddings_cache(
			cache_dir, cache_key, all_embeddings,
			args.model, normalize, len(frames)
		)
	
	# Compute patch-level changes
	print("\nComputing patch-level changes...")
	patch_changes = compute_patch_changes(all_embeddings)  # [T-1, N_patches]
	print(f"Change matrix shape: {patch_changes.shape}")
	
	# Compute metrics
	print("Computing change metrics...")
	total_change, concentration, entropy = compute_change_metrics(patch_changes)
	
	print(f"  Total change: mean={total_change.mean():.4f}, std={total_change.std():.4f}")
	print(f"  Concentration: mean={concentration.mean():.4f}, std={concentration.std():.4f}")
	print(f"  Entropy: mean={entropy.mean():.4f}, std={entropy.std():.4f}")
	
	# Apply temporal smoothing
	print(f"\nApplying temporal smoothing (window={args.temporal_window})...")
	total_change_smooth = temporal_smoothing(total_change, args.temporal_window)
	concentration_smooth = temporal_smoothing(concentration, args.temporal_window)
	entropy_smooth = temporal_smoothing(entropy, args.temporal_window)
	
	# Determine thresholds
	if args.target_count is not None:
		print(f"\nAuto-calibrating thresholds for target count: {args.target_count}")
		# Iteratively adjust thresholds to hit target
		# Start with provided percentiles and adjust concentration
		best_candidates = []
		for percentile in range(95, 0, -5):
			conc_thresh = np.percentile(concentration_smooth, percentile)
			total_thresh = np.percentile(total_change_smooth, args.total_change_percentile)
			entropy_thresh = np.percentile(entropy_smooth, args.entropy_percentile)
			
			candidates = select_candidate_frames(
				total_change_smooth,
				concentration_smooth,
				entropy_smooth,
				conc_thresh,
				total_thresh,
				entropy_thresh,
				args.min_spacing
			)
			
			best_candidates = candidates
			if len(candidates) <= args.target_count:
				break
		
		candidate_indices = best_candidates
		print(f"Selected {len(candidate_indices)} candidates (target: {args.target_count})")
	else:
		# Use percentile thresholds
		concentration_threshold = np.percentile(concentration_smooth, args.concentration_percentile)
		total_change_threshold = np.percentile(total_change_smooth, args.total_change_percentile)
		entropy_threshold = np.percentile(entropy_smooth, args.entropy_percentile)
		
		print(f"\nThresholds:")
		print(f"  Concentration: {concentration_threshold:.4f} ({args.concentration_percentile}th percentile)")
		print(f"  Total change: {total_change_threshold:.4f} ({args.total_change_percentile}th percentile)")
		print(f"  Entropy: {entropy_threshold:.4f} ({args.entropy_percentile}th percentile)")
		
		# Select candidates
		print("\nSelecting candidate frames...")
		candidate_indices = select_candidate_frames(
			total_change_smooth,
			concentration_smooth,
			entropy_smooth,
			concentration_threshold,
			total_change_threshold,
			entropy_threshold,
			args.min_spacing
		)
	
	print(f"Selected {len(candidate_indices)} candidate frames")
	
	if len(candidate_indices) == 0:
		print("Warning: No frames met the selection criteria")
		print("Try adjusting thresholds or reducing percentiles")
		raise SystemExit(0)
	
	# Apply diversity pruning if requested
	if args.diversity_sampling and len(candidate_indices) > 1:
		print("\nApplying diversity pruning...")
		
		# Get CLS embeddings (or mean of patches) for candidates
		candidate_embeddings = []
		for idx in candidate_indices:
			# Use mean of patch embeddings as frame representation
			frame_embedding = all_embeddings[idx].mean(dim=0)  # [D]
			candidate_embeddings.append(frame_embedding.numpy())
		
		candidate_embeddings = np.stack(candidate_embeddings)  # [N_candidates, D]
		
		# Normalize for distance computation
		candidate_embeddings = candidate_embeddings / (
			np.linalg.norm(candidate_embeddings, axis=1, keepdims=True) + EPSILON
		)
		
		# Determine number to keep (e.g., 50% or target_count)
		if args.target_count is not None:
			n_keep = min(args.target_count, len(candidate_indices))
		else:
			n_keep = max(len(candidate_indices) // 2, 1)
		
		# Farthest-point sampling
		fps_indices = farthest_point_sampling(candidate_embeddings, n_keep)
		final_indices = [candidate_indices[i] for i in fps_indices]
		
		print(f"Pruned to {len(final_indices)} diverse frames")
	else:
		final_indices = candidate_indices
	
	# Copy selected frames
	print(f"\nCopying {len(final_indices)} frames to: {output_dir}")
	for idx in final_indices:
		frame_num, filename, path = frames[idx]
		dest_path = output_dir / filename
		shutil.copy2(path, dest_path)
	
	# Save metrics CSV
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
		
		for i, (frame_num, filename, path) in enumerate(frames):
			# Metrics are for changes, so frame i has change i-1 (or None for first frame)
			if i == 0:
				# First frame has no change
				tc, conc, ent = None, None, None
			else:
				tc = float(total_change_smooth[i - 1])
				conc = float(concentration_smooth[i - 1])
				ent = float(entropy_smooth[i - 1])
			
			selected = 'yes' if i in final_indices else 'no'
			
			writer.writerow([i, frame_num, filename, tc, conc, ent, selected])
	
	# Print summary statistics
	print("\n" + "=" * 60)
	print("SUMMARY")
	print("=" * 60)
	print(f"Input frames: {len(frames)}")
	print(f"Selected frames: {len(final_indices)} ({100 * len(final_indices) / len(frames):.1f}%)")
	print(f"Output directory: {output_dir}")
	print(f"Metrics CSV: {csv_path}")
	
	if len(final_indices) > 0:
		selected_frame_numbers = [frames[i][0] for i in final_indices]
		print(f"\nSelected frame numbers: {selected_frame_numbers[:10]}", end='')
		if len(selected_frame_numbers) > 10:
			print(f" ... {selected_frame_numbers[-3:]}")
		else:
			print()
	
	print("\nDone!")


if __name__ == '__main__':
	main()
