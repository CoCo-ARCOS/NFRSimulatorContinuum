import os
import re

def main():
    with open('workflow.py', 'r') as f:
        original = f.read()

    # Extract sections
    # Everything before make_integrity_out_app
    parts = original.split('def make_integrity_out_app(executors):')
    header = parts[0]
    
    # Extract Application Apps
    app_parts = parts[1].split('# ==========================================\n# Application Apps\n# ==========================================')
    nfr_original = 'def make_integrity_out_app(executors):' + app_parts[0]
    
    main_parts = app_parts[1].split('# ==========================================\n# Main Workflow Execution\n# ==========================================')
    app_apps = '# ==========================================\n# Application Apps\n# ==========================================' + main_parts[0]
    main_workflow = '# ==========================================\n# Main Workflow Execution\n# ==========================================' + main_parts[1]

    # New NFR implementations for DICOM-by-DICOM
    new_nfr = '''def make_integrity_out_app(executors):
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
                if p.is_file():
                    h.update(p.name.encode('utf-8'))
                    hash_file(p)
        else:
            hash_file(in_path)
            
        hash_val = h.hexdigest()
        with open(output_hash_path, 'w') as f:
            json.dump({'hash': hash_val}, f)
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'integrity_out,{_t1-_t0:.4f}\\n')
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
        with open('workflow_timing.log', 'a') as _f: _f.write(f'compress_out,{_t1-_t0:.4f}\\n')
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
        with open('workflow_timing.log', 'a') as _f: _f.write(f'encrypt_out,{_t1-_t0:.4f}\\n')
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
        
        import time; _t1 = time.time()
        with open('workflow_timing.log', 'a') as _f: _f.write(f'encode_out,{_t1-_t0:.4f}\\n')
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
        with open('workflow_timing.log', 'a') as _f: _f.write(f'decode_in,{_t1-_t0:.4f}\\n')
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
        with open('workflow_timing.log', 'a') as _f: _f.write(f'decrypt_in,{_t1-_t0:.4f}\\n')
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
        with open('workflow_timing.log', 'a') as _f: _f.write(f'decompress_in,{_t1-_t0:.4f}\\n')
        return output_dir, hash_file_path
    return decompress_in

'''

    # Modify run_workflow dynamically
    run_wf = main_workflow.replace("edge_compressed = os.path.abspath(f'{edge_dir}/compressed_{i}.lz4')", "edge_compressed = os.path.abspath(f'{edge_dir}/compressed_{i}')")
    run_wf = run_wf.replace("edge_encrypted = os.path.abspath(f'{edge_dir}/encrypted_{i}.bin')", "edge_encrypted = os.path.abspath(f'{edge_dir}/encrypted_{i}')")
    
    run_wf = run_wf.replace("fog_decrypted = os.path.abspath(f'{fog_dir}/decrypted_{i}.bin')", "fog_decrypted = os.path.abspath(f'{fog_dir}/decrypted_{i}')")
    run_wf = run_wf.replace("fog_decompressed_tmp = os.path.abspath(f'{fog_dir}/decompressed_tmp_{i}.lz4')", "fog_decompressed_tmp = os.path.abspath(f'{fog_dir}/decompressed_tmp_{i}')")
    
    run_wf = run_wf.replace("fog_compressed = os.path.abspath(f'{fog_dir}/compressed_{i}.lz4')", "fog_compressed = os.path.abspath(f'{fog_dir}/compressed_{i}')")
    run_wf = run_wf.replace("fog_encrypted = os.path.abspath(f'{fog_dir}/encrypted_{i}.bin')", "fog_encrypted = os.path.abspath(f'{fog_dir}/encrypted_{i}')")
    
    run_wf = run_wf.replace("cloud_decrypted = os.path.abspath(f'{cloud_dir}/decrypted_{i}.bin')", "cloud_decrypted = os.path.abspath(f'{cloud_dir}/decrypted_{i}')")
    run_wf = run_wf.replace("cloud_decompressed_tmp = os.path.abspath(f'{cloud_dir}/decompressed_tmp_{i}.lz4')", "cloud_decompressed_tmp = os.path.abspath(f'{cloud_dir}/decompressed_tmp_{i}')")
    
    # Also in run_wf, edge_encoded_prefix = .../encoded_{i} -> output_dir = .../encoded_{i}
    # But wait, make_encode_out_app previously took output_prefix and we replaced it with output_dir in the signature!
    # "edge_encoded_prefix" is still technically the path passed, which acts as output_dir. Let's rename it for clarity.
    run_wf = run_wf.replace("edge_encoded_prefix = ", "edge_encoded_dir = ")
    run_wf = run_wf.replace("encode_edge(edge_encrypt, edge_encoded_prefix)", "encode_edge(edge_encrypt, edge_encoded_dir)")
    run_wf = run_wf.replace("decode_fog(edge_encode, fog_decrypted)", "decode_fog(edge_encode, fog_decrypted)") # edge_encode returns edge_encoded_dir
    
    run_wf = run_wf.replace("fog_encoded_prefix = ", "fog_encoded_dir = ")
    run_wf = run_wf.replace("encode_fog(fog_encrypt, fog_encoded_prefix)", "encode_fog(fog_encrypt, fog_encoded_dir)")

    with open('workflow_dicom_by_dicom.py', 'w') as f:
        f.write(header + new_nfr + '\n' + app_apps + run_wf)

    print("workflow_dicom_by_dicom.py generated successfully.")

if __name__ == "__main__":
    main()
