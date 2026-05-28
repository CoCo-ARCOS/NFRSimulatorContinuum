#!/usr/bin/env python3
import argparse
import csv
import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def float_any(row, *keys, default=0.0):
    for key in keys:
        value = row.get(key, "")
        if value in ("", None):
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return default


def parse_requirement_breakdown(row, prefix):
    requirements = []
    index = 1

    while True:
        label_key = f"{prefix}_requirement_{index}"
        seconds_key = f"{prefix}_requirement_{index}_seconds"
        if label_key not in row and seconds_key not in row:
            break

        label = row.get(label_key, "") or ""
        seconds = float_any(row, seconds_key)
        if label or seconds != 0.0:
            requirements.append(
                {
                    "index": index,
                    "label": label,
                    "seconds": seconds,
                }
            )
        index += 1

    return requirements


def new_stage_summary():
    return {
        "total": [],
        "worker": [],
        "input": [],
        "compression": [],
        "hash": [],
        "crypto": [],
        "application": [],
        "output": [],
        "transfer": [],
        "input_requirements": {},
        "output_requirements": {},
    }


def average_requirement_breakdown(requirement_map):
    averaged = []
    for index in sorted(requirement_map):
        entry = requirement_map[index]
        times = entry["times"]
        averaged.append(
            {
                "index": index,
                "label": entry.get("label", ""),
                "avg_seconds": sum(times) / len(times) if times else 0.0,
                "min_seconds": min(times) if times else 0.0,
                "max_seconds": max(times) if times else 0.0,
            }
        )
    return averaged


def stage_requirement_value(stage_data, pipeline, index):
    key = "avg_input_requirements" if pipeline == "input" else "avg_output_requirements"
    for requirement in stage_data.get(key, []):
        if requirement["index"] == index:
            return requirement.get("avg_seconds", 0.0)
    return 0.0


def collect_stage_requirement_segments(workers_summary, stage_names):
    workers = sorted(workers_summary)
    segments = []

    for stage in sorted(stage_names):
        stage_name = stage_names[stage]
        input_labels = {}
        output_labels = {}

        for worker_count in workers:
            stage_data = workers_summary[worker_count]["stage_averages"].get((stage, stage_name), {})
            for requirement in stage_data.get("avg_input_requirements", []):
                if requirement["index"] not in input_labels:
                    input_labels[requirement["index"]] = requirement.get("label", "")
            for requirement in stage_data.get("avg_output_requirements", []):
                if requirement["index"] not in output_labels:
                    output_labels[requirement["index"]] = requirement.get("label", "")

        for index in sorted(input_labels):
            label = input_labels[index] or f"input requirement {index}"
            segments.append(
                {
                    "stage": stage,
                    "stage_name": stage_name,
                    "kind": "input",
                    "index": index,
                    "label": f"S{stage} {stage_name} input {label}",
                }
            )

        segments.append(
            {
                "stage": stage,
                "stage_name": stage_name,
                "kind": "application",
                "index": 0,
                "label": f"S{stage} {stage_name} application",
            }
        )

        for index in sorted(output_labels):
            label = output_labels[index] or f"output requirement {index}"
            segments.append(
                {
                    "stage": stage,
                    "stage_name": stage_name,
                    "kind": "output",
                    "index": index,
                    "label": f"S{stage} {stage_name} output {label}",
                }
            )

    return segments


def duration_label(seconds):
    if seconds >= 3600.0:
        return f"{seconds / 3600.0:.1f}h"
    if seconds >= 60.0:
        return f"{seconds / 60.0:.1f}m"
    return f"{seconds:.1f}s"


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark simulator performance across worker counts.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config_distributed_example.json"),
        help="Base simulator JSON config file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        nargs="+",
        default=[1, 2, 3, 4, 5],
        help="Worker counts to benchmark.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of repeated runs per worker count.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_results"),
        help="Output directory for benchmark results and plots.",
    )
    parser.add_argument(
        "--simulator",
        type=Path,
        default=Path("./main"),
        help="Simulator executable relative to proxy_dd directory.",
    )
    parser.add_argument(
        "--run-plot-results",
        action="store_true",
        help="Run plot_results.py for each completed worker run.",
    )
    parser.add_argument(
        "--plot-script",
        type=Path,
        default=Path("plot_results.py"),
        help="Plot script to call for per-run results.",
    )
    parser.add_argument(
        "--with-stage-breakdown",
        action="store_true",
        help="Generate stacked benchmark plots where each worker-count bar is broken down by stage totals.",
    )
    parser.add_argument(
        "--with-timelines",
        action="store_true",
        help="Generate benchmark timeline plots from aggregated worker-scaling results.",
    )
    return parser.parse_args()


def load_config(config_path: Path):
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def write_config(config: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(config, fp, indent=2)


def clear_results_dir(results_dir: Path):
    if results_dir.exists():
        shutil.rmtree(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)


def run_simulation(simulator: Path, config_path: Path, base_dir: Path):
    simulator = simulator if simulator.is_absolute() else simulator.resolve()
    config_path = config_path if config_path.is_absolute() else config_path.resolve()
    if not simulator.exists():
        raise FileNotFoundError(f"Simulator executable not found: {simulator}")
    print(f"Running simulator with config: {config_path} {simulator}")
    result = subprocess.run(
        [str(simulator), str(config_path)],
        cwd=base_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Simulator failed with return code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
    return result.stdout, result.stderr


def copy_run_results(results_dir: Path, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    if not results_dir.exists():
        raise FileNotFoundError(f"Expected results directory not found: {results_dir}")
    for item in results_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, target_dir / item.name)
        elif item.is_dir():
            shutil.copytree(item, target_dir / item.name)


def read_stage_totals(results_dir: Path):
    path = results_dir / "stage_totals_by_workers.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing stage_totals_by_workers.csv in {results_dir}")
    rows = []
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            row["stage"] = int(row["stage"])
            row["workers"] = int(row["workers"])
            row["total_seconds"] = float_any(row, "total_seconds")
            # derive per-worker average if not present
            workers = row["workers"] if row.get("workers") else 1
            row["avg_worker_seconds"] = float_any(row, "avg_worker_seconds") if row.get("avg_worker_seconds") else (row["total_seconds"] / workers if workers > 0 else 0.0)
            row["input_seconds"] = float_any(row, "input_stage_seconds", "input_seconds")
            row["compression_seconds"] = float_any(
                row,
                "compression_seconds",
                default=float_any(row, "input_compression_seconds") + float_any(row, "output_compression_seconds"),
            )
            row["hash_seconds"] = float_any(
                row,
                "hash_seconds",
                default=float_any(row, "input_hash_seconds") + float_any(row, "output_hash_seconds"),
            )
            row["crypto_seconds"] = float_any(
                row,
                "crypto_seconds",
                default=float_any(row, "input_crypto_seconds") + float_any(row, "output_crypto_seconds"),
            )
            row["application_seconds"] = float_any(row, "application_seconds")
            row["output_seconds"] = float_any(row, "output_stage_seconds", "output_seconds")
            row["transfer_seconds"] = float_any(row, "transfer_seconds", default=0.0)
            row["input_requirements"] = parse_requirement_breakdown(row, "input")
            row["output_requirements"] = parse_requirement_breakdown(row, "output")
            rows.append(row)
    return rows


def aggregate_runs(run_results):
    workers_summary = {}
    stage_names = {}

    for worker_count, run_list in run_results.items():
        stage_data = defaultdict(new_stage_summary)
        pipeline_times = []
        pipeline_worker_times = []
        pipeline_input_times = []
        pipeline_compression_times = []
        pipeline_hash_times = []
        pipeline_crypto_times = []
        pipeline_output_times = []
        pipeline_application_times = []
        for run_info in run_list:
            rows = run_info["stage_totals"]
            stage_total = 0.0
            stage_worker_total = 0.0
            run_input_total = 0.0
            run_compression_total = 0.0
            run_hash_total = 0.0
            run_crypto_total = 0.0
            run_output_total = 0.0
            run_application_total = 0.0
            run_transfer_total = 0.0
            for row in rows:
                stage_total += row["total_seconds"]
                # derive avg worker seconds if not present
                avg_worker = row.get("avg_worker_seconds") if row.get("avg_worker_seconds") else (row["total_seconds"] / row["workers"] if row["workers"] > 0 else 0.0)
                stage_worker_total += avg_worker
                run_input_total += row["input_seconds"]
                run_compression_total += row.get("compression_seconds", 0.0)
                run_hash_total += row.get("hash_seconds", 0.0)
                run_crypto_total += row.get("crypto_seconds", 0.0)
                run_output_total += row["output_seconds"]
                run_application_total += row["application_seconds"]
                run_transfer_total += row.get("transfer_seconds", 0.0)
                stage_summary = stage_data[(row["stage"], row["stage_name"])]
                stage_summary["total"].append(row["total_seconds"])
                stage_summary["worker"].append(avg_worker)
                stage_summary["input"].append(row["input_seconds"])
                stage_summary["compression"].append(row.get("compression_seconds", 0.0))
                stage_summary["hash"].append(row.get("hash_seconds", 0.0))
                stage_summary["crypto"].append(row.get("crypto_seconds", 0.0))
                stage_summary["application"].append(row["application_seconds"])
                stage_summary["output"].append(row["output_seconds"])
                stage_summary["transfer"].append(row.get("transfer_seconds", 0.0))

                for requirement in row.get("input_requirements", []):
                    req_summary = stage_summary["input_requirements"].setdefault(
                        requirement["index"],
                        {"label": requirement.get("label", ""), "times": []},
                    )
                    if requirement.get("label") and not req_summary["label"]:
                        req_summary["label"] = requirement["label"]
                    req_summary["times"].append(requirement["seconds"])

                for requirement in row.get("output_requirements", []):
                    req_summary = stage_summary["output_requirements"].setdefault(
                        requirement["index"],
                        {"label": requirement.get("label", ""), "times": []},
                    )
                    if requirement.get("label") and not req_summary["label"]:
                        req_summary["label"] = requirement["label"]
                    req_summary["times"].append(requirement["seconds"])

            pipeline_times.append(stage_total)
            pipeline_worker_times.append(stage_worker_total)
            pipeline_input_times.append(run_input_total)
            pipeline_compression_times.append(run_compression_total)
            pipeline_hash_times.append(run_hash_total)
            pipeline_crypto_times.append(run_crypto_total)
            pipeline_output_times.append(run_output_total)
            pipeline_application_times.append(run_application_total)
            pipeline_transfer_times.append(run_transfer_total)

        averages = {}
        for (stage, stage_name), values in sorted(stage_data.items()):
            averages[(stage, stage_name)] = {
                "avg_total_seconds": sum(values["total"]) / len(values["total"]),
                "min_total_seconds": min(values["total"]),
                "max_total_seconds": max(values["total"]),
                "avg_worker_seconds": sum(values["worker"]) / len(values["worker"]),
                "min_worker_seconds": min(values["worker"]),
                "max_worker_seconds": max(values["worker"]),
                "avg_input_total_seconds": sum(values["input"]) / len(values["input"]),
                "avg_compression_total_seconds": sum(values["compression"]) / len(values["compression"]),
                "avg_hash_total_seconds": sum(values["hash"]) / len(values["hash"]),
                "avg_crypto_total_seconds": sum(values["crypto"]) / len(values["crypto"]),
                "avg_application_total_seconds": sum(values["application"]) / len(values["application"]),
                "avg_output_total_seconds": sum(values["output"]) / len(values["output"]),
                "avg_transfer_total_seconds": sum(values["transfer"]) / len(values["transfer"]),
                "avg_input_requirements": average_requirement_breakdown(values["input_requirements"]),
                "avg_output_requirements": average_requirement_breakdown(values["output_requirements"]),
            }

        workers_summary[worker_count] = {
            "stage_averages": averages,
            "pipeline_avg_seconds": sum(pipeline_times) / len(pipeline_times) if pipeline_times else 0.0,
            "pipeline_min_seconds": min(pipeline_times) if pipeline_times else 0.0,
            "pipeline_max_seconds": max(pipeline_times) if pipeline_times else 0.0,
            "pipeline_avg_worker_seconds": sum(pipeline_worker_times) / len(pipeline_worker_times) if pipeline_worker_times else 0.0,
            "pipeline_min_worker_seconds": min(pipeline_worker_times) if pipeline_worker_times else 0.0,
            "pipeline_max_worker_seconds": max(pipeline_worker_times) if pipeline_worker_times else 0.0,
            "pipeline_avg_input_seconds": sum(pipeline_input_times) / len(pipeline_input_times) if pipeline_input_times else 0.0,
            "pipeline_avg_compression_seconds": sum(pipeline_compression_times) / len(pipeline_compression_times) if pipeline_compression_times else 0.0,
            "pipeline_avg_hash_seconds": sum(pipeline_hash_times) / len(pipeline_hash_times) if pipeline_hash_times else 0.0,
            "pipeline_avg_crypto_seconds": sum(pipeline_crypto_times) / len(pipeline_crypto_times) if pipeline_crypto_times else 0.0,
            "pipeline_avg_output_seconds": sum(pipeline_output_times) / len(pipeline_output_times) if pipeline_output_times else 0.0,
            "pipeline_avg_application_seconds": sum(pipeline_application_times) / len(pipeline_application_times) if pipeline_application_times else 0.0,
            "pipeline_avg_transfer_seconds": sum(pipeline_transfer_times) / len(pipeline_transfer_times) if pipeline_transfer_times else 0.0,
            "runs": len(pipeline_times),
        }

        for (stage, stage_name) in averages:
            stage_names[stage] = stage_name

    return workers_summary, stage_names


def write_summary_csv(summary_path: Path, workers_summary, stage_names):
    fieldnames = [
        "workers",
        "stage",
        "stage_name",
        "avg_total_seconds",
        "min_total_seconds",
        "max_total_seconds",
        "avg_worker_seconds",
        "min_worker_seconds",
        "max_worker_seconds",
        "avg_input_total_seconds",
        "avg_compression_total_seconds",
        "avg_hash_total_seconds",
        "avg_crypto_total_seconds",
        "avg_application_total_seconds",
        "avg_output_total_seconds",
        "pipeline_avg_seconds",
        "pipeline_min_seconds",
        "pipeline_max_seconds",
        "pipeline_avg_worker_seconds",
        "pipeline_min_worker_seconds",
        "pipeline_max_worker_seconds",
        "pipeline_avg_input_seconds",
        "pipeline_avg_compression_seconds",
        "pipeline_avg_hash_seconds",
        "pipeline_avg_crypto_seconds",
        "pipeline_avg_output_seconds",
        "pipeline_avg_application_seconds",
        "pipeline_avg_transfer_seconds",
        "runs",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for worker_count in sorted(workers_summary):
            summary = workers_summary[worker_count]
            for (stage, stage_name), data in summary["stage_averages"].items():
                writer.writerow(
                    {
                        "workers": worker_count,
                        "stage": stage,
                        "stage_name": stage_name,
                        "avg_total_seconds": data["avg_total_seconds"],
                        "min_total_seconds": data["min_total_seconds"],
                        "max_total_seconds": data["max_total_seconds"],
                        "avg_worker_seconds": data["avg_worker_seconds"],
                        "min_worker_seconds": data["min_worker_seconds"],
                        "max_worker_seconds": data["max_worker_seconds"],
                        "avg_input_total_seconds": data.get("avg_input_total_seconds", 0.0),
                        "avg_compression_total_seconds": data.get("avg_compression_total_seconds", 0.0),
                        "avg_hash_total_seconds": data.get("avg_hash_total_seconds", 0.0),
                        "avg_crypto_total_seconds": data.get("avg_crypto_total_seconds", 0.0),
                        "avg_application_total_seconds": data.get("avg_application_total_seconds", 0.0),
                        "avg_output_total_seconds": data.get("avg_output_total_seconds", 0.0),
                        "pipeline_avg_seconds": summary["pipeline_avg_seconds"],
                        "pipeline_min_seconds": summary["pipeline_min_seconds"],
                        "pipeline_max_seconds": summary["pipeline_max_seconds"],
                        "pipeline_avg_worker_seconds": summary["pipeline_avg_worker_seconds"],
                        "pipeline_min_worker_seconds": summary["pipeline_min_worker_seconds"],
                        "pipeline_max_worker_seconds": summary["pipeline_max_worker_seconds"],
                        "pipeline_avg_input_seconds": summary.get("pipeline_avg_input_seconds", 0.0),
                        "pipeline_avg_compression_seconds": summary.get("pipeline_avg_compression_seconds", 0.0),
                        "pipeline_avg_hash_seconds": summary.get("pipeline_avg_hash_seconds", 0.0),
                        "pipeline_avg_crypto_seconds": summary.get("pipeline_avg_crypto_seconds", 0.0),
                        "pipeline_avg_output_seconds": summary.get("pipeline_avg_output_seconds", 0.0),
                        "pipeline_avg_application_seconds": summary.get("pipeline_avg_application_seconds", 0.0),
                        "pipeline_avg_transfer_seconds": summary.get("pipeline_avg_transfer_seconds", 0.0),
                        "runs": summary["runs"],
                    }
                )


def plot_stage_pipeline_breakdown(output_dir: Path, workers_summary, stage_names):
    workers = sorted(workers_summary)
    fig, ax = plt.subplots(figsize=(max(10, len(workers) * 1.5), 7))
    positions = list(range(len(workers)))
    bar_width = 0.6
    bottoms = [0.0] * len(workers)

    for stage_index, stage in enumerate(sorted(stage_names)):
        stage_name = stage_names[stage]
        values = [
            workers_summary[worker_count]["stage_averages"].get((stage, stage_name), {}).get("avg_total_seconds", 0.0)
            for worker_count in workers
        ]
        ax.bar(
            positions,
            values,
            bar_width,
            bottom=bottoms,
            label=f"S{stage} {stage_name}",
            color=plt.get_cmap("tab10").colors[stage_index % len(plt.get_cmap("tab10").colors)],
        )
        bottoms = [bottoms[i] + values[i] for i in range(len(values))]

    for position, total in zip(positions, bottoms):
        ax.text(position, total, f"{total:.1f}", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(positions)
    ax.set_xticklabels([str(worker) for worker in workers])
    ax.set_title("Pipeline stage breakdown by worker count")
    ax.set_xlabel("Workers")
    ax.set_ylabel("Seconds")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    path = output_dir / "worker_scaling_stage_pipeline_breakdown.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_stage_pipeline_timeline(output_dir: Path, workers_summary, stage_names):
    workers = sorted(workers_summary)
    fig_height = max(4, len(workers) * 0.9 + 1.5)
    fig, ax = plt.subplots(figsize=(13, fig_height))
    colors = plt.get_cmap("tab10").colors

    for row_index, worker_count in enumerate(workers):
        start = 0.0
        for stage_index, stage in enumerate(sorted(stage_names)):
            stage_name = stage_names[stage]
            duration = workers_summary[worker_count]["stage_averages"].get((stage, stage_name), {}).get("avg_total_seconds", 0.0)
            if duration <= 0.0:
                continue
            ax.barh(
                row_index,
                duration,
                left=start,
                height=0.55,
                color=colors[stage_index % len(colors)],
                edgecolor="white",
                linewidth=0.8,
                label=f"S{stage} {stage_name}" if row_index == 0 else None,
            )
            if duration > 0.0:
                ax.text(start + duration / 2.0, row_index, duration_label(duration), ha="center", va="center", fontsize=8, color="white")
            start += duration
        ax.text(start, row_index, f"  {duration_label(start)}", va="center", fontsize=8)

    ax.set_yticks(list(range(len(workers))))
    ax.set_yticklabels([str(worker) for worker in workers])
    ax.invert_yaxis()
    ax.set_xlabel("Cumulative seconds")
    ax.set_ylabel("Workers")
    ax.set_title("Stage timeline by worker count")
    ax.grid(axis="x", alpha=0.25)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=min(4, len(stage_names)))
    fig.tight_layout()
    path = output_dir / "worker_scaling_stage_timeline.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_stage_timeline_for_worker_count(output_dir: Path, worker_count: int, workers_summary, stage_names):
    summary = workers_summary[worker_count]
    rows = []
    for stage in sorted(stage_names):
        stage_name = stage_names[stage]
        stage_data = summary["stage_averages"].get((stage, stage_name), {})
        rows.append(
            {
                "stage": stage,
                "stage_name": stage_name,
                "input_seconds": stage_data.get("avg_input_total_seconds", 0.0),
                "application_seconds": stage_data.get("avg_application_total_seconds", 0.0),
                "output_seconds": stage_data.get("avg_output_total_seconds", 0.0),
            }
        )

    stage_totals = [
        row["input_seconds"] + row["application_seconds"] + row["output_seconds"]
        for row in rows
    ]
    if not any(total > 0.0 for total in stage_totals):
        return None

    fig_height = max(4, len(rows) * 0.65 + 1.5)
    fig, ax = plt.subplots(figsize=(12, fig_height))
    colors = {
        "input": "#4C78A8",
        "application": "#F58518",
        "output": "#54A24B",
    }
    legend_seen = set()
    total_pipeline_seconds = sum(stage_totals)
    cumulative_start = 0.0

    for y, (row, total) in enumerate(zip(rows, stage_totals)):
        segment_start = cumulative_start
        ax.axvline(segment_start, color="#dddddd", linewidth=0.8, zorder=0)

        segments = [
            ("input", row["input_seconds"]),
            ("application", row["application_seconds"]),
            ("output", row["output_seconds"]),
        ]
        for name, duration in segments:
            if duration <= 0.0:
                continue

            label = name if name not in legend_seen else None
            ax.barh(
                y,
                duration,
                left=segment_start,
                height=0.5,
                color=colors[name],
                edgecolor="white",
                linewidth=0.8,
                label=label,
            )
            legend_seen.add(name)

            if total_pipeline_seconds > 0.0 and duration / total_pipeline_seconds >= 0.015:
                ax.text(
                    segment_start + duration / 2.0,
                    y,
                    duration_label(duration),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white",
                )

            segment_start += duration

        ax.text(
            segment_start + total_pipeline_seconds * 0.01,
            y,
            duration_label(total),
            va="center",
            fontsize=8,
        )
        cumulative_start += total

    ax.axvline(cumulative_start, color="#bbbbbb", linewidth=1.0, zorder=0)
    ax.set_yticks(list(range(len(rows))))
    ax.set_yticklabels([f"S{row['stage']} {row['stage_name']}" for row in rows])
    ax.invert_yaxis()
    ax.set_xlabel("cumulative seconds")
    ax.set_title(f"Stage Execution Timeline ({worker_count} workers)")
    ax.grid(axis="x", alpha=0.25)
    if legend_seen:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=len(legend_seen))
    ax.set_xlim(0.0, cumulative_start * 1.08 if cumulative_start > 0.0 else 1.0)
    fig.tight_layout()
    path = output_dir / f"worker_scaling_stage_execution_timeline_w{worker_count}.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_stage_requirement_timeline(output_dir: Path, workers_summary, stage_names):
    workers = sorted(workers_summary)
    stage_segments = collect_stage_requirement_segments(workers_summary, stage_names)
    fig_height = max(4, len(workers) * 0.9 + 1.5)
    fig, ax = plt.subplots(figsize=(15, fig_height))
    colors = plt.get_cmap("tab20").colors

    for row_index, worker_count in enumerate(workers):
        start = 0.0
        for segment_index, segment in enumerate(stage_segments):
            stage_data = workers_summary[worker_count]["stage_averages"].get((segment["stage"], segment["stage_name"]), {})
            if segment["kind"] == "application":
                duration = stage_data.get("avg_application_total_seconds", 0.0)
            else:
                duration = stage_requirement_value(stage_data, segment["kind"], segment["index"])
            if duration <= 0.0:
                continue
            ax.barh(
                row_index,
                duration,
                left=start,
                height=0.55,
                color=colors[segment_index % len(colors)],
                edgecolor="white",
                linewidth=0.6,
                label=segment["label"] if row_index == 0 else None,
            )
            start += duration
        ax.text(start, row_index, f"  {duration_label(start)}", va="center", fontsize=8)

    ax.set_yticks(list(range(len(workers))))
    ax.set_yticklabels([str(worker) for worker in workers])
    ax.invert_yaxis()
    ax.set_xlabel("Cumulative seconds")
    ax.set_ylabel("Workers")
    ax.set_title("Stage and requirement timeline by worker count")
    ax.grid(axis="x", alpha=0.25)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3)
    fig.tight_layout()
    path = output_dir / "worker_scaling_stage_requirement_timeline.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_worker_scaling(output_dir: Path, workers_summary, stage_names, with_stage_breakdown=False, with_timelines=False):
    workers = sorted(workers_summary)
    pipeline_avg = [workers_summary[w]["pipeline_avg_seconds"] for w in workers]
    plt.figure(figsize=(10, 6))
    plt.plot(workers, pipeline_avg, marker="o", label="Pipeline total time")
    plt.title("Pipeline total time vs worker count")
    plt.xlabel("Workers")
    plt.ylabel("Seconds")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    pipeline_path = output_dir / "worker_scaling_pipeline_time.png"
    plt.savefig(pipeline_path, dpi=160)
    plt.close()

    pipeline_worker_avg = [workers_summary[w]["pipeline_avg_worker_seconds"] for w in workers]
    plt.figure(figsize=(10, 6))
    plt.plot(workers, pipeline_worker_avg, marker="o", label="Pipeline avg_worker_seconds")
    plt.title("Pipeline avg worker time vs worker count")
    plt.xlabel("Workers")
    plt.ylabel("Seconds")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    pipeline_worker_path = output_dir / "worker_scaling_pipeline_avg_worker_time.png"
    plt.savefig(pipeline_worker_path, dpi=160)
    plt.close()

    input_totals = [workers_summary[w].get("pipeline_avg_input_seconds", 0.0) for w in workers]
    application_totals = [workers_summary[w].get("pipeline_avg_application_seconds", 0.0) for w in workers]
    output_totals = [workers_summary[w].get("pipeline_avg_output_seconds", 0.0) for w in workers]
    plt.figure(figsize=(12, 7))
    bar_width = 0.6
    index = range(len(workers))
    plt.bar(index, input_totals, bar_width, label="Input pipeline")
    plt.bar(index, application_totals, bar_width, bottom=input_totals, label="Application")
    bottom_output = [i + a for i, a in zip(input_totals, application_totals)]
    plt.bar(index, output_totals, bar_width, bottom=bottom_output, label="Output pipeline")
    plt.xticks(index, [str(w) for w in workers])
    plt.title("Pipeline time breakdown vs worker count")
    plt.xlabel("Workers")
    plt.ylabel("Seconds")
    plt.legend()
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    stacked_path = output_dir / "worker_scaling_pipeline_breakdown.png"
    plt.savefig(stacked_path, dpi=160)
    plt.close()

    # Separate NFR breakdown plot so requirement costs do not get mixed with pipeline input/output totals.
    compression_totals = [workers_summary[w].get("pipeline_avg_compression_seconds", 0.0) for w in workers]
    hash_totals = [workers_summary[w].get("pipeline_avg_hash_seconds", 0.0) for w in workers]
    crypto_totals = [workers_summary[w].get("pipeline_avg_crypto_seconds", 0.0) for w in workers]
    plt.figure(figsize=(12, 7))
    plt.bar(index, compression_totals, bar_width, label="Compression")
    plt.bar(index, hash_totals, bar_width, bottom=compression_totals, label="Hash")
    bottom_nfr = [c + h for c, h in zip(compression_totals, hash_totals)]
    plt.bar(index, crypto_totals, bar_width, bottom=bottom_nfr, label="Crypto")
    plt.xticks(index, [str(w) for w in workers])
    plt.title("Pipeline NFR time breakdown vs worker count")
    plt.xlabel("Workers")
    plt.ylabel("Seconds")
    plt.legend()
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    nfr_path = output_dir / "worker_scaling_pipeline_nfr_breakdown.png"
    plt.savefig(nfr_path, dpi=160)
    plt.close()

    stage_segments = collect_stage_requirement_segments(workers_summary, stage_names)
    fig, ax = plt.subplots(figsize=(max(12, len(workers) * 1.4), 9))
    positions = list(range(len(workers)))
    bottoms = [0.0] * len(workers)
    colors = plt.get_cmap("tab20").colors
    plotted_segments = 0

    for segment_index, segment in enumerate(stage_segments):
        values = []
        for worker_count in workers:
            stage_data = workers_summary[worker_count]["stage_averages"].get(
                (segment["stage"], segment["stage_name"]),
                {},
            )
            if segment["kind"] == "application":
                value = stage_data.get("avg_application_total_seconds", 0.0)
            else:
                value = stage_requirement_value(stage_data, segment["kind"], segment["index"])
            values.append(value)

        if not any(value != 0.0 for value in values):
            continue

        ax.bar(
            positions,
            values,
            bar_width,
            bottom=bottoms,
            label=segment["label"],
            color=colors[segment_index % len(colors)],
        )
        bottoms = [bottoms[i] + values[i] for i in range(len(values))]
        plotted_segments += 1

    for position, total in zip(positions, bottoms):
        ax.text(position, total, f"{total:.1f}", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(positions)
    ax.set_xticklabels([str(worker) for worker in workers])
    ax.set_title("Stage and requirement breakdown by worker count")
    ax.set_xlabel("Workers")
    ax.set_ylabel("Seconds")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    if plotted_segments:
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.14),
            ncol=min(3, max(1, plotted_segments)),
        )
    fig.tight_layout()
    stage_breakdown_path = output_dir / "worker_scaling_stage_breakdown.png"
    fig.savefig(stage_breakdown_path, dpi=160)
    plt.close(fig)

    plt.figure(figsize=(12, 7))
    for stage in sorted(stage_names):
        stage_avg = [
            workers_summary[w]["stage_averages"].get((stage, stage_names[stage]), {}).get("avg_total_seconds", 0.0)
            for w in workers
        ]
        plt.plot(workers, stage_avg, marker="o", label=f"Stage {stage} {stage_names[stage]}")
    plt.title("Stage total time vs worker count")
    plt.xlabel("Workers")
    plt.ylabel("Seconds")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    stage_path = output_dir / "worker_scaling_stage_times.png"
    plt.savefig(stage_path, dpi=160)
    plt.close()

    plt.figure(figsize=(12, 7))
    for stage in sorted(stage_names):
        stage_avg_worker = [
            workers_summary[w]["stage_averages"].get((stage, stage_names[stage]), {}).get("avg_worker_seconds", 0.0)
            for w in workers
        ]
        plt.plot(workers, stage_avg_worker, marker="o", label=f"Stage {stage} {stage_names[stage]}")
    plt.title("Stage avg_worker_seconds vs worker count")
    plt.xlabel("Workers")
    plt.ylabel("Seconds")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    stage_worker_path = output_dir / "worker_scaling_stage_avg_worker_seconds.png"
    plt.savefig(stage_worker_path, dpi=160)
    plt.close()

    extra_paths = []
    if with_stage_breakdown:
        extra_paths.append(plot_stage_pipeline_breakdown(output_dir, workers_summary, stage_names))
    if with_timelines:
        extra_paths.append(plot_stage_pipeline_timeline(output_dir, workers_summary, stage_names))
        extra_paths.append(plot_stage_requirement_timeline(output_dir, workers_summary, stage_names))
        for worker_count in workers:
            worker_timeline_path = plot_stage_timeline_for_worker_count(output_dir, worker_count, workers_summary, stage_names)
            if worker_timeline_path:
                extra_paths.append(worker_timeline_path)

    return [
        pipeline_path,
        pipeline_worker_path,
        stacked_path,
        stage_breakdown_path,
        stage_path,
        stage_worker_path,
        *extra_paths,
    ]


def main():
    args = parse_args()
    base_dir = args.config.resolve().parent
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    template_config = load_config(args.config)
    run_results = defaultdict(list)
    results_dir = base_dir / "results"
    args.simulator = (base_dir / args.simulator).resolve()

    for worker_count in args.workers:
        for run_index in range(1, args.repeats + 1):
            print(f"Running worker count={worker_count} repeat={run_index}/{args.repeats}")
            config_copy = dict(template_config)
            config_copy["workers"] = worker_count
            temp_config_path = output_dir / f"config_workers_{worker_count}_run_{run_index}.json"
            write_config(config_copy, temp_config_path)

            clear_results_dir(results_dir)
            print(f"{args.simulator} {temp_config_path} {base_dir}")
            stdout, stderr = run_simulation(args.simulator, temp_config_path, base_dir)

            run_dir = output_dir / f"workers_{worker_count}" / f"run_{run_index}"
            copy_run_results(results_dir, run_dir)
            write_config(config_copy, run_dir / "config.json")
            with (run_dir / "simulator_stdout.txt").open("w", encoding="utf-8") as fp:
                fp.write(stdout)
            with (run_dir / "simulator_stderr.txt").open("w", encoding="utf-8") as fp:
                fp.write(stderr)

            stage_totals = read_stage_totals(run_dir)
            run_results[worker_count].append({"stage_totals": stage_totals})

            if args.run_plot_results:
                plot_out = run_dir / "plots"
                plot_out.mkdir(exist_ok=True, parents=True)
                subprocess.run(
                    ["python3", str(args.plot_script), "--results", str(run_dir), "--out", str(plot_out)],
                    cwd=base_dir,
                    check=True,
                )

    workers_summary, stage_names = aggregate_runs(run_results)
    summary_csv = output_dir / "worker_scaling_summary.csv"
    write_summary_csv(summary_csv, workers_summary, stage_names)
    generated_paths = plot_worker_scaling(
        output_dir,
        workers_summary,
        stage_names,
        with_stage_breakdown=args.with_stage_breakdown,
        with_timelines=args.with_timelines,
    )

    print(f"Benchmark complete. Summary written to {summary_csv}")
    print("Generated plots:")
    for path in generated_paths:
        print(f"  {path}")


if __name__ == "__main__":
    main()
