import re

with open('workflow.py', 'r') as f:
    code = f.read()

apps = [
    ('integrity_out', 'return input_path, output_hash_path'),
    ('compress_out', 'return output_compressed_path'),
    ('encrypt_out', 'return output_encrypted'),
    ('encode_out', 'return output_files'),
    ('decode_in', 'return output_encrypted'),
    ('decrypt_in', 'return output_compressed'),
    ('decompress_in', 'return str(Path(output_dir) / data_file_or_dir), str(Path(output_dir) / hash_file)'),
    ('verify_in', 'return data_path'),
    ('edge_acquisition', 'return str(study_dir)'),
    ('fog_preprocessing', 'return ""'),
    ('fog_preprocessing', 'return str(img_path)'),
    ('cloud_inference', 'return output_path, vis_dir')
]

for app_name, ret_stmt in apps:
    # Add start time
    code = re.sub(rf"(def {app_name}\([^)]*\):)", r"\1\n        import time\n        _t0 = time.time()", code, count=1)
    # Add end time before return
    indent = "        " if app_name.endswith("_out") or app_name.endswith("_in") else "    "
    if app_name == 'fog_preprocessing':
        indent = "    "
        code = code.replace(ret_stmt, f"import time; _t1 = time.time()\n{indent}with open('workflow_timing.log', 'a') as _f: _f.write(f'{app_name},{{_t1-_t0:.4f}}\\n')\n{indent}{ret_stmt}")
    else:
        code = code.replace(ret_stmt, f"import time; _t1 = time.time()\n{indent}with open('workflow_timing.log', 'a') as _f: _f.write(f'{app_name},{{_t1-_t0:.4f}}\\n')\n{indent}{ret_stmt}")

# Add overall timing
code = code.replace("def run_workflow(args):", "def run_workflow(args):\n    import time\n    _t_start = time.time()\n    with open('workflow_timing.log', 'w') as _f: _f.write('task,duration_seconds\\n')")
code = code.replace('print("All studies completed successfully!")', '_t_end = time.time()\n    print(f"\\n[TIMING] Overall execution time: {_t_end - _t_start:.4f} seconds")\n    print("All studies completed successfully!")')

with open('workflow.py', 'w') as f:
    f.write(code)
print("Patch applied.")
