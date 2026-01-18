#!/usr/bin/env python3
"""
Frame Selection Visualizer

Interactive GUI for tuning frame selection parameters using ViT embeddings,
with visualization of selected frames and Label Studio annotations.
"""

import json
import sys
import argparse
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Set, Optional, Tuple

import threading

from PySide6.QtWidgets import (
	QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
	QLabel, QSlider, QPushButton, QScrollArea, QGridLayout,
	QSplitter, QProgressDialog, QMessageBox, QGroupBox,
	QSpinBox, QDoubleSpinBox, QAbstractSpinBox, QCheckBox, QStyleFactory,
	QFileDialog, QDialog, QDialogButtonBox, QFormLayout, QComboBox, QLineEdit
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QRect, QPoint, QSettings
from PySide6.QtGui import (
	QPixmap, QColor, QPainter, QPen, QFont, QShortcut, QKeySequence,
	QAction, QActionGroup, QBrush, QPolygon
)

from utils import prepare_output_dir
from frame_selection_core import (
	SelectionParams,
	FrameSelectionData,
	collect_frames,
	load_cached_embeddings_data,
	create_frame_selection_data,
	find_existing_cache_in_dir,
	estimate_optimal_batch_size,
	format_cache_info,
	clear_other_caches,
	DEFAULT_MODEL,
	TransformConfig,
	EmbeddingConfig,
	EmbeddingMeta,
)


# Global settings instance (initialized in main())
settings: Optional[QSettings] = None


# ==============================================================================
# Annotation Data Structures and Helpers (adapted from prepare_yolo_dataset.py)
# ==============================================================================

# Default label colors for common annotations (for now hardcoded)
DEFAULT_LABEL_COLORS = {
	"none": QColor(114, 114, 121),
	"close": QColor(255, 0, 0),
	"away": QColor(0, 255, 0),
	"special": QColor(238, 129, 234),
	"left-front": QColor(0, 26, 128),
	"left-back": QColor(0, 26, 128),
	"right-front": QColor(0, 225, 255),
	"right-back": QColor(0, 225, 255),
}


def _collect_tracks(task_data: dict) -> List[dict]:
	"""Extract videorectangle tracks from Label Studio task data."""
	tracks = []
	annotations = task_data.get('annotations', [])
	if not annotations:
		return tracks
	for ann in annotations[0].get('result', []):
		if ann.get('type') == 'videorectangle':
			val = ann.get('value', {})
			seq = val.get('sequence', [])
			labels = val.get('labels', [])
			if seq and labels:
				tracks.append({'sequence': seq, 'labels': labels})
	return tracks


def build_frame_bboxes(
	tracks: List[dict], 
	frame_offset: int = 1
) -> Tuple[Dict[int, List[Tuple[List[str], float, float, float, float]]], Set[int]]:
	"""
	Return mapping frame_num (1-based with offset) -> list of (labels, x%, y%, w%, h%),
	and a set of keyframe indices (frame numbers that are actual keyframes in annotation).

	For each keyframe:
	- If disabled: add as a single frame (no interpolation)
	- If enabled: interpolate to next keyframe (enabled or disabled)
	- Last enabled keyframe with no successor: add as single frame
	
	Args:
		tracks: List of track dicts with 'sequence' and 'labels'
		frame_offset: Offset to add to frame numbers (default 1 for Label Studio 0-based to 1-based)
	"""
	frame_bboxes: Dict[int, List[Tuple[List[str], float, float, float, float]]] = {}
	keyframe_frame_numbers: Set[int] = set()

	for track in tracks:
		seq = sorted(track['sequence'], key=lambda s: s.get('frame', 0))
		labels = track['labels']

		for i, kf in enumerate(seq):
			kf_frame_num = kf['frame'] + frame_offset
			keyframe_frame_numbers.add(kf_frame_num)
			
			if not kf.get('enabled', False):
				# Disabled keyframe: just add it as a single frame
				frame_bboxes.setdefault(kf_frame_num, []).append(
					(labels, kf['x'], kf['y'], kf['width'], kf['height']))
			else:
				# Enabled keyframe: interpolate to next keyframe if it exists
				if i + 1 < len(seq):
					kf2 = seq[i + 1]
					a_frame = kf['frame']
					b_frame = kf2['frame']
					for f in range(a_frame, b_frame):
						t = (f - a_frame) / (b_frame - a_frame) if b_frame > a_frame else 0.0
						x = kf['x'] + (kf2['x'] - kf['x']) * t
						y = kf['y'] + (kf2['y'] - kf['y']) * t
						w = kf['width'] + (kf2['width'] - kf['width']) * t
						h = kf['height'] + (kf2['height'] - kf['height']) * t
						frame_bboxes.setdefault(f + frame_offset, []).append((labels, x, y, w, h))
				else:
					# Last keyframe: just add it
					frame_bboxes.setdefault(kf_frame_num, []).append(
						(labels, kf['x'], kf['y'], kf['width'], kf['height']))

	return frame_bboxes, keyframe_frame_numbers


@dataclass
class AnnotationData:
	"""Container for loaded Label Studio annotations."""
	json_path: Path
	task_id: int
	frame_offset: int
	# frame_number (1-based) -> list of (labels, x%, y%, w%, h%)
	frame_bboxes: Dict[int, List[Tuple[List[str], float, float, float, float]]] = field(default_factory=dict)
	# Set of frame numbers that are keyframes
	keyframe_frame_numbers: Set[int] = field(default_factory=set)
	# label -> QColor
	label_colors: Dict[str, QColor] = field(default_factory=dict)
	
	def get_bboxes_for_frame(self, frame_number: int) -> List[Tuple[List[str], float, float, float, float]]:
		"""Get bounding boxes for a specific frame number."""
		return self.frame_bboxes.get(frame_number, [])
	
	def is_keyframe(self, frame_number: int) -> bool:
		"""Check if a frame number is a keyframe."""
		return frame_number in self.keyframe_frame_numbers
	
	def has_annotations(self, frame_number: int) -> bool:
		"""Check if a frame has any annotations."""
		return frame_number in self.frame_bboxes
	
	def get_color_for_label(self, label: str) -> QColor:
		"""Get color for a label, with fallback to defaults."""
		label_lower = label.lower()
		if label_lower in self.label_colors:
			return self.label_colors[label_lower]
		if label_lower in DEFAULT_LABEL_COLORS:
			return DEFAULT_LABEL_COLORS[label_lower]
		return QColor(255, 0, 0)  # Default red


class AnnotationLoadDialog(QDialog):
	"""Dialog for loading Label Studio annotations with task ID and frame offset."""
	
	def __init__(self, parent=None):
		super().__init__(parent)
		self.setWindowTitle("Load Label Studio Annotations")
		self.setMinimumWidth(300)
		
		layout = QVBoxLayout(self)
		form_layout = QFormLayout()
		
		# Task ID input
		self.task_id_spin = QSpinBox()
		self.task_id_spin.setRange(1, 999999)
		self.task_id_spin.setValue(1)
		self.task_id_spin.setToolTip("Task ID from Label Studio export")
		form_layout.addRow("Task ID:", self.task_id_spin)
		
		# Frame offset input
		self.offset_spin = QSpinBox()
		self.offset_spin.setRange(-100, 100)
		self.offset_spin.setValue(1)
		self.offset_spin.setToolTip(
			"Offset to map Label Studio frame numbers to image file frame numbers.\n"
			"Default 1 because Label Studio uses 0-based frames but image files are often 1-based.")
		form_layout.addRow("Frame Offset:", self.offset_spin)
		
		layout.addLayout(form_layout)
		
		# Help text
		help_label = QLabel(
			"<small>Frame offset maps Label Studio frame numbers to image files.<br>"
			"Example: offset=1 means LS frame 0 → image frame 1</small>")
		help_label.setStyleSheet("color: #888;")
		layout.addWidget(help_label)
		
		# Buttons
		button_box = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
		button_box.accepted.connect(self.accept)
		button_box.rejected.connect(self.reject)
		layout.addWidget(button_box)
	
	def get_values(self) -> Tuple[int, int]:
		"""Return (task_id, frame_offset)."""
		return self.task_id_spin.value(), self.offset_spin.value()


# ==============================================================================
# Embedding Generation Dialog
# ==============================================================================

class EmbeddingGenerationDialog(QDialog):
	"""Dialog for configuring embedding generation parameters."""
	
	def __init__(
		self,
		parent=None,
		auto_batch_size: int = 8,
		current_config: Optional[EmbeddingConfig] = None,
		initial_force: bool = False,
	):
		super().__init__(parent)
		self.setWindowTitle("Generate Embeddings")
		self.setMinimumWidth(400)
		self.auto_batch_size = auto_batch_size
		self.initial_config = current_config
		self.initial_force = initial_force
		
		layout = QVBoxLayout(self)
		form_layout = QFormLayout()
		
		# Model selection
		self.model_combo = QComboBox()
		from frame_selection_core import get_common_timm_models
		common_timm_models = get_common_timm_models()
		self.model_combo.addItems(common_timm_models)
		self.model_combo.insertSeparator(len(common_timm_models))
		self.model_combo.addItem("Custom...")
		self.model_combo.setToolTip("ViT model to use for embeddings")
		form_layout.addRow("Model:", self.model_combo)
		
		# Custom model input (hidden by default)
		self.custom_model_input = QLineEdit()
		self.custom_model_input.setPlaceholderText("Enter TIMM model name")
		self.custom_model_input.setVisible(False)
		form_layout.addRow("", self.custom_model_input)
		self.model_combo.currentTextChanged.connect(self._on_model_changed)
		
		# Normalize checkbox
		self.normalize_checkbox = QCheckBox("L2 Normalize Embeddings")
		self.normalize_checkbox.setToolTip("Normalize patch embeddings to unit length")
		form_layout.addRow(self.normalize_checkbox)
		
		# Transform mode
		self.transform_combo = QComboBox()
		self.transform_combo.addItems(['crop', 'pad', 'scale'])
		self.transform_combo.setToolTip(
			"crop: Resize & crop (loses edges) | pad: Resize & pad (preserves data) | scale: Stretch to fit")
		form_layout.addRow("Transform:", self.transform_combo)
		
		# Alignment
		self.align_combo = QComboBox()
		self.align_combo.addItems(['center', 'top', 'bottom', 'left', 'right'])
		self.align_combo.setToolTip("Alignment for crop/pad along adjusted axis")
		form_layout.addRow("Alignment:", self.align_combo)
		
		# Batch size
		self.batch_size_spin = QSpinBox()
		self.batch_size_spin.setRange(1, 64)
		self.batch_size_spin.setToolTip("Number of images to process at once")
		
		batch_row_layout = QHBoxLayout()
		batch_row_layout.addWidget(self.batch_size_spin)
		batch_row_layout.addWidget(QLabel(f"(auto-detected: {auto_batch_size})"))
		batch_row_layout.addStretch()
		form_layout.addRow("Batch Size:", batch_row_layout)
		
		# Clear other caches checkbox
		self.clear_others_checkbox = QCheckBox("Clear other caches")
		self.clear_others_checkbox.setToolTip("Remove other cache files to save disk space")
		form_layout.addRow(self.clear_others_checkbox)
		
		# Force regenerate checkbox (allows user to choose whether to overwrite existing cache)
		self.force_checkbox = QCheckBox("Force regenerate (overwrite existing cache)")
		self.force_checkbox.setToolTip("Force regeneration even if a cache already exists")
		form_layout.addRow(self.force_checkbox)
		
		layout.addLayout(form_layout)
		
		# Buttons
		button_box = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
		button_box.accepted.connect(self.accept)
		button_box.rejected.connect(self.reject)
		layout.addWidget(button_box)
		
		# Load preferences
		self._load_preferences()
	
	def _load_preferences(self):
		"""Load previously selected settings from QSettings and current config."""
		batch_size_pref = settings.value("embedding/batch_size", self.auto_batch_size, int)
		clear_others_pref = settings.value("embedding/clear_others", True, bool)
		force_pref = settings.value("embedding/force", self.initial_force, bool)

		if self.initial_config.model not in [self.model_combo.itemText(i) for i in range(self.model_combo.count())]:
			self.model_combo.setCurrentText("Custom...")
			self.custom_model_input.setText(self.initial_config.model)
		else:
			self.model_combo.setCurrentText(self.initial_config.model)
		
		self.normalize_checkbox.setChecked(self.initial_config.normalize)
		self.transform_combo.setCurrentText(self.initial_config.transform.mode.value)
		self.align_combo.setCurrentText(self.initial_config.transform.alignment.value)
		self.batch_size_spin.setValue(batch_size_pref)
		self.clear_others_checkbox.setChecked(clear_others_pref)
		self.force_checkbox.setChecked(force_pref)
	
	def _save_preferences(self):
		"""Save current settings to QSettings."""
		config = self.get_config()
		settings.setValue("embedding/model", config.model)
		settings.setValue("embedding/normalize", config.normalize)
		settings.setValue("embedding/transform", config.transform.mode.value)
		settings.setValue("embedding/alignment", config.transform.alignment.value)
		settings.setValue("embedding/batch_size", self.batch_size_spin.value())
		settings.setValue("embedding/clear_others", self.clear_others_checkbox.isChecked())
		settings.setValue("embedding/force", self.force_checkbox.isChecked())
	
	def accept(self):
		"""Override accept to save preferences."""
		self._save_preferences()
		super().accept()

	def _on_model_changed(self, text: str):
		"""Show custom model input when 'Custom...' is selected."""
		self.custom_model_input.setVisible(text == "Custom...")

	def get_config(self) -> EmbeddingConfig:
		"""Return selected embedding configuration as a dataclass."""
		if self.model_combo.currentText() == "Custom...":
			model = self.custom_model_input.text().strip()
		else:
			model = self.model_combo.currentText()

		return EmbeddingConfig(
			model=model,
			normalize=self.normalize_checkbox.isChecked(),
			transform=TransformConfig.from_strings(
				self.transform_combo.currentText(),
				self.align_combo.currentText(),
			),
		)


# ==============================================================================
# Embedding Generation Worker Thread
# ==============================================================================

class EmbeddingGenerationWorker(QThread):
	"""Worker thread for computing embeddings in background."""
	
	progress = Signal(int, int)  # (done, total)
	finished = Signal(object, bool)  # (embeddings_tensor, from_cache)
	error = Signal(str)  # error_message

	def __init__(
		self,
		frames: List[Path],
		embedding_config: EmbeddingConfig,
		batch_size: int,
		cache_dir: Path,
		force: bool = False,
		clear_others: bool = True,
	):
		super().__init__()
		self.frames = frames
		self.embedding_config = embedding_config
		self.batch_size = batch_size
		self.cache_dir = cache_dir
		self.force = force
		self.clear_others = clear_others
		self._is_cancelled = False
		self._mutex = threading.RLock()
	
	def cancel(self):
		"""Request cancellation of ongoing work."""
		with self._mutex:
			self._is_cancelled = True
	
	def is_cancelled(self) -> bool:
		"""Check if cancellation was requested."""
		with self._mutex:
			return self._is_cancelled
	
	def run(self):
		"""Execute embedding generation in background."""
		try:
			from frame_selection_core import load_or_compute_embeddings
			
			config = self.embedding_config
			
			# Progress callback
			def progress_callback(done: int, total: int):
				if self.is_cancelled():
					raise InterruptedError("Embedding generation cancelled")
				self.progress.emit(done, total)
			
			# Load or compute embeddings
			embeddings, from_cache = load_or_compute_embeddings(
				cache_dir=self.cache_dir,
				frames=self.frames,
				config=config,
				force=self.force,
				batch_size=self.batch_size,
				progress=progress_callback,
				clear_others=self.clear_others,
			)
			
			if not self.is_cancelled():
				self.finished.emit(embeddings, from_cache)
			
		except Exception as exc:
			if not self.is_cancelled():
				error_msg = f"Error generating embeddings: {str(exc)}"
				self.error.emit(error_msg)


# ==============================================================================
# Navigation Panel
# ==============================================================================

class NavigationPanel(QWidget):
	"""Panel with navigation buttons for frames."""
	
	prev_frame = Signal()
	next_frame = Signal()
	prev_selected = Signal()
	next_selected = Signal()
	prev_keyframe = Signal()
	next_keyframe = Signal()
	toggle_force_select = Signal()
	
	def __init__(self):
		super().__init__()
		self.setup_ui()
	
	def setup_ui(self):
		layout = QHBoxLayout(self)
		layout.setContentsMargins(4, 4, 4, 4)
		layout.setSpacing(4)
		
		# Frame navigation
		self.prev_frame_btn = QPushButton("◀")
		self.prev_frame_btn.setToolTip("Previous frame (Left Arrow)")
		self.prev_frame_btn.setFixedWidth(32)
		self.prev_frame_btn.clicked.connect(self.prev_frame.emit)
		
		self.next_frame_btn = QPushButton("▶")
		self.next_frame_btn.setToolTip("Next frame (Right Arrow)")
		self.next_frame_btn.setFixedWidth(32)
		self.next_frame_btn.clicked.connect(self.next_frame.emit)
		
		# Selected frame navigation
		self.prev_selected_btn = QPushButton("◀◀")
		self.prev_selected_btn.setToolTip("Previous selected frame (Shift+Left)")
		self.prev_selected_btn.setFixedWidth(40)
		self.prev_selected_btn.clicked.connect(self.prev_selected.emit)
		
		self.next_selected_btn = QPushButton("▶▶")
		self.next_selected_btn.setToolTip("Next selected frame (Shift+Right)")
		self.next_selected_btn.setFixedWidth(40)
		self.next_selected_btn.clicked.connect(self.next_selected.emit)
		
		# Keyframe navigation
		self.prev_keyframe_btn = QPushButton("◁")
		self.prev_keyframe_btn.setToolTip("Previous keyframe (Ctrl+Left)")
		self.prev_keyframe_btn.setFixedWidth(32)
		self.prev_keyframe_btn.clicked.connect(self.prev_keyframe.emit)
		self.prev_keyframe_btn.setEnabled(False)  # Disabled until annotations loaded
		
		self.next_keyframe_btn = QPushButton("▷")
		self.next_keyframe_btn.setToolTip("Next keyframe (Ctrl+Right)")
		self.next_keyframe_btn.setFixedWidth(32)
		self.next_keyframe_btn.clicked.connect(self.next_keyframe.emit)
		self.next_keyframe_btn.setEnabled(False)  # Disabled until annotations loaded
		
		# Force select toggle button
		self.force_select_btn = QPushButton("F")
		self.force_select_btn.setToolTip("Toggle force select/unselect (F key)\nGreen=forced select, Red=forced unselect")
		self.force_select_btn.setFixedWidth(32)
		self.force_select_btn.clicked.connect(self.toggle_force_select.emit)
		
		# Add to layout with grouping
		layout.addWidget(QLabel("Frame:"))
		layout.addWidget(self.prev_frame_btn)
		layout.addWidget(self.next_frame_btn)
		layout.addSpacing(8)
		layout.addWidget(QLabel("Selected:"))
		layout.addWidget(self.prev_selected_btn)
		layout.addWidget(self.next_selected_btn)
		layout.addSpacing(8)
		layout.addWidget(QLabel("Keyframe:"))
		layout.addWidget(self.prev_keyframe_btn)
		layout.addWidget(self.next_keyframe_btn)
		layout.addSpacing(16)
		layout.addWidget(self.force_select_btn)
		layout.addStretch()
	
	def set_keyframe_navigation_enabled(self, enabled: bool):
		"""Enable/disable keyframe navigation buttons."""
		self.prev_keyframe_btn.setEnabled(enabled)
		self.next_keyframe_btn.setEnabled(enabled)


class ThumbnailWidget(QLabel):
	"""Widget displaying a single thumbnail with selection state."""
	
	clicked = Signal(int)
	
	# State colors
	COLOR_SELECTED = QColor(60, 120, 200, 180)      # Blue - selected for processing
	COLOR_ADDED = QColor(60, 200, 60, 180)          # Green - newly added
	COLOR_REMOVED = QColor(200, 60, 60, 180)        # Red - just removed
	COLOR_NORMAL = QColor(80, 80, 80, 120)          # Gray - not selected
	COLOR_PREVIEW = QColor(240, 200, 60, 200)       # Yellow - currently previewed
	COLOR_FORCED_SELECT = QColor(0, 200, 100, 200)  # Bright green - manually forced select
	COLOR_FORCED_UNSELECT = QColor(200, 100, 0, 200)  # Orange - manually forced unselect
	COLOR_KEYFRAME = QColor(255, 200, 0, 220)       # Gold - keyframe marker
	COLOR_HAS_ANNOTATION = QColor(150, 50, 200, 180)  # Purple - has annotation
	
	def __init__(self, index: int, thumbnail_size: int = 80):
		super().__init__()
		self.index = index
		self.thumbnail_size = thumbnail_size
		self.frame_number = None  # Will be set later
		self._baked_pixmap: Optional[QPixmap] = None
		self.is_selected_for_processing = False
		self.is_added = False
		self.is_removed = False
		self.is_previewed = False
		self.is_forced_select = False
		self.is_forced_unselect = False
		# Cache for rendered thumbnail with current state
		self._cached_display: Optional[QPixmap] = None
		self._cache_valid = False
		
		# Account for space below for filename label
		total_height = thumbnail_size + 8 + 20  # 20px for text
		self.setFixedSize(thumbnail_size + 8, total_height)
		self.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop)
		self.setStyleSheet("background-color: #333;")
	
	def set_baked_pixmap(self, pixmap: QPixmap):
		"""Set a pre-baked thumbnail pixmap (image + baked bboxes/markers)."""
		# Expect pixmap to already be the full display canvas (thumbnail + label area)
		self._baked_pixmap = pixmap
		self._cache_valid = False
		self._update_display()
	
	def set_frame_number(self, frame_number: int):
		"""Set the frame number label."""
		self.frame_number = frame_number
		self._cache_valid = False
		self._update_display()

	def set_forced_state(self, forced_select: bool, forced_unselect: bool):
		"""Set manual force select/unselect state."""
		if self.is_forced_select != forced_select or self.is_forced_unselect != forced_unselect:
			self.is_forced_select = forced_select
			self.is_forced_unselect = forced_unselect
			self._cache_valid = False
			self._update_display()
	
	def set_state(
		self,
		selected_for_processing: bool,
		added: bool = False,
		removed: bool = False,
		previewed: bool = False
	):
		"""Update selection/preview state."""
		changed = (
			self.is_selected_for_processing != selected_for_processing or
			self.is_added != added or
			self.is_removed != removed or
			self.is_previewed != previewed
		)
		self.is_selected_for_processing = selected_for_processing
		self.is_added = added
		self.is_removed = removed
		self.is_previewed = previewed
		if changed:
			self._cache_valid = False
			self._update_display()
	
	def _update_display(self):
		"""Redraw with current state."""
		if self._baked_pixmap is None:
			return
		
		# Create display pixmap with border and text space
		total_height = self.thumbnail_size + 8 + 20
		display = QPixmap(self.thumbnail_size + 8, total_height)
		display.fill(Qt.GlobalColor.transparent)
		
		painter = QPainter(display)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		
		# Determine border color and width
		if self.is_forced_select:
			color = self.COLOR_FORCED_SELECT
			border_width = 4
		elif self.is_forced_unselect:
			color = self.COLOR_FORCED_UNSELECT
			border_width = 4
		elif self.is_added:
			color = self.COLOR_ADDED
			border_width = 4
		elif self.is_removed:
			color = self.COLOR_REMOVED
			border_width = 4
		elif self.is_selected_for_processing:
			color = self.COLOR_SELECTED
			border_width = 3
		else:
			color = self.COLOR_NORMAL
			border_width = 1
		
		# Draw border
		pen = QPen(color, border_width)
		painter.setPen(pen)
		painter.drawRect(
			border_width // 2, 
			border_width // 2,
			self.thumbnail_size + 8 - border_width, 
			self.thumbnail_size + 8 - border_width)

		if self.is_previewed:
			preview_pen = QPen(self.COLOR_PREVIEW, 2, Qt.PenStyle.DashLine)
			painter.setPen(preview_pen)
			painter.drawRect(
				border_width // 2 + 2,
				border_width // 2 + 2,
				self.thumbnail_size + 8 - border_width - 4,
				self.thumbnail_size + 8 - border_width - 4)
		
		# Draw baked thumbnail (pre-rendered with bboxes/markers)
		painter.drawPixmap(0, 0, self._baked_pixmap)
		
		# Draw frame number text below
		if self.frame_number is not None:
			painter.setPen(QColor(200, 200, 200, 200))
			font = QFont("Arial", 7, QFont.Weight.Bold)
			painter.setFont(font)
			
			display_text = f"#{self.frame_number}"
			
			text_rect = QRect(0, self.thumbnail_size + 8, self.thumbnail_size + 8, 20)
			painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, display_text)
		
		painter.end()
		self._cached_display = display
		self._cache_valid = True
		self.setPixmap(display)
	
	def mousePressEvent(self, event):
		if event.button() == Qt.MouseButton.LeftButton:
			self.clicked.emit(self.index)


class ThumbnailGrid(QScrollArea):
	"""Scrollable grid of thumbnails."""
	
	thumbnail_clicked = Signal(int)
	
	def __init__(self, thumbnail_size: int = 80):
		super().__init__()
		self.thumbnail_size = thumbnail_size
		self.thumbnails: List[ThumbnailWidget] = []
		self._focused_thumbnail_index: Optional[int] = None
		
		# Setup scroll area
		self.setWidgetResizable(True)
		self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		
		# Container widget
		self.container = QWidget()
		self.grid_layout = QGridLayout(self.container)
		self.grid_layout.setSpacing(4)
		self.grid_layout.setContentsMargins(4, 4, 4, 4)
		self.setWidget(self.container)
		
		# Track columns for layout
		self.columns = 4
		# Reference to FrameSelectionData for baking/rebake
		self.data: Optional[FrameSelectionData] = None
	
	def set_frame_count(self, count: int):
		"""Initialize thumbnail widgets."""
		# Clear existing
		for thumb in self.thumbnails:
			thumb.deleteLater()
		self.thumbnails.clear()
		
		# Create new thumbnails
		for i in range(count):
			thumb = ThumbnailWidget(i, self.thumbnail_size)
			thumb.clicked.connect(self.thumbnail_clicked.emit)
			self.thumbnails.append(thumb)
		
		self._relayout()
	
	def _relayout(self):
		"""Relayout thumbnails in grid."""
		# Clear layout
		while self.grid_layout.count():
			item = self.grid_layout.takeAt(0)
			if item.widget():
				item.widget().setParent(None)
		
		# Add thumbnails
		for i, thumb in enumerate(self.thumbnails):
			row = i // self.columns
			col = i % self.columns
			self.grid_layout.addWidget(thumb, row, col)
	
	def resizeEvent(self, event):
		super().resizeEvent(event)
		# Recalculate columns based on width
		available_width = self.viewport().width() - 8
		new_columns = max(1, available_width // (self.thumbnail_size + 12))
		if new_columns != self.columns:
			self.columns = new_columns
			self._relayout()
	
	def keyPressEvent(self, event):
		"""Handle arrow key navigation when grid or thumbnail has focus."""
		key = event.key()
		modifiers = event.modifiers()

		# Pass arrow keys to parent; To move the grid scroll area `scroll_to_thumbnail` is used, or PgUp/PgDn.
		if key == Qt.Key.Key_Left or key == Qt.Key.Key_Right or key == Qt.Key.Key_Up or key == Qt.Key.Key_Down:
			self.parentWidget().keyPressEvent(event)
			return

		return super().keyPressEvent(event)
	
	def set_baked_thumbnail(self, index: int, pixmap: QPixmap, frame_number: int = None):
		"""Set a pre-baked pixmap and frame number for a specific thumbnail."""
		if 0 <= index < len(self.thumbnails):
			self.thumbnails[index].set_baked_pixmap(pixmap)
			if frame_number is not None:
				self.thumbnails[index].set_frame_number(frame_number)

	def set_data_and_bake(self, data: FrameSelectionData, annotation_data: Optional['AnnotationData'] = None):
		"""Create thumbnails from FrameSelectionData and bake bboxes/markers into pixmaps.
		Blocking operation but shows a progress dialog.
		"""
		# Clear existing thumbnails
		for thumb in self.thumbnails:
			thumb.deleteLater()
		self.thumbnails.clear()
		self.data = data
		count = len(self.data)
		progress = QProgressDialog("Preparing thumbnails...", "Cancel", 0, count, self)
		progress.setWindowModality(Qt.WindowModality.WindowModal)
		progress.setMinimumDuration(200)
		progress.setValue(0)
		for i in range(count):
			if progress.wasCanceled():
				break
			path = self.data.get_frame_path(i)
			frame_num = self.data.get_frame_number(i)
			bboxes = None
			is_keyframe = False
			if annotation_data is not None:
				bboxes = annotation_data.get_bboxes_for_frame(frame_num)
				is_keyframe = annotation_data.is_keyframe(frame_num)
			baked = self._bake_thumbnail(path, bboxes, is_keyframe, annotation_data)
			thumb = ThumbnailWidget(i, self.thumbnail_size)
			thumb.clicked.connect(self.thumbnail_clicked.emit)
			thumb.set_baked_pixmap(baked)
			thumb.set_frame_number(frame_num)
			self.thumbnails.append(thumb)
			progress.setValue(i+1)
		progress.close()
		self._relayout()

	def _bake_thumbnail(self, path: Path, bboxes: Optional[List[Tuple[List[str], float, float, float, float]]], is_keyframe: bool, annotation_data: Optional['AnnotationData'] = None) -> QPixmap:
		"""Create a baked pixmap with image, bboxes and optional markers and return it."""
		total_height = self.thumbnail_size + 8 + 20
		display = QPixmap(self.thumbnail_size + 8, total_height)
		display.fill(Qt.GlobalColor.transparent)
		painter = QPainter(display)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		orig = QPixmap(str(path))
		if orig.isNull():
			# draw placeholder
			painter.setPen(QColor(200,200,200))
			font = QFont("Arial", 7)
			painter.setFont(font)
			painter.drawText(display.rect(), Qt.AlignmentFlag.AlignCenter, "Failed to load")
			painter.end()
			return display
		scaled = orig.scaled(self.thumbnail_size, self.thumbnail_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
		x = (self.thumbnail_size + 8 - scaled.width()) // 2
		y = (self.thumbnail_size + 8 - scaled.height()) // 2
		painter.drawPixmap(x, y, scaled)
		# draw bboxes
		if bboxes:
			for labels, x_pct, y_pct, w_pct, h_pct in bboxes:
				# Choose color from annotation data if available, fall back to white
				if annotation_data and labels:
					color = annotation_data.get_color_for_label(labels[0] if labels else "")
					color.setAlpha(200)
				else:
					color = QColor(255, 255, 255, 200)
				pen = QPen(color, 1)
				painter.setPen(pen)
				bw = scaled.width()
				bh = scaled.height()
				bx = x + (x_pct / 100.0) * bw
				by = y + (y_pct / 100.0) * bh
				bw_px = (w_pct / 100.0) * bw
				bh_px = (h_pct / 100.0) * bh
				# Ensure min visible size
				rect = QRect(int(bx), int(by), max(1, int(bw_px)), max(1, int(bh_px)))
				painter.drawRect(rect)
		# keyframe marker
		if is_keyframe:
			triangle_size = 12
			triangle = QPolygon([
				QPoint(self.thumbnail_size + 8 - triangle_size, 0),
				QPoint(self.thumbnail_size + 8, 0),
				QPoint(self.thumbnail_size + 8, triangle_size)
			])
			painter.setPen(Qt.PenStyle.NoPen)
			painter.setBrush(QBrush(ThumbnailWidget.COLOR_KEYFRAME))
			painter.drawPolygon(triangle)
		painter.end()
		return display

	def rebake_all(self, annotation_data: Optional['AnnotationData']):
		"""Re-bake all thumbnails using current FrameSelectionData and new annotation data."""
		if self.data is None:
			return
		count = len(self.data)
		progress = QProgressDialog("Updating thumbnails...", "Cancel", 0, count, self)
		progress.setWindowModality(Qt.WindowModality.WindowModal)
		progress.setMinimumDuration(200)
		progress.setValue(0)
		for i in range(count):
			if progress.wasCanceled():
				break
			path = self.data.get_frame_path(i)
			frame_num = self.data.get_frame_number(i)
			bboxes = annotation_data.get_bboxes_for_frame(frame_num) if annotation_data else None
			is_kf = annotation_data.is_keyframe(frame_num) if annotation_data else False
			baked = self._bake_thumbnail(path, bboxes, is_kf, annotation_data)
			self.thumbnails[i].set_baked_pixmap(baked)
			progress.setValue(i+1)
		progress.close()

	def update_states(
		self,
		selected_for_processing: Set[int],
		previous_selected_for_processing: Set[int],
		selected_for_preview_index: Optional[int],
		forced_select: Optional[Set[int]] = None,
		forced_unselect: Optional[Set[int]] = None
	):
		"""Update thumbnail states based on processing/preview selections."""
		added = selected_for_processing - previous_selected_for_processing
		removed = previous_selected_for_processing - selected_for_processing
		forced_select = forced_select or set()
		forced_unselect = forced_unselect or set()
		
		for i, thumb in enumerate(self.thumbnails):
			is_selected = i in selected_for_processing
			is_added = i in added
			is_removed = i in removed
			is_previewed = selected_for_preview_index is not None and i == selected_for_preview_index
			thumb.set_state(is_selected, is_added, is_removed, is_previewed)
			thumb.set_forced_state(i in forced_select, i in forced_unselect)
	
	def update_annotation_info(
		self,
		annotation_data: Optional['AnnotationData'],
		frame_number_getter: Callable[[int], int]
	):
		"""Deprecated: re-bake thumbnails with new annotation data."""
		self.rebake_all(annotation_data)

	def scroll_to_thumbnail(self, index: int):
		"""Scroll to ensure thumbnail at index is visible."""
		if 0 <= index < len(self.thumbnails):
			self.ensureWidgetVisible(self.thumbnails[index])


class ParameterPanel(QWidget):
	"""Panel with parameter sliders and controls."""
	
	apply_clicked = Signal()
	
	def __init__(self):
		super().__init__()
		self.setup_ui()
	
	def setup_ui(self):
		layout = QVBoxLayout(self)
		layout.setSpacing(12)
		
		# Current parameters group
		current_group = QGroupBox("Current Parameters")
		current_layout = QVBoxLayout(current_group)
		
		# Concentration percentile
		self.concentration_slider, self.concentration_value = self._create_slider(
			"Concentration Percentile:", 0, 99, 90,
			"Higher = prefer more concentrated changes (stricter)")
		current_layout.addLayout(self._create_slider_row(
			"Concentration:", self.concentration_slider, self.concentration_value, "%"))
		
		# Total change percentile
		self.total_change_slider, self.total_change_value = self._create_slider(
			"Total Change Percentile:", 0, 99, 60,
			"Higher = require more total change (stricter)")
		current_layout.addLayout(self._create_slider_row(
			"Total Change:", self.total_change_slider, self.total_change_value, "%"))
		
		# Entropy percentile
		self.entropy_slider, self.entropy_value = self._create_slider(
			"Entropy Percentile:", 0, 99, 40,
			"Lower = prefer more focused changes (stricter)")
		current_layout.addLayout(self._create_slider_row(
			"Entropy:", self.entropy_slider, self.entropy_value, "%"))
		
		# Temporal window
		self.temporal_spin = QSpinBox()
		self.temporal_spin.setRange(1, 20)
		self.temporal_spin.setValue(5)
		self.temporal_spin.setToolTip("Frames for temporal smoothing")
		current_layout.addLayout(self._create_spin_row("Temporal Window:", self.temporal_spin))
		
		# Min spacing
		self.spacing_spin = QSpinBox()
		self.spacing_spin.setRange(1, 50)
		self.spacing_spin.setValue(5)
		self.spacing_spin.setToolTip("Minimum frames between selections")
		current_layout.addLayout(self._create_spin_row("Min Spacing:", self.spacing_spin))
		
		# Previous parameters display
		self.previous_group = QGroupBox("Previous Parameters")
		self.previous_layout = QVBoxLayout(self.previous_group)
		self.previous_labels = {}
		for name in ["Concentration", "Total Change", "Entropy", "Temporal Window", "Min Spacing"]:
			label = QLabel("--")
			row = QHBoxLayout()
			row.addWidget(QLabel(f"{name}:"))
			row.addStretch()
			row.addWidget(label)
			self.previous_layout.addLayout(row)
			self.previous_labels[name] = label
		
		# Horizontal layout for current and previous parameters side-by-side
		params_layout = QHBoxLayout()
		params_layout.addWidget(current_group, stretch=2)
		params_layout.addWidget(self.previous_group, stretch=1)
		layout.addLayout(params_layout)
		
		# Selection stats
		self.stats_label = QLabel("Selected for processing: -- / --")
		self.stats_label.setStyleSheet("font-size: 14px; font-weight: bold;")
		self.diff_label = QLabel("")
		self.diff_label.setStyleSheet("font-size: 12px;")
		
		stats_layout = QVBoxLayout()
		stats_layout.addWidget(self.stats_label)
		stats_layout.addWidget(self.diff_label)
		layout.addLayout(stats_layout)

		# Auto-apply toggle
		self.auto_apply_checkbox = QCheckBox("Auto-apply on change")
		layout.addWidget(self.auto_apply_checkbox)
		
		# Buttons
		button_layout = QHBoxLayout()
		
		self.apply_btn = QPushButton("Apply")
		self.apply_btn.setStyleSheet("font-size: 14px; padding: 8px 16px;")
		self.apply_btn.clicked.connect(self.apply_clicked.emit)
		
		self.reset_btn = QPushButton("Reset to Previous")
		self.reset_btn.clicked.connect(self._reset_to_previous)
		
		self.defaults_btn = QPushButton("Defaults")
		self.defaults_btn.clicked.connect(self._reset_to_defaults)
		
		button_layout.addWidget(self.apply_btn)
		button_layout.addWidget(self.reset_btn)
		button_layout.addWidget(self.defaults_btn)
		
		layout.addLayout(button_layout)
		layout.addStretch()
	
	def _create_slider(self, label: str, min_val: int, max_val: int, default: int, tooltip: str):
		slider = QSlider(Qt.Orientation.Horizontal)
		slider.setRange(min_val, max_val)
		slider.setValue(default)
		slider.setToolTip(tooltip)
		
		value_label = QLabel(str(default))
		value_label.setMinimumWidth(30)
		slider.valueChanged.connect(lambda v: value_label.setText(str(v)))
		
		return slider, value_label
	
	def _create_slider_row(self, label: str, slider: QSlider, value_label: QLabel, suffix: str = ""):
		layout = QHBoxLayout()
		name_label = QLabel(label)
		name_label.setMinimumWidth(100)
		layout.addWidget(name_label)
		layout.addWidget(slider, stretch=1)
		layout.addWidget(value_label)
		layout.addWidget(QLabel(suffix))
		return layout
	
	def _create_spin_row(self, label: str, spin: QSpinBox):
		layout = QHBoxLayout()
		name_label = QLabel(label)
		name_label.setMinimumWidth(100)
		layout.addWidget(name_label)
		layout.addStretch()
		layout.addWidget(spin)
		return layout
	
	def get_params(self) -> SelectionParams:
		"""Get current parameter values."""
		return SelectionParams(
			concentration_percentile=float(self.concentration_slider.value()),
			total_change_percentile=float(self.total_change_slider.value()),
			entropy_percentile=float(self.entropy_slider.value()),
			temporal_window=self.temporal_spin.value(),
			min_spacing=self.spacing_spin.value())
	
	def set_params(self, params: SelectionParams):
		"""Set parameter values."""
		self.concentration_slider.setValue(int(params.concentration_percentile))
		self.total_change_slider.setValue(int(params.total_change_percentile))
		self.entropy_slider.setValue(int(params.entropy_percentile))
		self.temporal_spin.setValue(params.temporal_window)
		self.spacing_spin.setValue(params.min_spacing)

	def connect_value_changes(self, slot, slider_released_slot=None):
		"""Connect parameter inputs to change and (optionally) slider-release handlers."""
		self.concentration_slider.valueChanged.connect(slot)
		self.total_change_slider.valueChanged.connect(slot)
		self.entropy_slider.valueChanged.connect(slot)
		self.temporal_spin.valueChanged.connect(slot)
		self.spacing_spin.valueChanged.connect(slot)
		if slider_released_slot is not None:
			self.concentration_slider.sliderReleased.connect(slider_released_slot)
			self.total_change_slider.sliderReleased.connect(slider_released_slot)
			self.entropy_slider.sliderReleased.connect(slider_released_slot)

	def is_auto_apply_enabled(self) -> bool:
		return self.auto_apply_checkbox.isChecked()

	def any_slider_dragging(self) -> bool:
		return (
			self.concentration_slider.isSliderDown()
			or self.total_change_slider.isSliderDown()
			or self.entropy_slider.isSliderDown()
		)
	
	def update_previous_display(self, params: SelectionParams):
		"""Update previous parameters display."""
		self.previous_labels["Concentration"].setText(f"{params.concentration_percentile:.0f}%")
		self.previous_labels["Total Change"].setText(f"{params.total_change_percentile:.0f}%")
		self.previous_labels["Entropy"].setText(f"{params.entropy_percentile:.0f}%")
		self.previous_labels["Temporal Window"].setText(str(params.temporal_window))
		self.previous_labels["Min Spacing"].setText(str(params.min_spacing))
		self._previous_params = params.copy()
	
	def update_stats(self, selected_for_processing: int, total: int, added: int, removed: int):
		"""Update selection statistics."""
		self.stats_label.setText(
			f"Selected for processing: {selected_for_processing} / {total} "
			f"({100*selected_for_processing/total:.1f}%)")
		
		diff_parts = []
		if added > 0:
			diff_parts.append(f'<span style="color: #4c4;">+{added} added</span>')
		if removed > 0:
			diff_parts.append(f'<span style="color: #c44;">-{removed} removed</span>')
		
		if diff_parts:
			self.diff_label.setText(" ".join(diff_parts))
		else:
			self.diff_label.setText("No changes")
	
	def _reset_to_previous(self):
		"""Reset sliders to previous values."""
		if hasattr(self, '_previous_params'):
			self.set_params(self._previous_params)
	
	def _reset_to_defaults(self):
		"""Reset sliders to default values."""
		self.set_params(SelectionParams())


class PreviewPanel(QWidget):
	"""Panel showing preview of selected image with optional annotation overlay."""
	
	show_annotations_changed = Signal(bool)
	
	def __init__(self):
		super().__init__()
		self._current_pixmap: Optional[QPixmap] = None
		self._current_bboxes: List[Tuple[List[str], float, float, float, float]] = []
		self._annotation_data: Optional[AnnotationData] = None
		self._show_annotations = True
		self.setup_ui()
	
	def setup_ui(self):
		layout = QVBoxLayout(self)
		
		# Image preview
		self.preview_label = QLabel()
		self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self.preview_label.setMinimumSize(300, 300)
		self.preview_label.setStyleSheet("background-color: #222; border: 1px solid #444;")
		layout.addWidget(self.preview_label, stretch=1)
		
		# Bottom row: info label + show annotations checkbox
		bottom_layout = QHBoxLayout()
		
		# Info label
		self.info_label = QLabel("Click a thumbnail to preview")
		self.info_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
		self.info_label.setStyleSheet("color: #888; padding: 8px;")
		bottom_layout.addWidget(self.info_label, stretch=1)
		
		# Show annotations checkbox
		self.show_annotations_checkbox = QCheckBox("Show annotations")
		self.show_annotations_checkbox.setChecked(True)
		self.show_annotations_checkbox.setToolTip("Toggle display of bounding box annotations on preview")
		self.show_annotations_checkbox.stateChanged.connect(self._on_show_annotations_changed)
		bottom_layout.addWidget(self.show_annotations_checkbox)
		
		layout.addLayout(bottom_layout)
	
	def _on_show_annotations_changed(self, state):
		self._show_annotations = state == Qt.CheckState.Checked.value
		self._refresh_display()
		self.show_annotations_changed.emit(self._show_annotations)
	
	def set_annotation_data(self, annotation_data: Optional[AnnotationData]):
		"""Set annotation data for drawing bboxes."""
		self._annotation_data = annotation_data
		self.show_annotations_checkbox.setEnabled(annotation_data is not None)
	
	def set_image(
		self, 
		path: Path, 
		filename: str, 
		frame_number: int, 
		selected_for_processing: bool,
		bboxes: Optional[List[Tuple[List[str], float, float, float, float]]] = None
	):
		"""Display an image along with its frame number and optional bounding boxes."""
		pixmap = QPixmap(str(path))
		if pixmap.isNull():
			self.preview_label.setText("Failed to load image")
			self._current_pixmap = None
			return
		
		self._current_pixmap = pixmap
		self._current_bboxes = bboxes or []
		self._refresh_display()
		
		status = "SELECTED for processing" if selected_for_processing else "not selected for processing"
		bbox_info = f", {len(self._current_bboxes)} annotations" if self._current_bboxes else ""
		self.info_label.setText(f"Frame {frame_number}: {filename} ({status}{bbox_info})")
	
	def _refresh_display(self):
		"""Refresh the preview display with current pixmap and annotations."""
		if self._current_pixmap is None:
			return
		
		# Scale to fit the preview area
		target_size = self.preview_label.size()
		scaled = self._current_pixmap.scaled(
			target_size,
			Qt.AspectRatioMode.KeepAspectRatio,
			Qt.TransformationMode.SmoothTransformation
		)
		
		# Draw bounding boxes if enabled and available
		if self._show_annotations and self._current_bboxes and self._annotation_data:
			# Calculate scale factor from original to scaled
			scale_x = scaled.width() / self._current_pixmap.width()
			scale_y = scaled.height() / self._current_pixmap.height()
			
			# Create a copy to draw on
			display = QPixmap(scaled)
			painter = QPainter(display)
			painter.setRenderHint(QPainter.RenderHint.Antialiasing)
			
			img_width = self._current_pixmap.width()
			img_height = self._current_pixmap.height()
			
			for labels, x_pct, y_pct, w_pct, h_pct in self._current_bboxes:
				# Convert percentage to pixel coordinates in original image
				x = x_pct * img_width / 100.0
				y = y_pct * img_height / 100.0
				w = w_pct * img_width / 100.0
				h = h_pct * img_height / 100.0
				
				# Scale to display coordinates
				x_scaled = int(x * scale_x)
				y_scaled = int(y * scale_y)
				w_scaled = int(w * scale_x)
				h_scaled = int(h * scale_y)
				
				# Get color for label
				label_name = '-'.join(labels) if labels else "unknown"
				color = self._annotation_data.get_color_for_label(labels[0] if labels else "")
				
				# Draw rectangle
				pen = QPen(color, 2)
				painter.setPen(pen)
				painter.setBrush(Qt.BrushStyle.NoBrush)
				painter.drawRect(x_scaled, y_scaled, w_scaled, h_scaled)
				
				# Draw label text
				font = QFont("Arial", 10, QFont.Weight.Bold)
				painter.setFont(font)
				# Background for text
				text_rect = painter.fontMetrics().boundingRect(label_name)
				text_bg_rect = QRect(
					x_scaled, 
					max(0, y_scaled - text_rect.height() - 4),
					text_rect.width() + 8,
					text_rect.height() + 4
				)
				painter.fillRect(text_bg_rect, QColor(0, 0, 0, 150))
				painter.setPen(color)
				painter.drawText(
					x_scaled + 4, 
					max(text_rect.height(), y_scaled - 4), 
					label_name
				)
			
			painter.end()
			self.preview_label.setPixmap(display)
		else:
			self.preview_label.setPixmap(scaled)
	
	def resizeEvent(self, event):
		super().resizeEvent(event)
		# Refresh display when resized to redraw at new size
		self._refresh_display()


class MainWindow(QMainWindow):
	"""Main application window."""
	
	def __init__(
		self,
		input_dir: Optional[Path] = None,
		output_dir: Optional[Path] = None,
		label_studio_annotations: Optional[Path] = None,
		label_studio_task_id: Optional[int] = None,
	):
		super().__init__()
		self.input_dir = input_dir
		self.output_dir = output_dir
		self.startup_ls_path: Optional[Path] = label_studio_annotations
		self.startup_ls_task_id: Optional[int] = label_studio_task_id
		
		# Frame selection data
		self.data: Optional[FrameSelectionData] = None
		self.current_processing_selection: Set[int] = set()
		self.previous_processing_selection: Set[int] = set()
		self.selected_for_preview_index: Optional[int] = None
		self.manually_selected: Set[int] = set()
		self.manually_unselected: Set[int] = set()
		self.current_params = SelectionParams(
			concentration_percentile=settings.value("selection/concentration_percentile", 90.0, float),
			total_change_percentile=settings.value("selection/total_change_percentile", 60.0, float),
			entropy_percentile=settings.value("selection/entropy_percentile", 40.0, float),
			temporal_window=settings.value("selection/temporal_window", 5, int),
			min_spacing=settings.value("selection/min_spacing", 5, int))
		self.previous_params = SelectionParams()
		
		# Annotation data
		self.annotation_data: Optional[AnnotationData] = None
		
		# Embedding configuration
		self.embedding_config: EmbeddingConfig = EmbeddingConfig(
			model=settings.value("embedding/model", DEFAULT_MODEL, str),
			normalize=settings.value("embedding/normalize", True, bool),
			transform=TransformConfig.from_strings(
				settings.value("embedding/transform", "pad", str),
				settings.value("embedding/alignment", "center", str),
			),
		)
		
		# Worker thread
		self.embedding_worker: Optional[EmbeddingGenerationWorker] = None
		
		self.setup_ui()
		self._update_window_title()
		self._init_menubar()
		self._init_shortcuts()
		self.param_panel.connect_value_changes(self._on_params_changed, self._on_slider_released)
		self.param_panel.auto_apply_checkbox.stateChanged.connect(self._on_auto_apply_toggled)
		self.param_panel.set_params(self.current_params)
		
		# Load data after UI is ready if input_dir is provided
		if self.input_dir is not None:
			QTimer.singleShot(100, self.load_data)
	
	def _update_window_title(self):
		"""Update window title based on current input directory."""
		if self.input_dir is not None:
			self.setWindowTitle(f"Frame Selection Visualizer - {self.input_dir.absolute()}")
		else:
			self.setWindowTitle("Frame Selection Visualizer - (no input loaded)")
	
	def setup_ui(self):
		# Central widget with splitter
		splitter = QSplitter(Qt.Orientation.Horizontal)
		
		# Left: thumbnail grid
		self.thumbnail_grid = ThumbnailGrid(thumbnail_size=80)
		self.thumbnail_grid.thumbnail_clicked.connect(self.on_thumbnail_clicked)
		splitter.addWidget(self.thumbnail_grid)
		
		# Right: preview + navigation + parameters
		right_panel = QWidget()
		right_layout = QVBoxLayout(right_panel)
		
		# Preview at top
		self.preview_panel = PreviewPanel()
		right_layout.addWidget(self.preview_panel, stretch=1)
		
		# Navigation panel
		self.nav_panel = NavigationPanel()
		self.nav_panel.prev_frame.connect(self._nav_left)
		self.nav_panel.next_frame.connect(self._nav_right)
		self.nav_panel.prev_selected.connect(self._nav_prev_selected)
		self.nav_panel.next_selected.connect(self._nav_next_selected)
		self.nav_panel.prev_keyframe.connect(self._nav_prev_keyframe)
		self.nav_panel.next_keyframe.connect(self._nav_next_keyframe)
		self.nav_panel.toggle_force_select.connect(self._toggle_force_select)
		right_layout.addWidget(self.nav_panel)

		# Parameters at bottom
		self.param_panel = ParameterPanel()
		self.param_panel.apply_clicked.connect(self.apply_selection)
		right_layout.addWidget(self.param_panel)
		
		splitter.addWidget(right_panel)
		
		# Set initial splitter sizes (60% thumbnails, 40% right panel)
		splitter.setSizes([600, 400])
		
		self.setCentralWidget(splitter)
		self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
		self.setFocus()
	
	def load_data(self):
		"""Load frame data from cache or generate embeddings if missing."""
		# Clear existing annotations when loading new data
		self.annotation_data = None
		self.preview_panel.set_annotation_data(None)
		self.nav_panel.set_keyframe_navigation_enabled(False)
		self.manually_selected.clear()
		self.manually_unselected.clear()
		
		# Collect frames
		frames = collect_frames(self.input_dir)
		if len(frames) < 2:
			QMessageBox.critical(self, "Error", f"Need at least 2 frames in {self.input_dir}\n\nTry opening a different directory.")
			return
		
		# Try to find existing caches
		cache_dir = self.input_dir / '.embedding_cache'
		cache_list = find_existing_cache_in_dir(cache_dir)
		
		cache_selection_cancelled = False
		if cache_list:
			# Multiple or single cache found
			selected_meta: Optional[EmbeddingMeta] = None
			should_clear_others = False
			
			if len(cache_list) == 1:
				# Single cache, use it directly
				selected_meta = cache_list[0]
			else:
				# Multiple caches - show selection dialog
				result = self._show_cache_selection_dialog(cache_list)
				if result is None:
					cache_selection_cancelled = True
				else:
					selected_meta, should_clear_others = result
			
			if selected_meta is not None:
				# Try to load the embeddings from selected cache
				embeddings = load_cached_embeddings_data(selected_meta.path)
				if embeddings is not None:
					# Successfully loaded from cache
					self.embedding_config = selected_meta.config
					# Clean up other caches if requested
					if should_clear_others and len(cache_list) > 1:
						cache_key = selected_meta.cache_key()
						if cache_key is not None:
							clear_other_caches(cache_dir, cache_key)
					self._load_embeddings_succeeded(frames, embeddings)
					return
				else:
					# Cache metadata exists but embeddings are broken/corrupted
					reply = QMessageBox.critical(
						self, "Broken cache",
						f"Cache metadata found but embeddings are corrupted.\n"
						f"Frames in cache: {selected_meta.frame_count}\n\n"
						f"Would you like to regenerate?",
						QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
					)
					if reply == QMessageBox.StandardButton.Yes:
						self._generate_embeddings(force=True)
					return
		
		if cache_list and cache_selection_cancelled:
			reply = QMessageBox.question(
				self, "Embeddings not loaded",
				"Cache selection was cancelled.\n\n"
				"Generate embeddings now?",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
		else:
			# No valid cache found
			reply = QMessageBox.question(
				self, "Embeddings not found",
				f"No embeddings cache found in {self.input_dir}\n\n"
				f"Generate embeddings now? ({len(frames)} frames)",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
		
		if reply == QMessageBox.StandardButton.Yes:
			self._generate_embeddings(force=True)
	
	def _show_cache_selection_dialog(self, cache_list: List[EmbeddingMeta]):
		"""Show dialog to select from multiple caches. Return (EmbeddingMeta, should_clear_others) or None if cancelled."""
		dialog = QDialog(self)
		dialog.setWindowTitle("Select cache")
		dialog.setMinimumWidth(500)
		
		layout = QVBoxLayout(dialog)
		layout.addWidget(QLabel("Multiple embedding caches found. Select one:"))
		
		combo = QComboBox()
		for meta in cache_list:
			info = format_cache_info(meta)
			combo.addItem(info, userData=meta)
		
		layout.addWidget(combo)
		
		# Add checkbox for clearing other caches
		clear_others_checkbox = QCheckBox("Clear other caches")
		clear_others_checkbox.setToolTip("Remove other cache files to save disk space")
		clear_others_default = settings.value("embedding/clear_others", True, bool)
		clear_others_checkbox.setChecked(clear_others_default)
		layout.addWidget(clear_others_checkbox)
		
		button_box = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
		button_box.accepted.connect(dialog.accept)
		button_box.rejected.connect(dialog.reject)
		layout.addWidget(button_box)
		
		if dialog.exec() == QDialog.Accepted:
			return (combo.currentData(), clear_others_checkbox.isChecked())
		return None
	
	def _load_embeddings_succeeded(self, frames: List[Path], embeddings):
		"""Handle successful embeddings load."""
		self.data = create_frame_selection_data(
			frames,
			embeddings,
			temporal_window=self.current_params.temporal_window)
		
		# If annotations were provided on startup, load them now so they are included in the initial bake
		if self.startup_ls_path:
			# Explicit task id may be None; load_label_studio_annotations will validate
			self.load_label_studio_annotations(self.startup_ls_path, self.startup_ls_task_id)
			# Clear to avoid re-loading
			self.startup_ls_path = None
			self.startup_ls_task_id = None
		
		# Initialize UI: bake thumbnails (includes annotation overlays if any)
		self.thumbnail_grid.set_data_and_bake(self.data, self.annotation_data)
		self.apply_selection()
	
	def _generate_embeddings(self, force: bool = False):
		"""Show embedding generation dialog and generate embeddings."""
		frames = collect_frames(self.input_dir)
		if len(frames) < 2:
			return
		
		# Get auto-detected batch size
		auto_batch_size = estimate_optimal_batch_size()
		
		# Show dialog
		dialog = EmbeddingGenerationDialog(
			self,
			auto_batch_size,
			current_config=self.embedding_config,
			initial_force=force)
		if dialog.exec() != QDialog.Accepted:
			return

		# Create progress dialog
		progress = QProgressDialog("Generating embeddings...", "Cancel", 0, len(frames), self)
		progress.setWindowModality(Qt.WindowModality.WindowModal)
		progress.setWindowTitle("Embedding generation")
		progress.setMinimumDuration(0)
		progress.setValue(0)
		# TODO: Start with "Initializing..." as torch and model loads, then switch to "Generating embeddings..." with frame count

		# Create worker thread
		self.embedding_worker = EmbeddingGenerationWorker(
			frames=frames,
			embedding_config=dialog.get_config(),
			batch_size=dialog.batch_size_spin.value(),
			cache_dir=(self.input_dir / '.embedding_cache'),
			force=dialog.force_checkbox.isChecked(),
			clear_others=dialog.clear_others_checkbox.isChecked())

		# Connect signals
		self.embedding_worker.progress.connect(progress.setValue)
		self.embedding_worker.finished.connect(lambda emb, _: self._on_embeddings_finished(frames, emb, progress))
		self.embedding_worker.error.connect(lambda err: self._on_embeddings_error(err, progress))
		progress.canceled.connect(self.embedding_worker.cancel)
		
		# Start worker
		self.embedding_worker.start()
	
	def _on_embeddings_finished(self, frames: List[Path], embeddings, progress: QProgressDialog):
		"""Handle successful embeddings generation."""
		progress.close()
		
		# Store embedding config
		if self.embedding_worker:
			self.embedding_config = self.embedding_worker.embedding_config
		
		# Load the embeddings
		self._load_embeddings_succeeded(frames, embeddings)
		
		QMessageBox.information(self, "Embeddings generation", "Embeddings generated successfully!")
	
	def _on_embeddings_error(self, error_msg: str, progress: QProgressDialog):
		"""Handle embeddings generation error."""
		progress.close()
		
		reply = QMessageBox.critical(
			self, "Error",
			f"{error_msg}\n\nWould you like to try again with different settings?",
			QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.No
		)
		
		if reply == QMessageBox.StandardButton.Retry:
			self._generate_embeddings(force=True)
	
	def apply_selection(self):
		"""Apply current parameters and update selection."""
		if self.data is None:
			return
		
		self.previous_processing_selection = self.current_processing_selection.copy()
		self.previous_params = self.current_params.copy()
		
		self.current_params = self.param_panel.get_params()
		
		if self.current_params.temporal_window != self.data.temporal_window:
			self.data.apply_smoothing(self.current_params.temporal_window)
		
		selected_indices = self.data.select_frames(
			self.current_params.concentration_percentile,
			self.current_params.total_change_percentile,
			self.current_params.entropy_percentile,
			self.current_params.min_spacing)
		self.current_processing_selection = set(selected_indices)
		
		# Apply manual force selections
		self.current_processing_selection = (
			(self.current_processing_selection | self.manually_selected) - self.manually_unselected
		)
		
		self._ensure_preview_selection()
		self._refresh_thumbnail_states()
		self._show_current_preview()
		
		added = self.current_processing_selection - self.previous_processing_selection
		removed = self.previous_processing_selection - self.current_processing_selection
		self.param_panel.update_stats(
			len(self.current_processing_selection),
			len(self.data),
			len(added),
			len(removed))
		
		self.param_panel.update_previous_display(self.previous_params)
		
		# Save current parameters to settings
		settings.setValue("selection/concentration_percentile", self.current_params.concentration_percentile)
		settings.setValue("selection/total_change_percentile", self.current_params.total_change_percentile)
		settings.setValue("selection/entropy_percentile", self.current_params.entropy_percentile)
		settings.setValue("selection/temporal_window", self.current_params.temporal_window)
		settings.setValue("selection/min_spacing", self.current_params.min_spacing)
		
		# Enable save action once data is loaded
		self.save_action.setEnabled(len(self.current_processing_selection) > 0)

	def _on_params_changed(self):
		"""Auto-apply when enabled to keep selection live."""
		if self.param_panel.any_slider_dragging():
			return
		if self.param_panel.is_auto_apply_enabled():
			self.apply_selection()

	def _on_auto_apply_toggled(self):
		"""When enabling auto-apply, immediately apply once."""
		if self.param_panel.is_auto_apply_enabled():
			self.apply_selection()

	def _on_slider_released(self):
		"""Apply once after slider drag ends when auto-apply is on."""
		if self.param_panel.is_auto_apply_enabled():
			self.apply_selection()

	def _ensure_preview_selection(self):
		"""Ensure preview index exists and stays within bounds."""
		if self.data is None or len(self.data) == 0:
			self.selected_for_preview_index = None
			return
		
		max_index = len(self.data) - 1
		if self.selected_for_preview_index is None:
			if self.current_processing_selection:
				self.selected_for_preview_index = min(self.current_processing_selection)
			else:
				self.selected_for_preview_index = 0
		else:
			self.selected_for_preview_index = min(
				max(self.selected_for_preview_index, 0),
				max_index)

	def _refresh_thumbnail_states(self):
		if self.data is None:
			return
		self.thumbnail_grid.update_states(
			self.current_processing_selection,
			self.previous_processing_selection,
			self.selected_for_preview_index,
			self.manually_selected,
			self.manually_unselected)

	def _show_preview(self, index: int):
		if self.data is None:
			return
		
		path = self.data.get_frame_path(index)
		filename = self.data.get_frame_filename(index)
		frame_number = self.data.get_frame_number(index)
		selected_for_processing = index in self.current_processing_selection
		
		# Get bounding boxes for this frame if annotations are loaded
		bboxes = None
		if self.annotation_data is not None:
			bboxes = self.annotation_data.get_bboxes_for_frame(frame_number)
		
		self.preview_panel.set_image(path, filename, frame_number, selected_for_processing, bboxes)

	def _show_current_preview(self):
		if self.data is None or self.selected_for_preview_index is None:
			return
		self._show_preview(self.selected_for_preview_index)

	def set_selected_for_preview(self, index: int):
		"""Set and display the previewed frame by index."""
		if self.data is None or len(self.data) == 0:
			return
		
		max_index = len(self.data) - 1
		self.selected_for_preview_index = max(0, min(index, max_index))
		self._refresh_thumbnail_states()
		self._show_current_preview()
		self.thumbnail_grid.scroll_to_thumbnail(self.selected_for_preview_index)

	def _focused_widget_blocks_navigation(self) -> bool:
		"""Avoid stealing arrows from inputs that naturally use them."""
		widget = QApplication.focusWidget()
		return isinstance(widget, (QSlider, QSpinBox, QDoubleSpinBox, QAbstractSpinBox))

	def _init_shortcuts(self):
		"""
		Register non-arrow shortcuts for preview navigation.
		
		Arrow keys are handled in keyPressEvent() so they don't interfere with sliders and spin-boxes that need them.
		"""
		self._shortcuts = []
		self._shortcuts.append(self._make_shortcut_sequence("Shift+Left", self._nav_prev_selected))
		self._shortcuts.append(self._make_shortcut_sequence("Shift+Right", self._nav_next_selected))
		self._shortcuts.append(self._make_shortcut_sequence("Ctrl+Left", self._nav_prev_keyframe))
		self._shortcuts.append(self._make_shortcut_sequence("Ctrl+Right", self._nav_next_keyframe))
		self._shortcuts.append(self._make_shortcut(Qt.Key.Key_F, self._toggle_force_select))

	def _init_menubar(self):
		menubar = self.menuBar()

		file_menu = menubar.addMenu("&File")
		
		open_action = QAction("&Open", self)
		open_action.setShortcut(QKeySequence.Open)
		open_action.triggered.connect(self._on_file_open)
		file_menu.addAction(open_action)
		
		save_action = QAction("&Save", self)
		save_action.setShortcut(QKeySequence.Save)
		save_action.triggered.connect(self._on_file_save)
		self.save_action = save_action  # Store for enabling/disabling
		file_menu.addAction(save_action)
		
		file_menu.addSeparator()
		
		# Add Label Studio annotations action
		load_annotations_action = QAction("Add Label Studio &Annotations...", self)
		load_annotations_action.triggered.connect(self._on_load_annotations)
		file_menu.addAction(load_annotations_action)
		
		clear_annotations_action = QAction("&Clear Annotations", self)
		clear_annotations_action.triggered.connect(self._on_clear_annotations)
		file_menu.addAction(clear_annotations_action)
		
		file_menu.addSeparator()
		
		exit_action = QAction("E&xit", self)
		exit_action.triggered.connect(self.close)
		file_menu.addAction(exit_action)

		# Edit menu with embedding generation
		edit_menu = menubar.addMenu("&Edit")
		
		generate_embeddings_action = QAction("&Embeddings...", self)
		generate_embeddings_action.triggered.connect(lambda: self._generate_embeddings())
		self.generate_embeddings_action = generate_embeddings_action
		edit_menu.addAction(generate_embeddings_action)

		view_menu = menubar.addMenu("&View")
		
		# Theme submenu with style + color scheme combinations
		theme_menu = view_menu.addMenu("&Theme")
		self._theme_action_group = QActionGroup(self)
		self._theme_action_group.setExclusive(True)
		
		# Generate theme options from available styles
		for style_name in QStyleFactory.keys():
			for color_scheme in ["Light", "Dark"]:
				label = f"{style_name} ({color_scheme})"
				action = QAction(label, self, checkable=True)
				action.triggered.connect(lambda checked, s=style_name, c=color_scheme.lower(): self._apply_theme(s, c))
				if label == "Fusion (Dark)":  # Default
					action.setChecked(True)
				self._theme_action_group.addAction(action)
				theme_menu.addAction(action)

		help_menu = menubar.addMenu("&Help")
		about_action = QAction("&About", self)
		about_action.triggered.connect(self._show_about)
		help_menu.addAction(about_action)
		
		# Initially disable actions if no data loaded
		self.save_action.setEnabled(self.data is not None)
		self.generate_embeddings_action.setEnabled(self.input_dir is not None)

	def _apply_theme(self, style_name: str, color_scheme: str):
		"""Apply color scheme first, then style."""
		if color_scheme == "light":
			QApplication.styleHints().setColorScheme(Qt.ColorScheme.Light)
		else:
			QApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
		QApplication.setStyle(style_name)

	def _on_file_open(self):
		"""Handle File > Open to select a new input directory."""
		folder = QFileDialog.getExistingDirectory(self, "Open Frame Directory", str(self.input_dir) if self.input_dir else "")
		if not folder:
			return
		
		input_dir = Path(folder)
		if not input_dir.exists():
			QMessageBox.critical(self, "Error", f"Directory does not exist: {input_dir}")
			return
		
		# Update input directory and reload data
		self.input_dir = input_dir
		self._update_window_title()
		self.load_data()

	def _on_file_save(self):
		"""Handle File > Save to export selected frames."""
		if self.data is None or len(self.current_processing_selection) == 0:
			QMessageBox.warning(self, "Warning", "No frames selected for saving.")
			return
		
		folder = QFileDialog.getExistingDirectory(
			self,
			"Select Output Directory",
			str(self.output_dir) if self.output_dir else ""
		)
		if not folder:
			return
		
		output_dir = Path(folder).resolve()
		
		# Prevent saving to the same directory as input frames
		if output_dir == self.input_dir.resolve():
			QMessageBox.critical(self, "Error", "Output directory cannot be the same as the input directory.")
			return
		
		# Check if directory is empty and ask for overwrite confirmation
		try:
			next(output_dir.iterdir())
			# Directory is not empty
			reply = QMessageBox.question(self, "Directory Not Empty",
				f"The directory '{output_dir.name}' is not empty.\n\nDelete all files and proceed?",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
			if reply != QMessageBox.StandardButton.Yes:
				return
			
			# Use prepare_output_dir to clear matching files
			try:
				prepare_output_dir(str(output_dir), ['*.png', '*.jpg', '*.jpeg', '*.webp'])
			except RuntimeError as e:
				QMessageBox.critical(self, "Error", f"Failed to clear directory: {e}")
				return
		except StopIteration:
			# Directory is empty
			output_dir.mkdir(parents=True, exist_ok=True)
		
		# Copy selected frames
		progress = QProgressDialog("Saving frames...", "Cancel", 0, len(self.current_processing_selection), self)
		progress.setWindowModality(Qt.WindowModality.WindowModal)
		progress.setMinimumDuration(0)
		
		copied = 0
		for idx in sorted(self.current_processing_selection):
			if progress.wasCanceled():
				break
			
			src_path = self.data.frames[idx]
			dest_path = output_dir / src_path.name
			try:
				shutil.copy2(src_path, dest_path)
				copied += 1
			except Exception as e:
				QMessageBox.critical(self, "Error", f"Failed to copy {src_path.name}: {e}")
				progress.close()
				return
			
			progress.setValue(copied)
			QApplication.processEvents()
		
		progress.close()
		self.output_dir = output_dir
		QMessageBox.information(self, "Success", f"Saved {copied} frames to:\n{output_dir}")

	def _show_about(self):
		QMessageBox.about(self, "About", "Hello world!")

	def _make_shortcut(self, key: Qt.Key, handler):
		sc = QShortcut(QKeySequence(key), self)
		sc.setContext(Qt.ShortcutContext.WindowShortcut)
		sc.activated.connect(handler)
		return sc

	def _make_shortcut_sequence(self, sequence: str, handler):
		sc = QShortcut(QKeySequence(sequence), self)
		sc.setContext(Qt.ShortcutContext.WindowShortcut)
		sc.activated.connect(handler)
		return sc

	def _nav_left(self):
		self._navigate(delta=-1, wrap=True)

	def _nav_right(self):
		self._navigate(delta=1, wrap=True)

	def _nav_up(self):
		columns = max(1, self.thumbnail_grid.columns)
		self._navigate(delta=-columns, wrap=False)

	def _nav_down(self):
		columns = max(1, self.thumbnail_grid.columns)
		self._navigate(delta=columns, wrap=False)

	def _nav_prev_selected(self):
		self._navigate_to_neighbor_selected(previous=True)

	def _nav_next_selected(self):
		self._navigate_to_neighbor_selected(previous=False)

	def _nav_prev_keyframe(self):
		self._navigate_to_neighbor_keyframe(previous=True)

	def _nav_next_keyframe(self):
		self._navigate_to_neighbor_keyframe(previous=False)

	def _navigate_to_neighbor_keyframe(self, previous: bool):
		"""Navigate to next/previous keyframe."""
		if self.data is None or len(self.data) == 0:
			return
		if self._focused_widget_blocks_navigation():
			return
		if self.annotation_data is None:
			return
		
		self._ensure_preview_selection()
		if self.selected_for_preview_index is None:
			return
		
		current_frame_num = self.data.get_frame_number(self.selected_for_preview_index)
		keyframe_nums = sorted(self.annotation_data.keyframe_frame_numbers)
		
		if not keyframe_nums:
			return
		
		if previous:
			candidates = [kf for kf in keyframe_nums if kf < current_frame_num]
			if candidates:
				target_frame_num = candidates[-1]
			else:
				return
		else:
			candidates = [kf for kf in keyframe_nums if kf > current_frame_num]
			if candidates:
				target_frame_num = candidates[0]
			else:
				return
		
		# Find index for target frame number
		for i in range(len(self.data)):
			if self.data.get_frame_number(i) == target_frame_num:
				self.set_selected_for_preview(i)
				break

	def _toggle_force_select(self):
		"""Toggle force select/unselect for current preview frame."""
		if self.data is None or self.selected_for_preview_index is None:
			return
		
		idx = self.selected_for_preview_index
		
		# Cycle through states: normal -> forced select -> forced unselect -> normal
		if idx in self.manually_selected:
			# Currently forced select -> change to forced unselect
			self.manually_selected.discard(idx)
			self.manually_unselected.add(idx)
		elif idx in self.manually_unselected:
			# Currently forced unselect -> change to normal
			self.manually_unselected.discard(idx)
		else:
			# Currently normal -> change to forced select
			self.manually_selected.add(idx)
		
		# Re-apply selection to update states
		self.apply_selection()

	def _on_load_annotations(self):
		"""Handle File > Add Label Studio Annotations."""
		# Show file picker for JSON
		json_path, _ = QFileDialog.getOpenFileName(
			self,
			"Select Label Studio JSON Export",
			str(self.input_dir) if self.input_dir else "",
			"JSON Files (*.json);;All Files (*)"
		)
		if not json_path:
			return
		
		json_path = Path(json_path)
		
		# Load JSON to get available task IDs
		try:
			with open(json_path, 'r', encoding='utf-8') as f:
				data = json.load(f)
		except Exception as e:
			QMessageBox.critical(self, "Error", f"Failed to load JSON file:\n{e}")
			return
		
		if not isinstance(data, list) or len(data) == 0:
			QMessageBox.critical(self, "Error", "JSON file does not contain any tasks.")
			return
		
		# Get available task IDs
		task_ids = [task.get('id') for task in data if task.get('id') is not None]
		if not task_ids:
			QMessageBox.critical(self, "Error", "No valid task IDs found in JSON.")
			return
		
		# Show dialog for task ID and offset
		dialog = AnnotationLoadDialog(self)
		dialog.task_id_spin.setRange(min(task_ids), max(task_ids))
		dialog.task_id_spin.setValue(task_ids[0])
		
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		
		task_id, frame_offset = dialog.get_values()
		
		# Load annotations using the chosen values
		self.load_label_studio_annotations(json_path, task_id, frame_offset)

	def load_label_studio_annotations(self, json_path: Path, task_id: int, frame_offset: int = 1) -> bool:
		# Load JSON
		try:
			with open(json_path, 'r', encoding='utf-8') as f:
				data = json.load(f)
		except Exception as e:
			QMessageBox.critical(self, "Error", f"Failed to load JSON file:\n{e}")
			return False
		
		if not isinstance(data, list) or len(data) == 0:
			QMessageBox.critical(self, "Error", "JSON file does not contain any tasks.")
			return False
		
		# Find the task
		task_data = None
		for task in data:
			if task.get('id') == task_id:
				task_data = task
				break
		if task_data is None:
			QMessageBox.critical(self, "Error", f"Task ID {task_id} not found in JSON file.")
			return False
		
		# Parse annotations
		tracks = _collect_tracks(task_data)
		if not tracks:
			QMessageBox.warning(self, "Warning", f"No videorectangle annotations found for task {task_id}.")
			return False
		
		frame_bboxes, keyframe_frame_numbers = build_frame_bboxes(tracks, frame_offset)
		
		# Create annotation data
		self.annotation_data = AnnotationData(
			json_path=json_path,
			task_id=task_id,
			frame_offset=frame_offset,
			frame_bboxes=frame_bboxes,
			keyframe_frame_numbers=keyframe_frame_numbers,
			label_colors=dict(DEFAULT_LABEL_COLORS))
		
		# Update UI
		self.preview_panel.set_annotation_data(self.annotation_data)
		self.nav_panel.set_keyframe_navigation_enabled(True)
		
		# Re-bake thumbnails to include annotation bboxes/markers
		if self.data is not None:
			self.thumbnail_grid.rebake_all(self.annotation_data)
		
		# Refresh current preview to show annotations
		self._show_current_preview()
		
		QMessageBox.information(self, "Annotations Loaded",
			f"Loaded {len(frame_bboxes)} annotated frames with {len(keyframe_frame_numbers)} keyframes\n"
			f"from task {task_id} (offset: {frame_offset})")
		return True

	def _on_clear_annotations(self):
		"""Handle File > Clear Annotations."""
		self.annotation_data = None
		self.preview_panel.set_annotation_data(None)
		self.nav_panel.set_keyframe_navigation_enabled(False)
		
		# Re-bake thumbnails without annotations
		if self.data is not None:
			self.thumbnail_grid.rebake_all(None)
		
		self._show_current_preview()

	def _navigate_to_neighbor_selected(self, previous: bool):
		if self.data is None or len(self.data) == 0:
			return
		if self._focused_widget_blocks_navigation():
			return
		if not self.current_processing_selection:
			return
		self._ensure_preview_selection()
		if self.selected_for_preview_index is None:
			return

		sorted_indices = sorted(self.current_processing_selection)
		current = self.selected_for_preview_index
		if previous:
			candidates = [i for i in sorted_indices if i < current]
			if candidates:
				self.set_selected_for_preview(candidates[-1])
		else:
			candidates = [i for i in sorted_indices if i > current]
			if candidates:
				self.set_selected_for_preview(candidates[0])

	def _navigate(self, delta: int, wrap: bool):
		if self.data is None or len(self.data) == 0:
			return
		if self._focused_widget_blocks_navigation():
			return
		self._ensure_preview_selection()
		if self.selected_for_preview_index is None:
			return

		count = len(self.data)
		if wrap:
			new_index = (self.selected_for_preview_index + delta) % count
		else:
			new_index = min(max(self.selected_for_preview_index + delta, 0), count - 1)

		if new_index != self.selected_for_preview_index:
			self.set_selected_for_preview(new_index)
	
	def on_thumbnail_clicked(self, index: int):
		"""Handle thumbnail click."""
		self.set_selected_for_preview(index)

	def keyPressEvent(self, event):
		"""Handle arrow keys for navigation, but only if no widget that needs them has focus."""
		if self._focused_widget_blocks_navigation():
			return super().keyPressEvent(event)
		
		key = event.key()
		modifiers = event.modifiers()
		
		# Handle plain arrow keys
		if modifiers == Qt.KeyboardModifier.NoModifier:
			if key == Qt.Key.Key_Left:
				self._nav_left()
				return
			elif key == Qt.Key.Key_Right:
				self._nav_right()
				return
			elif key == Qt.Key.Key_Up:
				self._nav_up()
				return
			elif key == Qt.Key.Key_Down:
				self._nav_down()
				return
		
		return super().keyPressEvent(event)


def main():
	parser = argparse.ArgumentParser(
		description="Interactive frame selection parameter tuning"
	)
	parser.add_argument("-i", "--input-dir", type=str, default=None,
		help="Directory containing input frames (must have embedding cache)")
	parser.add_argument("-o", "--output-dir", type=str, default=None,
		help="Directory where selected frames will be saved")
	parser.add_argument("--thumbnail-size", type=int, default=80,
		help="Thumbnail size in pixels (default: 80)")
	parser.add_argument("--label-studio-annotations", type=str, default=None,
		help="Path to Label Studio JSON export to load annotations from on startup")
	parser.add_argument("--label-studio-task-id", type=int, default=None,
		help="Optional task ID from the Label Studio JSON to load on startup")
	
	args = parser.parse_args()

	# Require task id when annotation JSON is provided via CLI
	if args.label_studio_annotations and args.label_studio_task_id is None:
		print("Error: --label-studio-task-id must be provided when --label-studio-annotations is used.", file=sys.stderr)
		return 2
	
	input_dir = Path(args.input_dir) if args.input_dir else None
	output_dir = Path(args.output_dir) if args.output_dir else None
	
	if input_dir is not None and not input_dir.exists():
		print(f"Error: Input directory does not exist: {input_dir}")
		return 1
	
	app = QApplication(sys.argv)
	
	# Configure QSettings to use INI file in script directory
	script_dir = Path(__file__).parent
	settings_path = script_dir / "frame_selection_visualizer.ini"
	global settings
	settings = QSettings(str(settings_path), QSettings.IniFormat)
	
	# Set default color scheme and style
	QApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
	app.setStyle("Fusion")
	
	# Additional stylesheet for specific widgets
	app.setStyleSheet("""
		QGroupBox {
			border: 1px solid palette(mid);
			border-radius: 4px;
			margin-top: 8px;
			padding-top: 8px;
			font-weight: bold;
		}
		QGroupBox::title {
			subcontrol-origin: margin;
			left: 8px;
			padding: 0 4px;
		}
	""")
	
	window = MainWindow(
		input_dir, output_dir, 
		label_studio_annotations=(Path(args.label_studio_annotations) if args.label_studio_annotations else None), 
		label_studio_task_id=args.label_studio_task_id)
	window.showMaximized()
	
	return app.exec()


if __name__ == "__main__":
	sys.exit(main())
