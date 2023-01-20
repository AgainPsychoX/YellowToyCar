import sys
import json
import argparse
import requests

default_config = {
	"uptime": 76915798,
	"network": {
		"mode": "ap",
		"fallback": 10000,
		"gateway": "0.0.0.0",
		"sta": {
			"ssid": "",
			"psk": "",
			"ip": "0.0.0.0",
			"mask": 20,
			"gateway": "0.0.0.0",
			"static": 0
		},
		"ap": {
			"ssid": "YellowToyCar",
			"psk": "AAaa11!!",
			"ip": "192.168.4.1",
			"mask": 24,
			"gateway": "192.168.4.1",
			"channel": 1,
			"hidden": 0
		}
	},
	"camera": {
		"framesize": 13,
		"pixformat": 4,
		"quality": 12,
		"bpc": 0,
		"wpc": 1,
		"hmirror": 0,
		"vflip": 0,
		"contrast": 0,
		"brightness": 0,
		"sharpness": 0,
		"denoise": 0,
		"gain_ceiling": 0,
		"agc": 1,
		"agc_gain": 0,
		"aec": 1,
		"aec2": 0,
		"ae_level": 0,
		"aec_value": 168,
		"awb": 1,
		"awb_gain": 1,
		"wb_mode": 0,
		"dcw": 1,
		"raw_gma": 1,
		"lenc": 1,
		"special": 0
	}
}

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--config-file', metavar='PATH', help='JSON file to be send as config. If not specified, uses some defaults.', required=False)
	parser.add_argument('--ip', '--address', help='IP of the device. Defaults to the one used for AP mode from new config.', required=False)
	parser.add_argument('--read-only', help='If set, only reads the request (GET request instead POST)', required=False, action='store_true')
	args = parser.parse_args()

	if args.config_file:
		with open(args.config_file, 'r') as file:
			new_config = json.load(file)
	else:
		print(f'Using default config')
		new_config = default_config

	if not args.ip:
		# use the AP one, from new config
		args.ip = new_config['network']['ap']['ip']
		print(f'Using IP: {args.ip}')

	if args.read_only:
		response = requests.get(f'http://{args.ip}/config', timeout=5)
	else:
		response = requests.post(f'http://{args.ip}/config', timeout=5, json=new_config)
	response_type = response.headers.get('Content-Type', '')
	print(f'Status code: {response.status_code}')
	print(f'Content type: {response_type}')
	print(f'Response length: {len(response.content)}')
	print('Response:')
	if ('application/json' in response_type):
		print(json.dumps(response.json(), indent=4))
	else:
		print(response.text)

if __name__ == '__main__':
	main()
