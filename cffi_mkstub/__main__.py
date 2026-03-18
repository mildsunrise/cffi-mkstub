import argparse
import importlib
import os.path
from . import write_type_stub

def main():
	parser = argparse.ArgumentParser(
		prog='cffi-mkstub',
		description='Type stub generator for cffi modules',
	)

	parser.add_argument('-m', '--module', help='Module to import')
	parser.add_argument('-p', '--path', help='Path to module')
	parser.add_argument('-o', '--output', help='Stub file output path')

	args = parser.parse_args()
	assert (args.module != None) != (args.path != None), 'exactly one of -m or -p must be provided'
	module = args.module
	if args.path != None:
		# https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly
		modname = os.path.basename(args.path).split('.')[0]
		spec = importlib.util.spec_from_file_location(modname, args.path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)

	write_type_stub(module, output_path=args.output)

if __name__ == '__main__':
	main()
