import os
import argparse
from pathlib import Path
import math
import shutil
import time
import concurrent.futures

from nfr_functions import (do_integrity, do_compress, do_encrypt, do_encode,
                           do_decode, do_decrypt, do_decompress, do_verify,
                           do_fog_preprocessing, do_cloud_inference)

# ==========================================
# Standard Python Pipeline Functions
# ==========================================

def edge_pipeline(chunk_files, edge_dir, chunk_idx, key_path):
    # 1. Edge Acquisition
    acq_dir = os.path.join(edge_dir, f"chunk_{chunk_idx}_acq")
    os.makedirs(acq_dir, exist_ok=True)
    _t0 = time.time()
    for i, f in enumerate(chunk_files):
        # Generate unique filename to prevent overwrites when mocking studies
        f_path = Path(f)
        unique_name = f"{i}_{f_path.name}"
        shutil.copy2(f, os.path.join(acq_dir, unique_name))
    _t1 = time.time()
    timing_log = os.environ.get("WORKFLOW_TIMING_LOG", "workflow_timing.log")
    with open(timing_log, 'a') as _f: _f.write(f'edge_acquisition,{_t1-_t0:.4f}\n')
    
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

def fog_pipeline(edge_encode_dir, fog_dir, chunk_idx, key_path):
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

def cloud_pipeline(fog_encode_dir, cloud_dir, chunk_idx, key_path):
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


def process_all(args):
    """Wrapper function that runs sequentially on a single OS process worker."""
    chunk_paths, edge_dir, fog_dir, cloud_dir, chunk_idx, key_path = args
    print(f"[Worker PID: {os.getpid()}] Starting ALL Chunk {chunk_idx} pipeline...")
    edge_encode_dir = edge_pipeline(chunk_paths, edge_dir, chunk_idx, key_path)
    fog_encode_dir = fog_pipeline(edge_encode_dir, fog_dir, chunk_idx, key_path)
    cloud_inf_dir = cloud_pipeline(fog_encode_dir, cloud_dir, chunk_idx, key_path)
    return chunk_idx, cloud_inf_dir

def process_edge(args):
    chunk_paths, edge_dir, chunk_idx, key_path = args
    print(f"[Worker PID: {os.getpid()}] Starting EDGE Chunk {chunk_idx} pipeline...")
    return chunk_idx, edge_pipeline(chunk_paths, edge_dir, chunk_idx, key_path)

def process_fog(args):
    edge_dir, fog_dir, chunk_idx, key_path = args
    print(f"[Worker PID: {os.getpid()}] Starting FOG Chunk {chunk_idx} pipeline...")
    edge_encode_dir = os.path.join(edge_dir, f"chunk_{chunk_idx}_encode")
    return chunk_idx, fog_pipeline(edge_encode_dir, fog_dir, chunk_idx, key_path)

def process_cloud(args):
    fog_dir, cloud_dir, chunk_idx, key_path = args
    print(f"[Worker PID: {os.getpid()}] Starting CLOUD Chunk {chunk_idx} pipeline...")
    fog_encode_dir = os.path.join(fog_dir, f"chunk_{chunk_idx}_encode")
    return chunk_idx, cloud_pipeline(fog_encode_dir, cloud_dir, chunk_idx, key_path)

# ==========================================
# Main Workflow Execution
# ==========================================

def run_workflow(args):
    edge_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_edge'))
    fog_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_fog'))
    cloud_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_cloud'))
    
    _t_start = time.time()
    
    timing_log = os.path.join(args.output_dir, 'workflow_timing.log')
    os.environ["WORKFLOW_TIMING_LOG"] = timing_log
    
    if args.stage in ['all', 'edge']:
        with open(timing_log, 'w') as _f: 
            _f.write('task,duration_seconds\n')
        
        key_path = os.path.join(args.output_dir, 'shared_key.bin')
        with open(key_path, 'wb') as f:
            f.write(os.urandom(32))
            
        os.makedirs(edge_dir, exist_ok=True)
        os.makedirs(fog_dir, exist_ok=True)
        os.makedirs(cloud_dir, exist_ok=True)
    else:
        key_path = os.path.join(args.output_dir, 'shared_key.bin')
    
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
    
    # Prepare arguments based on stage
    task_args = []
    for chunk_idx, chunk in enumerate(chunks):
        if args.stage == 'all':
            chunk_paths = [str(f) for f in chunk]
            task_args.append((chunk_paths, edge_dir, fog_dir, cloud_dir, chunk_idx, key_path))
            target_func = process_all
        elif args.stage == 'edge':
            chunk_paths = [str(f) for f in chunk]
            task_args.append((chunk_paths, edge_dir, chunk_idx, key_path))
            target_func = process_edge
        elif args.stage == 'fog':
            task_args.append((edge_dir, fog_dir, chunk_idx, key_path))
            target_func = process_fog
        elif args.stage == 'cloud':
            task_args.append((fog_dir, cloud_dir, chunk_idx, key_path))
            target_func = process_cloud
        
    print(f"Submitting to ProcessPoolExecutor (stage: {args.stage}) with max_workers={args.workers}...")
    
    # Launch Multiprocessing Pool
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(target_func, t_args): t_args for t_args in task_args}
        
        for future in concurrent.futures.as_completed(futures):
            try:
                chunk_idx, result_dir = future.result()
                print(f"Chunk {chunk_idx} completed. Output at: {result_dir}")
            except Exception as exc:
                print(f"Chunk generated an exception: {exc}")
        
    _t_end = time.time()
    overall_time = _t_end - _t_start
    print(f"\n[TIMING] Overall execution time: {overall_time:.4f} seconds")
    
    with open(timing_log, 'a') as _f:
        _f.write(f'stage_wall_clock_{args.stage},{overall_time:.4f}\n')
        
    print("All chunks completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OsteoCAD Multiprocessing Workflow")
    parser.add_argument("--dataset", type=str, default="/home/domizzi/Downloads/medicalimages/dicoms/", help="Path to base dataset")
    parser.add_argument("--studies", type=int, default=1, help="Number of mocked studies to process")
    parser.add_argument("--workers", type=int, default=2, help="Number of parallel workers per stage")
    parser.add_argument("--output_dir", type=str, default=".", help="Base directory for output files")
    parser.add_argument("--stage", type=str, choices=['all', 'edge', 'fog', 'cloud'], default='all', help="Which pipeline stage to run")
    args = parser.parse_args()
    
    run_workflow(args)
