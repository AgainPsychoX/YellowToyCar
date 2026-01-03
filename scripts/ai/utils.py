"""
Shared utilities for frame selection and preparation scripts.
"""

import shutil
from pathlib import Path
from typing import List, Optional


def prepare_output_dir(raw_path: str, overwrite_globs: Optional[List[str]] = None) -> None:
	"""Ensure output directory exists; clear using globs if provided."""
	path = Path(raw_path)
	
	# Create directory if it doesn't exist
	if not path.exists():
		path.mkdir(parents=True)
		return
	
	# Check if directory is empty
	try:
		next(path.iterdir())
	except StopIteration:
		# Directory is empty
		return
	
	# Directory is non-empty
	if overwrite_globs is None:
		raise FileExistsError(f"Output directory '{path}' is not empty. Use --overwrite to allow overwriting.")
	
	# Delete matching files/directories
	for pattern in overwrite_globs:
		for match in path.glob(pattern):
			if match == path:
				# Avoid deleting the output directory itself
				continue
			
			if match.is_file() or match.is_symlink():
				match.unlink()
			elif match.is_dir():
				shutil.rmtree(match)
			else:
				raise ValueError(f"Unexpected file type: {match}")
