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


def estimate_optimal_batch_size(device_type: str = 'auto') -> int:
	"""
	Estimate optimal batch size based on available memory.
	
	Args:
		device_type: 'auto' (detect), 'cuda', or 'cpu'
	
	Returns:
		Recommended batch size between 1 and 64.
	"""
	if device_type == 'auto':
		torch = _get_torch()
		device_type = 'cuda' if torch.cuda.is_available() else 'cpu'
	
	try:
		if device_type == 'cuda':
			torch = _get_torch()
			props = torch.cuda.get_device_properties(0)
			gpu_memory_gb = props.total_memory / (1024 ** 3)
			
			# Reserve 1GB for model and misc, estimate ~200MB per image
			available_for_batches = max(gpu_memory_gb - 1.0, 0.5)
			batch_size = max(1, int(available_for_batches / 0.2))
			return min(batch_size, 64)
		else:
			# CPU: use less aggressive batch size, estimate ~300MB per image
			try:
				import psutil
				available_mb = psutil.virtual_memory().available / (1024 ** 2)
				# Reserve 2GB for system/other apps
				available_for_batches_mb = max(available_mb - 2048, 512)
				batch_size = max(1, int(available_for_batches_mb / 300))
				return min(batch_size, 32)
			except Exception:
				# Fallback if psutil unavailable
				return 4
	except Exception:
		# Default fallback
		return 8


def get_common_timm_models() -> List[str]:
	"""Return list of common ViT models available in timm."""
	return [
		'vit_small_patch16_224.dino',
		'vit_base_patch16_224.dino',
		'vit_base_patch16_224',
		'vit_small_patch16_224',
		'vit_large_patch16_224.augreg_in21k_ft_in1k',
	]


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
	mode: TransformMode = TransformMode.PAD
	alignment: Alignment = Alignment.CENTER
	# Fill color for padding: use ImageNet mean by default for better compatibility
	fill: Tuple[float, float, float] = (0.485, 0.456, 0.406)

	@classmethod
	def from_strings(cls, mode: str, alignment: str = 'center') -> 'TransformConfig':
		"""Create config from CLI string arguments."""
		return cls(
			mode=TransformMode(mode.lower()),
			alignment=Alignment(alignment.lower()),
		)
	
	def to_dict(self) -> dict:
		return {
			'mode': self.mode.value,
			'alignment': self.alignment.value,
			'fill': list(self.fill),
		}
	
	@classmethod
	def from_dict(cls, data: dict) -> 'TransformConfig':
		return cls(
			mode=TransformMode(data['mode']),
			alignment=Alignment(data['alignment']),
			fill=tuple(data['fill']),
		)


# Constants
SUPPORTED_EXTENSIONS = ('.jpg', '.jpeg', '.png')
EPSILON = 1e-8
DEFAULT_MODEL = 'vit_base_patch16_224.dino'
IMAGE_SIZE = 224
DEFAULT_TRANSFORM_CONFIG = TransformConfig()


@dataclass
class EmbeddingConfig:
	"""Bundled settings for embedding computation and caching."""
	model: str = DEFAULT_MODEL
	model_input_size: int = IMAGE_SIZE
	normalize: bool = True
	transform: TransformConfig = field(default_factory=TransformConfig)

	def to_dict(self) -> dict:
		return {
			'model': self.model,
			'model_input_size': self.model_input_size,
			'normalize': self.normalize,
			'transform': self.transform.to_dict(),
		}
	
	@classmethod
	def from_dict(cls, data: dict) -> 'EmbeddingConfig':
		return cls(
			model=data['model'],
			model_input_size=data.get('model_input_size', IMAGE_SIZE),
			normalize=data['normalize'],
			transform=TransformConfig.from_dict(data['transform']),
		)
	
	def __eq__(self, other) -> bool:
		"""Compare two EmbeddingConfig instances for equality."""
		if not isinstance(other, EmbeddingConfig):
			return False
		return (
			self.model == other.model and
			self.model_input_size == other.model_input_size and
			self.normalize == other.normalize and
			self.transform == other.transform
		)


@dataclass
class EmbeddingMeta:
	"""Metadata for cached embeddings."""
	config: EmbeddingConfig
	frame_count: int
	embedding_shape: Tuple[int, int, int]
	version: int

	# Non-serializable fields (set after loading from disk)
	path: Optional[Path] = field(default=None, init=False, repr=False)

	def cache_key(self) -> Optional[str]:
		"""
		Return cache key string derived from the meta file path (if set).

		Extracts the key from a filename like 'embeddings_<key>.json'. Returns None
		if `self.path` is not set.
		"""
		if self.path is None:
			return None
		return self.path.stem.replace('embeddings_', '')
	
	def to_dict(self) -> dict:
		"""Serialize to dictionary for JSON storage."""
		return {
			'version': self.version,
			'config': self.config.to_dict(),
			'frame_count': self.frame_count,
			'embedding_shape': list(self.embedding_shape),
		}
	
	@classmethod
	def from_dict(cls, data: dict) -> 'EmbeddingMeta':
		"""Deserialize from dictionary."""
		return cls(
			config=EmbeddingConfig.from_dict(data['config']),
			frame_count=data['frame_count'],
			embedding_shape=tuple(data['embedding_shape']),
			version=data['version'],
		)
	
	def __eq__(self, other) -> bool:
		"""Compare metadata for equality."""
		if not isinstance(other, EmbeddingMeta):
			return False
		return (
			self.config.model == other.config.model and
			self.config.model_input_size == other.config.model_input_size and
			self.config.normalize == other.config.normalize and
			self.config.transform == other.config.transform and
			self.version == other.version
		)


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

def compute_cache_key(config: EmbeddingConfig, frames: List[Path]) -> str:
	"""
	Compute a deterministic cache hash key for embeddings given an EmbeddingConfig 
	and list of frames paths (uses filenames only).
	"""
	# Single SHA1 hasher: serialize config fields and frame filenames deterministically
	hasher = hashlib.sha1(usedforsecurity=False)
	# Config fields
	hasher.update(config.model.encode('utf-8'))
	hasher.update(b'\x00')
	hasher.update(str(config.model_input_size).encode('utf-8'))
	hasher.update(b'\x00')
	hasher.update(str(bool(config.normalize)).encode('utf-8'))
	hasher.update(b'\x00')
	# Transform fields
	transform = config.transform
	transform_repr = (
		f"{transform.mode.value}|{transform.alignment.value}|"
		f"{','.join(str(c) for c in transform.fill)}"
	)
	hasher.update(transform_repr.encode('utf-8'))
	# Append delimiter and frame filenames (sorted)
	hasher.update(b'\x00')
	for path in sorted(frames, key=lambda p: p.name):
		hasher.update(path.name.encode('utf-8'))
		hasher.update(b'\x00')
	return hasher.hexdigest()


def get_cache_path(cache_dir: Path, cache_key: str) -> Tuple[Path, Path]:
	"""Get paths for cache tensor and metadata files."""
	tensor_path = cache_dir / f"embeddings_{cache_key}.pt"
	meta_path = cache_dir / f"embeddings_{cache_key}.json"
	return tensor_path, meta_path


def find_existing_cache_in_dir(cache_dir: Path) -> List[EmbeddingMeta]:
	"""Find all valid caches in directory. Empty list if none found."""
	if not cache_dir.exists():
		return []
	
	results = []
	try:
		for meta_path in sorted(cache_dir.glob("embeddings_*.json")):
			meta = load_cached_embeddings_meta(meta_path)
			if meta is not None:
				results.append(meta)
	except Exception:
		pass
	
	return results


def format_cache_info(meta: EmbeddingMeta) -> str:
	"""Format cache metadata as human-readable string."""
	model = meta.config.model
	normalize = meta.config.normalize
	frame_count = meta.frame_count
	shape = meta.embedding_shape
	
	norm_str = "normalized" if normalize else "not normalized"
	shape_str = f"{shape[0]}x{shape[1]}x{shape[2]}"
	
	return f"{model} ({frame_count} frames, {norm_str}, shape: {shape_str})"


def clear_other_caches(cache_dir: Path, keep_key: str) -> None:
	"""Remove all cache files except the one with keep_key."""
	if not cache_dir.exists():
		return
	
	try:
		for pt_file in cache_dir.glob("embeddings_*.pt"):
			key = pt_file.stem.replace('embeddings_', '')
			if key != keep_key:
				pt_file.unlink()
		
		for json_file in cache_dir.glob("embeddings_*.json"):
			key = json_file.stem.replace('embeddings_', '')
			if key != keep_key:
				json_file.unlink()
	except Exception as exc:
		print(f"Warning: Failed to clear old caches: {exc}")


def load_cached_embeddings_meta(cache_path: Path) -> Optional[EmbeddingMeta]:
	"""Load embedding metadata from cache JSON file."""
	if not cache_path.exists():
		return None
	
	try:
		with open(cache_path, 'r') as f:
			data = json.load(f)

		if data.get('version') != 1:
			return None
		
		meta = EmbeddingMeta.from_dict(data)
		meta.path = cache_path
		return meta
		
	except Exception:
		return None


def load_cached_embeddings_data(cache_path: Path) -> Optional["torch.Tensor"]:
	"""Load embeddings tensor from cache PT file."""
	# Convert .json path to .pt path
	pt_path = cache_path.with_suffix('.pt')
	
	if not pt_path.exists():
		return None
	
	try:
		torch = _get_torch()
		embeddings = torch.load(pt_path, map_location='cpu', weights_only=True)
		return embeddings
	except Exception:
		return None


def save_embeddings_cache(
	cache_dir: Path,
	frames: List[Path],
	config: EmbeddingConfig,
	embeddings: "torch.Tensor",
	clear_others: bool = True,
) -> None:
	"""Persist embeddings tensor and metadata to cache directory."""
	cache_dir.mkdir(parents=True, exist_ok=True)
	cache_key = compute_cache_key(config, frames)
	tensor_path, meta_path = get_cache_path(cache_dir, cache_key)

	try:
		# Clear other cache files to save space (embeddings can be huge)
		if clear_others:
			clear_other_caches(cache_dir, cache_key)
		
		torch = _get_torch()
		torch.save(embeddings, tensor_path)

		meta = EmbeddingMeta(
			config=config,
			frame_count=len(frames),
			embedding_shape=tuple(embeddings.shape),
			version=1,
		)
		
		with open(meta_path, 'w') as f:
			json.dump(meta.to_dict(), f, indent='\t')
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
			_AlignedPad(image_size, config.alignment, config.fill),
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
	cache_dir: Path,
	frames: List[Path],
	config: EmbeddingConfig,
	force: bool = False,
	batch_size: int = 8,
	progress: Callable[[int, int], None] | None = None,
	clear_others: bool = True,
) -> Tuple["torch.Tensor", bool]:
	"""
	Load embeddings from cache or compute them if missing.

	Args:
		cache_dir: Directory for caching embeddings.
		frames: List of Path objects for frames to process.
		config: Embedding configuration (model, normalize, transform, input size).
		force: Force recompute even if cache exists.
		batch_size: Number of images to process at once.
		progress: Callback for progress updates (done, total).
		clear_others: If True, remove other cache files after saving. Default True.

	Returns: Tuple of (embeddings, from_cache)
	"""
	if not force:
		# Try to load from cache
		cache_key = compute_cache_key(config, frames)
		tensor_path, meta_path = get_cache_path(cache_dir, cache_key)
		
		if meta_path.exists():
			meta = load_cached_embeddings_meta(meta_path)
			if meta is not None:
				# Verify config matches (sanity, cache key already should be enough)
				if meta.config == config:
					# Load embeddings data
					embeddings = load_cached_embeddings_data(tensor_path)
					if embeddings is not None:
						return embeddings, True

	torch = _get_torch()
	device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

	model = load_model(config.model, device)
	transform = get_transform(config.model_input_size, config.transform)
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
		embeddings = extract_patch_embeddings(model, batch_tensor, normalize=config.normalize)
		all_embeddings_list.append(embeddings.cpu())

		if progress is not None:
			progress(min(start + batch_size, len(frames)), len(frames))

	all_embeddings = torch.cat(all_embeddings_list, dim=0)
	save_embeddings_cache(cache_dir, frames, config, all_embeddings, clear_others=clear_others)

	return all_embeddings, False


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
