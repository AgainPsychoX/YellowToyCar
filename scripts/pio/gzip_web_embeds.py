Import('env') # type: ignore
env, DefaultEnvironment = env, DefaultEnvironment # type: ignore

import os
import gzip
import glob

def gzip_file(src, dst):
	with open(src, 'rb') as src, gzip.open(dst, 'wb') as dst:
		for chunk in iter(lambda: src.read(4096), b""):
			dst.write(chunk)

filetypes_to_gzip = ['html', 'js', 'css']

src_dir_path = env.get('PROJECT_SRC_DIR')

files_to_gzip = []
for extension in filetypes_to_gzip:
	files_to_gzip.extend(glob.glob(os.path.join(src_dir_path, '*.' + extension ) ) )

# TODO: https://pypi.org/project/css-html-js-minify/

for source_file_path in files_to_gzip:
	target_file_path = source_file_path + '.gz'
	if (os.path.exists(target_file_path)):
		if (os.path.getmtime(source_file_path) <= os.path.getmtime(target_file_path)):
			continue
		os.remove(target_file_path)
	print('Compressing web for embedding: ' + source_file_path)
	gzip_file(source_file_path, target_file_path)
