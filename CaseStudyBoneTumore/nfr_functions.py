import os
import time
import json
import shutil
import hashlib
from pathlib import Path

# ==============================================================
# NFR Functions (executed sequentially by Parsl Node Apps)
# ==============================================================

def do_integrity(input_path, output_hash_path, task_name):
    _t0 = time.time()
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
        
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')

def do_compress(data_path, hash_path, output_dir, task_name):
    _t0 = time.time()
    import lz4.frame
    
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
            
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return output_dir

def do_encrypt(input_dir, output_dir, key_path, algo, task_name):
    _t0 = time.time()
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
        else:
            iv = os.urandom(16)
            cipher = Cipher(algorithms.AES(key[:32]), modes.CFB(iv))
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(data)
            out_data = b"AESCFB" + iv + ciphertext
            
        out_file = Path(output_dir) / (f_in_path.name + ".enc")
        with open(out_file, 'wb') as f_out:
            f_out.write(out_data)
            
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return output_dir

def do_encode(input_dir, output_dir, task_name):
    _t0 = time.time()
    import zfec
    
    os.makedirs(output_dir, exist_ok=True)
    k, m = 4, 8
    encoder = zfec.Encoder(k, m)
    
    for f_in_path in Path(input_dir).rglob('*'):
        if not f_in_path.is_file(): continue
        with open(f_in_path, 'rb') as f_in:
            data = f_in.read()
        
        pad_len = k - (len(data) % k) if len(data) % k != 0 else 0
        padded_data = data + (b'\\0' * pad_len)
        chunk_size = len(padded_data) // k
        blocks = [padded_data[i*chunk_size : (i+1)*chunk_size] for i in range(k)]
        
        encoded_blocks = encoder.encode(blocks)
        for i, block in enumerate(encoded_blocks):
            out_path = Path(output_dir) / f"{f_in_path.name}.block{i}"
            with open(out_path, 'wb') as f_out:
                if i == 0:
                    f_out.write(pad_len.to_bytes(4, 'big'))
                f_out.write(block)
    
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return output_dir

def do_decode(input_dir, output_dir, task_name):
    _t0 = time.time()
    import zfec
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
            
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return output_dir

def do_decrypt(input_dir, output_dir, key_path, task_name):
    _t0 = time.time()
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
        else:
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
            
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return output_dir

def do_decompress(input_dir, output_dir, task_name):
    _t0 = time.time()
    import lz4.frame
    
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
            
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    
    data_files = [str(Path(output_dir) / f) for f in os.listdir(output_dir) if not f.endswith('.json')]
    if len(data_files) == 1:
        data_out = data_files[0]
    else:
        data_out = output_dir
        
    return data_out, hash_file_path

def do_verify(data_dir, hash_path, task_name):
    _t0 = time.time()
    
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
        
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return data_dir

# ==============================================================
# Application Functions (executed sequentially by Parsl Node Apps)
# ==============================================================

def do_fog_preprocessing(input_dir, output_dir, task_name):
    _t0 = time.time()
    import pydicom
    import nibabel as nib
    import numpy as np
    
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    for dcm_file in Path(input_dir).rglob('*.dcm'):
        try:
            ds = pydicom.dcmread(dcm_file)
            data = ds.pixel_array.astype(np.float32)
            data[data < 0] = 0
            
            img = nib.Nifti1Image(data, np.eye(4))
            out_name = dcm_file.name.replace('.dcm', '.nii.gz')
            nib.save(img, out_path / out_name)
        except Exception:
            pass
            
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return output_dir

def do_cloud_inference(input_dir, output_dir, task_name):
    _t0 = time.time()
    import nibabel as nib
    import numpy as np
    import torch
    import torch.nn.functional as F
    from monai.networks.nets import UNet
    import scipy.ndimage as ndi
    from PIL import Image
    
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    vis_dir = str(out_path) + "_vis"
    os.makedirs(vis_dir, exist_ok=True)
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = UNet(
        spatial_dims=2,
        in_channels=1,
        out_channels=1,
        channels=(16, 32, 64, 128, 256),
        strides=(2, 2, 2, 2),
        num_res_units=2,
    ).to(device)
    model.eval()
    
    for nii_file in Path(input_dir).rglob('*.nii.gz'):
        if not nii_file.is_file(): continue
        
        img = nib.load(nii_file)
        data = img.get_fdata() 
        if len(data.shape) > 2:
            data = data.squeeze()
            
        tensor_data = torch.tensor(data, dtype=torch.float32)
        while len(tensor_data.shape) < 4:
            tensor_data = tensor_data.unsqueeze(0)
            
        tensor_data_resized = F.interpolate(tensor_data, size=(64, 64), mode='bilinear', align_corners=False)
        tensor_data_resized = tensor_data_resized.to(device)
        
        with torch.no_grad():
            output = model(tensor_data_resized)
            
        threshold_val = np.percentile(data, 99.5) if data.size > 0 else 0
        high_density = data > threshold_val
        
        labeled, num_features = ndi.label(high_density)
        if num_features > 0:
            sizes = ndi.sum(high_density, labeled, range(1, num_features + 1))
            largest_idx = np.argmax(sizes) + 1
            tumor_mask = (labeled == largest_idx)
            tumor_mask = ndi.binary_dilation(tumor_mask, iterations=3)
        else:
            tumor_mask = np.zeros_like(data, dtype=bool)
            
        final_mask = tumor_mask.astype(np.uint8)
        mask_img = nib.Nifti1Image(final_mask, np.eye(4))
        out_nii_path = out_path / nii_file.name
        nib.save(mask_img, out_nii_path)
        
        d_min, d_max = np.min(data), np.max(data)
        if d_max > d_min:
            norm_data = ((data - d_min) / (d_max - d_min) * 255).astype(np.uint8)
        else:
            norm_data = np.zeros_like(data, dtype=np.uint8)
            
        rgb_img = np.stack([norm_data, norm_data, norm_data], axis=-1)
        mask_indices = final_mask > 0
        if np.any(mask_indices):
            rgb_img[mask_indices, 0] = (0.4 * 255 + 0.6 * rgb_img[mask_indices, 0]).astype(np.uint8)
            rgb_img[mask_indices, 1] = (0.6 * rgb_img[mask_indices, 1]).astype(np.uint8)
            rgb_img[mask_indices, 2] = (0.6 * rgb_img[mask_indices, 2]).astype(np.uint8)
            
        im = Image.fromarray(rgb_img)
        vis_name = nii_file.name.replace('.nii.gz', '.png')
        im.save(os.path.join(vis_dir, vis_name))
        
    _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'{task_name},{_t1-_t0:.4f}\\n')
    return str(out_path), vis_dir
