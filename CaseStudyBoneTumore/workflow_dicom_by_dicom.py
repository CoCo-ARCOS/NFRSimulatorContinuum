import os
import argparse
import tarfile
import hashlib
import json
import shutil
from pathlib import Path
import lz4.frame
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import zfec
import dicom2nifti
import pydicom
import nibabel as nib
import numpy as np

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
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, walltime="12:00:00", worker_init="module load python/3.12")
            ),
            HighThroughputExecutor(
                label='fog',
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, walltime="12:00:00", worker_init="module load python/3.12")
            ),
            HighThroughputExecutor(
                label='cloud',
                working_dir="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/",
                provider=SlurmProvider(partition="large",cores_per_node=workers,nodes_per_block=1, init_blocks=1, walltime="12:00:00", worker_init="module load python/3.12")
            ),
        ]
    
    config = Config(executors=executors, strategy=None)
    parsl.load(config)

# ==========================================
# NFR App Factories
# ==========================================

def make_integrity_out_app(executors):
    @python_app(executors=executors)
    def integrity_out(input_path, output_hash_path):
        import time
        _t0 = time.time()
        import hashlib
        import json
        from pathlib import Path
        
        in_path = Path(input_path)
        h = hashlib.sha3_256()
        
        def hash_file(f_path):
            with open(f_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    h.update(chunk)

        if in_path.is_dir():
            for p in sorted(in_path.rglob('*')):
                if p.is_file() and p.name != Path(output_hash_path).name:
                    h.update(p.name.encode('utf-8'))
                    hash_file(p)
        else:
            hash_file(in_path)
            
        hash_val = h.hexdigest()
        with open(output_hash_path, 'w') as f:
            json.dump({'hash': hash_val}, f)
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'integrity_out,{_t1-_t0:.4f}\n')
        return input_path, output_hash_path
    return integrity_out

def make_compress_out_app(executors):
    @python_app(executors=executors)
    def compress_out(paths_tuple, output_dir):
        import time
        _t0 = time.time()
        data_path, hash_path = paths_tuple
        import lz4.frame
        import os
        from pathlib import Path
        
        os.makedirs(output_dir, exist_ok=True)
        in_path = Path(data_path)
        files_to_compress = [Path(hash_path)]
        if in_path.is_dir():
            files_to_compress.extend(p for p in in_path.rglob('*') if p.is_file())
        else:
            files_to_compress.append(in_path)
            
        for f_in_path in files_to_compress:
            out_file = Path(output_dir) / (f_in_path.name + ".lz4")
            with open(f_in_path, 'rb') as f_in, open(out_file, 'wb') as f_out:
                f_out.write(lz4.frame.compress(f_in.read()))
                
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'compress_out,{_t1-_t0:.4f}\n')
        return output_dir
    return compress_out

def make_encrypt_out_app(executors, algo='chacha20'):
    @python_app(executors=executors)
    def encrypt_out(input_dir, output_dir, key_path):
        import time
        _t0 = time.time()
        import os
        from pathlib import Path
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        
        os.makedirs(output_dir, exist_ok=True)
        with open(key_path, 'rb') as kf:
            key = kf.read()
            
        for f_in_path in Path(input_dir).rglob('*'):
            if not f_in_path.is_file(): continue
            with open(f_in_path, 'rb') as f_in:
                data = f_in.read()
                
            if algo == 'chacha20':
                nonce = os.urandom(16)
                cipher = Cipher(algorithms.ChaCha20(key[:32], nonce), mode=None)
                encryptor = cipher.encryptor()
                ciphertext = encryptor.update(data)
                out_data = b"CHACHA" + nonce + ciphertext
            else: # aes
                iv = os.urandom(16)
                cipher = Cipher(algorithms.AES(key[:32]), modes.CFB(iv))
                encryptor = cipher.encryptor()
                ciphertext = encryptor.update(data)
                out_data = b"AESCFB" + iv + ciphertext
                
            out_file = Path(output_dir) / (f_in_path.name + ".enc")
            with open(out_file, 'wb') as f_out:
                f_out.write(out_data)
                
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'encrypt_out,{_t1-_t0:.4f}\n')
        return output_dir
    return encrypt_out

def make_encode_out_app(executors):
    @python_app(executors=executors)
    def encode_out(input_dir, output_dir):
        import time
        _t0 = time.time()
        import zfec
        import os
        from pathlib import Path
        
        os.makedirs(output_dir, exist_ok=True)
        k, m = 4, 8
        encoder = zfec.Encoder(k, m)
        
        for f_in_path in Path(input_dir).rglob('*'):
            if not f_in_path.is_file(): continue
            with open(f_in_path, 'rb') as f_in:
                data = f_in.read()
            
            pad_len = k - (len(data) % k) if len(data) % k != 0 else 0
            padded_data = data + (b'\0' * pad_len)
            chunk_size = len(padded_data) // k
            blocks = [padded_data[i*chunk_size : (i+1)*chunk_size] for i in range(k)]
            
            encoded_blocks = encoder.encode(blocks)
            for i, block in enumerate(encoded_blocks):
                out_path = Path(output_dir) / f"{f_in_path.name}.block{i}"
                with open(out_path, 'wb') as f_out:
                    if i == 0:
                        f_out.write(pad_len.to_bytes(4, 'big'))
                    f_out.write(block)
        
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'encode_out,{_t1-_t0:.4f}\n')
        return output_dir
    return encode_out

def make_decode_in_app(executors):
    @python_app(executors=executors)
    def decode_in(input_dir, output_dir):
        import time
        _t0 = time.time()
        import zfec
        import os
        from pathlib import Path
        from collections import defaultdict
        
        os.makedirs(output_dir, exist_ok=True)
        k, m = 4, 8
        decoder = zfec.Decoder(k, m)
        
        groups = defaultdict(list)
        for f_in_path in Path(input_dir).rglob('*'):
            if not f_in_path.is_file(): continue
            name = f_in_path.name
            base_name = name.rsplit('.block', 1)[0]
            groups[base_name].append(f_in_path)
            
        for base_name, bpaths in groups.items():
            blocks = []
            blocknums = []
            pad_len = 0
            for i, bpath in enumerate(bpaths):
                if i >= k: break
                with open(bpath, 'rb') as f:
                    if str(bpath).endswith("block0"):
                        pad_len = int.from_bytes(f.read(4), 'big')
                    data = f.read()
                    blocks.append(data)
                    blocknums.append(int(str(bpath).split("block")[-1]))
                    
            decoded_blocks = decoder.decode(blocks, blocknums)
            decoded = b"".join(decoded_blocks)
            if pad_len > 0:
                decoded = decoded[:-pad_len]
                
            out_file = Path(output_dir) / base_name
            with open(out_file, 'wb') as f:
                f.write(decoded)
                
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'decode_in,{_t1-_t0:.4f}\n')
        return output_dir
    return decode_in

def make_decrypt_in_app(executors):
    @python_app(executors=executors)
    def decrypt_in(input_dir, output_dir, key_path):
        import time
        _t0 = time.time()
        import os
        from pathlib import Path
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        
        os.makedirs(output_dir, exist_ok=True)
        with open(key_path, 'rb') as kf:
            key = kf.read()
            
        for f_in_path in Path(input_dir).rglob('*'):
            if not f_in_path.is_file(): continue
            with open(f_in_path, 'rb') as f_in:
                data = f_in.read()
                
            header = data[:6]
            if header == b"CHACHA":
                nonce = data[6:22]
                ciphertext = data[22:]
                cipher = Cipher(algorithms.ChaCha20(key[:32], nonce), mode=None)
            else: # AESCFB
                iv = data[6:22]
                ciphertext = data[22:]
                cipher = Cipher(algorithms.AES(key[:32]), modes.CFB(iv))
                
            decryptor = cipher.decryptor()
            plaintext = decryptor.update(ciphertext)
            
            base_name = f_in_path.name
            if base_name.endswith('.enc'):
                base_name = base_name[:-4]
                
            out_file = Path(output_dir) / base_name
            with open(out_file, 'wb') as f_out:
                f_out.write(plaintext)
                
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'decrypt_in,{_t1-_t0:.4f}\n')
        return output_dir
    return decrypt_in

def make_decompress_in_app(executors):
    @python_app(executors=executors)
    def decompress_in(input_dir, output_dir):
        import time
        _t0 = time.time()
        import lz4.frame
        import os
        from pathlib import Path
        
        os.makedirs(output_dir, exist_ok=True)
        hash_file_path = None
        
        for f_in_path in Path(input_dir).rglob('*'):
            if not f_in_path.is_file(): continue
            with open(f_in_path, 'rb') as f_in:
                decompressed_data = lz4.frame.decompress(f_in.read())
                
            base_name = f_in_path.name
            if base_name.endswith('.lz4'):
                base_name = base_name[:-4]
                
            out_file = Path(output_dir) / base_name
            with open(out_file, 'wb') as f_out:
                f_out.write(decompressed_data)
                
            if base_name.endswith('.json') and 'hash' in base_name:
                hash_file_path = str(out_file)
        
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'decompress_in,{_t1-_t0:.4f}\n')
        
        data_files = [str(Path(output_dir) / f) for f in os.listdir(output_dir) if not f.endswith('.json')]
        if len(data_files) == 1:
            data_out = data_files[0]
        else:
            data_out = output_dir
            
        return data_out, hash_file_path
    return decompress_in


def make_verify_in_app(executors):
    @python_app(executors=executors)
    def verify_in(paths_tuple):
        import time
        _t0 = time.time()
        data_dir, hash_path = paths_tuple
        import hashlib
        import json
        from pathlib import Path
        
        with open(hash_path, 'r') as f:
            expected_hash = json.load(f)['hash']
            
        in_path = Path(data_dir)
        h = hashlib.sha3_256()
        
        def hash_file(f_path):
            with open(f_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    h.update(chunk)

        if in_path.is_dir():
            for p in sorted(in_path.rglob('*')):
                if p.is_file() and p.name != Path(hash_path).name:
                    h.update(p.name.encode('utf-8'))
                    hash_file(p)
        else:
            hash_file(in_path)
            
        actual_hash = h.hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"Hash mismatch! Expected {expected_hash}, got {actual_hash}")
            
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'verify_in,{_t1-_t0:.4f}\\n')
        return data_dir
    return verify_in

# ==========================================
# Application Apps
# ==========================================

@python_app(executors=['edge'])
def edge_acquisition(dataset_dir, output_dir, study_idx):
    import time
    _t0 = time.time()
    import shutil
    import random
    from pathlib import Path
    
    dataset_path = Path(dataset_dir)
    study_dir = Path(output_dir) / f"study_{study_idx}"
    study_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate mock study based on original dataset
    dicom_files = list(dataset_path.glob('**/*.dcm'))
    if not dicom_files:
        dicom_files = [f for f in dataset_path.rglob('*') if f.is_file()]
        
    for i, f in enumerate(dicom_files):
        # Adding a bit of randomness to mock distinct studies
        out_name = f"image_{i:04d}.dcm"
        shutil.copy2(f, study_dir / out_name)
        
    import time; _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'edge_acquisition,{_t1-_t0:.4f}\n')
    return str(study_dir)

@python_app(executors=['fog'])
def fog_preprocessing(input_dir, output_dir):
    import time
    _t0 = time.time()
    import os
    import dicom2nifti
    import dicom2nifti.settings
    from pathlib import Path
    import nibabel as nib
    import numpy as np
    
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Isolate ROI / artifact removal (mock logic for demo: thresholding DICOMs before NIfTI)
    # Since pydicom modifying all files is slow, we'll let dicom2nifti do the conversion first
    dicom2nifti.settings.disable_validate_slice_increment()
    try:
        dicom2nifti.convert_directory(input_dir, output_dir, compression=True, reorient=True)
    except Exception as e:
        # fallback if DICOMs are invalid for conversion: create a mock NIfTI
        print("DICOM to NIfTI failed, creating mock NIfTI:", e)
        mock_data = np.zeros((128, 128, 128), dtype=np.float32)
        mock_img = nib.Nifti1Image(mock_data, np.eye(4))
        nib.save(mock_img, out_path / "preprocessed.nii.gz")
        
    # Anonymization / Post-processing on NIfTI
    nifti_files = list(out_path.glob('*.nii.gz'))
    if nifti_files:
        img_path = nifti_files[0]
        img = nib.load(img_path)
        data = img.get_fdata()
        # Mock isolate ROI (remove values below threshold)
        data[data < 0] = 0
        new_img = nib.Nifti1Image(data, img.affine)
        nib.save(new_img, img_path)
        import time; _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'fog_preprocessing,{_t1-_t0:.4f}\n')
    return str(img_path)
    import time; _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'fog_preprocessing,{_t1-_t0:.4f}\n')
    return ""

@python_app(executors=['cloud'])
def cloud_inference(input_nifti, output_path):
    import time
    _t0 = time.time()
    import nibabel as nib
    import numpy as np
    import torch
    from monai.networks.nets import UNet
    
    img = nib.load(input_nifti)
    data = img.get_fdata()
    
    import torch.nn.functional as F
    
    # Convert to PyTorch tensor (batch, channel, D, H, W)
    tensor_data = torch.tensor(data, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
    
    # Interpolate to 64x64x64 to fit the UNet memory footprint
    tensor_data_resized = F.interpolate(tensor_data, size=(64, 64, 64), mode='trilinear', align_corners=False)
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tensor_data_resized = tensor_data_resized.to(device)
    
    # Initialize a standard 3D UNet for medical image segmentation
    model = UNet(
        spatial_dims=3,
        in_channels=1,
        out_channels=1, # Background vs Tumor
        channels=(16, 32, 64, 128, 256),
        strides=(2, 2, 2, 2),
        num_res_units=2,
    ).to(device)
    
    model.eval()
    # Real Inference (Forward Pass)
    # This runs the actual UNet to simulate the compute time and memory of the Deep Learning model
    with torch.no_grad():
        output = model(tensor_data_resized)
        
    # Since we don't have gigabytes of specific osteosarcoma weights loaded, the raw argmax 
    # would just be random noise. To visually segment "just the tumor", we use a heuristic 
    # to isolate the densest bone/calcified mass in the scan to act as the network's output.
    import scipy.ndimage as ndi
    
    # Isolate the top 0.5% brightest pixels (typical for dense bone tumors in CT)
    threshold_val = np.percentile(data, 99.5)
    high_density = data > threshold_val
    
    # Find the largest connected component to simulate a single localized tumor mass
    labeled, num_features = ndi.label(high_density)
    if num_features > 0:
        sizes = ndi.sum(high_density, labeled, range(1, num_features + 1))
        largest_idx = np.argmax(sizes) + 1
        tumor_mask = (labeled == largest_idx)
        # Dilate to form a cohesive mass
        tumor_mask = ndi.binary_dilation(tumor_mask, iterations=3)
    else:
        tumor_mask = np.zeros_like(data, dtype=bool)
        
    final_mask = tumor_mask.astype(np.uint8)
    
    mask_img = nib.Nifti1Image(final_mask, img.affine)
    nib.save(mask_img, output_path)
    
    # Save PNG overlays for each slice (DICOM) in the 3D volume
    import os
    from PIL import Image
    
    vis_dir = output_path.replace(".nii.gz", "_vis")
    os.makedirs(vis_dir, exist_ok=True)
    
    d_min, d_max = np.min(data), np.max(data)
    if d_max > d_min:
        norm_data = ((data - d_min) / (d_max - d_min) * 255).astype(np.uint8)
    else:
        norm_data = np.zeros_like(data, dtype=np.uint8)
        
    for i in range(data.shape[2]):
        base_img = norm_data[:, :, i]
        mask_slice = final_mask[:, :, i]
        
        # Create RGB image
        rgb_img = np.stack([base_img, base_img, base_img], axis=-1)
        mask_indices = mask_slice > 0
        
        # Overlay mask in red with 40% opacity
        if np.any(mask_indices):
            rgb_img[mask_indices, 0] = (0.4 * 255 + 0.6 * rgb_img[mask_indices, 0]).astype(np.uint8)
            rgb_img[mask_indices, 1] = (0.6 * rgb_img[mask_indices, 1]).astype(np.uint8)
            rgb_img[mask_indices, 2] = (0.6 * rgb_img[mask_indices, 2]).astype(np.uint8)
            
        im = Image.fromarray(rgb_img)
        im.save(os.path.join(vis_dir, f"slice_{i:03d}.png"))
    
    import time; _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'cloud_inference,{_t1-_t0:.4f}\n')
    return output_path, vis_dir

# ==========================================
# Main Workflow Execution
# ==========================================

def run_workflow(args):
    import os
    edge_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_edge'))
    fog_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_fog'))
    cloud_dir = os.path.abspath(os.path.join(args.output_dir, 'tmp_cloud'))
    import time
    _t_start = time.time()
    with open('workflow_timing.log', 'w') as _f: _f.write('task,duration_seconds\n')
    setup_parsl(use_local=args.local, workers=args.workers)
    
    # Initialize App instances
    integrity_edge = make_integrity_out_app(['edge'])
    compress_edge = make_compress_out_app(['edge'])
    encrypt_edge = make_encrypt_out_app(['edge'], algo='chacha20')
    encode_edge = make_encode_out_app(['edge'])
    
    decode_fog = make_decode_in_app(['fog'])
    decrypt_fog = make_decrypt_in_app(['fog'])
    decompress_fog = make_decompress_in_app(['fog'])
    verify_fog = make_verify_in_app(['fog'])
    
    integrity_fog = make_integrity_out_app(['fog'])
    compress_fog = make_compress_out_app(['fog'])
    encrypt_fog = make_encrypt_out_app(['fog'], algo='aes')
    encode_fog = make_encode_out_app(['fog'])
    
    decode_cloud = make_decode_in_app(['cloud'])
    decrypt_cloud = make_decrypt_in_app(['cloud'])
    decompress_cloud = make_decompress_in_app(['cloud'])
    verify_cloud = make_verify_in_app(['cloud'])
    
    # Create key file for encryption
    key_path = os.path.abspath('shared_key.bin')
    with open(key_path, 'wb') as f:
        f.write(os.urandom(32))
        
    os.makedirs(edge_dir, exist_ok=True)
    os.makedirs(fog_dir, exist_ok=True)
    os.makedirs(cloud_dir, exist_ok=True)
    
    futures = []
    
    for i in range(args.studies):
        print(f"Submitting workflow for study {i}...")
        
        # === EDGE ===
        edge_data = edge_acquisition(args.dataset, edge_dir, i)
        edge_hash_path = os.path.abspath(f'{edge_dir}/hash_{i}.json')
        edge_integrity = integrity_edge(edge_data, edge_hash_path)
        
        edge_compressed = os.path.abspath(f'{edge_dir}/compressed_{i}')
        edge_compress = compress_edge(edge_integrity, edge_compressed)
        
        edge_encrypted = os.path.abspath(f'{edge_dir}/encrypted_{i}')
        edge_encrypt = encrypt_edge(edge_compress, edge_encrypted, key_path)
        
        edge_encoded_dir = os.path.abspath(f'{edge_dir}/encoded_{i}')
        edge_encode = encode_edge(edge_encrypt, edge_encoded_dir)
        
        # === FOG ===
        fog_decrypted = os.path.abspath(f'{fog_dir}/decrypted_{i}')
        fog_decode = decode_fog(edge_encode, fog_decrypted)
        
        fog_decompressed_tmp = os.path.abspath(f'{fog_dir}/decompressed_tmp_{i}')
        fog_decrypt = decrypt_fog(fog_decode, fog_decompressed_tmp, key_path)
        
        fog_decompressed_dir = os.path.abspath(f'{fog_dir}/decompressed_{i}')
        fog_decompress = decompress_fog(fog_decrypt, fog_decompressed_dir)
        
        fog_verify = verify_fog(fog_decompress)
        
        fog_preprocessed_dir = os.path.abspath(f'{fog_dir}/preprocessed_{i}')
        fog_preprocess = fog_preprocessing(fog_verify, fog_preprocessed_dir)
        
        fog_hash_path = os.path.abspath(f'{fog_dir}/hash_{i}.json')
        fog_integrity = integrity_fog(fog_preprocess, fog_hash_path)
        
        fog_compressed = os.path.abspath(f'{fog_dir}/compressed_{i}')
        fog_compress = compress_fog(fog_integrity, fog_compressed)
        
        fog_encrypted = os.path.abspath(f'{fog_dir}/encrypted_{i}')
        fog_encrypt = encrypt_fog(fog_compress, fog_encrypted, key_path)
        
        fog_encoded_dir = os.path.abspath(f'{fog_dir}/encoded_{i}')
        fog_encode = encode_fog(fog_encrypt, fog_encoded_dir)
        
        # === CLOUD ===
        cloud_decrypted = os.path.abspath(f'{cloud_dir}/decrypted_{i}')
        cloud_decode = decode_cloud(fog_encode, cloud_decrypted)
        
        cloud_decompressed_tmp = os.path.abspath(f'{cloud_dir}/decompressed_tmp_{i}')
        cloud_decrypt = decrypt_cloud(cloud_decode, cloud_decompressed_tmp, key_path)
        
        cloud_decompressed_dir = os.path.abspath(f'{cloud_dir}/decompressed_{i}')
        cloud_decompress = decompress_cloud(cloud_decrypt, cloud_decompressed_dir)
        
        cloud_verify = verify_cloud(cloud_decompress)
        
        cloud_output = os.path.abspath(f'{cloud_dir}/inference_mask_{i}.nii.gz')
        cloud_infer = cloud_inference(cloud_verify, cloud_output)
        
        futures.append(cloud_infer)
        
    print("Waiting for workflows to complete...")
    for i, f in enumerate(futures):
        result = f.result()
        print(f"Study {i} completed. Output at: {result}")
        
    _t_end = time.time()
    print(f"\n[TIMING] Overall execution time: {_t_end - _t_start:.4f} seconds")
    print("All studies completed successfully!")

    # Explicitly shut down the DataFlowKernel and executors
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
