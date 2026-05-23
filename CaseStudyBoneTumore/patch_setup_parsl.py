import sys

with open("workflow_dicom_by_dicom.py", "r") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if line.startswith("def setup_parsl"):
        start_idx = i
    if start_idx != -1 and line.startswith("# =========================================="):
        end_idx = i - 1
        break

new_setup = """def setup_parsl(use_local=False, workers=2):
    print(f"Setting up Parsl with use_local={use_local} and workers={workers}...")
    if use_local:
        executors = [
            ThreadPoolExecutor(label='edge', max_threads=workers),
            ThreadPoolExecutor(label='fog', max_threads=workers),
            ThreadPoolExecutor(label='cloud', max_threads=workers),
        ]
    else:
        executors = [
            HighThroughputExecutor(
                label='edge',
                max_workers_per_node=workers,
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, walltime="12:00:00", worker_init="module load python/3.12")
            ),
            HighThroughputExecutor(
                label='fog',
                max_workers_per_node=workers,
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, walltime="12:00:00", worker_init="module load python/3.12")
            ),
            HighThroughputExecutor(
                label='cloud',
                max_workers_per_node=workers,
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, walltime="12:00:00", worker_init="module load python/3.12")
            ),
        ]
    
    config = Config(executors=executors, strategy=None)
    parsl.load(config)

"""

if start_idx != -1 and end_idx != -1:
    lines[start_idx:end_idx] = [new_setup]
    with open("workflow_dicom_by_dicom.py", "w") as f:
        f.writelines(lines)
    print("Patched successfully")
else:
    print("Could not find boundaries")
