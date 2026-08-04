import os
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))

from nfr_tuner import get_tuned_nfrs
import real_pipeline_reference.pipeline_runner as runner

# Attempt to load DagOnStar. If not available in the local evaluation environment,
# we provide a lightweight functional mock to demonstrate the DAG and NFR tuning orchestration.
from dagonstar import Workflow, task



@task(name="GenerateData")
def generate_data(size_bytes: int) -> bytes:
    """Generate some random data."""
    return os.urandom(size_bytes)

@task(name="NFR_Compress")
def nfr_compress_task(data: bytes, tuned_nfr: dict) -> tuple:
    """Apply tuned compression NFR."""
    start = time.perf_counter()
    compressed = runner.compress_payload(data, tuned_nfr["algorithm"])
    return compressed, time.perf_counter() - start

@task(name="NFR_Cipher")
def nfr_cipher_task(data: bytes, tuned_nfr: dict) -> tuple:
    """Apply tuned cipher NFR."""
    start = time.perf_counter()
    encrypted, meta = runner.encrypt_payload(data, tuned_nfr["algorithm"], tuned_nfr.get("config", {}))
    return encrypted, meta, time.perf_counter() - start

@task(name="NFR_Hash")
def nfr_hash_task(data: bytes, tuned_nfr: dict) -> tuple:
    """Apply tuned hash NFR."""
    start = time.perf_counter()
    digest = runner.hash_digest(data, tuned_nfr["algorithm"], tuned_nfr.get("config", {}).get("hmac_key", b''))
    return digest, time.perf_counter() - start

@task(name="AppCompute")
def application_task(data: bytes) -> tuple:
    """Mock application logic that transforms data."""
    import hashlib
    start = time.perf_counter()
    digest = hashlib.sha256(data).digest()
    mixed = bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(data))
    return mixed, time.perf_counter() - start

def main():
    print("Obtaining tuned NFRs using the proposed approach...")
    tuned_nfrs = get_tuned_nfrs()
    print(f"Tuned NFR Configuration: {tuned_nfrs}")
    
    workflow = Workflow(name="NFR_Evaluation_Continuum")
    
    # In a true DagOnStar execution, we define the tasks and their inputs 
    # to implicitly or explicitly resolve dependencies.
    print("\nExecuting DagOnStar Data-Oriented Tasks...")
    
    data = generate_data(1024 * 1024) # 1 MB
    
    # Stage 1 & 2: Hash and Cipher NFRs acting on the initial data
    digest, hash_time = nfr_hash_task(data, tuned_nfrs["hash"])
    encrypted_data, meta, cipher_time = nfr_cipher_task(data, tuned_nfrs["cipher"])
    
    # Stage 3: App Compute (simulating compute on encrypted or decrypted payload depending on pipeline)
    app_data, app_time = application_task(encrypted_data)
    
    # Stage 4: Compress NFR acting on the output
    compressed_data, compress_time = nfr_compress_task(app_data, tuned_nfrs["compress"])
    
    workflow.make_dependencies()
    workflow.run()
    
    print("\nWorkflow Execution Summary:")
    print(f"- Data generated: 1 MB")
    print(f"- Hash ({tuned_nfrs['hash']['algorithm']}) time: {hash_time:.6f}s")
    print(f"- Cipher ({tuned_nfrs['cipher']['algorithm']}) time: {cipher_time:.6f}s")
    print(f"- App Compute time: {app_time:.6f}s")
    print(f"- Compress ({tuned_nfrs['compress']['algorithm']}) time: {compress_time:.6f}s")
    print(f"- Final payload size: {len(compressed_data)} bytes")

if __name__ == "__main__":
    main()
