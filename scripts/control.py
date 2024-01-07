import argparse
from copy import copy
from dataclasses import dataclass
import requests
import time
import keyboard
import socket
import struct
from math import floor

def clamp(value, low, high): 
	return max(low, min(value, high))

################################################################################

class CarControlData:
	"""
	Set of data used to control the car.

	Motor values range from 0.0 to 1.0 (full stop to full speed).
	"""

	def __init__(self) -> None:
		self.left_motor = 0
		self.right_motor = 0
		self.main_light = False
		self.other_light = False

	@property
	def flags(self):
		flags = 0
		if self.main_light:
			flags |= 0b00000001
		if self.other_light:
			flags |= 0b00000010
		if self.left_motor < 0:
			flags |= 0b01000000
		if self.right_motor < 0:
			flags |= 0b10000000
		return flags

	def to_short_packet(self):
		return struct.pack('BBBB', 
			1, 
			self.flags, 
			round(abs(self.left_motor) * 255), 
			round(abs(self.right_motor) * 255)
		)
	
	def to_long_packet(self):
		return struct.pack('BBHff', 
			2, 
			self.flags, 
			0, 
			self.left_motor * 100, 
			self.right_motor * 100
		)

########################################

class CarConnection:
	"""Represents connection to the car."""

	def __init__(self, ip: str, address: str, port_udp = 83, use_short_packet = False, show_packets = True) -> None:
		self.ip = ip
		self.address = address
		self.port_udp = port_udp
		self.use_short_packet = use_short_packet
		self.show_packets = show_packets

	def get_status(self):
		response = requests.get(f'http://{self.address}/status', timeout=5)
		response.raise_for_status()
		return response.json()

	def _connect(self):
		try:
			o = self.get_status()
			self.start_time = time.time()
			self.start_uptime = int(o['uptime'])
			self.start_uptime_ms = round(self.start_uptime / 1000)
		except requests.exceptions.HTTPError as e:
			print(f'Querying device status failed with status code: {e.response.status_code}')
			return None
		except requests.exceptions.ConnectTimeout as e:
			print(e)
			return None
		print(f'Status retrieved, uptime: {self.start_uptime_ms}ms')

		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # UDP
		self.sock.connect((self.ip, self.port_udp))
		local_port = self.sock.getsockname()[1]
		print(f'UDP socket open at local port {local_port}')
		# TODO: implement some ping mechanism in the UDP server and use here to test UDP connection

		return self

	@staticmethod
	def connect(address: str, port_udp = 83, use_short_packet = False, show_packets = True):
		ip = socket.gethostbyname(address)
		
		print(f"Connecting to {ip}...")
		instance = CarConnection(ip, address, port_udp, use_short_packet, show_packets)
		if instance._connect() is None:
			return None
		print(f"Connected to {ip}")
		return instance

	def close(self):
		self.sock.close()

	def control_udp(self, data: CarControlData):
		"""Sends the UDP packet to control the car"""
		if self.use_short_packet:
			bytes = data.to_short_packet()
		else:
			bytes = data.to_long_packet()
		self.sock.sendto(bytes, (self.ip, self.port_udp))
		if self.show_packets:
			expected_uptime_ms = self.start_uptime_ms + floor((time.time() - self.start_time) * 1000)
			if self.use_short_packet:
				print(f'  ({expected_uptime_ms}) udp: ShortControlPacket: F:{data.flags:02X} T:0ms L:{round(abs(data.left_motor) * 255) * 100:.2f} R:{round(abs(data.right_motor) * 255):.3f}')
			else:
				print(f'  ({expected_uptime_ms}) udp: LongControlPacket: F:{data.flags:02X} T:0ms L:{data.left_motor * 100:.2f} R{data.right_motor * 100:.2f}')

	def control_http(self, data: CarControlData):
		"""Sends the HTTP packet to control the car"""
		# TODO: actually code control in HTTP server of the car
		return
		# Hacky, but simple way to skip waiting for response, see https://stackoverflow.com/a/45601591/4880243
		try:
			requests.post(f"http://{self.address}/config", timeout=0.01, json={
				"control": {
					"mainLight": int(data.main_light),
					"otherLight": int(data.other_light),
					"left": round(data.left_motor * 100, 2), 
					"right": round(data.right_motor * 100, 2),
				},
				"silent": 1 # don't generate output 
			})
		except requests.exceptions.ConnectTimeout as e:
			print(e)
		except requests.exceptions.ReadTimeout: 
			pass

########################################

class DryRunCarConnection(CarConnection):
	def _connect(self):
		return self

	def close(self):
		pass

	def control_udp(self, data: CarControlData):
		print(f'UDP LM: {data.left_motor:.3f}, RM: {data.right_motor:.3f}, ML: {data.main_light}, OL: {data.other_light}')

	def control_http(self, data: CarControlData):
		print(f'HTTP LM: {data.left_motor:.3f}, RM: {data.right_motor:.3f}, ML: {data.main_light}, OL: {data.other_light}')

########################################

@dataclass
class BaseDrivingModelOptions:
	interval: float = 0.100
	min_speed: float = 0.1
	max_speed: float = 1.0
	blink_other_light: bool = True

class BaseDrivingModel(CarControlData):
	"""Base driving model, allowing for raw operations."""

	def __init__(self, options: BaseDrivingModelOptions) -> None:
		super().__init__()
		self.options = options
		self.last_update = time.time()

	@property
	def delta_time(self):
		"""Delta time, float in seconds, from last update."""
		return time.time() - self.last_update

	@property
	def time_until_next_update(self):
		return max(0, self.options.interval - self.delta_time)

	def bind(self, connection: CarConnection):
		self.connection = connection

	def packet(self):
		if self.options.blink_other_light:
			self.other_light = not self.other_light
		self.update()
		self.connection.control_udp(self)

	def toggle_main_light(self):
		self.main_light = not self.main_light
		self.packet()

	def toggle_other_light(self):
		self.other_light = not self.other_light
		self.packet()

	def update(self):
		if abs(self.left_motor) < self.options.min_speed:
			self.left_motor = 0
		else:
			self.left_motor = clamp(self.left_motor, -self.options.max_speed, self.options.max_speed)

		if abs(self.right_motor) < self.options.min_speed:
			self.right_motor = 0
		else:
			self.right_motor = clamp(self.right_motor, -self.options.max_speed, self.options.max_speed)

		self.last_update = time.time()

	def stop(self):
		self.left_motor = 0
		self.right_motor = 0
		self.other_light = True
		self.connection.control_udp(self)
		self.connection.control_http(self)

	def brake(self):
		self.raw(0, 0)

	def idle(self):
		self.left_motor = 0
		self.right_motor = 0

	def raw(self, left: float, right: float):
		self.left_motor = left
		self.right_motor = right
		self.packet()
	
	def rotate(self, speed = 1.0):
		self.raw(speed, -speed)

########################################

class VectorizedDrivingModel(BaseDrivingModel):
	"""Driving model where movement is represented by a vector of direction and speed."""

	NEUTRAL_DIRECTION = 0.0

	def __init__(self, options) -> None:
		super().__init__(options)
		self.speed = 0
		self.direction = self.NEUTRAL_DIRECTION
		self.is_raw = False

	def update(self):
		if not self.is_raw:
			self.left_motor = self.speed * (1 + self.direction) / (1 + abs(self.direction))
			self.right_motor = self.speed * (1 - self.direction) / (1 + abs(self.direction))
		super().update()

	def stop(self):
		self.is_raw = True
		self.speed = 0
		self.direction = self.NEUTRAL_DIRECTION
		super().stop()

	def brake(self):
		self.is_raw = False
		self.speed = 0

	def idle(self):
		self.speed = 0
		self.direction = self.NEUTRAL_DIRECTION

	def raw(self, left: float, right: float):
		self.is_raw = True
		super().raw(left, right)

	def turn(self, direction = NEUTRAL_DIRECTION):
		self.is_raw = False
		self.direction = direction
		self.packet()

	def throttle(self, speed = 0):
		self.is_raw = False
		self.speed = speed
		self.packet()

	# TODO: smoothing for rotation

########################################

@dataclass
class SmoothedDrivingModelOptions(BaseDrivingModelOptions):
	speed_response: float = 1
	speed_decay: float = 0.5
	direction_response: float = 1
	direction_decay: float = 0.5

	def changes_multiplied(self, factor):
		new = copy(self)
		new.speed_response *= factor
		new.speed_decay *= factor
		new.direction_response *= factor
		new.direction_decay *= factor
		return new

class SmoothedVectorizedDrivingModel(VectorizedDrivingModel):
	"""
	Driving model where movement is represented by a vector of direction and speed, 
	with smoothing by limiting values change in time.
	"""

	def __init__(self, options: SmoothedDrivingModelOptions) -> None:
		super().__init__(options)
		self.options = options # redundant, to get proper type hinting
		self.target_speed = 1
		self.target_direction = VectorizedDrivingModel.NEUTRAL_DIRECTION

	def update(self):
		speed_delta = self.delta_time * (
			self.options.speed_decay if self.target_speed == 0 else self.options.speed_response)
		if self.speed < self.target_speed:
			self.speed = min(self.target_speed, self.speed + speed_delta)
		else:
			self.speed = max(self.target_speed, self.speed - speed_delta)

		direction_delta = self.delta_time * (
			self.options.direction_decay if self.target_direction == VectorizedDrivingModel.NEUTRAL_DIRECTION else self.options.direction_response)
		if self.direction < self.target_direction:
			self.direction = min(self.target_direction, self.direction + direction_delta)
		else:
			self.direction = max(self.target_direction, self.direction - direction_delta)

		super().update()

	def stop(self):
		self.target_direction = VectorizedDrivingModel.NEUTRAL_DIRECTION
		self.target_speed = 0
		super().stop()

	def brake(self):
		self.is_raw = False
		self.target_speed = -0.0001 # dirty way to use response instead decay

	def idle(self):
		self.is_raw = False
		self.target_speed = 0
		self.target_direction = self.NEUTRAL_DIRECTION

	def turn(self, direction = VectorizedDrivingModel.NEUTRAL_DIRECTION):
		self.is_raw = False
		self.target_direction = direction

	def throttle(self, speed = 0):
		self.is_raw = False
		self.target_speed = speed

################################################################################

def main():
	parser = argparse.ArgumentParser(
		description='''This script allows to control the car by continuously reading keyboard inputs and sending packets.''',
		epilog='''Note: The 'keyboard' library were used (requires sudo under Linux), and it hooks work also out of focus, which is benefit and issue at the same time, so please care.''',
		formatter_class=argparse.ArgumentDefaultsHelpFormatter
	)
	parser.add_argument('--ip', '--address',   default='192.168.4.1', dest='address', required=False, help='IP of the device.')
	parser.add_argument('--port',              default=83, type=int, required=False, help='Port of UDP control server.')
	parser.add_argument('--interval',          default=100, type=int, required=False, help='Interval between control packets in milliseconds.')
	parser.add_argument('--dry-run',           action='store_true', required=False, help='Performs dry-run for testing.')
	parser.add_argument('--show-packets',      action='store_true', required=False, help='Show sent packets.')
	parser.add_argument('--short-packet-type', action='store_true', required=False, help='Uses short packet type instead long.')
	parser.add_argument('--no-blink',          action='store_true', required=False, help='Prevents default behaviour of constant status led blinking.')
	driving = parser.add_argument_group('Driving model')
	driving.add_argument('--max-speed',          default=1.0, type=float, required=False, metavar='VALUE', help='Maximal speed. From 0.0 for still to 1.0 for full.')
	driving.add_argument('--min-speed',          default=0.1, type=float, required=False, metavar='VALUE', help='Minimal speed to drive motor. Used to avoid motor noises and damage.')
	driving.add_argument('--speed-response',     default=1.0, type=float, required=False, metavar='VALUE', help='Speed change (per second) towards target value.')
	driving.add_argument('--speed-decay',        default=1.0, type=float, required=False, metavar='VALUE', help='Drop in speed (per second) when idle.')
	driving.add_argument('--direction-response', default=1.0, type=float, required=False, metavar='VALUE', help='Factor how fast direction changes (per second).')
	driving.add_argument('--direction-decay',    default=2.0, type=float, required=False, metavar='VALUE', help='Factor how fast direction returns to neutral when idle.')
	driving.add_argument('--rotate-speed',       default=0.5, type=float, required=False, metavar='VALUE', help='Speed for rotation in place.')
	args = parser.parse_args()

	if args.dry_run:
		args.show_packets = True

	if args.dry_run:
		connection = DryRunCarConnection.connect(args.address, args.port, args.short_packet_type, args.show_packets)
	else:
		connection = CarConnection.connect(args.address, args.port, args.short_packet_type, args.show_packets)

	base_driving_model_options = SmoothedDrivingModelOptions(
		interval=args.interval / 1000, # from milliseconds to seconds float
		blink_other_light=(not args.no_blink),
		max_speed=args.max_speed,
		min_speed=args.min_speed,
		speed_response=args.speed_response,
		speed_decay=args.speed_decay,
		direction_response=args.direction_response,
		direction_decay=args.direction_decay,
	)
	driving_model = SmoothedVectorizedDrivingModel(base_driving_model_options)
	driving_model.bind(connection)

	vectorized_mode = True
	max_speed = base_driving_model_options.max_speed

	def controls():
		"""Prints controls help"""
		print('Controls:')
		print('\tWASD (or arrows) keys to move; QE to rotate;') 
		print('\tF to toggle main light; R to toggle the other light;')
		print('\tV to toggle between vectorized (smoothed) and raw mode;')
		# TODO: allow change parameters while running
		# print('\t[/] to select and +/- to modify one of the parameters;') 
		print('\tSpace to stop immediately (uses both UDP and HTTP);')
		print('\tESC or CTRL+C to exit.')

	try:
		controls()
		time.sleep(0.500)

		while True:
			if keyboard.is_pressed('escape'):
				driving_model.stop()
				print('Stopped: Escape pressed')
				break

			if keyboard.is_pressed('space'):
				driving_model.stop()
				time.sleep(0.200)
				continue

			if keyboard.is_pressed('?') or keyboard.is_pressed('/'):
				controls()
				time.sleep(0.333)

			if keyboard.is_pressed('v'):
				vectorized_mode = not vectorized_mode
				if vectorized_mode:
					print('Toggled to vectorized mode')
				else:
					print('Toggled to raw mode')
				time.sleep(0.333)

			if keyboard.is_pressed('['):
				max_speed -= 0.10 if keyboard.is_pressed('shift') else 0.01
				max_speed = clamp(max_speed, -1, 1)
				print(f'Max speed: {max_speed:.3f}')
			elif keyboard.is_pressed(']'):
				max_speed += 0.10 if keyboard.is_pressed('shift') else 0.01
				max_speed = clamp(max_speed, -1, 1)
				print(f'Max speed: {max_speed:.3f}')
			base_driving_model_options.max_speed = max_speed

			if keyboard.is_pressed('f'):
				driving_model.toggle_main_light()
				time.sleep(0.100)
			if keyboard.is_pressed('r'):
				driving_model.options.blink_other_light = False
				driving_model.toggle_other_light()
				time.sleep(0.100)

			up = keyboard.is_pressed('w') or keyboard.is_pressed('up')
			down = keyboard.is_pressed('s') or keyboard.is_pressed('down')
			left = keyboard.is_pressed('a') or keyboard.is_pressed('left')
			right = keyboard.is_pressed('d') or keyboard.is_pressed('right')
			rotate_left = keyboard.is_pressed('q')
			rotate_right = keyboard.is_pressed('e')

			driving_model.idle()

			if vectorized_mode:
				factor = 1
				if keyboard.is_pressed('shift'):
					factor = 2
				elif keyboard.is_pressed('ctrl'):
					factor = 0.25
				if factor == 1:
					driving_model.options = base_driving_model_options
				else:
					driving_model.options = base_driving_model_options.changes_multiplied(factor)

				if rotate_left:
					driving_model.rotate(-args.rotate_speed * factor)
				elif rotate_right:
					driving_model.rotate(args.rotate_speed * factor)
				elif up:
					driving_model.throttle(1.0)
				elif down:
					driving_model.throttle(-1.0)
				if left:
					driving_model.turn(-1.0)
				elif right:
					driving_model.turn(1.0)

			else: # raw mode
				speed = (args.max_speed * 0.8) if not keyboard.is_pressed('ctrl') else args.min_speed * 3
				speed = args.max_speed if keyboard.is_pressed('shift') else speed

				# TODO: avoid sudden +/- changes
				if rotate_left:
					driving_model.raw(-speed, speed)
				elif rotate_right:
					driving_model.raw(speed, -speed)
				elif up:
					if left and not right:
						driving_model.raw(speed / 3, speed)
					elif right and not left:
						driving_model.raw(speed, speed / 3)
					else:
						driving_model.raw(speed, speed)
				elif down:
					if left and not right:
						driving_model.raw(-speed / 3, -speed)
					elif right and not left:
						driving_model.raw(-speed, -speed / 3)
					else:
						driving_model.raw(-speed, -speed)

			driving_model.packet()
			time.sleep(driving_model.time_until_next_update)

	except KeyboardInterrupt:
		print('Stopping: Interrupted by user')

	driving_model.stop()
	time.sleep(0.100)
	connection.close()

################################################################################

if __name__ == '__main__':
	main()
