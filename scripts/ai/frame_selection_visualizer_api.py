"""
Frame Selection Visualizer - Filter Script API

This module defines the public API for custom filter scripts.
Import these types in your filter script to define filtering logic.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List


class ForceState(Enum):
	"""Frame selection force state for filter scripts."""
	NEUTRAL = 0      # Let algorithm decide
	SELECT = 1       # Force include
	UNSELECT = 2     # Force exclude


@dataclass
class BBox:
	"""Single bounding box with its labels."""
	labels: List[str]     # Can be multiple labels per bbox
	x: float             # Center x as percentage (0-100)
	y: float             # Center y as percentage (0-100)
	w: float             # Width as percentage (0-100)
	h: float             # Height as percentage (0-100)


@dataclass
class FrameInfo:
	"""Information about a single frame for filter scripts."""
	idx: int                    # Index in frames list
	frame_number: int           # Frame number from filename
	path: Path                  # Full path to image
	bboxes: List[BBox]          # Empty list if no annotations
	is_keyframe: bool           # Whether this is a keyframe
	
	@property
	def labels(self) -> List[str]:
		"""All unique labels present in this frame (convenience property)."""
		result = set()
		for bbox in self.bboxes:
			result.update(bbox.labels)
		return sorted(result)
