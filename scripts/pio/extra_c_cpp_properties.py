import os
import glob
import sys
import subprocess
import json
from time import sleep
import inspect

class JSONWithCommentsDecoder(json.JSONDecoder):
	def __init__(self, **kw):
		super().__init__(**kw)

	def decode(self, s: str):
		s = '\n'.join(l if not l.lstrip().startswith('//') else '' for l in s.split('\n'))
		return super().decode(s)

try:
	# PlatformIO/SCons injected stuff
	Import('env' ,'projenv') # type: ignore
	env, projenv, DefaultEnvironment = env, projenv, DefaultEnvironment # type: ignore
except NameError:
	# Running detached from PlatformIO/SCons
	sleep(1)
	# sys.stdout = sys.stderr = open('xd.log', 'a+') # for debugging the script

	with open('.vscode/c_cpp_properties.json', 'r') as f:
		c_cpp_properties = json.load(f, cls=JSONWithCommentsDecoder)

	def check_if_configuration_exists(configuration):
		for existing_configuration in c_cpp_properties['configurations']:
			if existing_configuration['name'] == configuration['name']:
				return True
		return False

	# Add extra configurations (eases working with non-PlatformIO managed parts of the project)
	try:
		with open('.vscode/c_cpp_properties.extra.json', 'r') as f:
			extra_properties = json.load(f, cls=JSONWithCommentsDecoder)
			for configuration in extra_properties['configurations']:
				if check_if_configuration_exists(configuration): 
					print(f'[extra_c_cpp_properties] Configuration "{configuration["name"]}" already exists, skipping.')
					continue
				c_cpp_properties['configurations'].append(configuration)
	except (IOError, json.JSONDecodeError) as error:
		print(f'[extra_c_cpp_properties] Ignoring extra configurations:', error)

	# Apply some fixes to the PlatformIO configuration
	for configuration in c_cpp_properties['configurations']:
		if configuration['name'] == 'PlatformIO':
			# Modify `intelliSenseMode` for PlatformIO configuration to ARM to fix pointer size issues
			# See https://github.com/platformio/platformio-core/issues/3745
			configuration['intelliSenseMode'] = 'gcc-arm'

			# Add extra include/browse paths
			# TODO: make it configurable outside this script
			# Here, to ease analysis of the code of the (managed) components, that have `private_include`s.
			private_include_dirs = \
				glob.glob('./components/**/private_include', recursive=True) + \
				glob.glob('./managed_components/**/private_include', recursive=True)
			paths = [os.path.abspath(r).replace('\\', '/') for r in private_include_dirs]
			def add_the_paths(array):
				for path in paths:
					array.append(path)
			add_the_paths(configuration['includePath'])
			add_the_paths(configuration['browse']['path'])

	with open('.vscode/c_cpp_properties.json', 'w') as f:
		json.dump(c_cpp_properties, f, indent=4)
else:
	# Running directly by PlatformIO/SCons
	# Need to spawn new process, since existing scripts wait for us to finish, before re-generating the templated files
	this_script_file = inspect.getfile(lambda: None) # can't use sys.argv[0] nor __file__ in SCons loaded scripts
	p = subprocess.Popen([sys.executable, this_script_file], creationflags=subprocess.CREATE_NO_WINDOW)
