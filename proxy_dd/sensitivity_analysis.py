#!/usr/bin/env python3
import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

import benchmark_workers as bw


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run one-factor-at-a-time sensitivity benchmarks for the simulator."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config_distributed_example.json"),
        help="Baseline simulator JSON config file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sensitivity_results"),
        help="Directory to save benchmark runs and output charts.",
    )
    parser.add_argument(
        "--simulator",
        type=Path,
        default=Path("./main"),
        help="Simulator executable relative to the proxy_dd folder.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of repeated runs per sensitivity value.",
    )
    parser.add_argument(
        "--factors",
        nargs="+",
        choices=[
            "payload_size",
            "devices",
            "workers",
            "network_bandwidth",
            "storage_bandwidth",
            "hardware_profile",
            "nfr_pipeline",
        ],
        default=[
            "payload_size",
            "devices",
            "workers",
            "network_bandwidth",
            "storage_bandwidth",
            "hardware_profile",
            "nfr_pipeline",
        ],
        help="Sensitivity factors to benchmark.",
    )
    parser.add_argument(
        "--machine-datasets-root",
        type=Path,
        default=Path("results_different_machines/organized"),
        help="Root directory containing organized machine datasets used by hardware-profile experiments.",
    )
    return parser.parse_args()


def read_traces_cfg(traces_path: Path):
    rows = []
    with traces_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split()
            if len(parts) < 8:
                continue
            rows.append(
                {
                    "MUESTRAS": int(parts[0]),
                    "inter_arrival": float(parts[1]),
                    "DISTRIBUTION": int(parts[2]),
                    "mean": float(parts[3]),
                    "stddev": float(parts[4]),
                    "SIZE": float(parts[5]),
                    "stddevS": float(parts[6]),
                    "Concurrency": int(parts[7]),
                }
            )
    return rows


def write_traces_cfg(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(
                "{} {:.6f} {} {:.6f} {:.6f} {:.6f} {:.6f} {}\n".format(
                    row["MUESTRAS"],
                    row["inter_arrival"],
                    row["DISTRIBUTION"],
                    row["mean"],
                    row["stddev"],
                    row["SIZE"],
                    row["stddevS"],
                    row["Concurrency"],
                )
            )


def get_stage_objects(rows):
    return sum(int(row.get("MUESTRAS", 0)) for row in rows)


def as_label(value):
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def scale_links(config: dict, bandwidth_mb_s: float):
    if "links" not in config or not isinstance(config["links"], list):
        return
    for link in config["links"]:
        if isinstance(link, dict):
            link["b_net"] = bandwidth_mb_s


def scale_stage_filesystem(config: dict, bandwidth_mb_s: float):
    config["b_fs"] = bandwidth_mb_s
    config["b_fs_read"] = bandwidth_mb_s
    config["b_fs_write"] = bandwidth_mb_s
    for stage in config.get("stages", []):
        if isinstance(stage, dict):
            stage["b_fs"] = bandwidth_mb_s
            stage["b_fs_read"] = bandwidth_mb_s
            stage["b_fs_write"] = bandwidth_mb_s


def scale_application_time(config: dict, multiplier: float):
    if "stages" in config and isinstance(config["stages"], list):
        for stage in config["stages"]:
            if isinstance(stage, dict) and "application_mean_service_time" in stage:
                stage["application_mean_service_time"] = float(stage["application_mean_service_time"]) * multiplier


def set_hardware_profile(config: dict, profile: str, real_values_path=None):
    if real_values_path is not None:
        config["real_values_dir"] = str(real_values_path)
    machines = config.get("machines", [])
    if not isinstance(machines, list):
        return

    for machine in machines:
        if not isinstance(machine, dict):
            continue
        machine["hardware_profile"] = profile
        machine.pop("real_values_dir", None)


def update_nfr_pipeline(config: dict, mode: str):
    if "stages" not in config or not isinstance(config["stages"], list):
        return

    for stage in config["stages"]:
        if not isinstance(stage, dict):
            continue

        output_reqs = stage.get("output_requirements", [])
        if not isinstance(output_reqs, list):
            output_reqs = []

        if mode == "minimal":
            stage["output_requirements"] = [req for req in output_reqs if req.get("type") == "hash"] or []
        elif mode == "baseline":
            # leave baseline unchanged
            continue
        elif mode == "heavy":
            extra = [
                {"type": "compress", "algorithm": "ZSTD"},
                {"type": "cipher", "algorithm": "RS"},
                {"type": "hash", "algorithm": "SHA3_256"},
            ]
            # preserve existing stage requirements and add a strong pipeline set
            stage["output_requirements"] = []
            stage["output_requirements"].extend(extra)
        elif mode == "maximal":
            stage["output_requirements"] = [
                {"type": "compress", "algorithm": "ZSTD"},
                {"type": "hash", "algorithm": "SHA3_256"},
                {"type": "cipher", "algorithm": "RS"},
                {"type": "hash", "algorithm": "BLAKE3"},
            ]
        else:
            raise ValueError(f"Unsupported NFR mode: {mode}")


def summarize_stage_totals(rows):
    summary = {
        "pipeline_total_seconds": 0.0,
        "pipeline_max_stage_seconds": 0.0,
        "pipeline_input_seconds": 0.0,
        "pipeline_application_seconds": 0.0,
        "pipeline_output_seconds": 0.0,
        "pipeline_compression_seconds": 0.0,
        "pipeline_hash_seconds": 0.0,
        "pipeline_crypto_seconds": 0.0,
        "stage_count": len(rows),
        "objects": 0,
    }
    for row in rows:
        total = float(row.get("total_seconds", 0.0))
        summary["pipeline_total_seconds"] += total
        summary["pipeline_max_stage_seconds"] = max(summary["pipeline_max_stage_seconds"], total)
        summary["pipeline_input_seconds"] += float(row.get("input_seconds", 0.0))
        summary["pipeline_application_seconds"] += float(row.get("application_seconds", 0.0))
        summary["pipeline_output_seconds"] += float(row.get("output_seconds", 0.0))
        summary["pipeline_compression_seconds"] += float(row.get("compression_seconds", 0.0))
        summary["pipeline_hash_seconds"] += float(row.get("hash_seconds", 0.0))
        summary["pipeline_crypto_seconds"] += float(row.get("crypto_seconds", 0.0))
        summary["objects"] = int(row.get("objects", summary["objects"]))
    return summary


def load_config_file(config_path: Path):
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def build_factor_scenarios(config: dict):
    return {
        "payload_size": [
            {"label": "1MB", "payload_size": 1_000_000},
            {"label": "10MB", "payload_size": 10_000_000},
            {"label": "100MB", "payload_size": 100_000_000},
            {"label": "1000MB", "payload_size": 1_000_000_000}
        ],
        "devices": [
            {"label": "20-devices", "MUESTRAS": 20},
            {"label": "50-devices", "MUESTRAS": 50},
            {"label": "100-devices", "MUESTRAS": 100},
            {"label": "200-devices", "MUESTRAS": 200},
            {"label": "400-devices", "MUESTRAS": 400},
        ],
        "workers": [
            {"label": "1-worker", "workers": 1},
            {"label": "2-workers", "workers": 2},
            {"label": "4-workers", "workers": 4},
            {"label": "8-workers", "workers": 8},
            {"label": "16-workers", "workers": 16},
        ],
        "network_bandwidth": [
            {"label": "10MBps", "bandwidth": 10},
            {"label": "25MBps", "bandwidth": 25},
            {"label": "50MBps", "bandwidth": 50},
            {"label": "100MBps", "bandwidth": 100},
            {"label": "200MBps", "bandwidth": 200},
        ],
        "storage_bandwidth": [
            {"label": "10MBps", "bandwidth": 10},
            {"label": "50MBps", "bandwidth": 50},
            {"label": "100MBps", "bandwidth": 100},
            {"label": "250MBps", "bandwidth": 250},
            {"label": "500MBps", "bandwidth": 500},
        ],
        "hardware_profile": [
            {"label": "c3", "profile": "c3"},
            {"label": "dianalap", "profile": "dianalap"},
            {"label": "toge", "profile": "toge"},
        ],
        "nfr_pipeline": [
            {"label": "minimal", "mode": "minimal"},
            {"label": "baseline", "mode": "baseline"},
            {"label": "heavy", "mode": "heavy"},
            {"label": "maximal", "mode": "maximal"},
        ],
    }


def run_sensitivity_benchmarks(args):
    baseline_config_path = args.config.resolve()
    baseline_dir = baseline_config_path.parent
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    simulator_path = (baseline_dir / args.simulator).resolve() if not args.simulator.is_absolute() else args.simulator.resolve()

    baseline_config = load_config_file(baseline_config_path)
    machine_datasets_root = (baseline_dir / args.machine_datasets_root).resolve() if not args.machine_datasets_root.is_absolute() else args.machine_datasets_root.resolve()
    if machine_datasets_root.exists():
        try:
            relative_root = machine_datasets_root.relative_to(baseline_dir)
        except ValueError:
            relative_root = machine_datasets_root
    else:
        relative_root = args.machine_datasets_root
    # Prefer structured 'traces' provided in the JSON; otherwise load legacy traces.cfg
    if "traces" in baseline_config and isinstance(baseline_config["traces"], list):
        trace_rows = baseline_config["traces"]
    else:
        traces_file_name = baseline_config.get("traces_fileName", "traces.cfg")
        baseline_traces_path = (baseline_dir / traces_file_name).resolve()
        if not baseline_traces_path.exists():
            raise FileNotFoundError(f"Baseline traces config not found: {baseline_traces_path}")
        trace_rows = read_traces_cfg(baseline_traces_path)
        if not trace_rows:
            raise ValueError("Baseline traces.cfg appears empty or malformed")

    scenarios = build_factor_scenarios(baseline_config)
    all_summary = []
    worker_stage_runs = defaultdict(list)

    for factor in args.factors:
        if factor not in scenarios:
            continue
        group = scenarios[factor]
        for item in group:
            value_label = item["label"]
            config_copy = json.loads(json.dumps(baseline_config))
            config_copy["traces_fileName"] = str(Path(traces_file_name).name)
            runs = []

            for repeat in range(1, args.repeats + 1):
                run_dir = output_dir / factor / value_label / f"run_{repeat}"
                run_dir.mkdir(parents=True, exist_ok=True)

                # Update traces inline in the JSON config (no traces.cfg files)
                if factor == "payload_size":
                    modified_rows = [dict(r) for r in trace_rows]
                    modified_rows[0]["SIZE"] = float(item["payload_size"])
                    config_copy["traces"] = modified_rows
                    current_trace_rows = modified_rows
                elif factor == "devices":
                    modified_rows = [dict(r) for r in trace_rows]
                    modified_rows[0]["MUESTRAS"] = int(item["MUESTRAS"])
                    config_copy["traces"] = modified_rows
                    current_trace_rows = modified_rows
                else:
                    # keep baseline traces in the config
                    config_copy["traces"] = trace_rows
                    current_trace_rows = trace_rows

                if factor == "workers":
                    config_copy["workers"] = int(item["workers"])
                elif factor == "network_bandwidth":
                    scale_links(config_copy, float(item["bandwidth"]))
                elif factor == "storage_bandwidth":
                    scale_stage_filesystem(config_copy, float(item["bandwidth"]))
                elif factor == "hardware_profile":
                    profile_root = relative_root if isinstance(relative_root, Path) else Path(relative_root)
                    profile_values_dir = profile_root / item["profile"] / "real_values"
                    set_hardware_profile(config_copy, item["profile"], real_values_path=profile_values_dir)
                elif factor == "nfr_pipeline":
                    update_nfr_pipeline(config_copy, item["mode"])

                # remove legacy traces_fileName to avoid file-based behavior
                if "traces_fileName" in config_copy:
                    config_copy.pop("traces_fileName", None)
                config_path = run_dir / "config.json"
                bw.write_config(config_copy, config_path)
                bw.clear_results_dir(run_dir / "results")

                print(f"Running sensitivity factor={factor} value={value_label} repeat={repeat}/{args.repeats}")
                stdout, stderr = bw.run_simulation(simulator_path, config_path, run_dir)
                with (run_dir / "simulator_stdout.txt").open("w", encoding="utf-8") as fp:
                    fp.write(stdout)
                with (run_dir / "simulator_stderr.txt").open("w", encoding="utf-8") as fp:
                    fp.write(stderr)

                stage_rows = bw.read_stage_totals(run_dir / "results")
                metrics = summarize_stage_totals(stage_rows)
                metrics["trace_objects"] = get_stage_objects(current_trace_rows)
                metrics["workers"] = config_copy.get("workers", baseline_config.get("workers", 0))
                runs.append(metrics)
                if factor == "workers":
                    worker_stage_runs[int(config_copy.get("workers", baseline_config.get("workers", 0)))].append(
                        {"stage_totals": stage_rows}
                    )

            if not runs:
                continue

            averaged = {k: sum(run[k] for run in runs) / len(runs) for k in runs[0] if isinstance(runs[0][k], (int, float))}
            averaged["factor"] = factor
            averaged["value_label"] = value_label
            averaged["value"] = item.get("payload_size") or item.get("MUESTRAS") or item.get("workers") or item.get("bandwidth") or item.get("profile") or item.get("multiplier") or item.get("mode")
            averaged["workers"] = runs[0]["workers"]
            averaged["repeat_count"] = len(runs)
            all_summary.append(averaged)

    summary_csv = output_dir / "sensitivity_summary.csv"
    fieldnames = [
        "factor",
        "value_label",
        "value",
        "workers",
        "trace_objects",
        "repeat_count",
        "pipeline_total_seconds",
        "pipeline_max_stage_seconds",
        "pipeline_input_seconds",
        "pipeline_application_seconds",
        "pipeline_output_seconds",
        "pipeline_compression_seconds",
        "pipeline_hash_seconds",
        "pipeline_crypto_seconds",
        "stage_count",
        "objects",
    ]
    with summary_csv.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_summary:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    plot_sensitivity_results(output_dir, all_summary)

    if worker_stage_runs:
        worker_plots_dir = output_dir / "workers_detailed_plots"
        worker_plots_dir.mkdir(parents=True, exist_ok=True)
        workers_summary, stage_names = bw.aggregate_runs(worker_stage_runs)
        bw.write_summary_csv(output_dir / "workers_detailed_summary.csv", workers_summary, stage_names)
        bw.plot_worker_scaling(
            worker_plots_dir,
            workers_summary,
            stage_names,
            with_stage_breakdown=True,
            with_timelines=True,
        )

    print(f"Sensitivity analysis complete. Summary: {summary_csv}")


def plot_sensitivity_results(output_dir: Path, all_summary):
    grouped = defaultdict(list)
    for row in all_summary:
        grouped[row["factor"]].append(row)

    for factor, rows in grouped.items():
        numeric_values = [row["value"] for row in rows if isinstance(row["value"], (int, float))]
        if numeric_values:
            rows_sorted = sorted(rows, key=lambda row: float(row["value"]))
        else:
            rows_sorted = sorted(rows, key=lambda row: row["value_label"])

        labels = [row["value_label"] for row in rows_sorted]
        total_time = [row["pipeline_total_seconds"] for row in rows_sorted]
        max_stage = [row["pipeline_max_stage_seconds"] for row in rows_sorted]
        input_time = [row["pipeline_input_seconds"] for row in rows_sorted]
        app_time = [row["pipeline_application_seconds"] for row in rows_sorted]
        output_time = [row["pipeline_output_seconds"] for row in rows_sorted]

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(labels, total_time, marker="o", label="Pipeline total")
        ax.plot(labels, max_stage, marker="o", label="Max stage")
        ax.set_title(f"Sensitivity: {factor} - total and max stage time")
        ax.set_xlabel(factor.replace("_", " ").title())
        ax.set_ylabel("Seconds")
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(output_dir / f"sensitivity_{factor}_total_vs_{factor}.png", dpi=160)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(labels, input_time, marker="o", label="Input")
        ax.plot(labels, app_time, marker="o", label="Application")
        ax.plot(labels, output_time, marker="o", label="Output")
        ax.set_title(f"Sensitivity: {factor} - pipeline stage breakdown")
        ax.set_xlabel(factor.replace("_", " ").title())
        ax.set_ylabel("Seconds")
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(output_dir / f"sensitivity_{factor}_breakdown.png", dpi=160)
        plt.close(fig)

        if any(row["pipeline_compression_seconds"] for row in rows_sorted) or any(row["pipeline_hash_seconds"] for row in rows_sorted) or any(row["pipeline_crypto_seconds"] for row in rows_sorted):
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(labels, [row["pipeline_compression_seconds"] for row in rows_sorted], marker="o", label="Compression")
            ax.plot(labels, [row["pipeline_hash_seconds"] for row in rows_sorted], marker="o", label="Hash")
            ax.plot(labels, [row["pipeline_crypto_seconds"] for row in rows_sorted], marker="o", label="Crypto")
            ax.set_title(f"Sensitivity: {factor} - NFR overhead")
            ax.set_xlabel(factor.replace("_", " ").title())
            ax.set_ylabel("Seconds")
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.legend()
            fig.tight_layout()
            fig.savefig(output_dir / f"sensitivity_{factor}_nfr.png", dpi=160)
            plt.close(fig)


def main():
    args = parse_args()
    run_sensitivity_benchmarks(args)


if __name__ == "__main__":
    main()
