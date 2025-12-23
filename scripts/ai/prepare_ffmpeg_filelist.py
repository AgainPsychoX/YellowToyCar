#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path
import re
from typing import List, Optional, Tuple


def find_image_files(frames_dir: Path) -> List[Path]:
	extensions = ('.jpg', '.jpeg', '.JPG', '.JPEG')
	return [p for p in frames_dir.iterdir() if p.suffix in extensions and p.is_file()]


def extract_number(fn: str) -> Optional[int]:
	# Try to get the first group of digits in the filename
	m = re.search(r"(\d+)", fn)
	if not m:
		return None
	try:
		return int(m.group(1))
	except Exception:
		return None


def sort_files(files: List[Path]) -> Tuple[List[Path], bool]:
	"""Sort by numeric token if possible, else lexicographically.
	Returns (sorted_files, used_numeric_sort)"""
	nums = [(extract_number(p.name), p) for p in files]
	if all(n is not None for n, _ in nums):
		ordered = [p for _, p in sorted(nums, key=lambda x: x[0])]
		return ordered, True
	# fallback: lexicographic sort
	ordered = sorted(files, key=lambda p: p.name)
	return ordered, False


def find_missing_numbers(sorted_with_nums: List[Tuple[int, Path]]) -> List[int]:
	if not sorted_with_nums:
		return []
	nums = [n for n, _ in sorted_with_nums]
	mn, mx = min(nums), max(nums)
	s = set(nums)
	return [i for i in range(mn, mx + 1) if i not in s]


def write_filelist(filelist_path: Path, files: List[Path], duration: float, verbose: bool = False) -> None:
	if not files:
		raise ValueError("No image files to write to filelist")

	def q_single(s: str) -> str:
		# Use single quotes around paths and escape any single-quote inside
		# Most file names won't have an apostrophe; this is just defensive.
		escaped = s.replace("'", "\\'")
		return "'" + escaped + "'"

	def absolute_posix_path(p: Path) -> str:
		"""Return an absolute path with forward slashes suitable for ffmpeg.
		Converts MSYS/Cygwin '/d/...' and Windows 'd:/...' to 'D:/...'."""
		rp = p.resolve()
		# Normalize to a forward-slash string first
		s = str(rp).replace('\\', '/')

		# MSYS/Cygwin-style leading '/d/...' -> 'D:/...'
		m = re.match(r'^/([A-Za-z])/(.*)', s)
		if m:
			s = f"{m.group(1).upper()}:/{m.group(2)}"
		else:
			# Windows-style 'd:/...' -> 'D:/...'
			m2 = re.match(r'^([A-Za-z]):/(.*)', s)
			if m2:
				s = f"{m2.group(1).upper()}:/{m2.group(2)}"
		# Otherwise leave POSIX paths as-is
		return s

	base = filelist_path.resolve().parent

	with filelist_path.open('w', encoding='utf-8') as out:
		for p in files:
			# Try to use a path relative to the filelist location when possible.
			# Use os.path.relpath to support Windows behaviour across platforms.
			use_rel = False
			rel_text = None
			try:
				rel_text = os.path.relpath(p.resolve(), start=base)
				# Normalize separators to forward slashes
				rel_text = rel_text.replace('\\', '/')
				# If relpath starts with '..' it's still a valid relative path; still usable
				use_rel = True
			except Exception:
				use_rel = False

			if use_rel and rel_text:
				path_text = rel_text
				which = 'relative'
			else:
				path_text = absolute_posix_path(p)
				which = 'absolute'

			out.write(f"file {q_single(path_text)}\n")
			out.write(f"duration {duration:.6f}\n")
			if verbose:
				print(f"wrote {which}: {path_text}")
		# Append last file again (ffmpeg concat demuxer quirk) without duration
		last = files[-1]
		# Use same decision logic for the final entry
		try:
			rel_text = os.path.relpath(last.resolve(), start=base).replace('\\', '/')
		except Exception:
			rel_text = None
		if rel_text:
			last_path = rel_text
			last_which = 'relative'
		else:
			last_path = absolute_posix_path(last)
			last_which = 'absolute'
		out.write(f"file {q_single(last_path)}\n")
		if verbose:
			print(f"wrote {last_which}: {last_path}")


def main(argv: Optional[List[str]] = None) -> int:
	epilog = '''
This writes lines like:
    file 'path/to/frame0001.jpg'
    duration 0.083333
    ...

Note: the concat demuxer requires the last file to be listed again; this script appends it automatically.

Example ffmpeg command (concat demuxer):
    ffmpeg -f concat -safe 0 -i filelist.txt -vsync vfr -c:v libx264 -pix_fmt yuv420p output.mp4
'''
	p = argparse.ArgumentParser(
		description="Write an FFmpeg concat filelist with per-frame durations",
		epilog=epilog,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument('frames_dir', type=Path, help='Directory containing frame images (jpg/jpeg)')
	p.add_argument('output_file', type=Path, help='Path to write the filelist (e.g., filelist.txt)')
	p.add_argument('framerate', type=float, help='Expected framerate (frames per second)')
	p.add_argument('--allow-gaps', action='store_true', help='Do not fail on missing sequence numbers')
	p.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

	args = p.parse_args(argv)

	frames_dir: Path = args.frames_dir
	out_file: Path = args.output_file
	framerate: float = args.framerate

	if not frames_dir.exists() or not frames_dir.is_dir():
		print(f"Error: frames directory '{frames_dir}' does not exist or is not a directory", file=sys.stderr)
		return 2

	files = find_image_files(frames_dir)
	if not files:
		print(f"Error: no image files (*.jpg, *.jpeg) found in {frames_dir}", file=sys.stderr)
		return 3

	sorted_files, used_num = sort_files(files)

	if args.verbose:
		print(f"Found {len(files)} image files; using {'numeric' if used_num else 'lexicographic'} sort")

	if used_num:
		sorted_with_nums = [(extract_number(p.name), p) for p in sorted_files]
		missing = find_missing_numbers(sorted_with_nums)
		if missing:
			print(f"Warning: missing {len(missing)} frame(s): {missing[:10]}{'...' if len(missing)>10 else ''}")
			if not args.allow_gaps:
				print("Use --allow-gaps to write filelist despite missing frames", file=sys.stderr)
				return 4

	duration = 1.0 / framerate if framerate > 0 else 0.0
	write_filelist(out_file, sorted_files, duration)

	print(f"Wrote filelist to {out_file} (frames={len(sorted_files)}, duration={duration:.6f}s)")
	if args.verbose:
		print("Example ffmpeg command (concat demuxer):")
		print(f"  ffmpeg -f concat -safe 0 -i {out_file} -vsync vfr -c:v libx264 -pix_fmt yuv420p output.mp4")

	return 0


if __name__ == '__main__':
	raise SystemExit(main())
