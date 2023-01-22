import os
import argparse
import requests
import time
import keyboard
import socket
import struct

def clamp(value, low, high): 
	return max(low, min(value, high))

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--ip', '--address', help='IP of the device.', required=False, default='192.168.4.1')
	parser.add_argument('--port', help='Port of UDP control server.', required=False, default=83, type=int)
	parser.add_argument('--dry-run', help='Performs dry-run for testing.', required=False, action='store_true')
	parser.add_argument('--short-packet-type', help='Uses short packet type instead long.', required=False, action='store_true')
	args = parser.parse_args()

	# Testing connection
	if not args.dry_run:
		response = requests.get(f'http://{args.ip}/status?detailed=1', timeout=5)
		if not response.ok:
			print(f'Querying device status failed with status code: {response.status_code}')
			exit(1)
		print('Device found')

		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # UDP
		# sock.connect((args.ip, args.port))
		print('Socket open')

	interval = 0.100 # seconds
	base_gain = 0.1
	max_speed = 1.0
	vectorized_mode = True

	left_motor = 0.0 # 0 to 1
	right_motor = 0.0
	main_light = False
	other_light = False

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
	while True:
		if keyboard.is_pressed('escape'):
			break

		if keyboard.is_pressed('space'):
			left_motor = 0
			right_motor = 0
			main_light = False
			other_light = False
			sendControlUDP()
			sendControlHTTP()
			time.sleep(interval / 2)
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
		
		if keyboard.is_pressed('-'):
			base_gain -= 0.010 if keyboard.is_pressed('shift') else 0.001
			print(f'Gain: {base_gain:.3f}')
		elif keyboard.is_pressed('+') or keyboard.is_pressed('='):
			base_gain += 0.010 if keyboard.is_pressed('shift') else 0.001
			print(f'Gain: {base_gain:.3f}')

		if keyboard.is_pressed('['):
			max_speed -= 0.010 if keyboard.is_pressed('shift') else 0.001
			max_speed = clamp(max_speed, -1, 1)
			print(f'Max speed: {max_speed:.3f}')
		elif keyboard.is_pressed(']'):
			max_speed += 0.010 if keyboard.is_pressed('shift') else 0.001
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

		time.sleep(interval)

		left_motor = clamp(left_motor, -max_speed, max_speed)
		right_motor = clamp(right_motor, -max_speed, max_speed)
		sendControlUDP()


if __name__ == '__main__':
	main()
