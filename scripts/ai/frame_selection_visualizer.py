#!/usr/bin/env python3
"""
Frame Selection Visualizer

Interactive GUI for tuning frame selection parameters.
Requires embeddings cache to be pre-computed by select_frames_vit.py
"""

import sys
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import List, Set, Optional

from PySide6.QtWidgets import (
	QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
	QLabel, QSlider, QPushButton, QScrollArea, QGridLayout,
	QFrame, QSplitter, QProgressDialog, QMessageBox, QGroupBox,
	QSpinBox, QDoubleSpinBox, QAbstractSpinBox, QCheckBox, QStyleFactory
)
from PySide6.QtCore import Qt, Signal, QSize, QThread, QTimer, QRect
from PySide6.QtGui import (
	QPixmap, QImage, QColor, QPainter, QPen, QFont, QShortcut, QKeySequence,
	QAction, QActionGroup
)

from frame_selection_core import load_frame_data, FrameSelectionData


@dataclass
class SelectionParams:
	"""Parameters for frame selection."""
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


class ThumbnailWidget(QLabel):
	"""Widget displaying a single thumbnail with selection state."""
	
	clicked = Signal(int)
	
	# State colors
	COLOR_SELECTED = QColor(60, 120, 200, 180)      # Blue - selected for processing
	COLOR_ADDED = QColor(60, 200, 60, 180)          # Green - newly added
	COLOR_REMOVED = QColor(200, 60, 60, 180)        # Red - just removed
	COLOR_NORMAL = QColor(80, 80, 80, 120)          # Gray - not selected
	COLOR_PREVIEW = QColor(240, 200, 60, 200)       # Yellow - currently previewed
	
	def __init__(self, index: int, thumbnail_size: int = 80):
		super().__init__()
		self.index = index
		self.thumbnail_size = thumbnail_size
		self.frame_number = None  # Will be set later
		self.pixmap_original: Optional[QPixmap] = None
		self.is_selected_for_processing = False
		self.is_added = False
		self.is_removed = False
		self.is_previewed = False
		
		# Account for space below for filename label
		total_height = thumbnail_size + 8 + 20  # 20px for text
		self.setFixedSize(thumbnail_size + 8, total_height)
		self.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop)
		self.setStyleSheet("background-color: #333;")
	
	def set_pixmap(self, pixmap: QPixmap):
		"""Set the thumbnail pixmap."""
		self.pixmap_original = pixmap.scaled(
			self.thumbnail_size, self.thumbnail_size,
			Qt.AspectRatioMode.KeepAspectRatio,
			Qt.TransformationMode.SmoothTransformation)
		self._update_display()
	
	def set_frame_number(self, frame_number: int):
		"""Set the frame number label."""
		self.frame_number = frame_number
		self._update_display()
	
	def set_state(
		self,
		selected_for_processing: bool,
		added: bool = False,
		removed: bool = False,
		previewed: bool = False
	):
		"""Update selection/preview state."""
		self.is_selected_for_processing = selected_for_processing
		self.is_added = added
		self.is_removed = removed
		self.is_previewed = previewed
		self._update_display()
	
	def _update_display(self):
		"""Redraw with current state."""
		if self.pixmap_original is None:
			return
		
		# Create display pixmap with border and text space
		total_height = self.thumbnail_size + 8 + 20
		display = QPixmap(self.thumbnail_size + 8, total_height)
		display.fill(Qt.GlobalColor.transparent)
		
		painter = QPainter(display)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		
		# Determine border color
		if self.is_added:
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
		
		# Draw thumbnail centered
		x = (self.thumbnail_size + 8 - self.pixmap_original.width()) // 2
		y = (self.thumbnail_size + 8 - self.pixmap_original.height()) // 2
		painter.drawPixmap(x, y, self.pixmap_original)
		
		# Draw frame number text below
		if self.frame_number is not None:
			painter.setPen(QColor(200, 200, 200, 200))
			font = QFont("Arial", 7, QFont.Weight.Bold)
			painter.setFont(font)
			
			display_text = f"#{self.frame_number}"
			
			text_rect = QRect(0, self.thumbnail_size + 8, self.thumbnail_size + 8, 20)
			painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, display_text)
		
		painter.end()
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
	
	def set_thumbnail(self, index: int, pixmap: QPixmap, frame_number: int = None):
		"""Set pixmap and frame number for a specific thumbnail."""
		if 0 <= index < len(self.thumbnails):
			self.thumbnails[index].set_pixmap(pixmap)
			if frame_number is not None:
				self.thumbnails[index].set_frame_number(frame_number)
	
	def update_states(
		self,
		selected_for_processing: Set[int],
		previous_selected_for_processing: Set[int],
		selected_for_preview_index: Optional[int]
	):
		"""Update thumbnail states based on processing/preview selections."""
		added = selected_for_processing - previous_selected_for_processing
		removed = previous_selected_for_processing - selected_for_processing
		
		for i, thumb in enumerate(self.thumbnails):
			is_selected = i in selected_for_processing
			is_added = i in added
			is_removed = i in removed
			is_previewed = selected_for_preview_index is not None and i == selected_for_preview_index
			thumb.set_state(is_selected, is_added, is_removed, is_previewed)

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
			f"({100*selected_for_processing/total:.1f}%)"
		)
		
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
	"""Panel showing preview of selected image."""
	
	def __init__(self):
		super().__init__()
		self.setup_ui()
	
	def setup_ui(self):
		layout = QVBoxLayout(self)
		
		# Image preview
		self.preview_label = QLabel()
		self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self.preview_label.setMinimumSize(300, 300)
		self.preview_label.setStyleSheet("background-color: #222; border: 1px solid #444;")
		layout.addWidget(self.preview_label, stretch=1)
		
		# Info label
		self.info_label = QLabel("Click a thumbnail to preview")
		self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self.info_label.setStyleSheet("color: #888; padding: 8px;")
		layout.addWidget(self.info_label)
	
	def set_image(self, path: Path, filename: str, frame_number: int, selected_for_processing: bool):
		"""Display an image along with its frame number."""
		pixmap = QPixmap(str(path))
		if pixmap.isNull():
			self.preview_label.setText("Failed to load image")
			return
		
		# Scale to fit
		scaled = pixmap.scaled(
			self.preview_label.size(),
			Qt.AspectRatioMode.KeepAspectRatio,
			Qt.TransformationMode.SmoothTransformation
		)
		self.preview_label.setPixmap(scaled)
		
		status = "SELECTED for processing" if selected_for_processing else "not selected for processing"
		self.info_label.setText(f"Frame {frame_number}: {filename} ({status})")
	
	def resizeEvent(self, event):
		super().resizeEvent(event)
		# Could refresh preview on resize, but skip for now


class MainWindow(QMainWindow):
	"""Main application window."""
	
	def __init__(self, input_dir: Path):
		super().__init__()
		self.input_dir = input_dir
		self.data: Optional[FrameSelectionData] = None
		self.current_processing_selection: Set[int] = set()
		self.previous_processing_selection: Set[int] = set()
		self.selected_for_preview_index: Optional[int] = None
		self.current_params = SelectionParams()
		self.previous_params = SelectionParams()
		
		self.setup_ui()
		self.setWindowTitle(f"Frame Selection Visualizer - {input_dir.name}")
		self._init_menubar()
		self._init_shortcuts()
		self.param_panel.connect_value_changes(self._on_params_changed, self._on_slider_released)
		self.param_panel.auto_apply_checkbox.stateChanged.connect(self._on_auto_apply_toggled)
		
		# Load data after UI is ready
		QTimer.singleShot(100, self.load_data)
	
	def setup_ui(self):
		# Central widget with splitter
		splitter = QSplitter(Qt.Orientation.Horizontal)
		
		# Left: thumbnail grid
		self.thumbnail_grid = ThumbnailGrid(thumbnail_size=80)
		self.thumbnail_grid.thumbnail_clicked.connect(self.on_thumbnail_clicked)
		splitter.addWidget(self.thumbnail_grid)
		
		# Right: preview + parameters
		right_panel = QWidget()
		right_layout = QVBoxLayout(right_panel)
		
		# Preview at top
		self.preview_panel = PreviewPanel()
		right_layout.addWidget(self.preview_panel, stretch=1)
		
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
		"""Load frame data from cache."""
		progress = QProgressDialog("Loading frame data...", None, 0, 0, self)
		progress.setWindowModality(Qt.WindowModality.WindowModal)
		progress.setMinimumDuration(0)
		progress.show()
		QApplication.processEvents()
		
		self.data = load_frame_data(self.input_dir, temporal_window=self.current_params.temporal_window)
		
		progress.close()
		
		if self.data is None:
			QMessageBox.critical(
				self,
				"Error",
				f"No embedding cache found in {self.input_dir}\n\n"
				"Run select_frames_vit.py first to generate embeddings cache."
			)
			QTimer.singleShot(100, self.close)
			return
		
		# Initialize UI
		self.thumbnail_grid.set_frame_count(len(self.data))
		
		# Load thumbnails in background
		self.load_thumbnails()
		
		# Apply initial selection
		self.apply_selection()
	
	def load_thumbnails(self):
		"""Load thumbnail images."""
		if self.data is None:
			return
		
		progress = QProgressDialog("Loading thumbnails...", "Cancel", 0, len(self.data), self)
		progress.setWindowModality(Qt.WindowModality.WindowModal)
		progress.setMinimumDuration(500)
		
		for i in range(len(self.data)):
			if progress.wasCanceled():
				break
			
			path = self.data.get_frame_path(i)
			frame_num = self.data.frames[i][0]  # Get frame number from tuple
			pixmap = QPixmap(str(path))
			if not pixmap.isNull():
				self.thumbnail_grid.set_thumbnail(i, pixmap, frame_num)
			
			progress.setValue(i + 1)
			if i % 50 == 0:
				QApplication.processEvents()
		
		progress.close()
	
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
			self.current_params.min_spacing
		)
		self.current_processing_selection = set(selected_indices)
		
		self._ensure_preview_selection()
		self._refresh_thumbnail_states()
		self._show_current_preview()
		
		added = self.current_processing_selection - self.previous_processing_selection
		removed = self.previous_processing_selection - self.current_processing_selection
		self.param_panel.update_stats(
			len(self.current_processing_selection),
			len(self.data),
			len(added),
			len(removed)
		)
		
		self.param_panel.update_previous_display(self.previous_params)

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
			self.selected_for_preview_index
		)

	def _show_preview(self, index: int):
		if self.data is None:
			return
		
		path = self.data.get_frame_path(index)
		filename = self.data.get_frame_filename(index)
		frame_number = self.data.frames[index][0]
		selected_for_processing = index in self.current_processing_selection
		self.preview_panel.set_image(path, filename, frame_number, selected_for_processing)

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
		"""Register arrow key shortcuts for preview navigation."""
		self._shortcuts = []
		self._shortcuts.append(self._make_shortcut(Qt.Key.Key_Left, self._nav_left))
		self._shortcuts.append(self._make_shortcut(Qt.Key.Key_Right, self._nav_right))
		self._shortcuts.append(self._make_shortcut(Qt.Key.Key_Up, self._nav_up))
		self._shortcuts.append(self._make_shortcut(Qt.Key.Key_Down, self._nav_down))
		self._shortcuts.append(self._make_shortcut_sequence("Shift+Left", self._nav_prev_selected))
		self._shortcuts.append(self._make_shortcut_sequence("Shift+Right", self._nav_next_selected))

	def _init_menubar(self):
		menubar = self.menuBar()

		file_menu = menubar.addMenu("&File")
		exit_action = QAction("E&xit", self)
		exit_action.triggered.connect(self.close)
		file_menu.addAction(exit_action)

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

	def _apply_theme(self, style_name: str, color_scheme: str):
		"""Apply color scheme first, then style."""
		if color_scheme == "light":
			QApplication.styleHints().setColorScheme(Qt.ColorScheme.Light)
		else:
			QApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
		QApplication.setStyle(style_name)

	def _show_about(self):
		QMessageBox.about(self, "About", "Hello world!")

	def _make_shortcut(self, key: Qt.Key, handler):
		sc = QShortcut(QKeySequence(key), self)
		sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
		sc.activated.connect(handler)
		return sc

	def _make_shortcut_sequence(self, sequence: str, handler):
		sc = QShortcut(QKeySequence(sequence), self)
		sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
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
		"""Defer key handling; navigation handled via shortcuts to avoid double triggers."""
		return super().keyPressEvent(event)


def main():
	parser = argparse.ArgumentParser(
		description="Interactive frame selection parameter tuning"
	)
	parser.add_argument(
		"-i", "--input-dir",
		type=str,
		required=True,
		help="Directory containing input frames (must have embedding cache)"
	)
	parser.add_argument(
		"--thumbnail-size",
		type=int,
		default=80,
		help="Thumbnail size in pixels (default: 80)"
	)
	
	args = parser.parse_args()
	input_dir = Path(args.input_dir)
	
	if not input_dir.exists():
		print(f"Error: Input directory does not exist: {input_dir}")
		return 1
	
	app = QApplication(sys.argv)
	
	# Set default color scheme and style
	QApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
	app.setStyle("Fusion")
	
	# Additional stylesheet for specific widgets
	app.setStyleSheet("""
		QGroupBox {
			border: 1px solid #444;
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
		/*
		QMainWindow, QWidget {
			background-color: #2b2b2b;
			color: #ddd;
		}
		QGroupBox {
			border: 1px solid #444;
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
		QPushButton {
			background-color: #444;
			border: 1px solid #555;
			border-radius: 4px;
			padding: 6px 12px;
		}
		QPushButton:hover {
			background-color: #555;
		}
		QPushButton:pressed {
			background-color: #333;
		}
		QSlider::groove:horizontal {
			height: 6px;
			background: #444;
			border-radius: 3px;
		}
		QSlider::handle:horizontal {
			width: 16px;
			margin: -5px 0;
			background: #888;
			border-radius: 8px;
		}
		QSlider::handle:horizontal:hover {
			background: #aaa;
		}
		QSpinBox, QDoubleSpinBox {
			background-color: #333;
			border: 1px solid #444;
			border-radius: 3px;
			padding: 4px;
		}
		QScrollArea {
			border: none;
		}
		QScrollBar:vertical {
			background: #333;
			width: 12px;
		}
		QScrollBar::handle:vertical {
			background: #555;
			border-radius: 6px;
			min-height: 20px;
		}
		QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
			height: 0;
		}
				   */
	""")
	
	window = MainWindow(input_dir)
	window.showMaximized()
	
	return app.exec()


if __name__ == "__main__":
	sys.exit(main())
