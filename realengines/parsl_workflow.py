import os
import sys
from pathlib import Path
import time
import parsl
from parsl.app.app import python_app
from parsl.config import Config
from parsl.executors.threads import ThreadPoolExecutor

# Make sure we can import from nfr_tuner and real_pipeline_reference
sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))

from nfr_tuner import get_tuned_nfrs
import real_pipeline_reference.pipeline_runner as runner

# Configure Parsl for local execution as requested
config = Config(
    executors=[
        ThreadPoolExecutor(
            max_threads=4,
            label='local_threads'
        )
    ]
)
parsl.load(config)

@python_app
def generate_data(size_bytes: int) -> bytes:
    """Generate some random data."""
    import os
    return os.urandom(size_bytes)

@python_app
def nfr_compress_task(data: bytes, tuned_nfr: dict) -> tuple:
    """Apply tuned compression NFR."""
    import real_pipeline_reference.pipeline_runner as r
    import time
    start = time.perf_counter()
    compressed = r.compress_payload(data, tuned_nfr["algorithm"])
    return compressed, time.perf_counter() - start

@python_app
def nfr_cipher_task(data: bytes, tuned_nfr: dict) -> tuple:
    """Apply tuned cipher NFR."""
    import real_pipeline_reference.pipeline_runner as r
    import time
    start = time.perf_counter()
    encrypted, meta = r.encrypt_payload(data, tuned_nfr["algorithm"], tuned_nfr.get("config", {}))
    return encrypted, meta, time.perf_counter() - start

@python_app
def nfr_hash_task(data: bytes, tuned_nfr: dict) -> tuple:
    """Apply tuned hash NFR."""
    import real_pipeline_reference.pipeline_runner as r
    import time
    start = time.perf_counter()
    digest = r.hash_digest(data, tuned_nfr["algorithm"], tuned_nfr.get("config", {}).get("hmac_key", b''))
    return digest, time.perf_counter() - start

@python_app
def application_task(data: bytes) -> tuple:
    """Mock application logic that transforms data."""
    import hashlib
    import time
    start = time.perf_counter()
    digest = hashlib.sha256(data).digest()
    mixed = bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(data))
    return mixed, time.perf_counter() - start

def main():
    print("Obtaining tuned NFRs using the proposed approach...")
    tuned_nfrs = get_tuned_nfrs()
    print(f"Tuned NFR Configuration: {tuned_nfrs}")
    
    print("\nStarting Parsl Workflow Evaluation...")
    # 1. Generate Input Data
    data_future = generate_data(1024 * 1024) # 1 MB
    
    # 2. Stage 1: Add Integrity (Hash) NFR as a workflow task
    hash_future = nfr_hash_task(data_future, tuned_nfrs["hash"])
    
    # 3. Stage 2: Add Confidentiality (Cipher) NFR as a workflow task
    cipher_future = nfr_cipher_task(data_future, tuned_nfrs["cipher"])
    
    # Wait for cipher to finish before passing to app (simulating network or disk boundary)
    encrypted_data, meta, cipher_time = cipher_future.result()
    
    # 4. Stage 3: Application Compute
    app_future = application_task(encrypted_data)
    app_data, app_time = app_future.result()
    
    # 5. Stage 4: Add Compression NFR as a workflow task
    compress_future = nfr_compress_task(app_data, tuned_nfrs["compress"])
    compressed_data, compress_time = compress_future.result()
    
    # 6. Gather results
    digest, hash_time = hash_future.result()
    
    print("\nWorkflow Execution Summary:")
    print(f"- Data generated: 1 MB")
    print(f"- Hash ({tuned_nfrs['hash']['algorithm']}) time: {hash_time:.6f}s")
    print(f"- Cipher ({tuned_nfrs['cipher']['algorithm']}) time: {cipher_time:.6f}s")
    print(f"- App Compute time: {app_time:.6f}s")
    print(f"- Compress ({tuned_nfrs['compress']['algorithm']}) time: {compress_time:.6f}s")
    print(f"- Final payload size: {len(compressed_data)} bytes")
    
if __name__ == "__main__":
    main()
