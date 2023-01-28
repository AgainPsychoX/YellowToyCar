import argparse
import requests
import json
from benedict import benedict

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--status', help='Request status before sending/requesting config.', required=False, action='store_true')
	parser.add_argument('--status-only', help='Only request status.', required=False, action='store_true')
	parser.add_argument('--config-file', metavar='PATH', help='JSON file to be send as config.', required=False)
	parser.add_argument('--wifi-mode', help='Overwrite WiFi mode from config.', required=False, choices=['ap', 'sta', 'apsta', 'nat', 'null'])
	parser.add_argument('--ip', '--address', help='IP of the device. Defaults to the one used for AP mode from new config or 192.168.4.1.', required=False)
	parser.add_argument('--read-only', help='If set, only reads the request (GET request instead POST)', required=False, action='store_true')
	parser.add_argument('--restart', help='Sends restart request.', required=False, action='store_true')
	args = parser.parse_args()
	args.status = args.status or args.status_only

	if args.config_file:
		target_config = benedict(args.config_file, format='json')
	else:
		target_config = benedict()

	if args.wifi_mode:
		target_config['network.mode'] = args.wifi_mode

	if not args.ip:
		if target_config.get('network.ap.ip'):
			args.ip = target_config['network.ap.ip']
			print(f'Using IP from AP configuration: {args.ip}')
		elif target_config.get('network.sta.ip'):
			args.ip = target_config['network.sta.ip']
			print(f'Using IP from STA configuration: {args.ip}')
		else:
			args.ip = '192.168.4.1' # default for ESP32 esp-idf
			print(f'No config with IP provided, falling back to using default IP: {args.ip}')

	if args.status:
		print('--- Status ---')
		response = requests.get(f'http://{args.ip}/status?detailed=1', timeout=5)
		response_type = response.headers.get('Content-Type', '')
		print(f'Status code: {response.status_code}')
		print(f'Content type: {response_type}')
		print(f'Response length: {len(response.content)}')
		if ('application/json' in response_type):
			try:
				text = json.dumps(response.json(), indent=4)
				print('Response (JSON):')
				print(text)
			except requests.exceptions.JSONDecodeError as e:
				print(e)
				print('Response as text')
				print(response.text)
		else:
			print('Response as text')
			print(response.text)

		if args.status_only:
			exit(0)
		print('--- Config ---')

	if args.read_only or len(target_config) == 0:
		response = requests.get(f'http://{args.ip}/config', timeout=5)
	else:
		response = requests.post(f'http://{args.ip}/config', timeout=5, json=target_config)
	response_type = response.headers.get('Content-Type', '')
	print(f'Status code: {response.status_code}')
	print(f'Content type: {response_type}')
	print(f'Response length: {len(response.content)}')
	if ('application/json' in response_type):
		try:
			text = json.dumps(response.json(), indent=4)
			print('Response (JSON):')
			print(text)
		except requests.exceptions.JSONDecodeError as e:
			print(e)
			print('Response as text')
			print(response.text)
	else:
		print('Response as text')
		print(response.text)

if __name__ == '__main__':
	main()
