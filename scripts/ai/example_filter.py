#!/usr/bin/env python3
"""
Example Filter Script for Frame Selection Visualizer

This script implements custom filtering logic for datasets with:
1. Missing/incomplete labels - some frames should have at least 2 bboxes
2. Ambiguous transitions - certain label transitions have no objective ground truth
3. Noisy annotations - very small bboxes that should be excluded

Usage:
	python frame_selection_visualizer.py --input-dir path/to/frames --filter-script example_filter.py

The filter_frames function is called after embeddings load (and after annotations if provided).
"""

from pathlib import Path
from typing import List, Dict, Set

from frame_selection_visualizer_api import ForceState, BBox, FrameInfo


def filter_frames(frames: List[FrameInfo]) -> Dict[int, ForceState]:
	"""
	Apply custom filtering logic to frames.
	
	Args:
		frames: List of FrameInfo for all frames in sequence order
		
	Returns:
		Dict mapping frame idx -> ForceState
		Only return entries you want to force (don't need NEUTRAL entries)
	"""
	result = {}
	
	# Track statistics for summary
	stats = {
		'incomplete_annotations': [],
		'transitions': [],
		'tiny_bboxes': []
	}
	
	# Rule 1: Exclude frames with incomplete annotations
	# Dataset requires at least 2 bboxes per frame (based on object count)
	MIN_BBOXES = 2
	
	for frame in frames:
		if len(frame.bboxes) < MIN_BBOXES:
			stats['incomplete_annotations'].append((frame.idx, len(frame.bboxes)))
			result[frame.idx] = ForceState.UNSELECT
	
	# Rule 2: Detect and skip transition frames between certain label states
	# These transitions are subjectively ambiguous with no clear ground truth
	TRANSITION_LABELS = [
		{'close', 'special'},       # Ambiguous transition between close and special states
		{'away', 'right-back'},     # Unclear when object transitions from away to right-back
		{'away', 'left-back'},      # Unclear when object transitions from away to left-back
	]
	
	for i in range(len(frames) - 1):
		curr_frame = frames[i]
		next_frame = frames[i + 1]
		
		# Skip if either frame has no annotations
		if not curr_frame.labels or not next_frame.labels:
			continue
		
		curr_labels = set(curr_frame.labels)
		next_labels = set(next_frame.labels)
		
		# Check if this is a transition between conflicting states
		for transition_pair in TRANSITION_LABELS:
			# Check if we're transitioning between the two states in the pair
			curr_has = curr_labels & transition_pair
			next_has = next_labels & transition_pair
			
			# If current has one state and next has a different state from the pair
			if curr_has and next_has and curr_has != next_has:
				# Exclude both frames in the transition
				result[curr_frame.idx] = ForceState.UNSELECT
				result[next_frame.idx] = ForceState.UNSELECT
				stats['transitions'].append((curr_frame.idx, next_frame.idx, curr_has, next_has))
	
	# Rule 3: Skip frames with very small bboxes (likely annotation noise)
	# This could be added to UI in future, but kept in filter for now
	MIN_BBOX_SIZE = 3.0  # percentage (width or height)
	
	for frame in frames:
		for bbox in frame.bboxes:
			if bbox.w < MIN_BBOX_SIZE or bbox.h < MIN_BBOX_SIZE:
				stats['tiny_bboxes'].append((frame.idx, bbox.w, bbox.h))
				result[frame.idx] = ForceState.UNSELECT
				break  # One tiny bbox is enough to exclude the frame
	
	# Print summary
	frames_with_annotations = sum(1 for f in frames if f.bboxes)
	total_exclusions = sum(1 for s in result.values() if s == ForceState.UNSELECT)
	
	print(f"\n=== Filter Summary ===")
	print(f"Total frames: {len(frames)} ({frames_with_annotations} annotated)")
	print(f"Excluded frames: {total_exclusions}")
	print(f"  - Incomplete annotations (<{MIN_BBOXES} bboxes): {len(stats['incomplete_annotations'])}")
	print(f"  - Ambiguous transitions: {len(stats['transitions'])} pairs ({len(stats['transitions'])*2} frames)")
	print(f"  - Tiny bboxes (<{MIN_BBOX_SIZE}%): {len(stats['tiny_bboxes'])}")
	print(f"=======================\n")
	
	return result


if __name__ == "__main__":
	# Test the filter with mock data using actual dataset labels
	print("Testing filter with mock data...\n")
	
	mock_frames = [
		# Normal frame with 2 bboxes
		FrameInfo(0, 1, Path("frame_001.jpg"), 
				  [BBox(['close'], 50, 50, 10, 10), BBox(['away'], 30, 30, 8, 8)], False),
		# Incomplete annotation - only 1 bbox (should be excluded)
		FrameInfo(1, 2, Path("frame_002.jpg"), 
				  [BBox(['close'], 50, 50, 10, 10)], False),
		# Transition from 'close' to 'special' (should be excluded)
		FrameInfo(2, 3, Path("frame_003.jpg"), 
				  [BBox(['close'], 50, 50, 10, 10), BBox(['away'], 30, 30, 8, 8)], False),
		FrameInfo(3, 4, Path("frame_004.jpg"), 
				  [BBox(['special'], 50, 50, 10, 10), BBox(['away'], 30, 30, 8, 8)], False),
		# Frame with tiny bbox (should be excluded)
		FrameInfo(4, 5, Path("frame_005.jpg"), 
				  [BBox(['close'], 50, 50, 2, 2), BBox(['away'], 30, 30, 8, 8)], False),
		# Normal frame
		FrameInfo(5, 6, Path("frame_006.jpg"), 
				  [BBox(['away'], 50, 50, 10, 10), BBox(['left-back'], 30, 30, 8, 8)], False),
	]
	
	filtered = filter_frames(mock_frames)
	
	print("Excluded frames:")
	for idx, state in sorted(filtered.items()):
		if state == ForceState.UNSELECT:
			print(f"  Frame {idx}: {mock_frames[idx].labels}")
	print(f"Frames to exclude: {sum(1 for s in filtered.values() if s == ForceState.UNSELECT)}")
