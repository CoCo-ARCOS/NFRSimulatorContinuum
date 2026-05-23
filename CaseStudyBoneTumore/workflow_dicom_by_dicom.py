import os
import argparse
from pathlib import Path
import math

import parsl
from parsl.config import Config
from parsl.executors import HighThroughputExecutor, ThreadPoolExecutor
from parsl.providers import SlurmProvider
from parsl.app.app import python_app

def setup_parsl(use_local=False, workers=2):
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
                max_workers=workers,
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                cores_per_worker=1,
                max_workers_per_node=workers,
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, max_blocks=workers, walltime="12:00:00", worker_init="module load python/3.12")
            ),
            HighThroughputExecutor(
                label='fog',
                cores_per_worker=1,
                max_workers_per_node=workers,
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, max_blocks=workers, walltime="12:00:00", worker_init="module load python/3.12")
            ),
            HighThroughputExecutor(
                label='cloud',
                cores_per_worker=1,
                max_workers_per_node=workers,
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, max_blocks=workers, walltime="12:00:00", worker_init="module load python/3.12")
            ),
        ]
    
    config = Config(executors=executors, strategy=None)
    parsl.load(config)

# ==========================================
# Bundled Node Pipeline Apps
# ==========================================

@python_app(executors=['edge'])
def edge_pipeline(chunk_files, edge_dir, chunk_idx, key_path, source_dir):
    import sys
    if source_dir not in sys.path:
        sys.path.append(source_dir)
    import os
    import shutil
    from pathlib import Path
    import time
    from nfr_functions import do_integrity, do_compress, do_encrypt, do_encode
    
    # 1. Edge Acquisition
    acq_dir = os.path.join(edge_dir, f"chunk_{chunk_idx}_acq")
    os.makedirs(acq_dir, exist_ok=True)
    _t0 = time.time()
    for f in chunk_files:
        shutil.copy2(f, acq_dir)
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'edge_acquisition,{_t1-_t0:.4f}\n')
    
    # 2. Integrity
    hash_path = os.path.join(edge_dir, f"chunk_{chunk_idx}.hash.json")
    do_integrity(acq_dir, hash_path, 'integrity_out')
    
    # 3. Compress
    comp_dir = os.path.join(edge_dir, f"chunk_{chunk_idx}_comp")
    do_compress(acq_dir, hash_path, comp_dir, 'compress_out')
    
    # 4. Encrypt
    enc_dir = os.path.join(edge_dir, f"chunk_{chunk_idx}_enc")
    do_encrypt(comp_dir, enc_dir, key_path, 'chacha20', 'encrypt_out')
    
    # 5. Encode
    encode_dir = os.path.join(edge_dir, f"chunk_{chunk_idx}_encode")
    do_encode(enc_dir, encode_dir, 'encode_out')
    
    return encode_dir

@python_app(executors=['fog'])
def fog_pipeline(edge_encode_dir, fog_dir, chunk_idx, key_path, source_dir):
    import sys
    if source_dir not in sys.path:
        sys.path.append(source_dir)
    import os
    from nfr_functions import (do_decode, do_decrypt, do_decompress, do_verify,
                               do_fog_preprocessing, do_integrity, do_compress,
                               do_encrypt, do_encode)
    
    # 1. Decode
    dec_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_dec")
    do_decode(edge_encode_dir, dec_dir, 'decode_in')
    
    # 2. Decrypt
    decrypt_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_decrypt")
    do_decrypt(dec_dir, decrypt_dir, key_path, 'decrypt_in')
    
    # 3. Decompress
    decomp_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_decomp")
    data_out, hash_out = do_decompress(decrypt_dir, decomp_dir, 'decompress_in')
    
    # 4. Verify
    do_verify(data_out, hash_out, 'verify_in')
    
    # 5. Preprocessing
    prep_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_prep")
    do_fog_preprocessing(data_out, prep_dir, 'fog_preprocessing')
    
    # 6. Integrity
    hash_path = os.path.join(fog_dir, f"chunk_{chunk_idx}_prep.hash.json")
    do_integrity(prep_dir, hash_path, 'integrity_out')
    
    # 7. Compress
    comp_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_comp")
    do_compress(prep_dir, hash_path, comp_dir, 'compress_out')
    
    # 8. Encrypt
    enc_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_enc")
    do_encrypt(comp_dir, enc_dir, key_path, 'aes', 'encrypt_out')
    
    # 9. Encode
    encode_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_encode")
    do_encode(enc_dir, encode_dir, 'encode_out')
    
    return encode_dir

@python_app(executors=['cloud'])
def cloud_pipeline(fog_encode_dir, cloud_dir, chunk_idx, key_path, source_dir):
    import sys
    if source_dir not in sys.path:
        sys.path.append(source_dir)
    import os
    from nfr_functions import (do_decode, do_decrypt, do_decompress, do_verify,
                               do_cloud_inference)
    
    # 1. Decode
    dec_dir = os.path.join(cloud_dir, f"chunk_{chunk_idx}_dec")
    do_decode(fog_encode_dir, dec_dir, 'decode_in')
    
    # 2. Decrypt
    decrypt_dir = os.path.join(cloud_dir, f"chunk_{chunk_idx}_decrypt")
    do_decrypt(dec_dir, decrypt_dir, key_path, 'decrypt_in')
    
    # 3. Decompress
    decomp_dir = os.path.join(cloud_dir, f"chunk_{chunk_idx}_decomp")
    data_out, hash_out = do_decompress(decrypt_dir, decomp_dir, 'decompress_in')
    
    # 4. Verify
    do_verify(data_out, hash_out, 'verify_in')
    
    # 5. Inference
    inf_dir = os.path.join(cloud_dir, f"chunk_{chunk_idx}_inf")
    out_mask, vis_dir = do_cloud_inference(data_out, inf_dir, 'cloud_inference')
    
    return inf_dir

# ==========================================
# Main Workflow Execution
# ==========================================

def run_workflow(args):
    import time
    
    edge_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_edge'))
    fog_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_fog'))
    cloud_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_cloud'))
    
    _t_start = time.time()
    with open('workflow_timing.log', 'w') as _f: _f.write('task,duration_seconds\n')
    
    setup_parsl(use_local=args.local, workers=args.workers)
    
    key_path = os.path.abspath('shared_key.bin')
    with open(key_path, 'wb') as f:
        f.write(os.urandom(32))
        
    os.makedirs(edge_dir, exist_ok=True)
    os.makedirs(fog_dir, exist_ok=True)
    os.makedirs(cloud_dir, exist_ok=True)
    
    # Get all DICOMs
    dataset_path = Path(args.dataset)
    dicom_files = list(dataset_path.glob('**/*.dcm'))
    if not dicom_files:
        dicom_files = [f for f in dataset_path.rglob('*') if f.is_file()]
    
    # Mock studies by duplicating the file list if args.studies > 1
    all_files = []
    for i in range(args.studies):
        all_files.extend(dicom_files)
        
    total_files = len(all_files)
    num_chunks = args.workers
    
    chunk_size = math.ceil(total_files / num_chunks)
    chunks = [all_files[i:i + chunk_size] for i in range(0, total_files, chunk_size)]
    
    print(f"Total files: {total_files}. Partitioned into {len(chunks)} balanced chunks (max {chunk_size} files/chunk).")
    
    source_dir = os.path.dirname(os.path.abspath(__file__))
    
    futures = []
    
    for chunk_idx, chunk in enumerate(chunks):
        chunk_paths = [str(f) for f in chunk]
        
        # Dispatch the chunk across the 3 nodes
        edge_future = edge_pipeline(chunk_paths, edge_dir, chunk_idx, key_path, source_dir)
        fog_future = fog_pipeline(edge_future, fog_dir, chunk_idx, key_path, source_dir)
        cloud_future = cloud_pipeline(fog_future, cloud_dir, chunk_idx, key_path, source_dir)
        
        futures.append(cloud_future)
        
    print("Waiting for workflows to complete...")
    for i, f in enumerate(futures):
        result = f.result()
        print(f"Chunk {i} completed. Output at: {result}")
        
    _t_end = time.time()
    print(f"\n[TIMING] Overall execution time: {_t_end - _t_start:.4f} seconds")
    print("All chunks completed successfully!")

    parsl.dfk().cleanup()
    parsl.clear()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OsteoCAD Parsl Workflow")
    parser.add_argument("--dataset", type=str, default="/home/domizzi/Downloads/medicalimages/dicoms/", help="Path to base dataset")
    parser.add_argument("--studies", type=int, default=1, help="Number of mocked studies to process")
    parser.add_argument("--workers", type=int, default=2, help="Number of parallel workers per stage")
    parser.add_argument("--output_dir", type=str, default=".", help="Base directory for output files")
    parser.add_argument("--local", action="store_true", help="Run locally using ThreadPoolExecutor instead of Slurm")
    args = parser.parse_args()
    
    run_workflow(args)
