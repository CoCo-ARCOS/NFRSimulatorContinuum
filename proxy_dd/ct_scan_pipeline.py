#!/usr/bin/env python3
"""Create and optionally run the CT scan continuum case-study pipeline."""
import argparse
from pathlib import Path

import benchmark_workers as bw


COMPRESSION_ALGOS = {
    "none": None,
    "lz4": "LZ4",
    "zlib": "ZLIB",
    "zstd": "ZSTD",
    "bz2": "BZ2",
}

HASH_ALGO = "SHA256"
EDGE_CONFIDENTIALITY_ALGO = "CHACHA20"
FOG_CLOUD_CONFIDENTIALITY_ALGO = "AES"
RELIABILITY_ALGO = "RS"

REFERENCE_TIMINGS = {
    "edge_acquisition": {
        "seconds_per_study": 300.0,
        "source": "Cleveland Clinic CT scan page: scan itself usually takes fewer than five minutes.",
        "url": "https://my.clevelandclinic.org/health/diagnostics/4808-ct-computed-tomography-scan",
    },
    "fog_preprocessing": {
        "seconds_per_study": 20.0,
        "source": "Kitware medical image analysis architecture note: CT preprocessing often takes 10-30 seconds per scan.",
        "url": "https://www.kitware.com/system-architecture-for-cloud-based-medical-image-analysis/",
    },
    "cloud_inference": {
        "seconds_per_study": 44.9,
        "source": "FLARE 2024/OpenReview nnU-Net pan-cancer segmentation entry: faster method mean inference time was 44.9 seconds.",
        "url": "https://openreview.net/forum?id=9tui0BoCj7",
    },
}

APPLICATION_SIZE_FACTORS = {
    "edge_acquisition": 1.0,
    "fog_preprocessing": 0.85,
    "cloud_inference": 0.25,
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build and run the CT scan edge/fog/cloud pipeline using the proxy_dd simulator."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path.home() / "Downloads" / "medicalimages" / "dicoms" / "Px9",
        help="Reference DICOM dataset directory used to derive CT scan workload size.",
    )
    parser.add_argument(
        "--studies",
        type=int,
        default=1,
        help="Number of studies to simulate, using the passed dataset as a single-study sample.",
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        choices=list(COMPRESSION_ALGOS.keys()),
        default="lz4",
        help="Compression algorithm used where the figure shows data compression. The case-study default is LZ4.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of workers per stage.",
    )
    parser.add_argument(
        "--simulator",
        type=Path,
        default=Path("./main"),
        help="Simulator executable path relative to the proxy_dd directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ct_scan_pipeline_results"),
        help="Directory where config and run results are saved.",
    )
    parser.add_argument(
        "--write-config-only",
        action="store_true",
        help="Write the CT workflow config JSON and skip running the simulator.",
    )
    parser.add_argument(
        "--inter-arrival",
        type=float,
        default=0.10,
        help="Mean object inter-arrival time in seconds.",
    )
    parser.add_argument(
        "--bandwidth",
        type=float,
        default=100.0,
        help="Edge-to-fog network bandwidth in MB/s.",
    )
    parser.add_argument(
        "--fog-cloud-bandwidth",
        type=float,
        default=50.0,
        help="Fog-to-cloud network bandwidth in MB/s.",
    )
    parser.add_argument(
        "--edge-fog-latency-ms",
        type=float,
        default=10.0,
        help="Edge-to-fog link latency in milliseconds.",
    )
    parser.add_argument(
        "--fog-cloud-latency-ms",
        type=float,
        default=50.0,
        help="Fog-to-cloud link latency in milliseconds.",
    )
    parser.add_argument(
        "--storage-bandwidth",
        type=float,
        default=150.0,
        help="Filesystem bandwidth in MB/s for stage storage operations.",
    )
    parser.add_argument(
        "--hardware-profile",
        type=str,
        default="dantelap",
        help="Hardware profile used by the edge, fog, and cloud machine nodes.",
    )
    parser.add_argument(
        "--real-values-dir",
        type=Path,
        default=None,
        help=(
            "Directory with measured service-time CSVs. Defaults to "
            "results_different_machines/organized/<hardware-profile>/real_values when it exists."
        ),
    )
    parser.add_argument(
        "--edge-acquisition-seconds-per-study",
        type=float,
        default=REFERENCE_TIMINGS["edge_acquisition"]["seconds_per_study"],
        help="Application time for the CT image acquisition stage, before per-object scaling.",
    )
    parser.add_argument(
        "--preprocessing-seconds-per-study",
        type=float,
        default=REFERENCE_TIMINGS["fog_preprocessing"]["seconds_per_study"],
        help="Application time for artifact removal, DICOM-to-NIFTI conversion, and anonymization, before per-object scaling.",
    )
    parser.add_argument(
        "--inference-seconds-per-study",
        type=float,
        default=REFERENCE_TIMINGS["cloud_inference"]["seconds_per_study"],
        help="Application time for tumor segmentation inference, before per-object scaling.",
    )
    return parser.parse_args()


def inspect_dataset(dataset_dir: Path):
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset path not found: {dataset_dir}")

    files = [path for path in dataset_dir.rglob("*") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    object_count = len(files)
    if object_count == 0:
        raise ValueError(f"Dataset path contains no files: {dataset_dir}")
    average_size_bytes = total_bytes / object_count
    return object_count, total_bytes, average_size_bytes


def requirement(task_type, algorithm, label):
    return {"type": task_type, "algorithm": algorithm, "label": label}


def compression_requirements(algorithm):
    if algorithm is None:
        return []
    return [requirement("compress", algorithm, f"Data compression ({algorithm})")]


def mirrored_input_requirements(output_requirements):
    return [dict(item) for item in reversed(output_requirements)]


def stage_seconds_per_object(seconds_per_study, object_count):
    return float(seconds_per_study) / max(1, int(object_count))


def build_stage(
    stage_id,
    name,
    storage_bandwidth,
    inter_arrival,
    application_seconds_per_object,
    application_size_factor,
    input_requirements,
    output_requirements,
):
    return {
        "id": stage_id,
        "name": name,
        "b_fs": storage_bandwidth,
        "b_fs_read": storage_bandwidth,
        "b_fs_write": storage_bandwidth,
        "inter_arrival": inter_arrival,
        "application_mean_service_time": application_seconds_per_object,
        "application_size_factor": application_size_factor,
        "input_requirements": input_requirements,
        "output_requirements": output_requirements,
    }


def build_ct_scan_config(
    object_count: int,
    average_size_bytes: float,
    studies: int,
    compression_algo: str,
    workers: int,
    storage_bandwidth: float,
    edge_fog_bandwidth: float,
    fog_cloud_bandwidth: float,
    edge_fog_latency_ms: float,
    fog_cloud_latency_ms: float,
    inter_arrival: float,
    hardware_profile: str,
    real_values_dir: Path,
    edge_acquisition_seconds_per_study: float,
    preprocessing_seconds_per_study: float,
    inference_seconds_per_study: float,
):
    payload_size = int(average_size_bytes)
    total_objects = object_count * max(1, studies)
    compress_algo = COMPRESSION_ALGOS[compression_algo]

    stage_study_seconds = {
        "edge_acquisition": edge_acquisition_seconds_per_study,
        "fog_preprocessing": preprocessing_seconds_per_study,
        "cloud_inference": inference_seconds_per_study,
    }
    stage_object_seconds = {
        name: stage_seconds_per_object(seconds, object_count)
        for name, seconds in stage_study_seconds.items()
    }

    trace = [
        {
            "MUESTRAS": total_objects,
            "inter_arrival": inter_arrival,
            "DISTRIBUTION": 3,
            "mean": 15.0,
            "stddev": 0.6,
            "SIZE": float(payload_size),
            "stddevS": 0.1,
            "Concurrency": 1,
        }
    ]

    edge_output = [
        requirement("hash", HASH_ALGO, "Integrity (SHA-256)"),
        *compression_requirements(compress_algo),
        requirement("cipher", EDGE_CONFIDENTIALITY_ALGO, "Data confidentiality (ChaCha20)"),
        requirement("cipher", RELIABILITY_ALGO, "Reliability (IDA)"),
    ]
    fog_output = [
        *compression_requirements(compress_algo),
        requirement("cipher", FOG_CLOUD_CONFIDENTIALITY_ALGO, "Data confidentiality (AES)"),
        requirement("cipher", RELIABILITY_ALGO, "Reliability (IDA)"),
    ]
    cloud_output = [
        *compression_requirements(compress_algo),
        requirement("cipher", FOG_CLOUD_CONFIDENTIALITY_ALGO, "Data confidentiality (AES)"),
        requirement("cipher", RELIABILITY_ALGO, "Reliability (IDA)"),
    ]

    stages = [
        build_stage(
            1,
            "edge_acquisition",
            storage_bandwidth,
            inter_arrival,
            stage_object_seconds["edge_acquisition"],
            APPLICATION_SIZE_FACTORS["edge_acquisition"],
            [],
            edge_output,
        ),
        build_stage(
            2,
            "fog_preprocessing",
            storage_bandwidth,
            inter_arrival,
            stage_object_seconds["fog_preprocessing"],
            APPLICATION_SIZE_FACTORS["fog_preprocessing"],
            mirrored_input_requirements(edge_output),
            fog_output,
        ),
        build_stage(
            3,
            "cloud_inference",
            storage_bandwidth,
            inter_arrival,
            stage_object_seconds["cloud_inference"],
            APPLICATION_SIZE_FACTORS["cloud_inference"],
            mirrored_input_requirements(fog_output),
            cloud_output,
        ),
    ]

    def machine_node(name, stage_name):
        node = {"name": name, "stages": [stage_name], "hardware_profile": hardware_profile}
        if real_values_dir is not None:
            node["real_values_dir"] = str(real_values_dir)
        return node

    return {
        "workers": workers,
        "traces_number": 1,
        "traces": trace,
        "agent_type": "output",
        "stages": stages,
        "compression_algo": compress_algo or "",
        "hashing_algo": HASH_ALGO,
        "ida_algo": RELIABILITY_ALGO,
        "ida_k": 8,
        "ida_m": 4,
        "aes_key_bits": 256,
        "b_fs": storage_bandwidth,
        "b_fs_read": storage_bandwidth,
        "b_fs_write": storage_bandwidth,
        "machines": [
            machine_node("edge", "stage1"),
            machine_node("fog", "stage2"),
            machine_node("cloud", "stage3"),
        ],
        "links": [
            {
                "from": "edge",
                "to": "fog",
                "b_net": edge_fog_bandwidth,
                "latency_ms": edge_fog_latency_ms,
            },
            {
                "from": "fog",
                "to": "cloud",
                "b_net": fog_cloud_bandwidth,
                "latency_ms": fog_cloud_latency_ms,
            },
        ],
        "metadata": {
            "case_study_figure": "proxy_dd/casestudy.pdf",
            "notes": [
                "The PDF has edge acquisition, fog preprocessing, and cloud inference application stages.",
                "Input NFR agents are modeled as the reverse of the previous stage output NFR pipeline, matching the simulator's decode/verify behavior.",
                "LEA is shown as an edge confidentiality option in the figure, but this simulator profile has measured CHACHA20 and AES values; CHACHA20 is used.",
                "Application timings are stored per study here and divided by the dataset file count for per-object simulator service times.",
            ],
            "reference_timings": REFERENCE_TIMINGS,
            "stage_seconds_per_study": stage_study_seconds,
            "stage_seconds_per_object": stage_object_seconds,
            "real_values_dir": str(real_values_dir) if real_values_dir is not None else "",
            "nfr_mapping": {
                "Integrity (SHA-256)": {"type": "hash", "algorithm": HASH_ALGO},
                "Data compression (LZ4)": {"type": "compress", "algorithm": compress_algo or "none"},
                "Data confidentiality (ChaCha or LEA)": {
                    "type": "cipher",
                    "algorithm": EDGE_CONFIDENTIALITY_ALGO,
                },
                "Data confidentiality (AES)": {
                    "type": "cipher",
                    "algorithm": FOG_CLOUD_CONFIDENTIALITY_ALGO,
                },
                "Reliability (IDA)": {"type": "cipher", "algorithm": RELIABILITY_ALGO},
            },
        },
}


def resolve_real_values_dir(path_arg: Path, hardware_profile: str):
    proxy_dir = Path(__file__).resolve().parent
    if path_arg is not None:
        path = path_arg if path_arg.is_absolute() else proxy_dir / path_arg
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Real-values directory not found: {path}")
        return path

    profile_dir = (
        proxy_dir
        / "results_different_machines"
        / "organized"
        / hardware_profile
        / "real_values"
    )
    return profile_dir.resolve() if profile_dir.exists() else None


def main():
    args = parse_args()
    object_count, total_bytes, average_size_bytes = inspect_dataset(args.dataset)
    real_values_dir = resolve_real_values_dir(args.real_values_dir, args.hardware_profile)
    print(f"Dataset: {args.dataset}")
    print(f"  files: {object_count}")
    print(f"  total size: {total_bytes / 1_000_000:.3f} MB")
    print(f"  average size: {average_size_bytes / 1_000_000:.3f} MB")
    if real_values_dir is not None:
        print(f"  real values: {real_values_dir}")

    config = build_ct_scan_config(
        object_count=object_count,
        average_size_bytes=average_size_bytes,
        studies=args.studies,
        compression_algo=args.algorithm,
        workers=args.workers,
        storage_bandwidth=args.storage_bandwidth,
        edge_fog_bandwidth=args.bandwidth,
        fog_cloud_bandwidth=args.fog_cloud_bandwidth,
        edge_fog_latency_ms=args.edge_fog_latency_ms,
        fog_cloud_latency_ms=args.fog_cloud_latency_ms,
        inter_arrival=args.inter_arrival,
        hardware_profile=args.hardware_profile,
        real_values_dir=real_values_dir,
        edge_acquisition_seconds_per_study=args.edge_acquisition_seconds_per_study,
        preprocessing_seconds_per_study=args.preprocessing_seconds_per_study,
        inference_seconds_per_study=args.inference_seconds_per_study,
    )
    print(
        f"Simulating {args.studies} study(ies) with {object_count} files each "
        f"=> total objects={object_count * max(1, args.studies)}"
    )
    print("Application service times per object:")
    for stage_name, seconds in config["metadata"]["stage_seconds_per_object"].items():
        print(f"  {stage_name}: {seconds:.6f} s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    config_path = args.output_dir / "ct_scan_pipeline_config.json"
    bw.write_config(config, config_path)
    print(f"Wrote config to: {config_path}")

    if args.write_config_only:
        return

    simulator = args.simulator
    if not simulator.is_absolute():
        simulator = Path(__file__).resolve().parent / simulator

    if not simulator.exists():
        raise FileNotFoundError(f"Simulator executable not found: {simulator}")

    results_dir = args.output_dir / "results"
    bw.clear_results_dir(results_dir)
    stdout, stderr = bw.run_simulation(simulator, config_path, args.output_dir)
    with (args.output_dir / "simulator_stdout.txt").open("w", encoding="utf-8") as fp:
        fp.write(stdout)
    with (args.output_dir / "simulator_stderr.txt").open("w", encoding="utf-8") as fp:
        fp.write(stderr)
    print(f"Simulation complete; results written to: {results_dir}")


if __name__ == "__main__":
    main()
