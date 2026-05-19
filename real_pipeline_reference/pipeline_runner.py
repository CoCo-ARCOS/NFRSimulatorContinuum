#!/usr/bin/env python3
import argparse
import bz2
import csv
import hashlib
import hmac
import json
import lzma
import os
import random
import shutil
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import tempfile
from functools import partial

try:
    from Crypto.Cipher import AES, ChaCha20
    from Crypto.Random import get_random_bytes
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    AES = None
    ChaCha20 = None
    get_random_bytes = None
    pad = None
    unpad = None

try:
    import ascon
except ImportError:
    ascon = None

try:
    import blake3
except ImportError:
    blake3 = None

try:
    import lz4.frame
except ImportError:
    lz4 = None
else:
    lz4 = lz4.frame

try:
    import zstandard as zstd
except ImportError:
    zstd = None

try:
    from zfec.easyfec import Decoder, Encoder
except ImportError:
    Decoder = None
    Encoder = None


SUPPORTED_HASHES = {"SHA256", "SHA3_256", "BLAKE3", "HMAC_SHA256"}
SUPPORTED_COMPRESSORS = {"ZLIB", "BZ2", "LZMA", "LZ4", "ZSTD"}
SUPPORTED_CIPHERS = {"AES", "CHACHA20", "ASCON", "RS"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a real reference pipeline aligned with the benchmark implementations."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("real_pipeline_reference/sample_config.json"),
        help="Pipeline configuration file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of workers to use. Defaults to config value or CPU count.",
    )
    run_only = parser.add_mutually_exclusive_group()
    run_only.add_argument(
        "--run-only",
        choices=("input", "output"),
        help="Run only the stage input requirements or only the stage output requirements.",
    )
    run_only.add_argument(
        "--input-only",
        action="store_true",
        help="Run only the stage input requirements and skip application/output processing.",
    )
    run_only.add_argument(
        "--output-only",
        action="store_true",
        help="Run only the stage output requirements and skip input/application processing.",
    )
    return parser.parse_args()


def load_config(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def write_bytes(path: Path, data: bytes):
    with path.open("wb") as fp:
        fp.write(data)


def read_bytes(path: Path):
    with path.open("rb") as fp:
        return fp.read()


def metadata_path(work_dir: Path, stage_name: str, file_name: str):
    return work_dir / stage_name / f"{file_name}.meta.json"


def data_path(work_dir: Path, stage_name: str, file_name: str):
    return work_dir / stage_name / file_name


def require_dependency(name: str, dependency, install_hint: str):
    if dependency is None:
        raise RuntimeError(f"{name} is required for this pipeline. Install it with: {install_hint}")


def normalize_hash_algorithm(algorithm: str):
    algorithm = algorithm.upper()
    if algorithm not in SUPPORTED_HASHES:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")
    return algorithm


def normalize_compress_algorithm(algorithm: str):
    algorithm = algorithm.upper()
    if algorithm not in SUPPORTED_COMPRESSORS:
        raise ValueError(f"Unsupported compression algorithm: {algorithm}")
    return algorithm


def normalize_cipher_algorithm(algorithm: str):
    algorithm = algorithm.upper()
    if algorithm not in SUPPORTED_CIPHERS:
        raise ValueError(f"Unsupported cipher algorithm: {algorithm}")
    return algorithm


def generate_input_files(config, input_dir: Path):
    ensure_dir(input_dir)
    spec = config.get("generate_input", {})
    file_count = int(spec.get("files", 0))
    size_mb = float(spec.get("size_mb", 1))
    seed = int(spec.get("seed", 42))
    mode = str(spec.get("mode", "random")).lower()
    rng = random.Random(seed)

    for index in range(file_count):
        path = input_dir / f"object_{index:03d}.bin"
        if path.exists():
            continue
        size_bytes = max(1, int(size_mb * 1048576))
        if mode == "compressible":
            base_pattern = (
                b"GET /api/v1/users/data HTTP/1.1\nHost: example.com\nStatus: 200 OK\n" * 10
            )
            base_pattern += b"Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5
            multiplier = (size_bytes // len(base_pattern)) + 1
            payload = (base_pattern * multiplier)[:size_bytes]
        else:
            payload = bytes(rng.randrange(0, 256) for _ in range(size_bytes))
        write_bytes(path, payload)


def measure_write(path: Path, data: bytes):
    start = time.perf_counter()
    with path.open("wb") as fp:
        fp.write(data)
    return time.perf_counter() - start


def measure_read(path: Path):
    start = time.perf_counter()
    with path.open("rb") as fp:
        data = fp.read()
    return data, time.perf_counter() - start


def build_stage_temp_paths(temp_dir: Path, stage_name: str, file_name: str, requirement_index: int, suffix: str):
    safe_file = file_name.replace("/", "_")
    prefix = f"{stage_name}_{requirement_index:02d}_{safe_file}_{suffix}"
    return temp_dir / prefix


def hash_digest(data: bytes, algorithm: str, hmac_key: bytes):
    algorithm = normalize_hash_algorithm(algorithm)
    if algorithm == "SHA256":
        return hashlib.sha256(data).hexdigest()
    if algorithm == "SHA3_256":
        return hashlib.sha3_256(data).hexdigest()
    if algorithm == "BLAKE3":
        require_dependency("BLAKE3", blake3, "pip install blake3")
        return blake3.blake3(data).hexdigest()
    if algorithm == "HMAC_SHA256":
        return hmac.new(hmac_key, data, hashlib.sha256).hexdigest()
    raise ValueError(f"Unsupported hash algorithm: {algorithm}")


def compress_payload(data: bytes, algorithm: str):
    algorithm = normalize_compress_algorithm(algorithm)
    if algorithm == "ZLIB":
        return zlib.compress(data)
    if algorithm == "BZ2":
        return bz2.compress(data)
    if algorithm == "LZMA":
        return lzma.compress(data)
    if algorithm == "LZ4":
        require_dependency("LZ4", lz4, "pip install lz4")
        return lz4.compress(data)
    if algorithm == "ZSTD":
        require_dependency("Zstandard", zstd, "pip install zstandard")
        return zstd.ZstdCompressor().compress(data)
    raise ValueError(f"Unsupported compression algorithm: {algorithm}")


def decompress_payload(data: bytes, algorithm: str):
    algorithm = normalize_compress_algorithm(algorithm)
    if algorithm == "ZLIB":
        return zlib.decompress(data)
    if algorithm == "BZ2":
        return bz2.decompress(data)
    if algorithm == "LZMA":
        return lzma.decompress(data)
    if algorithm == "LZ4":
        require_dependency("LZ4", lz4, "pip install lz4")
        return lz4.decompress(data)
    if algorithm == "ZSTD":
        require_dependency("Zstandard", zstd, "pip install zstandard")
        return zstd.ZstdDecompressor().decompress(data)
    raise ValueError(f"Unsupported compression algorithm: {algorithm}")


def encrypt_payload(data: bytes, algorithm: str, config):
    algorithm = normalize_cipher_algorithm(algorithm)
    if algorithm == "AES":
        require_dependency("PyCryptodome", AES, "pip install pycryptodome")
        key_bits = int(config.get("aes_key_bits", 256))
        if key_bits not in (128, 192, 256):
            raise ValueError(f"Unsupported AES key size: {key_bits}")
        key = get_random_bytes(key_bits // 8)
        iv = get_random_bytes(16)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(data, AES.block_size))
        metadata = {"algorithm": algorithm, "key": key.hex(), "iv": iv.hex()}
        return encrypted, metadata
    if algorithm == "CHACHA20":
        require_dependency("PyCryptodome", ChaCha20, "pip install pycryptodome")
        key = get_random_bytes(32)
        cipher = ChaCha20.new(key=key)
        encrypted = cipher.encrypt(data)
        metadata = {"algorithm": algorithm, "key": key.hex(), "nonce": cipher.nonce.hex()}
        return encrypted, metadata
    if algorithm == "ASCON":
        require_dependency("ASCON", ascon, "pip install ascon")
        require_dependency("PyCryptodome", get_random_bytes, "pip install pycryptodome")
        key = get_random_bytes(16)
        nonce = get_random_bytes(16)
        associated_data = b""
        encrypted = ascon.encrypt(key, nonce, associated_data, data, variant="Ascon-128")
        metadata = {
            "algorithm": algorithm,
            "key": key.hex(),
            "nonce": nonce.hex(),
            "associated_data": associated_data.hex(),
        }
        return encrypted, metadata
    if algorithm == "RS":
        require_dependency("ZFEC", Encoder, "pip install zfec")
        k = int(config.get("ida_k", 8))
        m = int(config.get("ida_m", 4))
        total_blocks = k + m
        encoder = Encoder(k, total_blocks)
        fragments = encoder.encode(data)
        fragment_size = len(fragments[0]) if fragments else 0
        serialized = b"".join(fragments)
        metadata = {
            "algorithm": algorithm,
            "k": k,
            "m": m,
            "fragment_size": fragment_size,
            "original_size": len(data),
        }
        return serialized, metadata
    raise ValueError(f"Unsupported cipher algorithm: {algorithm}")


def decrypt_payload(data: bytes, metadata):
    algorithm = normalize_cipher_algorithm(metadata["algorithm"])
    if algorithm == "AES":
        require_dependency("PyCryptodome", AES, "pip install pycryptodome")
        key = bytes.fromhex(metadata["key"])
        iv = bytes.fromhex(metadata["iv"])
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(data), AES.block_size)
    if algorithm == "CHACHA20":
        require_dependency("PyCryptodome", ChaCha20, "pip install pycryptodome")
        key = bytes.fromhex(metadata["key"])
        nonce = bytes.fromhex(metadata["nonce"])
        cipher = ChaCha20.new(key=key, nonce=nonce)
        return cipher.decrypt(data)
    if algorithm == "ASCON":
        require_dependency("ASCON", ascon, "pip install ascon")
        key = bytes.fromhex(metadata["key"])
        nonce = bytes.fromhex(metadata["nonce"])
        associated_data = bytes.fromhex(metadata.get("associated_data", ""))
        decrypted = ascon.decrypt(key, nonce, associated_data, data, variant="Ascon-128")
        if decrypted is None:
            raise RuntimeError("ASCON decryption failed.")
        return decrypted
    if algorithm == "RS":
        require_dependency("ZFEC", Decoder, "pip install zfec")
        k = int(metadata["k"])
        m = int(metadata["m"])
        total_blocks = k + m
        fragment_size = int(metadata["fragment_size"])
        expected_size = fragment_size * total_blocks
        if len(data) != expected_size:
            raise RuntimeError("Serialized RS fragments have unexpected size.")
        fragments = [
            data[index * fragment_size:(index + 1) * fragment_size]
            for index in range(total_blocks)
        ]
        decoder = Decoder(k, total_blocks)
        available_indices = list(range(k))
        available_fragments = fragments[:k]
        decoded = decoder.decode(available_fragments, available_indices, padlen=0)
        return decoded[: int(metadata["original_size"])]
    raise ValueError(f"Unsupported cipher algorithm: {algorithm}")


def load_metadata(path: Path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def save_metadata(path: Path, metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(metadata, fp, indent=2)
        fp.write("\n")


def apply_hash_output(data: bytes, metadata, algorithm: str, config, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    temp_orig = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "orig.bin")
    io_write = measure_write(temp_orig, data)
    raw_data_from_disk, io_read = measure_read(temp_orig)
    start = time.perf_counter()
    digest = hash_digest(raw_data_from_disk, algorithm, config["hmac_key"])
    compute = time.perf_counter() - start
    metadata.setdefault("hashes", {})[algorithm] = digest
    if temp_orig.exists():
        temp_orig.unlink()
    return data, io_write + io_read + compute, compute


def apply_hash_input(data: bytes, metadata, algorithm: str, config, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    temp_orig = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "orig.bin")
    io_write = measure_write(temp_orig, data)
    raw_data_from_disk, io_read = measure_read(temp_orig)
    start = time.perf_counter()
    digest = hash_digest(raw_data_from_disk, algorithm, config["hmac_key"])
    compute = time.perf_counter() - start
    expected = metadata.get("hashes", {}).get(algorithm)
    metadata.setdefault("hash_verification", {})[algorithm] = {
        "expected": expected,
        "actual": digest,
        "match": (expected == digest) if expected is not None else None,
    }
    if temp_orig.exists():
        temp_orig.unlink()
    return data, io_write + io_read + compute, compute


def apply_compress_output(data: bytes, metadata, algorithm: str, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    temp_orig = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "orig.bin")
    temp_comp = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "comp.bin")
    io_write_orig = measure_write(temp_orig, data)
    raw_data_from_disk, io_read_orig = measure_read(temp_orig)
    start = time.perf_counter()
    compressed = compress_payload(raw_data_from_disk, algorithm)
    compute = time.perf_counter() - start
    io_write_comp = measure_write(temp_comp, compressed)
    compressed_from_disk, io_read_comp = measure_read(temp_comp)
    metadata.setdefault("compression_stack", []).append(
        {"algorithm": algorithm, "original_size": len(data)}
    )
    total = io_write_orig + io_read_orig + compute + io_write_comp + io_read_comp
    if temp_orig.exists():
        temp_orig.unlink()
    if temp_comp.exists():
        temp_comp.unlink()
    return compressed_from_disk, total, compute


def apply_compress_input(data: bytes, metadata, algorithm: str, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    temp_comp = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "comp.bin")
    io_write_comp = measure_write(temp_comp, data)
    compressed_from_disk, io_read_comp = measure_read(temp_comp)
    start = time.perf_counter()
    decompressed = decompress_payload(compressed_from_disk, algorithm)
    compute = time.perf_counter() - start
    stack = metadata.get("compression_stack", [])
    if stack:
        stack.pop()
    total = io_write_comp + io_read_comp + compute
    if temp_comp.exists():
        temp_comp.unlink()
    return decompressed, total, compute


def apply_cipher_output(data: bytes, metadata, algorithm: str, config, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    temp_orig = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "orig.bin")
    temp_enc = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "enc.bin")
    io_write_orig = measure_write(temp_orig, data)
    raw_data_from_disk, io_read_orig = measure_read(temp_orig)
    start = time.perf_counter()
    encrypted, cipher_metadata = encrypt_payload(raw_data_from_disk, algorithm, config)
    compute = time.perf_counter() - start
    io_write_enc = measure_write(temp_enc, encrypted)
    encrypted_from_disk, io_read_enc = measure_read(temp_enc)
    metadata.setdefault("cipher_stack", []).append(cipher_metadata)
    total = io_write_orig + io_read_orig + compute + io_write_enc + io_read_enc
    if temp_orig.exists():
        temp_orig.unlink()
    if temp_enc.exists():
        temp_enc.unlink()
    return encrypted_from_disk, total, compute


def apply_cipher_input(data: bytes, metadata, algorithm: str, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    temp_enc = build_stage_temp_paths(stage_temp_dir, stage_name, file_name, requirement_index, "enc.bin")
    io_write_enc = measure_write(temp_enc, data)
    encrypted_from_disk, io_read_enc = measure_read(temp_enc)
    stack = metadata.get("cipher_stack", [])
    if not stack:
        raise RuntimeError(f"No stored cipher metadata found for {algorithm}.")
    cipher_metadata = stack.pop()
    if normalize_cipher_algorithm(cipher_metadata["algorithm"]) != normalize_cipher_algorithm(algorithm):
        raise RuntimeError(
            f"Cipher mismatch while decoding {file_name}: expected {cipher_metadata['algorithm']}, got {algorithm}."
        )
    start = time.perf_counter()
    decrypted = decrypt_payload(encrypted_from_disk, cipher_metadata)
    compute = time.perf_counter() - start
    total = io_write_enc + io_read_enc + compute
    if temp_enc.exists():
        temp_enc.unlink()
    return decrypted, total, compute


def apply_output_requirement(data: bytes, metadata, requirement, config, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    task_type = requirement["type"]
    algorithm = requirement["algorithm"].upper()

    if task_type == "hash":
        return apply_hash_output(data, metadata, algorithm, config, stage_temp_dir, stage_name, file_name, requirement_index)
    if task_type == "compress":
        return apply_compress_output(data, metadata, algorithm, stage_temp_dir, stage_name, file_name, requirement_index)
    if task_type == "cipher":
        return apply_cipher_output(data, metadata, algorithm, config, stage_temp_dir, stage_name, file_name, requirement_index)
    raise ValueError(f"Unsupported requirement type: {task_type}")


def apply_input_requirement(data: bytes, metadata, requirement, config, stage_temp_dir: Path, stage_name: str, file_name: str, requirement_index: int):
    task_type = requirement["type"]
    algorithm = requirement["algorithm"].upper()

    if task_type == "hash":
        return apply_hash_input(data, metadata, algorithm, config, stage_temp_dir, stage_name, file_name, requirement_index)
    if task_type == "compress":
        return apply_compress_input(data, metadata, algorithm, stage_temp_dir, stage_name, file_name, requirement_index)
    if task_type == "cipher":
        return apply_cipher_input(data, metadata, algorithm, stage_temp_dir, stage_name, file_name, requirement_index)
    raise ValueError(f"Unsupported requirement type: {task_type}")


def transform_application_bytes(data: bytes, size_factor: float):
    digest = hashlib.sha256(data).digest() or b"\x00"
    mixed = bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(data))
    target_size = max(1, int(len(mixed) * size_factor))
    if target_size <= len(mixed):
        return mixed[:target_size]

    extension = bytearray()
    seed = hashlib.sha256(mixed).digest()
    while len(mixed) + len(extension) < target_size:
        seed = hashlib.sha256(seed).digest()
        extension.extend(seed)
    return mixed + bytes(extension[: target_size - len(mixed)])


def apply_application(data: bytes, size_factor: float, stage_temp_dir: Path, stage_name: str, file_name: str):
    temp_input = stage_temp_dir / f"{stage_name}_{file_name.replace('/', '_')}_app_input.bin"
    temp_output = stage_temp_dir / f"{stage_name}_{file_name.replace('/', '_')}_app_output.bin"
    io_write_in = measure_write(temp_input, data)
    input_from_disk, io_read_in = measure_read(temp_input)
    compute_start = time.perf_counter()
    transformed = transform_application_bytes(input_from_disk, size_factor)
    compute = time.perf_counter() - compute_start
    io_write_out = measure_write(temp_output, transformed)
    output_from_disk, io_read_out = measure_read(temp_output)
    if temp_input.exists():
        temp_input.unlink()
    if temp_output.exists():
        temp_output.unlink()
    return output_from_disk, {
        "application_read_seconds": io_write_in + io_read_in,
        "application_compute_seconds": compute,
        "application_write_seconds": io_write_out + io_read_out,
        "application_total_seconds": io_write_in + io_read_in + compute + io_write_out + io_read_out,
    }


def normalize_stage(stage):
    normalized = dict(stage)
    normalized.setdefault("input_requirements", [])
    normalized.setdefault("output_requirements", [])
    normalized.setdefault("application_size_factor", 1.0)
    return normalized


def derive_stage_inputs(stages):
    derived = []
    previous_output_requirements = []
    for stage in stages:
        normalized = normalize_stage(stage)
        explicit_inputs = list(normalized.get("input_requirements", []))
        if explicit_inputs:
            normalized["effective_input_requirements"] = explicit_inputs
        else:
            normalized["effective_input_requirements"] = list(reversed(previous_output_requirements))
        derived.append(normalized)
        previous_output_requirements = list(normalized.get("output_requirements", []))
    return derived


def prepare_runtime_config(config):
    runtime = dict(config)
    runtime["hmac_key"] = os.urandom(32)
    return runtime


def requirement_family(requirement):
    task_type = requirement["type"].lower()
    if task_type == "compress":
        return "compression"
    if task_type == "hash":
        return "hash"
    if task_type == "cipher":
        return "crypto"
    return "other"


def requirement_label(requirement, direction):
    task_type = requirement["type"].lower()
    algorithm = requirement["algorithm"].upper()
    if direction == "input":
        if task_type == "compress":
            action = "uncompress"
        elif task_type == "hash":
            action = "hash_verify"
        elif task_type == "cipher":
            action = "unencrypt"
        else:
            action = task_type
    else:
        if task_type == "compress":
            action = "compress"
        elif task_type == "hash":
            action = "hash_calculate"
        elif task_type == "cipher":
            action = "encrypt"
        else:
            action = task_type
    return f"{action}:{algorithm}"


def init_stage_total(stage_index, stage_name, workers, objects):
    return {
        "stage": stage_index + 1,
        "stage_name": stage_name,
        "workers": workers,
        "objects": objects,
        "input_stage_seconds": 0.0,
        "input_stage_compute_seconds": 0.0,
        "input_compression_seconds": 0.0,
        "input_compression_compute_seconds": 0.0,
        "input_hash_seconds": 0.0,
        "input_hash_compute_seconds": 0.0,
        "input_crypto_seconds": 0.0,
        "input_crypto_compute_seconds": 0.0,
        "application_seconds": 0.0,
        "application_compute_seconds": 0.0,
        "output_stage_seconds": 0.0,
        "output_stage_compute_seconds": 0.0,
        "output_compression_seconds": 0.0,
        "output_compression_compute_seconds": 0.0,
        "output_hash_seconds": 0.0,
        "output_hash_compute_seconds": 0.0,
        "output_crypto_seconds": 0.0,
        "output_crypto_compute_seconds": 0.0,
        "total_seconds": 0.0,
        "total_compute_seconds": 0.0,
        "input_requirement_labels": [],
        "input_requirement_seconds": [],
        "input_requirement_compute_seconds": [],
        "output_requirement_labels": [],
        "output_requirement_seconds": [],
        "output_requirement_compute_seconds": [],
    }


def process_stage_object(
    obj,
    stage,
    runtime_config,
    temp_root_path: Path,
    work_dir: Path,
    experiment_start: float,
):
    file_name = obj["file_name"]
    current_data = obj["data"]
    metadata = obj["metadata"]
    arrival_time = float(obj["arrival_time"])
    stage_name = stage["name"]
    stage_temp_dir = temp_root_path / stage_name / file_name
    ensure_dir(stage_temp_dir)
    service_start_time = time.perf_counter() - experiment_start

    stage_input_time = 0.0
    stage_output_time = 0.0
    stage_input_compute_time = 0.0
    stage_output_compute_time = 0.0
    input_requirement_times = []
    output_requirement_times = []
    input_requirement_compute_times = []
    output_requirement_compute_times = []

    run_only = runtime_config.get("run_only")

    if run_only != "output":
        for index, requirement in enumerate(stage["effective_input_requirements"]):
            current_data, elapsed, compute_elapsed = apply_input_requirement(
                current_data,
                metadata,
                requirement,
                runtime_config,
                stage_temp_dir,
                stage_name,
                file_name,
                index,
            )
            stage_input_time += elapsed
            stage_input_compute_time += compute_elapsed
            input_requirement_times.append(elapsed)
            input_requirement_compute_times.append(compute_elapsed)

    app_input_copy = bytes(current_data)
    app_times = {
        "application_read_seconds": 0.0,
        "application_compute_seconds": 0.0,
        "application_write_seconds": 0.0,
        "application_total_seconds": 0.0,
    }

    if run_only is None:
        current_data, app_times = apply_application(
            app_input_copy,
            float(stage.get("application_size_factor", 1.0)),
            stage_temp_dir,
            stage_name,
            file_name,
        )

        for index, requirement in enumerate(stage["output_requirements"]):
            current_data, elapsed, compute_elapsed = apply_output_requirement(
                current_data,
                metadata,
                requirement,
                runtime_config,
                stage_temp_dir,
                stage_name,
                file_name,
                index,
            )
            stage_output_time += elapsed
            stage_output_compute_time += compute_elapsed
            output_requirement_times.append(elapsed)
            output_requirement_compute_times.append(compute_elapsed)
    elif run_only == "output":
        for index, requirement in enumerate(stage["output_requirements"]):
            current_data, elapsed, compute_elapsed = apply_output_requirement(
                current_data,
                metadata,
                requirement,
                runtime_config,
                stage_temp_dir,
                stage_name,
                file_name,
                index,
            )
            stage_output_time += elapsed
            stage_output_compute_time += compute_elapsed
            output_requirement_times.append(elapsed)
            output_requirement_compute_times.append(compute_elapsed)

    stage_dir = work_dir / stage_name
    ensure_dir(stage_dir)
    # Avoid overloading filesystem with intermediary objects
    # write_bytes(data_path(work_dir, stage_name, file_name), current_data)
    # save_metadata(metadata_path(work_dir, stage_name, file_name), metadata)

    file_row = {
        "file": file_name,
        "stage": stage_name,
        "input_seconds": stage_input_time,
        "input_compute_seconds": stage_input_compute_time,
        "application_read_seconds": app_times["application_read_seconds"],
        "application_compute_seconds": app_times["application_compute_seconds"],
        "application_write_seconds": app_times["application_write_seconds"],
        "application_total_seconds": app_times["application_total_seconds"],
        "output_seconds": stage_output_time,
        "output_compute_seconds": stage_output_compute_time,
        "input_size_bytes": len(app_input_copy),
        "output_size_bytes": len(current_data),
        "application_size_factor": float(stage.get("application_size_factor", 1.0)),
    }
    completion_time = time.perf_counter() - experiment_start
    total_service_time = (
        stage_input_time + app_times["application_total_seconds"] + stage_output_time
    )
    timeline_row = {
        "file": file_name,
        "stage": stage_name,
        "arrival_time_seconds": arrival_time,
        "service_start_time_seconds": service_start_time,
        "completion_time_seconds": completion_time,
        "waiting_time_seconds": service_start_time - arrival_time,
        "service_time_seconds": total_service_time,
        "response_time_seconds": completion_time - arrival_time,
    }

    return {
        "file_name": file_name,
        "data": current_data,
        "metadata": metadata,
        "available_time": completion_time,
        "file_row": file_row,
        "timeline_row": timeline_row,
        "stage_input_time": stage_input_time,
        "stage_output_time": stage_output_time,
        "stage_input_compute_time": stage_input_compute_time,
        "stage_output_compute_time": stage_output_compute_time,
        "app_times": app_times,
        "input_requirement_times": input_requirement_times,
        "output_requirement_times": output_requirement_times,
        "input_requirement_compute_times": input_requirement_compute_times,
        "output_requirement_compute_times": output_requirement_compute_times,
    }


def update_stage_total_from_result(stage_total, stage, result):
    stage_total["input_stage_seconds"] += result["stage_input_time"]
    stage_total["input_stage_compute_seconds"] += result["stage_input_compute_time"]
    stage_total["application_seconds"] += result["app_times"]["application_total_seconds"]
    stage_total["application_compute_seconds"] += result["app_times"]["application_compute_seconds"]
    stage_total["output_stage_seconds"] += result["stage_output_time"]
    stage_total["output_stage_compute_seconds"] += result["stage_output_compute_time"]
    stage_total["total_seconds"] += (
        result["stage_input_time"]
        + result["app_times"]["application_total_seconds"]
        + result["stage_output_time"]
    )
    stage_total["total_compute_seconds"] += (
        result["stage_input_compute_time"]
        + result["app_times"]["application_compute_seconds"]
        + result["stage_output_compute_time"]
    )

    for index, requirement in enumerate(stage["effective_input_requirements"]):
        elapsed = result["input_requirement_times"][index]
        compute_elapsed = result["input_requirement_compute_times"][index]
        while len(stage_total["input_requirement_seconds"]) <= index:
            stage_total["input_requirement_seconds"].append(0.0)
        while len(stage_total["input_requirement_compute_seconds"]) <= index:
            stage_total["input_requirement_compute_seconds"].append(0.0)
        stage_total["input_requirement_seconds"][index] += elapsed
        stage_total["input_requirement_compute_seconds"][index] += compute_elapsed

        family = requirement_family(requirement)
        if family == "compression":
            stage_total["input_compression_seconds"] += elapsed
            stage_total["input_compression_compute_seconds"] += compute_elapsed
        elif family == "hash":
            stage_total["input_hash_seconds"] += elapsed
            stage_total["input_hash_compute_seconds"] += compute_elapsed
        elif family == "crypto":
            stage_total["input_crypto_seconds"] += elapsed
            stage_total["input_crypto_compute_seconds"] += compute_elapsed

    for index, requirement in enumerate(stage["output_requirements"]):
        elapsed = result["output_requirement_times"][index]
        compute_elapsed = result["output_requirement_compute_times"][index]
        while len(stage_total["output_requirement_seconds"]) <= index:
            stage_total["output_requirement_seconds"].append(0.0)
        while len(stage_total["output_requirement_compute_seconds"]) <= index:
            stage_total["output_requirement_compute_seconds"].append(0.0)
        stage_total["output_requirement_seconds"][index] += elapsed
        stage_total["output_requirement_compute_seconds"][index] += compute_elapsed

        family = requirement_family(requirement)
        if family == "compression":
            stage_total["output_compression_seconds"] += elapsed
            stage_total["output_compression_compute_seconds"] += compute_elapsed
        elif family == "hash":
            stage_total["output_hash_seconds"] += elapsed
            stage_total["output_hash_compute_seconds"] += compute_elapsed
        elif family == "crypto":
            stage_total["output_crypto_seconds"] += elapsed
            stage_total["output_crypto_compute_seconds"] += compute_elapsed


def build_stage_totals_rows(stage_totals):
    max_input = max((len(stage_total["input_requirement_labels"]) for stage_total in stage_totals), default=0)
    max_output = max((len(stage_total["output_requirement_labels"]) for stage_total in stage_totals), default=0)
    rows = []
    for stage_total in stage_totals:
        row = {
            "stage": stage_total["stage"],
            "stage_name": stage_total["stage_name"],
            "workers": stage_total["workers"],
            "objects": stage_total["objects"],
            "input_stage_seconds": stage_total["input_stage_seconds"],
            "input_stage_compute_seconds": stage_total["input_stage_compute_seconds"],
            "input_compression_seconds": stage_total["input_compression_seconds"],
            "input_compression_compute_seconds": stage_total["input_compression_compute_seconds"],
            "input_hash_seconds": stage_total["input_hash_seconds"],
            "input_hash_compute_seconds": stage_total["input_hash_compute_seconds"],
            "input_crypto_seconds": stage_total["input_crypto_seconds"],
            "input_crypto_compute_seconds": stage_total["input_crypto_compute_seconds"],
            "application_seconds": stage_total["application_seconds"],
            "application_compute_seconds": stage_total["application_compute_seconds"],
            "output_stage_seconds": stage_total["output_stage_seconds"],
            "output_stage_compute_seconds": stage_total["output_stage_compute_seconds"],
            "output_compression_seconds": stage_total["output_compression_seconds"],
            "output_compression_compute_seconds": stage_total["output_compression_compute_seconds"],
            "output_hash_seconds": stage_total["output_hash_seconds"],
            "output_hash_compute_seconds": stage_total["output_hash_compute_seconds"],
            "output_crypto_seconds": stage_total["output_crypto_seconds"],
            "output_crypto_compute_seconds": stage_total["output_crypto_compute_seconds"],
            "total_seconds": stage_total["total_seconds"],
            "total_compute_seconds": stage_total["total_compute_seconds"],
        }
        for index in range(max_input):
            label = stage_total["input_requirement_labels"][index] if index < len(stage_total["input_requirement_labels"]) else ""
            value = stage_total["input_requirement_seconds"][index] if index < len(stage_total["input_requirement_seconds"]) else 0.0
            compute_value = stage_total["input_requirement_compute_seconds"][index] if index < len(stage_total["input_requirement_compute_seconds"]) else 0.0
            row[f"input_requirement_{index + 1}"] = label
            row[f"input_requirement_{index + 1}_seconds"] = value
            row[f"input_requirement_{index + 1}_compute_seconds"] = compute_value
        for index in range(max_output):
            label = stage_total["output_requirement_labels"][index] if index < len(stage_total["output_requirement_labels"]) else ""
            value = stage_total["output_requirement_seconds"][index] if index < len(stage_total["output_requirement_seconds"]) else 0.0
            compute_value = stage_total["output_requirement_compute_seconds"][index] if index < len(stage_total["output_requirement_compute_seconds"]) else 0.0
            row[f"output_requirement_{index + 1}"] = label
            row[f"output_requirement_{index + 1}_seconds"] = value
            row[f"output_requirement_{index + 1}_compute_seconds"] = compute_value
        rows.append(row)
    return rows, max_input, max_output


def summarize_queue_metrics(timeline_rows):
    by_stage = {}
    for row in timeline_rows:
        by_stage.setdefault(row["stage"], []).append(row)

    summaries = []
    for stage_name, rows in by_stage.items():
        arrival_times = sorted(row["arrival_time_seconds"] for row in rows)
        interarrivals = [
            arrival_times[index] - arrival_times[index - 1]
            for index in range(1, len(arrival_times))
        ]
        waiting_times = [row["waiting_time_seconds"] for row in rows]
        service_times = [row["service_time_seconds"] for row in rows]
        response_times = [row["response_time_seconds"] for row in rows]
        summaries.append(
            {
                "stage": stage_name,
                "objects": len(rows),
                "first_arrival_seconds": arrival_times[0] if arrival_times else 0.0,
                "last_arrival_seconds": arrival_times[-1] if arrival_times else 0.0,
                "mean_interarrival_seconds": (
                    sum(interarrivals) / len(interarrivals) if interarrivals else 0.0
                ),
                "mean_waiting_seconds": sum(waiting_times) / len(waiting_times) if waiting_times else 0.0,
                "mean_service_seconds": sum(service_times) / len(service_times) if service_times else 0.0,
                "mean_response_seconds": sum(response_times) / len(response_times) if response_times else 0.0,
            }
        )
    return summaries


def run_pipeline(config, base_dir: Path, workers_override=None, run_only=None):
    runtime_config = prepare_runtime_config(config)
    runtime_config["run_only"] = run_only
    input_dir = (base_dir / config.get("input_dir", "generated_input")).resolve()
    work_dir = (base_dir / config.get("work_dir", "work")).resolve()
    output_dir = (base_dir / config.get("output_dir", "output")).resolve()
    results_dir = (base_dir / config.get("results_dir", "results")).resolve()
    workers = int(workers_override or config.get("workers") or (os.cpu_count() or 1))
    workers = max(1, workers)

    if work_dir.exists():
        shutil.rmtree(work_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    ensure_dir(results_dir)

    generate_input_files(config, input_dir)
    input_files = sorted(path for path in input_dir.iterdir() if path.is_file())
    stages = derive_stage_inputs(config.get("stages", []))

    file_rows = []
    timeline_rows = []
    stage_totals_map = {}
    experiment_start = time.perf_counter()
    objects = [
        {
            "file_name": file_path.name,
            "data": read_bytes(file_path),
            "metadata": {"source_file": file_path.name},
            "arrival_time": 0.0,
        }
        for file_path in input_files
    ]

    with tempfile.TemporaryDirectory(prefix="real_pipeline_") as temp_root:
        temp_root_path = Path(temp_root)
        for stage_index, stage in enumerate(stages):
            stage_name = stage["name"]
            stage_totals_map[stage_name] = init_stage_total(stage_index, stage_name, workers, len(objects))
            stage_totals_map[stage_name]["input_requirement_labels"] = [
                requirement_label(requirement, "input")
                for requirement in stage["effective_input_requirements"]
            ]
            stage_totals_map[stage_name]["output_requirement_labels"] = [
                requirement_label(requirement, "output")
                for requirement in stage["output_requirements"]
            ]

            with ProcessPoolExecutor(max_workers=workers) as executor:
                func = partial(
                    process_stage_object,
                    stage=stage,
                    runtime_config=runtime_config,
                    temp_root_path=temp_root_path,
                    work_dir=work_dir,
                    experiment_start=experiment_start,
                )
                results = list(executor.map(func, objects))

            next_objects = []
            for result in results:
                next_objects.append(
                    {
                        "file_name": result["file_name"],
                        "data": result["data"],
                        "metadata": result["metadata"],
                        "arrival_time": result["available_time"],
                    }
                )
                update_stage_total_from_result(stage_totals_map[stage_name], stage, result)
                file_rows.append(result["file_row"])
                timeline_rows.append(result["timeline_row"])
            objects = next_objects

        # Avoid writing final simulated objects to disk during large scale benchmarks
        # for obj in objects:
        #     write_bytes(output_dir / obj["file_name"], obj["data"])

    stage_totals = list(stage_totals_map.values())
    queue_summaries = summarize_queue_metrics(timeline_rows)
    write_stage_summary(results_dir / "stage_summary.csv", stage_totals)
    write_stage_totals_by_workers(results_dir / "stage_totals_by_workers.csv", stage_totals)
    write_file_metrics(results_dir / "file_stage_metrics.csv", file_rows)
    write_stage_timeline(results_dir / "stage_timeline.csv", timeline_rows)
    write_stage_queue_summary(results_dir / "stage_queue_summary.csv", queue_summaries)
    write_overall_summary(results_dir / "overall_summary.json", stage_totals, file_rows)


def write_stage_summary(path: Path, stage_totals):
    fieldnames = [
        "stage",
        "files",
        "input_seconds",
        "application_seconds",
        "output_seconds",
        "total_seconds",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for totals in stage_totals:
            writer.writerow(
                {
                    "stage": totals["stage_name"],
                    "files": totals["objects"],
                    "input_seconds": totals["input_stage_seconds"],
                    "application_seconds": totals["application_seconds"],
                    "output_seconds": totals["output_stage_seconds"],
                    "total_seconds": totals["total_seconds"],
                }
            )


def write_stage_totals_by_workers(path: Path, stage_totals):
    rows, max_input, max_output = build_stage_totals_rows(stage_totals)
    fieldnames = [
        "stage",
        "stage_name",
        "workers",
        "objects",
        "input_stage_seconds",
        "input_stage_compute_seconds",
        "input_compression_seconds",
        "input_compression_compute_seconds",
        "input_hash_seconds",
        "input_hash_compute_seconds",
        "input_crypto_seconds",
        "input_crypto_compute_seconds",
        "application_seconds",
        "application_compute_seconds",
        "output_stage_seconds",
        "output_stage_compute_seconds",
        "output_compression_seconds",
        "output_compression_compute_seconds",
        "output_hash_seconds",
        "output_hash_compute_seconds",
        "output_crypto_seconds",
        "output_crypto_compute_seconds",
        "total_seconds",
        "total_compute_seconds",
    ]
    for index in range(max_input):
        fieldnames.extend(
            [
                f"input_requirement_{index + 1}",
                f"input_requirement_{index + 1}_seconds",
                f"input_requirement_{index + 1}_compute_seconds",
            ]
        )
    for index in range(max_output):
        fieldnames.extend(
            [
                f"output_requirement_{index + 1}",
                f"output_requirement_{index + 1}_seconds",
                f"output_requirement_{index + 1}_compute_seconds",
            ]
        )

    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_file_metrics(path: Path, rows):
    fieldnames = [
        "file",
        "stage",
        "input_seconds",
        "input_compute_seconds",
        "application_read_seconds",
        "application_compute_seconds",
        "application_write_seconds",
        "application_total_seconds",
        "output_seconds",
        "output_compute_seconds",
        "input_size_bytes",
        "output_size_bytes",
        "application_size_factor",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_stage_timeline(path: Path, rows):
    fieldnames = [
        "file",
        "stage",
        "arrival_time_seconds",
        "service_start_time_seconds",
        "completion_time_seconds",
        "waiting_time_seconds",
        "service_time_seconds",
        "response_time_seconds",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_stage_queue_summary(path: Path, rows):
    fieldnames = [
        "stage",
        "objects",
        "first_arrival_seconds",
        "last_arrival_seconds",
        "mean_interarrival_seconds",
        "mean_waiting_seconds",
        "mean_service_seconds",
        "mean_response_seconds",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_overall_summary(path: Path, stage_totals, file_rows):
    summary = {
        "stage_count": len(stage_totals),
        "file_count": len({row["file"] for row in file_rows}),
        "pipeline_total_seconds": sum(totals["total_seconds"] for totals in stage_totals),
        "stages": stage_totals,
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2)
        fp.write("\n")


def main():
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    run_only = None
    if args.input_only:
        run_only = "input"
    elif args.output_only:
        run_only = "output"
    elif args.run_only:
        run_only = args.run_only

    run_pipeline(
        config,
        config_path.parent,
        workers_override=args.workers,
        run_only=run_only,
    )


if __name__ == "__main__":
    main()
