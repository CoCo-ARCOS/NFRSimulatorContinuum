#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt


SERVICE_METRICS = [
    "input_stage_seconds",
    "input_compression_seconds",
    "input_hash_seconds",
    "input_crypto_seconds",
    "application_seconds",
    "output_stage_seconds",
    "output_compression_seconds",
    "output_hash_seconds",
    "output_crypto_seconds",
    "total_seconds",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare the real pipeline against simulator stage reports."
    )
    parser.add_argument(
        "--real-results",
        type=Path,
        default=Path("real_pipeline_reference/results"),
        help="Path to the real pipeline results directory or CSV file.",
    )
    parser.add_argument(
        "--simulator-results",
        type=Path,
        required=True,
        help="Path to the simulator results directory or CSV file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Optional worker count to select from the CSVs. Defaults to the largest available count.",
    )
    parser.add_argument(
        "--mode",
        choices=("queue", "service"),
        default="queue",
        help="Comparison mode: queue compares real stage elapsed against simulator stage totals; service compares summed component times.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("real_pipeline_reference/comparison_results"),
        help="Directory where comparison outputs will be written.",
    )
    return parser.parse_args()


def read_csv_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def float_value(row, key, default=0.0):
    value = row.get(key, "")
    if value in ("", None):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def resolve_csv(path: Path, name: str):
    if path.is_dir():
        candidate = path / name
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"No {name} found under {path}")
    return path


def pick_workers(rows, requested_workers=None):
    available_workers = sorted(
        {
            int(float_value(row, "workers"))
            for row in rows
            if row.get("workers", "") not in ("", None)
        }
    )
    if not available_workers:
        return rows, None
    selected = requested_workers if requested_workers is not None else max(available_workers)
    filtered = [row for row in rows if int(float_value(row, "workers")) == selected]
    return filtered, selected


def max_requirement_index(row, prefix):
    index = 1
    while f"{prefix}_requirement_{index}" in row:
        index += 1
    return index - 1


def load_stage_totals(path: Path, workers=None):
    rows = read_csv_rows(resolve_csv(path, "stage_totals_by_workers.csv"))
    if not rows:
        return {}

    filtered_rows, selected_workers = pick_workers(rows, requested_workers=workers)
    stage_map = {}
    for row in filtered_rows:
        stage_name = row.get("stage_name") or row.get("stage") or ""
        normalized = {
            "stage_name": stage_name,
            "workers": selected_workers,
            "objects": int(float_value(row, "objects")),
        }
        for metric in SERVICE_METRICS:
            normalized[metric] = float_value(row, metric)

        for prefix in ("input", "output"):
            labels = []
            seconds = []
            for index in range(1, max_requirement_index(row, prefix) + 1):
                labels.append(row.get(f"{prefix}_requirement_{index}", ""))
                seconds.append(float_value(row, f"{prefix}_requirement_{index}_seconds"))
            normalized[f"{prefix}_requirement_labels"] = labels
            normalized[f"{prefix}_requirement_seconds"] = seconds

        stage_map[stage_name] = normalized
    return stage_map


def load_real_queue_map(path: Path):
    queue_summary_path = resolve_csv(path, "stage_queue_summary.csv")
    timeline_path = resolve_csv(path, "stage_timeline.csv")
    queue_rows = read_csv_rows(queue_summary_path)
    timeline_rows = read_csv_rows(timeline_path)

    elapsed_by_stage = {}
    for row in timeline_rows:
        stage_name = row["stage"]
        stage_metrics = elapsed_by_stage.setdefault(
            stage_name,
            {
                "first_arrival_seconds": None,
                "last_completion_seconds": None,
            },
        )
        arrival = float_value(row, "arrival_time_seconds")
        completion = float_value(row, "completion_time_seconds")
        if stage_metrics["first_arrival_seconds"] is None or arrival < stage_metrics["first_arrival_seconds"]:
            stage_metrics["first_arrival_seconds"] = arrival
        if stage_metrics["last_completion_seconds"] is None or completion > stage_metrics["last_completion_seconds"]:
            stage_metrics["last_completion_seconds"] = completion

    stage_map = {}
    for row in queue_rows:
        stage_name = row["stage"]
        elapsed = elapsed_by_stage.get(stage_name, {})
        first_arrival = elapsed.get("first_arrival_seconds", float_value(row, "first_arrival_seconds"))
        last_completion = elapsed.get("last_completion_seconds", first_arrival)
        stage_map[stage_name] = {
            "stage_name": stage_name,
            "objects": int(float_value(row, "objects")),
            "first_arrival_seconds": first_arrival,
            "last_arrival_seconds": float_value(row, "last_arrival_seconds"),
            "last_completion_seconds": last_completion,
            "stage_elapsed_seconds": max(0.0, last_completion - first_arrival),
            "mean_interarrival_seconds": float_value(row, "mean_interarrival_seconds"),
            "mean_waiting_seconds": float_value(row, "mean_waiting_seconds"),
            "mean_service_seconds": float_value(row, "mean_service_seconds"),
            "mean_response_seconds": float_value(row, "mean_response_seconds"),
        }
    return stage_map


def ordered_stage_names(*maps):
    names = []
    seen = set()
    for stage_map in maps:
        for name in stage_map:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def compare_service_maps(real_map, simulator_map):
    rows = []
    overall = {f"real_{metric}": 0.0 for metric in SERVICE_METRICS}
    overall.update({f"sim_{metric}": 0.0 for metric in SERVICE_METRICS})

    for stage_name in ordered_stage_names(real_map, simulator_map):
        real_row = real_map.get(stage_name, {})
        sim_row = simulator_map.get(stage_name, {})
        row = {"stage_name": stage_name}

        for metric in SERVICE_METRICS:
            row[f"real_{metric}"] = float(real_row.get(metric, 0.0))
            row[f"sim_{metric}"] = float(sim_row.get(metric, 0.0))
            error = row[f"sim_{metric}"] - row[f"real_{metric}"]
            ape = abs(error) / row[f"real_{metric}"] * 100.0 if row[f"real_{metric}"] > 0 else 0.0
            row[f"error_{metric}"] = error
            row[f"ape_{metric}_percent"] = ape
            overall[f"real_{metric}"] += row[f"real_{metric}"]
            overall[f"sim_{metric}"] += row[f"sim_{metric}"]

        for prefix in ("input", "output"):
            real_labels = real_row.get(f"{prefix}_requirement_labels", [])
            sim_labels = sim_row.get(f"{prefix}_requirement_labels", [])
            real_seconds = real_row.get(f"{prefix}_requirement_seconds", [])
            sim_seconds = sim_row.get(f"{prefix}_requirement_seconds", [])
            max_len = max(len(real_labels), len(sim_labels))
            for index in range(max_len):
                row[f"real_{prefix}_requirement_{index + 1}"] = real_labels[index] if index < len(real_labels) else ""
                row[f"sim_{prefix}_requirement_{index + 1}"] = sim_labels[index] if index < len(sim_labels) else ""
                row[f"real_{prefix}_requirement_{index + 1}_seconds"] = real_seconds[index] if index < len(real_seconds) else 0.0
                row[f"sim_{prefix}_requirement_{index + 1}_seconds"] = sim_seconds[index] if index < len(sim_seconds) else 0.0

        rows.append(row)

    for metric in SERVICE_METRICS:
        error = overall[f"sim_{metric}"] - overall[f"real_{metric}"]
        ape = abs(error) / overall[f"real_{metric}"] * 100.0 if overall[f"real_{metric}"] > 0 else 0.0
        overall[f"error_{metric}"] = error
        overall[f"ape_{metric}_percent"] = ape

    return rows, overall


def compare_queue_map(real_queue_map, simulator_stage_map):
    rows = []
    overall = {
        "real_stage_elapsed_seconds": 0.0,
        "sim_stage_elapsed_seconds": 0.0,
        "real_objects": 0,
        "sim_objects": 0,
    }
    for stage_name in ordered_stage_names(real_queue_map, simulator_stage_map):
        real_row = real_queue_map.get(stage_name, {})
        sim_row = simulator_stage_map.get(stage_name, {})
        real_elapsed = float(real_row.get("stage_elapsed_seconds", 0.0))
        sim_elapsed = float(sim_row.get("total_seconds", 0.0))
        error = sim_elapsed - real_elapsed
        ape = abs(error) / real_elapsed * 100.0 if real_elapsed > 0 else 0.0
        row = {
            "stage_name": stage_name,
            "real_stage_elapsed_seconds": real_elapsed,
            "sim_stage_elapsed_seconds": sim_elapsed,
            "error_stage_elapsed_seconds": error,
            "ape_stage_elapsed_percent": ape,
            "real_objects": int(real_row.get("objects", 0)),
            "sim_objects": int(sim_row.get("objects", 0)),
            "real_mean_interarrival_seconds": float(real_row.get("mean_interarrival_seconds", 0.0)),
            "real_mean_waiting_seconds": float(real_row.get("mean_waiting_seconds", 0.0)),
            "real_mean_service_seconds": float(real_row.get("mean_service_seconds", 0.0)),
            "real_mean_response_seconds": float(real_row.get("mean_response_seconds", 0.0)),
        }
        rows.append(row)
        overall["real_stage_elapsed_seconds"] += real_elapsed
        overall["sim_stage_elapsed_seconds"] += sim_elapsed
        overall["real_objects"] += row["real_objects"]
        overall["sim_objects"] += row["sim_objects"]

    overall_error = overall["sim_stage_elapsed_seconds"] - overall["real_stage_elapsed_seconds"]
    overall_ape = (
        abs(overall_error) / overall["real_stage_elapsed_seconds"] * 100.0
        if overall["real_stage_elapsed_seconds"] > 0
        else 0.0
    )
    overall["error_stage_elapsed_seconds"] = overall_error
    overall["ape_stage_elapsed_percent"] = overall_ape
    return rows, overall


def write_csv(path: Path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload):
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
        fp.write("\n")


def plot_stage_elapsed_comparison(path: Path, rows):
    labels = [row["stage_name"] for row in rows]
    x = list(range(len(labels)))
    width = 0.36

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar([value - width / 2 for value in x], [row["real_stage_elapsed_seconds"] for row in rows], width=width, label="real pipeline")
    ax.bar([value + width / 2 for value in x], [row["sim_stage_elapsed_seconds"] for row in rows], width=width, label="simulator")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Seconds")
    ax.set_title("Stage Elapsed Time: Real Pipeline vs Simulator")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_real_queue_metrics(path: Path, rows):
    labels = [row["stage_name"] for row in rows]
    x = list(range(len(labels)))
    width = 0.2
    metrics = [
        ("real_mean_interarrival_seconds", "Interarrival"),
        ("real_mean_waiting_seconds", "Waiting"),
        ("real_mean_service_seconds", "Service"),
        ("real_mean_response_seconds", "Response"),
    ]

    fig, ax = plt.subplots(figsize=(12, 5.5))
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]
    for (metric, label), offset in zip(metrics, offsets):
        ax.bar([value + offset for value in x], [row[metric] for row in rows], width=width, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Seconds")
    ax.set_title("Real Pipeline Queue Metrics by Stage")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_component_comparison(path: Path, rows):
    labels = [row["stage_name"] for row in rows]
    x = list(range(len(labels)))
    width = 0.36

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=False)
    specs = [
        ("input_stage_seconds", "Input"),
        ("application_seconds", "Application"),
        ("output_stage_seconds", "Output"),
    ]
    for ax, (metric, title) in zip(axes, specs):
        ax.bar([value - width / 2 for value in x], [row[f"real_{metric}"] for row in rows], width=width, label="real")
        ax.bar([value + width / 2 for value in x], [row[f"sim_{metric}"] for row in rows], width=width, label="simulator")
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Seconds")
    axes[-1].legend()
    fig.suptitle("Stage Component Comparison")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_family_breakdown(path: Path, rows):
    labels = [row["stage_name"] for row in rows]
    x = list(range(len(labels)))
    width = 0.36

    fig, axes = plt.subplots(2, 3, figsize=(16, 8), sharex=True)
    specs = [
        ("input_compression_seconds", "Input Compression"),
        ("input_hash_seconds", "Input Hash"),
        ("input_crypto_seconds", "Input Crypto"),
        ("output_compression_seconds", "Output Compression"),
        ("output_hash_seconds", "Output Hash"),
        ("output_crypto_seconds", "Output Crypto"),
    ]
    for ax, (metric, title) in zip(axes.flat, specs):
        ax.bar([value - width / 2 for value in x], [row[f"real_{metric}"] for row in rows], width=width, label="real")
        ax.bar([value + width / 2 for value in x], [row[f"sim_{metric}"] for row in rows], width=width, label="simulator")
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.grid(axis="y", alpha=0.25)
    axes[0][0].set_ylabel("Seconds")
    axes[1][0].set_ylabel("Seconds")
    axes[0][2].legend()
    fig.suptitle("Requirement Family Comparison")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_queue_summary(path: Path, rows, overall, real_source, simulator_source):
    lines = [
        "# Real Pipeline vs Simulator (Queue Mode)",
        "",
        f"- real queue source: `{real_source}`",
        f"- simulator source: `{simulator_source}`",
        "",
        "## Overall",
        "",
        "| Metric | Real | Simulator | Error | APE (%) |",
        "|---|---:|---:|---:|---:|",
        f"| stage_elapsed_seconds | {overall['real_stage_elapsed_seconds']:.6f} | {overall['sim_stage_elapsed_seconds']:.6f} | {overall['error_stage_elapsed_seconds']:.6f} | {overall['ape_stage_elapsed_percent']:.2f} |",
        "",
        "## Per Stage",
        "",
        "| Stage | Real elapsed (s) | Simulator total (s) | Error (s) | APE (%) | Mean interarrival (s) | Mean waiting (s) | Mean service (s) | Mean response (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['stage_name']} | {row['real_stage_elapsed_seconds']:.6f} | {row['sim_stage_elapsed_seconds']:.6f} | "
            f"{row['error_stage_elapsed_seconds']:.6f} | {row['ape_stage_elapsed_percent']:.2f} | "
            f"{row['real_mean_interarrival_seconds']:.6f} | {row['real_mean_waiting_seconds']:.6f} | "
            f"{row['real_mean_service_seconds']:.6f} | {row['real_mean_response_seconds']:.6f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_service_summary(path: Path, rows, overall, real_source, simulator_source):
    lines = [
        "# Real Pipeline vs Simulator (Service Sum Mode)",
        "",
        f"- real service source: `{real_source}`",
        f"- simulator source: `{simulator_source}`",
        "",
        "## Overall",
        "",
        "| Metric | Real (s) | Simulator (s) | Error (s) | APE (%) |",
        "|---|---:|---:|---:|---:|",
    ]

    summary_metrics = [
        "input_stage_seconds",
        "application_seconds",
        "output_stage_seconds",
        "total_seconds",
        "input_compression_seconds",
        "input_hash_seconds",
        "input_crypto_seconds",
        "output_compression_seconds",
        "output_hash_seconds",
        "output_crypto_seconds",
    ]
    for metric in summary_metrics:
        lines.append(
            f"| {metric} | {overall[f'real_{metric}']:.6f} | {overall[f'sim_{metric}']:.6f} | "
            f"{overall[f'error_{metric}']:.6f} | {overall[f'ape_{metric}_percent']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Per Stage",
            "",
            "| Stage | Real total (s) | Simulator total (s) | Error (s) | APE (%) |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['stage_name']} | {row['real_total_seconds']:.6f} | {row['sim_total_seconds']:.6f} | "
            f"{row['error_total_seconds']:.6f} | {row['ape_total_seconds_percent']:.2f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_service_fieldnames(rows):
    fieldnames = ["stage_name"]
    for metric in SERVICE_METRICS:
        fieldnames.extend(
            [
                f"real_{metric}",
                f"sim_{metric}",
                f"error_{metric}",
                f"ape_{metric}_percent",
            ]
        )

    max_input = 0
    max_output = 0
    for row in rows:
        for key in row:
            if key.startswith("real_input_requirement_") and key.endswith("_seconds"):
                max_input = max(max_input, int(key.split("_")[3]))
            if key.startswith("real_output_requirement_") and key.endswith("_seconds"):
                max_output = max(max_output, int(key.split("_")[3]))

    for index in range(1, max_input + 1):
        fieldnames.extend(
            [
                f"real_input_requirement_{index}",
                f"sim_input_requirement_{index}",
                f"real_input_requirement_{index}_seconds",
                f"sim_input_requirement_{index}_seconds",
            ]
        )
    for index in range(1, max_output + 1):
        fieldnames.extend(
            [
                f"real_output_requirement_{index}",
                f"sim_output_requirement_{index}",
                f"real_output_requirement_{index}_seconds",
                f"sim_output_requirement_{index}_seconds",
            ]
        )
    return fieldnames


def build_queue_fieldnames():
    return [
        "stage_name",
        "real_stage_elapsed_seconds",
        "sim_stage_elapsed_seconds",
        "error_stage_elapsed_seconds",
        "ape_stage_elapsed_percent",
        "real_objects",
        "sim_objects",
        "real_mean_interarrival_seconds",
        "real_mean_waiting_seconds",
        "real_mean_service_seconds",
        "real_mean_response_seconds",
    ]


def main():
    args = parse_args()
    repo_dir = Path(__file__).resolve().parent.parent

    real_input = (repo_dir / args.real_results).resolve() if not args.real_results.is_absolute() else args.real_results.resolve()
    simulator_input = (repo_dir / args.simulator_results).resolve() if not args.simulator_results.is_absolute() else args.simulator_results.resolve()
    out_dir = (repo_dir / args.out_dir).resolve() if not args.out_dir.is_absolute() else args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    simulator_stage_map = load_stage_totals(simulator_input, workers=args.workers)

    if args.mode == "queue":
        real_queue_map = load_real_queue_map(real_input)
        comparison_rows, overall = compare_queue_map(real_queue_map, simulator_stage_map)
        write_csv(out_dir / "stage_time_comparison.csv", comparison_rows, build_queue_fieldnames())
        write_json(out_dir / "overall_comparison.json", overall)
        write_queue_summary(
            out_dir / "comparison_summary.md",
            comparison_rows,
            overall,
            resolve_csv(real_input, "stage_queue_summary.csv"),
            resolve_csv(simulator_input, "stage_totals_by_workers.csv"),
        )
        plot_stage_elapsed_comparison(out_dir / "stage_elapsed_comparison.png", comparison_rows)
        plot_real_queue_metrics(out_dir / "real_queue_metrics.png", comparison_rows)
    else:
        real_stage_map = load_stage_totals(real_input, workers=args.workers)
        comparison_rows, overall = compare_service_maps(real_stage_map, simulator_stage_map)
        write_csv(out_dir / "stage_time_comparison.csv", comparison_rows, build_service_fieldnames(comparison_rows))
        write_json(out_dir / "overall_comparison.json", overall)
        write_service_summary(
            out_dir / "comparison_summary.md",
            comparison_rows,
            overall,
            resolve_csv(real_input, "stage_totals_by_workers.csv"),
            resolve_csv(simulator_input, "stage_totals_by_workers.csv"),
        )
        plot_stage_elapsed_comparison(out_dir / "stage_total_comparison.png", [
            {
                "stage_name": row["stage_name"],
                "real_stage_elapsed_seconds": row["real_total_seconds"],
                "sim_stage_elapsed_seconds": row["sim_total_seconds"],
            }
            for row in comparison_rows
        ])
        plot_component_comparison(out_dir / "stage_component_comparison.png", comparison_rows)
        plot_family_breakdown(out_dir / "stage_family_comparison.png", comparison_rows)

    print(f"Comparison written to {out_dir}")


if __name__ == "__main__":
    main()
