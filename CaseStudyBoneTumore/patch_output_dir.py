import sys

with open('workflow.py', 'r') as f:
    code = f.read()

# Inject directory variables
inj = """def run_workflow(args):
    import os
    edge_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_edge'))
    fog_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_fog'))
    cloud_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_cloud'))"""
code = code.replace("def run_workflow(args):", inj)

# Replace makedirs
code = code.replace("os.makedirs('tmp_edge', exist_ok=True)", "os.makedirs(edge_dir, exist_ok=True)")
code = code.replace("os.makedirs('tmp_fog', exist_ok=True)", "os.makedirs(fog_dir, exist_ok=True)")
code = code.replace("os.makedirs('tmp_cloud', exist_ok=True)", "os.makedirs(cloud_dir, exist_ok=True)")

# Replace abspath passing exact string
code = code.replace("os.path.abspath('tmp_edge')", "edge_dir")
code = code.replace("os.path.abspath('tmp_fog')", "fog_dir")
code = code.replace("os.path.abspath('tmp_cloud')", "cloud_dir")

# Replace prefixes in f-strings
code = code.replace("f'tmp_edge/", "f'{edge_dir}/")
code = code.replace("f'tmp_fog/", "f'{fog_dir}/")
code = code.replace("f'tmp_cloud/", "f'{cloud_dir}/")

# Add argparse
argparse_line = '    parser.add_argument("--workers", type=int, default=2, help="Number of parallel workers per stage")'
argparse_inj = argparse_line + '\n    parser.add_argument("--output_dir", type=str, default=".", help="Base directory for output files")'
code = code.replace(argparse_line, argparse_inj)

with open('workflow.py', 'w') as f:
    f.write(code)
print("Output dir patched.")
