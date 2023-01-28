import argparse
import requests
import time
import keyboard
import socket
import struct
from math import floor;

def clamp(value, low, high): 
	return max(low, min(value, high))

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--ip', '--address', help='IP of the device. Default: 192.168.4.1', required=False, default='192.168.4.1')
	parser.add_argument('--port', help='Port of UDP control server. Default: 83', required=False, default=83, type=int)
	parser.add_argument('--interval', help='Interval between control packets in milliseconds. Default: 100', required=False, default=100, type=int)
	parser.add_argument('--dry-run', help='Performs dry-run for testing.', required=False, action='store_true')
	parser.add_argument('--show-packets', help='Show sent packets (like in dry run).', required=False, action='store_true')
	parser.add_argument('--short-packet-type', help='Uses short packet type instead long.', required=False, action='store_true')
	parser.add_argument('--no-blink', help='Prevents default behaviour of constant status led blinking.', required=False, action='store_true')
	driving = parser.add_argument_group('Driving model')
	driving.add_argument('--max-speed', metavar='VALUE', help='Initial maximal speed. From 0.0 for still to 1.0 for full.', required=False, default=1.0, type=float)
	driving.add_argument('--min-speed', metavar='VALUE', help='Minimal speed to drive motor. Used to avoid motor noises and damage.', required=False, default=0.1, type=float)
	driving.add_argument('--acceleration', metavar='VALUE', help='Initial acceleration per second.', required=False, default=1, type=float)
	args = parser.parse_args()

	if args.dry_run:
		args.show_packets = True

	# Testing connection
	if not args.dry_run:
		response = requests.get(f'http://{args.ip}/status', timeout=5)
		if not response.ok:
			print(f'Querying device status failed with status code: {response.status_code}')
			exit(1)
		print('Device found')

		start_time = time.time()
		start_uptime = int(response.json()['uptime']) # us
		start_uptime_ms = round(start_uptime / 1000)

		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # UDP
		# sock.connect((args.ip, args.port))
		print('Socket open')

	max_speed = args.max_speed or 1.0
	min_speed = args.min_speed or 0.1 # below means cutting off down to 0, avoid weird noises
	base_gain = args.acceleration or 1 # per second
	vectorized_mode = True

	left_motor = 0.0 # 0 to 1
	right_motor = 0.0
	main_light = False
	other_light = False
	blink_other_light = not args.no_blink

	def sendControlUDP():
		"""Sends the UDP packet to control the car"""
		if args.dry_run:
			print(f'UDP LM: {left_motor:.3f}, RM: {right_motor:.3f}, ML: {main_light}, OL: {other_light}')
		else:
			flags = 0
			if main_light:
				flags |= 0b00000001
			if other_light:
				flags |= 0b00000010
			if left_motor < 0:
				flags |= 0b01000000
			if right_motor < 0:
				flags |= 0b01000000
			if args.short_packet_type:
				bytes = struct.pack('BBBB', 1, flags, round(abs(left_motor) * 255), round(abs(right_motor) * 255))
			else:
				bytes = struct.pack('BBHff', 2, flags, 0, left_motor * 100, right_motor * 100)
			sock.sendto(bytes, (args.ip, args.port))
			if args.show_packets:
				expected_uptime_ms = start_uptime_ms + floor((time.time() - start_time) * 1000)
				if args.short_packet_type:
					print(f'  ({expected_uptime_ms}) udp: ShortControlPacket: F:{flags:02X} T:0ms L:{round(abs(left_motor) * 255) * 100:.2f} R:{round(abs(right_motor) * 255):.3f}')
				else:
					print(f'  ({expected_uptime_ms}) udp: LongControlPacket: F:{flags:02X} T:0ms L:{left_motor * 100:.2f} R{right_motor * 100:.2f}')

	def sendControlHTTP():
		"""Sends the HTTP packet to control the car"""
		if args.dry_run:
			print(f'UDP LM: {left_motor:.3f}, RM: {right_motor:.3f}, ML: {main_light}, OL: {other_light}')
		else:
			# TODO: actually code control in HTTP server of the car
			return
			# Hacky, but simple way to skip waiting for response, see https://stackoverflow.com/a/45601591/4880243
			try:
				requests.post(f'http://{args.ip}/config', timeout=0.000001, json={
					"control": {
						"mainLight": int(main_light),
						"otherLight": int(other_light),
						"left": round(left_motor * 100, 2), 
						"right": round(right_motor * 100, 2),
					},
					"silent": 1 # don't generate output 
				})
			except requests.exceptions.ReadTimeout: 
				pass

	def help():
		"""Prints controls help"""
		print('Controls:')
		print('\tWASD (or arrows) keys to move; QE to rotate;') 
		print('\tF to toggle main light; R to toggle the other light;')
		print('\tSpace to stop (immediately, uses both UDP and HTTP);')
		print('\tV to toggle between vectorized (smoothed) and raw mode;')
		print('\t+/- to modify gain; [/] to modify max speed;')
		print('\tShift to temporary uncap speed; ESC to exit.')

	help()
	time.sleep(0.500)

	last_update_time = time.time()
	while True:
		delta_time = time.time() - last_update_time

		if keyboard.is_pressed('escape'):
			break

		if keyboard.is_pressed('space'):
			left_motor = 0
			right_motor = 0
			main_light = False
			other_light = False
			sendControlUDP()
			sendControlHTTP()
			time.sleep(0.200)
			continue

		if keyboard.is_pressed('v'):
			vectorized_mode = not vectorized_mode
			if vectorized_mode:
				print('Toggled to vectorized mode')
			else:
				print('Toggled to raw mode')
			time.sleep(0.333)

		gain = base_gain
		if keyboard.is_modifier('shift'):
			gain *= 2
		elif keyboard.is_modifier('ctrl'):
			gain /= 4
		gain *= delta_time
		
		if keyboard.is_pressed('-'):
			base_gain -= 0.10 if keyboard.is_pressed('shift') else 0.01
			print(f'Gain: {base_gain:.3f}')
		elif keyboard.is_pressed('+') or keyboard.is_pressed('='):
			base_gain += 0.10 if keyboard.is_pressed('shift') else 0.01
			print(f'Gain: {base_gain:.3f}')

		if keyboard.is_pressed('['):
			max_speed -= 0.10 if keyboard.is_pressed('shift') else 0.01
			max_speed = clamp(max_speed, -1, 1)
			print(f'Max speed: {max_speed:.3f}')
		elif keyboard.is_pressed(']'):
			max_speed += 0.10 if keyboard.is_pressed('shift') else 0.01
			max_speed = clamp(max_speed, -1, 1)
			print(f'Max speed: {max_speed:.3f}')

		if keyboard.is_pressed('f'):
			main_light = not main_light
			time.sleep(0.100)
		if keyboard.is_pressed('r'):
			other_light = not other_light
			time.sleep(0.100)

		if vectorized_mode:
			fade = 1 - gain
			left_motor *= fade
			right_motor *= fade
			
			if keyboard.is_pressed('w') or keyboard.is_pressed('up'):
				left_motor += gain
				right_motor += gain
			if keyboard.is_pressed('a') or keyboard.is_pressed('left'):
				left_motor -= gain / 2
				right_motor += gain / 2
			if keyboard.is_pressed('s') or keyboard.is_pressed('down'):
				left_motor -= gain
				right_motor -= gain
			if keyboard.is_pressed('d') or keyboard.is_pressed('right'):
				left_motor += gain / 2
				right_motor -= gain / 2

			if keyboard.is_pressed('q'):
				left_motor -= gain
				right_motor += gain
			if keyboard.is_pressed('e'):
				left_motor -= gain
				right_motor += gain
		else:
			left_motor = 0
			right_motor = 0

			if keyboard.is_pressed('w') or keyboard.is_pressed('up'):
				left_motor = gain
				right_motor = gain
			elif keyboard.is_pressed('a') or keyboard.is_pressed('left'):
				# left_motor = gain
				right_motor = gain
			elif keyboard.is_pressed('s') or keyboard.is_pressed('down'):
				left_motor = -gain
				right_motor = -gain
			elif keyboard.is_pressed('d') or keyboard.is_pressed('right'):
				left_motor = gain
				# right_motor = gain

			elif keyboard.is_pressed('q'):
				left_motor = -gain
				right_motor = gain
			elif keyboard.is_pressed('e'):
				left_motor = gain
				right_motor = -gain

		left_motor = clamp(left_motor, -max_speed, max_speed)
		right_motor = clamp(right_motor, -max_speed, max_speed)
		if abs(left_motor) < min_speed:
			left_motor = 0
		if abs(right_motor) < min_speed:
			right_motor = 0

		if blink_other_light:
			other_light = not other_light

		sendControlUDP()

		last_update_time = time.time()
		time.sleep(args.interval / 1000) # milliseconds

if __name__ == '__main__':
	main()
