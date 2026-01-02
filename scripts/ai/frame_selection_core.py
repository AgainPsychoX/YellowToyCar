#!/usr/bin/env python3
"""
Frame Selection Core Logic

Shared utilities for ViT-based frame selection, used by both CLI and GUI tools.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Tuple, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
	import torch
	from PIL import Image

# Lazy imports
_torch = None
_F = None
_transforms = None
_Image = None
_timm = None


def _get_torch():
	"""Lazy import torch."""
	global _torch
	if _torch is None:
		import torch
		_torch = torch
	return _torch


def _get_torch_f():
	"""Lazy import torch.nn.functional."""
	global _F
	if _F is None:
		import torch.nn.functional as F
		_F = F
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


class TransformMode(Enum):
	"""Image transform mode for resizing to target dimensions."""
	CROP = 'crop'      # Resize larger dim, crop smaller (loses data)
	PAD = 'pad'        # Resize to fit, pad remaining space (preserves all data)
	SCALE = 'scale'    # Stretch/squeeze to exact dimensions (may distort)


class Alignment(Enum):
	"""Alignment for crop/pad operations along the adjusted axis."""
	CENTER = 'center'
	TOP = 'top'       # Keep top of image (crop bottom, or pad at bottom)
	BOTTOM = 'bottom' # Keep bottom of image (crop top, or pad at top)
	LEFT = 'left'     # Keep left of image (crop right, or pad at right)
	RIGHT = 'right'   # Keep right of image (crop left, or pad at left)


@dataclass
class TransformConfig:
	"""Configuration for image preprocessing transform."""
	mode: TransformMode = TransformMode.CROP
	alignment: Alignment = Alignment.CENTER
	# Pad fill: use ImageNet mean by default for better compatibility
	pad_fill: Tuple[float, float, float] = (0.485, 0.456, 0.406)

	@classmethod
	def from_strings(cls, mode: str, alignment: str = 'center') -> 'TransformConfig':
		"""Create config from CLI string arguments."""
		return cls(
			mode=TransformMode(mode.lower()),
			alignment=Alignment(alignment.lower()),
		)

	def cache_key_part(self) -> str:
		"""Return a string representation for cache key computation."""
		return f"{self.mode.value}:{self.alignment.value}"


# Constants
SUPPORTED_EXTENSIONS = ('.jpg', '.jpeg', '.png')
EPSILON = 1e-8
DEFAULT_MODEL = 'vit_small_patch16_224.dino'
IMAGE_SIZE = 224
DEFAULT_TRANSFORM_CONFIG = TransformConfig()


@dataclass
class SelectionParams:
	"""Parameters controlling frame selection thresholds and smoothing."""
	concentration_percentile: float = 90.0
	total_change_percentile: float = 60.0
	entropy_percentile: float = 40.0
	temporal_window: int = 5
	min_spacing: int = 5

	def copy(self) -> "SelectionParams":
		return SelectionParams(
			concentration_percentile=self.concentration_percentile,
			total_change_percentile=self.total_change_percentile,
			entropy_percentile=self.entropy_percentile,
			temporal_window=self.temporal_window,
			min_spacing=self.min_spacing)


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


def collect_frames(input_dir: Path) -> List[Path]:
	"""
	Collect all valid image frames from input directory.
	
	Returns:
		List of Path objects sorted by filename.
		Frame index in list = display frame number - 1 (0-indexed).
	"""
	frames = []
	
	for entry in input_dir.iterdir():
		if not entry.is_file():
			continue
		
		if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
			continue
		
		frames.append(entry)
	
	# Sort by filename
	frames.sort(key=lambda p: p.name)
	return frames


def compute_cache_key(
	model_name: str,
	frame_paths: List[Path],
	normalize: bool,
	transform_config: TransformConfig = None
) -> str:
	"""Compute a hash-based cache key for embeddings (uses filenames only, not full paths)."""
	if transform_config is None:
		transform_config = DEFAULT_TRANSFORM_CONFIG

	hasher = hashlib.sha256()
	hasher.update(model_name.encode('utf-8'))
	hasher.update(b'\x00')
	hasher.update(str(normalize).encode('utf-8'))
	hasher.update(b'\x00')
	hasher.update(transform_config.cache_key_part().encode('utf-8'))
	hasher.update(b'\x00')
	for path in sorted(frame_paths, key=lambda p: p.name):
		hasher.update(path.name.encode('utf-8'))
		hasher.update(b'\x00')
	return hasher.hexdigest()[:16]


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
	"""Load embeddings from cache if valid."""
	tensor_path, meta_path = get_cache_path(cache_dir, cache_key)
	
	if not tensor_path.exists() or not meta_path.exists():
		return None
	
	try:
		with open(meta_path, 'r') as f:
			meta = json.load(f)
		
		if meta.get('model') != model_name:
			return None
		if meta.get('normalize') != normalize:
			return None
		if meta.get('cache_version') != 1:
			return None
		
		torch = _get_torch()
		embeddings = torch.load(tensor_path, map_location='cpu', weights_only=True)
		return embeddings
		
	except Exception:
		return None


def save_embeddings_cache(
	cache_dir: Path,
	cache_key: str,
	embeddings: "torch.Tensor",
	model_name: str,
	normalize: bool,
	frame_count: int
) -> None:
	"""Persist embeddings tensor and metadata to cache directory."""
	cache_dir.mkdir(parents=True, exist_ok=True)
	tensor_path, meta_path = get_cache_path(cache_dir, cache_key)

	try:
		torch = _get_torch()
		torch.save(embeddings, tensor_path)

		meta = {
			'cache_version': 1,
			'model': model_name,
			'normalize': normalize,
			'frame_count': frame_count,
			'embedding_shape': list(embeddings.shape),
		}
		with open(meta_path, 'w') as f:
			json.dump(meta, f, indent=2)
		print(f"Cached embeddings to: {tensor_path}")

	except Exception as exc:
		print(f"Warning: Failed to save cache: {exc}")


def clear_cache_dir(cache_dir: Path) -> None:
	"""Remove cached embedding files from a cache directory."""
	if not cache_dir.exists():
		return

	for f in cache_dir.glob("embeddings_*.pt"):
		f.unlink()
	for f in cache_dir.glob("embeddings_*.json"):
		f.unlink()


def compute_patch_changes(embeddings: "torch.Tensor") -> "torch.Tensor":
	"""Compute per-patch L2 distance between consecutive frames."""
	torch = _get_torch()
	diffs = embeddings[1:] - embeddings[:-1]
	changes = torch.norm(diffs, p=2, dim=-1)
	return changes


def compute_change_metrics(
	patch_changes: "torch.Tensor"
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
	"""
	Compute change concentration metrics.
	
	Returns:
		- total_change: Mean change across all patches [T-1]
		- concentration: Max/mean ratio [T-1]
		- entropy: Shannon entropy of normalized changes [T-1]
	"""
	changes_np = patch_changes.cpu().numpy()
	
	total_change = changes_np.mean(axis=1)
	max_change = changes_np.max(axis=1)
	concentration = max_change / (total_change + EPSILON)
	
	entropy = np.zeros(len(changes_np))
	for i, frame_changes in enumerate(changes_np):
		p = frame_changes / (frame_changes.sum() + EPSILON)
		entropy[i] = -np.sum(p * np.log(p + EPSILON))
	
	return total_change, concentration, entropy


def temporal_smoothing(signal: np.ndarray, window: int) -> np.ndarray:
	"""Apply moving average smoothing."""
	if window <= 1:
		return signal
	
	padded = np.pad(signal, (window // 2, window // 2), mode='edge')
	kernel = np.ones(window) / window
	smoothed = np.convolve(padded, kernel, mode='valid')
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
	
	Returns:
		List of frame indices (0-indexed into original frame list)
	"""
	mask = (
		(concentration >= concentration_threshold) &
		(total_change >= total_change_threshold) &
		(entropy <= entropy_threshold)
	)
	
	candidate_indices = np.where(mask)[0]
	candidate_indices = candidate_indices + 1  # Change metrics start from frame 1
	
	if len(candidate_indices) == 0:
		return []
	
	# Keep only local maxima in concentration
	selected = []
	for idx in candidate_indices:
		is_local_max = True
		for offset in range(-min_spacing, min_spacing + 1):
			if offset == 0:
				continue
			neighbor_idx = idx + offset
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
	"""Select a diverse subset of vectors using farthest-point sampling."""
	n_points = len(embeddings)
	if n_samples >= n_points:
		return np.arange(n_points)

	# Compute full pairwise distances; fallback to numpy if sklearn is unavailable.
	try:
		from sklearn.metrics import pairwise_distances
		distances = pairwise_distances(embeddings, embeddings, metric='euclidean')
	except Exception:
		diff = embeddings[:, None, :] - embeddings[None, :, :]
		distances = np.linalg.norm(diff, axis=2)

	selected = [np.random.randint(n_points)]
	for _ in range(n_samples - 1):
		min_distances = distances[:, selected].min(axis=1)
		next_point = min_distances.argmax()
		selected.append(next_point)

	return np.array(sorted(selected))


class _AlignedCrop:
	"""Crop to square with configurable alignment along the longer axis."""

	def __init__(self, size: int, alignment: Alignment):
		self.size = size
		self.alignment = alignment

	def __call__(self, img: "Image.Image") -> "Image.Image":
		w, h = img.size
		crop_size = self.size

		if w > h:
			# Landscape: crop horizontally
			if self.alignment == Alignment.LEFT:
				left = 0
			elif self.alignment == Alignment.RIGHT:
				left = w - crop_size
			else:  # CENTER (or TOP/BOTTOM which don't apply here)
				left = (w - crop_size) // 2
			return img.crop((left, 0, left + crop_size, h))
		elif h > w:
			# Portrait: crop vertically
			if self.alignment == Alignment.TOP:
				top = 0
			elif self.alignment == Alignment.BOTTOM:
				top = h - crop_size
			else:  # CENTER (or LEFT/RIGHT which don't apply here)
				top = (h - crop_size) // 2
			return img.crop((0, top, w, top + crop_size))
		else:
			# Already square
			return img


class _AlignedPad:
	"""Pad to square with configurable alignment along the shorter axis."""

	def __init__(self, size: int, alignment: Alignment, fill: Tuple[float, float, float]):
		self.size = size
		self.alignment = alignment
		# Convert normalized fill (0-1) to 0-255 for PIL
		self.fill = tuple(int(c * 255) for c in fill)

	def __call__(self, img: "Image.Image") -> "Image.Image":
		Image = _get_pil()
		w, h = img.size

		if w == h == self.size:
			# Already the right size
			return img

		new_img = Image.new('RGB', (self.size, self.size), self.fill)

		if w < h:
			# Portrait: pad horizontally
			pad_total = self.size - w
			if self.alignment == Alignment.LEFT:
				paste_x = 0
			elif self.alignment == Alignment.RIGHT:
				paste_x = pad_total
			else:  # CENTER (or TOP/BOTTOM which don't apply here)
				paste_x = pad_total // 2
			new_img.paste(img, (paste_x, 0))
		elif h < w:
			# Landscape: pad vertically
			pad_total = self.size - h
			if self.alignment == Alignment.TOP:
				paste_y = 0
			elif self.alignment == Alignment.BOTTOM:
				paste_y = pad_total
			else:  # CENTER (or LEFT/RIGHT which don't apply here)
				paste_y = pad_total // 2
			new_img.paste(img, (0, paste_y))
		else:
			# Square but wrong size - just paste centered
			new_img.paste(img, (0, 0))

		return new_img


class _ResizePreservingAspect:
	"""
	Resize image preserving aspect ratio.
	
	Args:
		size: Target size for one dimension.
		fit_inside: If True, largest dim = size (image fits inside square).
		            If False, smallest dim = size (square fits inside image).
	"""

	def __init__(self, size: int, fit_inside: bool):
		self.size = size
		self.fit_inside = fit_inside

	def __call__(self, img: "Image.Image") -> "Image.Image":
		transforms = _get_transforms()
		w, h = img.size

		# For square images, both modes produce the same result
		if w == h:
			new_w = new_h = self.size
		elif self.fit_inside:
			# Scale so largest dimension == size (for padding)
			if w >= h:
				new_w = self.size
				new_h = int(h * self.size / w)
			else:
				new_h = self.size
				new_w = int(w * self.size / h)
		else:
			# Scale so smallest dimension == size (for cropping)
			if w <= h:
				new_w = self.size
				new_h = int(h * self.size / w)
			else:
				new_h = self.size
				new_w = int(w * self.size / h)

		return transforms.functional.resize(
			img, (new_h, new_w),
			interpolation=transforms.InterpolationMode.BICUBIC
		)


def get_transform(
	image_size: int = IMAGE_SIZE,
	config: TransformConfig = None
):
	"""
	Return torchvision transform used for ViT preprocessing.

	Args:
		image_size: Target square size for the output image.
		config: Transform configuration specifying mode, alignment, and pad fill.
			If None, uses DEFAULT_TRANSFORM_CONFIG (crop with center alignment).

	Transform modes:
		- CROP: Resize preserving aspect (smallest dim = target), then crop.
			Fast, but loses data on edges.
		- PAD: Resize preserving aspect (largest dim = target), then pad.
			Preserves all image data.
		- SCALE: Directly resize to target dimensions.
			Fastest, but may distort aspect ratio.
	
	Note: For square input images, all modes produce identical results.
	"""
	if config is None:
		config = DEFAULT_TRANSFORM_CONFIG

	transforms = _get_transforms()

	if config.mode == TransformMode.SCALE:
		# Direct resize - may distort aspect ratio
		return transforms.Compose([
			transforms.Resize(
				(image_size, image_size),
				interpolation=transforms.InterpolationMode.BICUBIC
			),
			transforms.ToTensor(),
			transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
		])

	elif config.mode == TransformMode.CROP:
		# Resize so smallest dim == image_size, then crop the excess
		return transforms.Compose([
			_ResizePreservingAspect(image_size, fit_inside=False),
			_AlignedCrop(image_size, config.alignment),
			transforms.ToTensor(),
			transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
		])

	elif config.mode == TransformMode.PAD:
		# Resize so largest dim == image_size, then pad the rest
		return transforms.Compose([
			_ResizePreservingAspect(image_size, fit_inside=True),
			_AlignedPad(image_size, config.alignment, config.pad_fill),
			transforms.ToTensor(),
			transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
		])

	else:
		raise ValueError(f"Unknown transform mode: {config.mode}")


def load_model(model_name: str, device: "torch.device"):
	"""Load a pretrained ViT model and move it to device."""
	timm = _get_timm()

	try:
		model = timm.create_model(model_name, pretrained=True)
	except Exception as exc:
		raise RuntimeError(f"Error loading model '{model_name}': {exc}") from exc

	model = model.to(device)
	model.eval()

	if not hasattr(model, 'forward_features'):
		raise RuntimeError(f"Model {model_name} does not expose patch embeddings")

	return model


def extract_patch_embeddings(
	model,
	image_tensor: "torch.Tensor",
	normalize: bool = True
) -> "torch.Tensor":
	"""Extract patch embeddings from a batch of images."""
	torch = _get_torch()
	F = _get_torch_f()
	with torch.no_grad():
		features = model.forward_features(image_tensor)
		patch_tokens = features[:, 1:, :]
		if normalize:
			patch_tokens = F.normalize(patch_tokens, p=2, dim=-1)
	return patch_tokens


def load_or_compute_embeddings(
	frames: List[Path],
	model_name: str,
	model_input_size: int = IMAGE_SIZE,
	cache_dir: Path = None,
	normalize: bool = True,
	force: bool = False,
	batch_size: int = 8,
	cache_key: str = None,
	progress: Callable[[int, int], None] | None = None,
	transform_config: TransformConfig = None,
) -> Tuple["torch.Tensor", str, bool]:
	"""
	Load embeddings from cache or compute them if missing.

	Args:
		frames: List of Path objects for frames to process.
		model_name: Name of the ViT model to use.
		model_input_size: Target square size for model input (typically 224).
		cache_dir: Directory for caching embeddings.
		normalize: Whether to L2-normalize patch embeddings.
		force: Force recompute even if cache exists.
		batch_size: Number of images to process at once.
		cache_key: Pre-computed cache key (optional).
		progress: Callback for progress updates (done, total).
		transform_config: Image transform configuration (crop/pad/scale with alignment).

	Returns: Tuple of (embeddings, cache_key, from_cache)
	"""
	if transform_config is None:
		transform_config = DEFAULT_TRANSFORM_CONFIG

	cache_key = cache_key or compute_cache_key(model_name, frames, normalize, transform_config)

	if not force:
		embeddings = load_cached_embeddings(cache_dir, cache_key, model_name, normalize)
		if embeddings is not None:
			return embeddings, cache_key, True

	torch = _get_torch()
	device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

	model = load_model(model_name, device)
	transform = get_transform(model_input_size, transform_config)
	Image = _get_pil()
	all_embeddings_list = []

	for start in range(0, len(frames), batch_size):
		batch_paths = frames[start:start + batch_size]
		batch_images = []
		for path in batch_paths:
			with Image.open(path) as img:
				img_tensor = transform(img.convert('RGB'))
				batch_images.append(img_tensor)

		batch_tensor = torch.stack(batch_images).to(device)
		embeddings = extract_patch_embeddings(model, batch_tensor, normalize=normalize)
		all_embeddings_list.append(embeddings.cpu())

		if progress is not None:
			progress(min(start + batch_size, len(frames)), len(frames))

	all_embeddings = torch.cat(all_embeddings_list, dim=0)
	save_embeddings_cache(cache_dir, cache_key, all_embeddings, model_name, normalize, len(frames))

	return all_embeddings, cache_key, False


class FrameSelectionData:
	"""Container for frame selection data and operations."""
	
	def __init__(
		self,
		frames: List[Path],
		total_change: np.ndarray,
		concentration: np.ndarray,
		entropy: np.ndarray
	):
		self.frames = frames
		self.total_change_raw = total_change
		self.concentration_raw = concentration
		self.entropy_raw = entropy
		
		# Smoothed versions (updated when temporal_window changes)
		self.total_change = total_change.copy()
		self.concentration = concentration.copy()
		self.entropy = entropy.copy()
		self.temporal_window = 1
	
	def apply_smoothing(self, window: int):
		"""Apply temporal smoothing with given window."""
		self.temporal_window = window
		self.total_change = temporal_smoothing(self.total_change_raw, window)
		self.concentration = temporal_smoothing(self.concentration_raw, window)
		self.entropy = temporal_smoothing(self.entropy_raw, window)
	
	def get_percentile_thresholds(
		self,
		concentration_percentile: float,
		total_change_percentile: float,
		entropy_percentile: float
	) -> Tuple[float, float, float]:
		"""Convert percentiles to absolute threshold values."""
		conc_thresh = np.percentile(self.concentration, concentration_percentile)
		total_thresh = np.percentile(self.total_change, total_change_percentile)
		entropy_thresh = np.percentile(self.entropy, entropy_percentile)
		return conc_thresh, total_thresh, entropy_thresh
	
	def select_frames(
		self,
		concentration_percentile: float,
		total_change_percentile: float,
		entropy_percentile: float,
		min_spacing: int
	) -> List[int]:
		"""Select frames using percentile-based thresholds."""
		conc_thresh, total_thresh, entropy_thresh = self.get_percentile_thresholds(
			concentration_percentile, total_change_percentile, entropy_percentile
		)
		return select_candidate_frames(
			self.total_change,
			self.concentration,
			self.entropy,
			conc_thresh,
			total_thresh,
			entropy_thresh,
			min_spacing
		)
	
	def get_frame_path(self, index: int) -> Path:
		"""Get the file path for a frame by index."""
		return self.frames[index]
	
	def get_frame_filename(self, index: int) -> str:
		"""Get the filename for a frame by index."""
		return self.frames[index].name
	
	def get_frame_number(self, index: int) -> int:
		"""Get display frame number (1-based) for a frame by index."""
		return index + 1
	
	def __len__(self):
		return len(self.frames)


def create_frame_selection_data(
	frames: List[Path],
	embeddings: "torch.Tensor",
	temporal_window: int = 5
) -> FrameSelectionData:
	"""
	Create FrameSelectionData from frames and embeddings.
	
	Helper to build selection data after loading/computing embeddings.
	"""
	patch_changes = compute_patch_changes(embeddings)
	total_change, concentration, entropy = compute_change_metrics(patch_changes)
	
	data = FrameSelectionData(frames, total_change, concentration, entropy)
	data.apply_smoothing(temporal_window)
	
	return data
