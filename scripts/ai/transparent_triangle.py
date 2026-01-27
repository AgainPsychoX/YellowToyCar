#!/usr/bin/env python3
"""
Transparent Triangle Overlay
A frameless, transparent overlay window displaying a configurable triangle.
"""

import sys
import signal
import argparse
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath, QPolygonF


class TriangleOverlay(QWidget):
	angleChanged = Signal()
	
	def __init__(self, height: int = 400, color: QColor = None,
				 border_color: QColor = None,
				 initial_angle: float = 60.0, square_mode: bool = False,
				 right_corner: bool = False):
		super().__init__()
		
		# Triangle parameters
		self.triangle_height = height
		self.color = color if color is not None else QColor(0, 255, 0, 128)
		self.border_color = border_color if border_color is not None else QColor(255, 255, 255, 255)
		
		# Mode and angle state
		self.square_mode = square_mode
		self.right_corner_is_right = right_corner  # True = right, False = left
		
		# Angles (in degrees)
		if square_mode:
			# In square mode, initial_angle is the non-right bottom angle
			self.controlled_angle = max(1.0, min(89.0, initial_angle))
		else:
			# In symmetric mode, initial_angle is the bottom angle
			self.controlled_angle = max(1.0, min(89.5, initial_angle))
		
		# Calculate initial geometry
		self.top_angle = self._calculate_top_angle()
		self.update_geometry()
		
		# Interaction state
		self.dragging = False
		self.drag_start = QPointF()
		
		# Setup window
		self.setup_window()
		
	def setup_window(self):
		"""Configure window properties"""
		# Frameless, transparent, always on top
		self.setWindowFlags(
			Qt.WindowType.FramelessWindowHint |
			Qt.WindowType.WindowStaysOnTopHint |
			Qt.WindowType.Tool
		)
		self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
		self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
		self.setWindowTitle("Transparent Triangle Overlay")
		
		# Enable mouse tracking
		self.setMouseTracking(True)
		
	def _calculate_top_angle(self) -> float:
		"""Calculate top angle based on current mode and controlled angle"""
		if self.square_mode:
			# One angle is 90°, controlled_angle is the other bottom angle
			# Sum of angles = 180°
			return 180.0 - 90.0 - self.controlled_angle
		else:
			# Symmetric mode: both bottom angles are equal
			return 180.0 - 2 * self.controlled_angle
	
	def update_geometry(self, preserve_center: bool = False):
		"""Calculate triangle vertices and update window geometry"""
		import math
		
		# Store current center screen position if preserving
		if preserve_center and hasattr(self, 'center'):
			old_center_screen = self.mapToGlobal(self.center.toPoint())
		
		# Calculate angles
		if self.square_mode:
			if self.right_corner_is_right:
				left_angle = self.controlled_angle
				right_angle = 90.0
			else:
				left_angle = 90.0
				right_angle = self.controlled_angle
		else:
			left_angle = right_angle = self.controlled_angle
		
		top_angle = self._calculate_top_angle()
		
		# Store angles for display
		self.angles = {
			'top': top_angle,
			'left': left_angle,
			'right': right_angle
		}
		
		# Calculate triangle vertices based on mode
		margin = 80  # Margin for labels and markers
		
		if self.square_mode:
			# In square mode, we have a right triangle with height h
			# The right angle is at the base
			if self.right_corner_is_right:
				# Right corner is 90°, left corner is controlled_angle
				# From left corner: tan(left_angle) = height / base_width
				# So: base_width = height / tan(left_angle)
				base_width = self.triangle_height / math.tan(math.radians(left_angle))
				
				self.left_base = QPointF(margin, self.triangle_height + margin)
				self.right_base = QPointF(base_width + margin, self.triangle_height + margin)
				self.apex = QPointF(base_width + margin, margin)
			else:
				# Left corner is 90°, right corner is controlled_angle
				# From right corner: tan(right_angle) = height / base_width
				# So: base_width = height / tan(right_angle)
				base_width = self.triangle_height / math.tan(math.radians(right_angle))
				
				self.left_base = QPointF(margin, self.triangle_height + margin)
				self.right_base = QPointF(base_width + margin, self.triangle_height + margin)
				self.apex = QPointF(margin, margin)
		else:
			# Symmetric mode: isosceles triangle
			# For a triangle with height h and apex angle α at top:
			# Half-base = h * tan(α/2)
			half_base = self.triangle_height * math.tan(math.radians(top_angle / 2))
			base_width = 2 * half_base
			
			self.apex = QPointF(base_width / 2 + margin, margin)
			self.left_base = QPointF(margin, self.triangle_height + margin)
			self.right_base = QPointF(base_width + margin, self.triangle_height + margin)
		
		# Center point for dragging
		self.center = QPointF(
			(self.apex.x() + self.left_base.x() + self.right_base.x()) / 3,
			(self.apex.y() + self.left_base.y() + self.right_base.y()) / 3
		)
		
		# Calculate window size based on actual triangle bounds
		min_x = min(self.apex.x(), self.left_base.x(), self.right_base.x())
		max_x = max(self.apex.x(), self.left_base.x(), self.right_base.x())
		min_y = min(self.apex.y(), self.left_base.y(), self.right_base.y())
		max_y = max(self.apex.y(), self.left_base.y(), self.right_base.y())
		
		width = int(max_x - min_x + 2 * margin)
		height = int(max_y - min_y + 2 * margin)
		
		# Update window geometry
		if preserve_center and hasattr(self, 'center'):
			# Calculate new window position to keep center at same screen location
			new_window_pos = old_center_screen - self.center.toPoint()
			self.setGeometry(new_window_pos.x(), new_window_pos.y(), width, height)
		else:
			current_pos = self.pos()
			self.setGeometry(current_pos.x(), current_pos.y(), width, height)
		
		self.ensure_on_screen()
		
		self.update()
	
	def ensure_on_screen(self):
		"""Ensure center circle stays visible on screen"""
		screen_geometry = QApplication.primaryScreen().availableGeometry()
		
		# Get center position in screen coordinates
		center_screen = self.mapToGlobal(self.center.toPoint())
		handle_radius = 12  # Slightly larger than visual radius for safety margin
		
		# Calculate required window position adjustment
		window_pos = self.pos()
		adjust_x = 0
		adjust_y = 0
		
		# Check if center is outside screen bounds (with handle radius)
		if center_screen.x() - handle_radius < screen_geometry.left():
			adjust_x = screen_geometry.left() - (center_screen.x() - handle_radius)
		elif center_screen.x() + handle_radius > screen_geometry.right():
			adjust_x = screen_geometry.right() - (center_screen.x() + handle_radius)
		
		if center_screen.y() - handle_radius < screen_geometry.top():
			adjust_y = screen_geometry.top() - (center_screen.y() - handle_radius)
		elif center_screen.y() + handle_radius > screen_geometry.bottom():
			adjust_y = screen_geometry.bottom() - (center_screen.y() + handle_radius)
		
		# Move window if adjustment needed
		if adjust_x != 0 or adjust_y != 0:
			self.move(window_pos.x() + adjust_x, window_pos.y() + adjust_y)
	
	def toggle_mode(self):
		"""Toggle between symmetric and square mode, preserving top angle"""
		if self.square_mode:
			# Switch to symmetric mode
			# In symmetric mode, both bottom angles are equal
			# top_angle = 180 - 2*bottom_angle
			# bottom_angle = (180 - top_angle) / 2
			self.square_mode = False
			new_angle = (180.0 - self.top_angle) / 2
			self.controlled_angle = max(1.0, min(89.5, new_angle))
		else:
			# Switch to square mode
			# In square mode: top_angle = 180 - 90 - other_angle
			# other_angle = 180 - 90 - top_angle = 90 - top_angle
			self.square_mode = True
			new_angle = 90.0 - self.top_angle
			self.controlled_angle = max(1.0, min(89.0, new_angle))
		
		self.top_angle = self._calculate_top_angle()
		self.update_geometry(preserve_center=True)
	
	def toggle_right_corner(self):
		"""Toggle right-angle corner position, enable square mode if needed"""
		if not self.square_mode:
			# Enable square mode first
			self.square_mode = True
			new_angle = 90.0 - self.top_angle
			self.controlled_angle = max(1.0, min(89.0, new_angle))
		
		# Toggle corner position
		self.right_corner_is_right = not self.right_corner_is_right
		self.update_geometry(preserve_center=True)
	
	def adjust_angle(self, delta: float):
		"""Adjust the controlled angle"""
		if self.square_mode:
			# Square mode: 1-89 degrees
			self.controlled_angle = max(1.0, min(89.0, self.controlled_angle + delta))
		else:
			# Symmetric mode: 1-89.5 degrees (to allow top angle > 1)
			self.controlled_angle = max(1.0, min(89.5, self.controlled_angle + delta))
		
		self.top_angle = self._calculate_top_angle()
		self.update_geometry(preserve_center=True)
	
	def adjust_height(self, delta: float):
		"""Adjust the triangle height"""
		# Scale delta based on current height for proportional adjustment
		step = max(5, self.triangle_height * 0.05)  # 5% or minimum 5px
		self.triangle_height = max(50, min(2000, self.triangle_height + delta * step))
		self.update_geometry(preserve_center=True)
	
	def paintEvent(self, event):
		"""Draw the triangle and indicators"""
		painter = QPainter(self)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		
		# Draw triangle
		triangle = QPolygonF([self.apex, self.left_base, self.right_base])
		path = QPainterPath()
		path.addPolygon(triangle)
		painter.fillPath(path, self.color)
		
		# Draw border
		painter.setPen(QPen(self.border_color, 2))
		painter.setBrush(Qt.BrushStyle.NoBrush)
		painter.drawPolygon(triangle)
		
		# Draw center handle (white circle)
		painter.setPen(Qt.PenStyle.NoPen)
		painter.setBrush(QColor(255, 255, 255, 200))
		handle_radius = 8
		painter.drawEllipse(self.center, handle_radius, handle_radius)
		
		# Draw right-angle marker in square mode
		if self.square_mode:
			corner = self.right_base if self.right_corner_is_right else self.left_base
			marker_size = 15
			
			# Determine directions for the square marker
			if self.right_corner_is_right:
				# Right corner has 90°
				dx1, dy1 = -marker_size, 0  # Left direction
				dx2, dy2 = 0, -marker_size  # Up direction
			else:
				# Left corner has 90°
				dx1, dy1 = marker_size, 0  # Right direction
				dx2, dy2 = 0, -marker_size  # Up direction
			
			# Draw as a single path to avoid overlapping corners
			pen = QPen(QColor(255, 255, 255, 200), 1)
			pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
			painter.setPen(pen)
			painter.setBrush(Qt.BrushStyle.NoBrush)  # Don't fill the path
			
			# Create a path for the right-angle marker
			marker_path = QPainterPath()
			marker_path.moveTo(corner.x() + dx1, corner.y() + dy1)
			marker_path.lineTo(corner.x() + dx1 + dx2, corner.y() + dy1 + dy2)
			marker_path.lineTo(corner.x() + dx2, corner.y() + dy2)
			painter.drawPath(marker_path)
		
		# Draw angle labels
		painter.setPen(QColor(255, 255, 255, 230))
		# Scale font size based on triangle height (base size 10 at height 200)
		font_size = max(8, min(14, int(10 * (self.triangle_height / 200))))
		font = QFont("Arial", font_size, QFont.Weight.Bold)
		painter.setFont(font)
		
		# Top angle (centered above apex, closer to triangle)
		top_text = f"{self.angles['top']:.1f}°"
		top_rect = QRectF(self.apex.x() - 50, self.apex.y() - 25, 100, 20)
		painter.drawText(top_rect, Qt.AlignmentFlag.AlignCenter, top_text)
		
		# Left angle (below and to the left of corner, outside triangle)
		left_text = f"{self.angles['left']:.1f}°"
		left_rect = QRectF(self.left_base.x() - 40, self.left_base.y(), 80, 20)
		painter.drawText(left_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, left_text)
		
		# Right angle (below and to the right of corner, outside triangle)
		right_text = f"{self.angles['right']:.1f}°"
		right_rect = QRectF(self.right_base.x() - 40, self.right_base.y(), 80, 20)
		painter.drawText(right_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, right_text)
	
	def mousePressEvent(self, event):
		"""Handle mouse press for dragging"""
		if event.button() == Qt.MouseButton.LeftButton:
			# Check if clicking on center handle
			dx = event.position().x() - self.center.x()
			dy = event.position().y() - self.center.y()
			distance = (dx * dx + dy * dy) ** 0.5
			
			if distance <= 12:  # Handle radius + small margin
				self.dragging = True
				self.drag_start = event.globalPosition() - QPointF(self.pos())
				event.accept()
	
	def mouseMoveEvent(self, event):
		"""Handle mouse move for dragging"""
		if self.dragging:
			new_pos = event.globalPosition() - self.drag_start
			self.move(int(new_pos.x()), int(new_pos.y()))
			event.accept()
	
	def mouseReleaseEvent(self, event):
		"""Handle mouse release"""
		if event.button() == Qt.MouseButton.LeftButton:
			self.dragging = False
			self.ensure_on_screen()  # Only check bounds after drag is complete
			event.accept()
	
	def wheelEvent(self, event):
		"""Handle mouse wheel for angle or height adjustment"""
		delta = event.angleDelta().y() / 120.0  # Standard wheel step
		modifiers = event.modifiers()
		
		# Check for Ctrl modifier for height adjustment
		if modifiers & Qt.KeyboardModifier.ControlModifier:
			self.adjust_height(delta)
		else:
			# Check for Shift modifier for fine angle adjustment
			if modifiers & Qt.KeyboardModifier.ShiftModifier:
				delta *= 0.1  # Fine adjustment
			
			self.adjust_angle(delta)
		
		event.accept()
	
	def keyPressEvent(self, event):
		"""Handle keyboard input"""
		key = event.key()
		modifiers = event.modifiers()
		
		# Check for Ctrl+C
		if key == Qt.Key.Key_C and modifiers & Qt.KeyboardModifier.ControlModifier:
			QApplication.quit()
		elif key in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
			QApplication.quit()
		elif key == Qt.Key.Key_S:
			self.toggle_mode()
		elif key == Qt.Key.Key_R:
			self.toggle_right_corner()
		else:
			event.ignore()


def prepare_QColor(color_str: str, alpha: int = None) -> QColor:
	color = QColor.fromString(color_str.strip())
	if not color.isValid():
		color = QColor.fromString('#' + color_str)
		if not color.isValid():
			raise ValueError(f"Invalid color format: {color_str}")

	if alpha is not None:
		color.setAlpha(alpha)
		return color
	if len(color_str.lstrip("#")) == 8: # assuming RRGGBBAA format
		return color
	else:
		color.setAlpha(64)
		return color

def main():
	"""Main application entry point"""
	parser = argparse.ArgumentParser(description="Transparent Triangle Overlay")
	parser.add_argument("--height", type=int, default=200,
		help="Triangle height in pixels (default: 200)")
	parser.add_argument("--color", type=str, default="00FF00",
		help="Triangle color as [#]RRGGBB[AA] (default: 00FF00/green)")
	parser.add_argument("--border", type=str, default="FFFFFF",
		help="Border color as [#]RRGGBB[AA] (default: FFFFFF/white)")
	parser.add_argument("--alpha", type=int, default=None,
		help="Alpha transparency 0-255 (default: 64). Applied to both if colors don't include alpha.")
	parser.add_argument("--angle", type=float, default=60.0,
		help="Initial controlled angle in degrees (default: 60)")
	parser.add_argument("--right", action="store_true",
		help="Place 90° corner at bottom-right (enables square mode)")
	parser.add_argument("--left", action="store_true",
		help="Place 90° corner at bottom-left (enables square mode)")
	
	args = parser.parse_args()
	
	# Parse and validate color
	try:
		color = prepare_QColor(args.color, args.alpha)
	except ValueError as e:
		print(f"Error: {e}")
		return 1
	
	# Parse border color (use alpha if specified)
	try:
		border_color = prepare_QColor(args.border, args.alpha)
	except ValueError as e:
		print(f"Error: {e}")
		return 1
	
	# Determine square mode and corner position
	# If either --right or --left is specified, enable square mode
	square_mode = args.right or args.left
	right_corner = args.right and not args.left
	
	# Create application
	app = QApplication(sys.argv)
	
	# Setup signal handler for Ctrl+C
	signal.signal(signal.SIGINT, lambda *_: QApplication.quit())
	
	# Use a timer to allow Python signal handlers to run
	# This ensures Ctrl+C is processed even when Qt event loop is busy
	timer = QTimer()
	timer.timeout.connect(lambda: None)  # Empty slot to let Python interrupt
	timer.start(100)  # Check every 100ms
	
	# Create and show overlay
	overlay = TriangleOverlay(
		height=args.height,
		color=color,
		border_color=border_color,
		initial_angle=args.angle,
		square_mode=square_mode,
		right_corner=right_corner
	)
	overlay.show()
	
	return app.exec()


if __name__ == "__main__":
	sys.exit(main())
