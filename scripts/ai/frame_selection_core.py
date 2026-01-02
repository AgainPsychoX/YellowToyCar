#!/usr/bin/env python3
"""
Frame Selection Core Logic

Shared utilities for ViT-based frame selection, used by both CLI and GUI tools.
"""

import hashlib
import json
from pathlib import Path
from typing import List, Tuple, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
	import torch

# Lazy imports
_torch = None


def _get_torch():
	"""Lazy import torch."""
	global _torch
	if _torch is None:
		import torch
		_torch = torch
	return _torch


# Constants
SUPPORTED_EXTENSIONS = ('.jpg', '.jpeg', '.png')
EPSILON = 1e-8


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
			continue
		
		frames.append((frame_num, entry.name, entry))
	
	frames.sort(key=lambda x: x[0])
	return frames


def compute_cache_key(
	model_name: str,
	frame_filenames: List[str],
	normalize: bool
) -> str:
	"""Compute a hash-based cache key for embeddings."""
	hasher = hashlib.sha256()
	hasher.update(model_name.encode('utf-8'))
	hasher.update(b'\x00')
	hasher.update(str(normalize).encode('utf-8'))
	hasher.update(b'\x00')
	for fname in sorted(frame_filenames):
		hasher.update(fname.encode('utf-8'))
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


class FrameSelectionData:
	"""Container for frame selection data and operations."""
	
	def __init__(
		self,
		frames: List[Tuple[int, str, Path]],
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
		return self.frames[index][2]
	
	def get_frame_filename(self, index: int) -> str:
		"""Get the filename for a frame by index."""
		return self.frames[index][1]
	
	def __len__(self):
		return len(self.frames)


def load_frame_data(
	input_dir: Path,
	model_name: str = 'vit_small_patch16_224.dino',
	normalize: bool = True,
	temporal_window: int = 5
) -> Optional[FrameSelectionData]:
	"""
	Load frame data from cache.
	
	Returns FrameSelectionData if cache exists, None otherwise.
	"""
	frames = collect_frames(input_dir)
	if len(frames) < 2:
		return None
	
	cache_dir = input_dir / '.embedding_cache'
	frame_filenames = [f[1] for f in frames]
	cache_key = compute_cache_key(model_name, frame_filenames, normalize)
	
	embeddings = load_cached_embeddings(cache_dir, cache_key, model_name, normalize)
	if embeddings is None:
		return None
	
	patch_changes = compute_patch_changes(embeddings)
	total_change, concentration, entropy = compute_change_metrics(patch_changes)
	
	data = FrameSelectionData(frames, total_change, concentration, entropy)
	data.apply_smoothing(temporal_window)
	
	return data
