#!/usr/bin/env python3
import argparse
import csv
import math
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate the simulator interpolation model against real measured times."
    )
    parser.add_argument(
        "--real-values-dir",
        type=Path,
        default=Path("real_values"),
        help="Directory containing cost-efficiency.csv, integrity.csv, and reliability.csv.",
    )
    parser.add_argument(
        "--hardware-profile",
        choices=("c3", "toge", "dianalap"),
        default=None,
        help="Shortcut for results_different_machines/organized/<profile>/real_values.",
    )
    parser.add_argument(
        "--machine-datasets-root",
        type=Path,
        default=Path("results_different_machines/organized"),
        help="Root directory containing organized machine datasets.",
    )
    parser.add_argument(
        "--mode",
        choices=("leave-one-out", "fit"),
        default="leave-one-out",
        help="Validation mode. leave-one-out excludes the evaluated point from interpolation.",
    )
    parser.add_argument(
        "--service-time-model",
        choices=("linear", "log-log"),
        default="linear",
        help="Service-time model to validate.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional CSV path to write per-sample validation results.",
    )
    parser.add_argument(
        "--helper",
        type=Path,
        default=Path("./validate_interpolation_helper"),
        help="Path to the compiled C helper that uses service_time.c interpolation functions.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("results/interpolation_validation_summary.csv"),
        help="CSV path for aggregated summary rows.",
    )
    parser.add_argument(
        "--plots-dir",
        type=Path,
        default=Path("results/interpolation_validation_plots"),
        help="Directory where validation plots will be written.",
    )
    return parser.parse_args()


def resolve_real_values_dir(repo_dir, real_values_dir, hardware_profile=None, machine_datasets_root=None):
    if hardware_profile:
        root = machine_datasets_root or Path("results_different_machines/organized")
        root = (repo_dir / root).resolve() if not root.is_absolute() else root.resolve()
        return (root / hardware_profile / "real_values").resolve()
    return (repo_dir / real_values_dir).resolve() if not real_values_dir.is_absolute() else real_values_dir.resolve()


def mae(values):
    return sum(abs(v) for v in values) / len(values) if values else 0.0


def rmse(values):
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


def mape(actual_pred_pairs):
    valid = []
    for actual, predicted in actual_pred_pairs:
        if actual == 0:
            continue
        valid.append(abs((actual - predicted) / actual) * 100.0)
    return sum(valid) / len(valid) if valid else 0.0


def load_cost_efficiency(real_values_dir):
    path = real_values_dir / "cost-efficiency.csv"
    datasets = []
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row_index, row in enumerate(reader):
            size_bytes = float(row["size_mb"]) * 1048576.0
            algorithm = row["algoritmo"].strip()
            datasets.append(
                {
                    "source_file": "cost-efficiency.csv",
                    "source_row": row_index,
                    "dataset": "cost-efficiency",
                    "operation": "compress",
                    "group": algorithm,
                    "size_bytes": size_bytes,
                    "actual_seconds": float(row["avg_comp_s"]),
                    "label": algorithm,
                }
            )
            datasets.append(
                {
                    "source_file": "cost-efficiency.csv",
                    "source_row": row_index,
                    "dataset": "cost-efficiency",
                    "operation": "decompress",
                    "group": algorithm,
                    "size_bytes": size_bytes,
                    "actual_seconds": float(row["avg_decomp_s"]),
                    "label": algorithm,
                }
            )
    return datasets


def load_integrity(real_values_dir):
    path = real_values_dir / "integrity.csv"
    datasets = []
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row_index, row in enumerate(reader):
            size_bytes = float(row["size_mb"]) * 1048576.0
            algorithm = row["algoritmo"].strip()
            datasets.append(
                {
                    "source_file": "integrity.csv",
                    "source_row": row_index,
                    "dataset": "integrity",
                    "operation": "hash",
                    "group": algorithm,
                    "size_bytes": size_bytes,
                    "actual_seconds": float(row["avg_time_seconds"]),
                    "label": algorithm,
                }
            )
    return datasets


def load_reliability(real_values_dir):
    path = real_values_dir / "reliability.csv"
    datasets = []
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row_index, row in enumerate(reader):
            size_bytes = float(row["size_mb"]) * 1048576.0
            algorithm = row["algoritmo"].strip()
            k = int(row["k_datos"])
            m = int(row["m_paridad"])
            group = f"{algorithm}:{k}:{m}"
            label = f"{algorithm} k={k} m={m}"
            datasets.append(
                {
                    "source_file": "reliability.csv",
                    "source_row": row_index,
                    "dataset": "reliability",
                    "operation": "encode",
                    "group": group,
                    "size_bytes": size_bytes,
                    "actual_seconds": float(row["avg_encoding_s"]),
                    "label": label,
                    "k": k,
                    "m": m,
                }
            )
            datasets.append(
                {
                    "source_file": "reliability.csv",
                    "source_row": row_index,
                    "dataset": "reliability",
                    "operation": "decode",
                    "group": group,
                    "size_bytes": size_bytes,
                    "actual_seconds": float(row["avg_decoding_s"]),
                    "label": label,
                    "k": k,
                    "m": m,
                }
            )
    return datasets


def load_all_samples(real_values_dir):
    return (
        load_cost_efficiency(real_values_dir)
        + load_integrity(real_values_dir)
        + load_reliability(real_values_dir)
    )


def group_samples(samples):
    grouped = defaultdict(list)
    for sample in samples:
        key = (sample["dataset"], sample["operation"], sample["group"])
        grouped[key].append(sample)
    return grouped


def build_helper(helper_path, repo_dir):
    source = repo_dir / "validate_interpolation_helper.c"
    service_time = repo_dir / "service_time.c"
    proxy_header = repo_dir / "proxy.h"
    helper_path = helper_path.resolve()

    rebuild = (
        not helper_path.exists()
        or helper_path.stat().st_mtime < source.stat().st_mtime
        or helper_path.stat().st_mtime < service_time.stat().st_mtime
        or helper_path.stat().st_mtime < proxy_header.stat().st_mtime
    )
    if not rebuild:
        return helper_path

    cmd = [
        "gcc",
        "-g",
        "-o",
        str(helper_path),
        str(source),
        str(service_time),
        "-lm",
    ]
    subprocess.run(cmd, cwd=repo_dir, check=True)
    return helper_path


def clone_real_values(real_values_dir, excluded_sample=None):
    temp_root = Path(tempfile.mkdtemp(prefix="interp_validate_"))
    temp_real_values = temp_root / "real_values"
    temp_real_values.mkdir(parents=True, exist_ok=True)

    for source_path in sorted(real_values_dir.glob("*.csv")):
        target_path = temp_real_values / source_path.name
        if excluded_sample is None or source_path.name != excluded_sample["source_file"]:
            shutil.copy2(source_path, target_path)
            continue

        with source_path.open(newline="", encoding="utf-8") as src_fp, target_path.open("w", newline="", encoding="utf-8") as dst_fp:
            reader = csv.reader(src_fp)
            writer = csv.writer(dst_fp)
            for row_index, row in enumerate(reader):
                if row_index == 0:
                    writer.writerow(row)
                    continue
                if row_index - 1 == excluded_sample["source_row"]:
                    continue
                writer.writerow(row)

    return temp_root


def predict_with_helper(helper_path, workspace_dir, sample, service_time_model):
    cmd = [
        str(helper_path),
        sample["operation"],
        sample["label"].split(" k=")[0] if sample["dataset"] == "reliability" else sample["label"],
        str(sample["size_bytes"]),
    ]
    if sample["dataset"] == "reliability":
        cmd.extend([str(sample["k"]), str(sample["m"])])
    cmd.append(service_time_model)

    result = subprocess.run(
        cmd,
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def validate_samples(samples, helper_path, real_values_dir, mode, service_time_model):
    results = []

    for sample in samples:
        workspace_dir = clone_real_values(real_values_dir, excluded_sample=sample if mode == "leave-one-out" else None)
        try:
            predicted = predict_with_helper(helper_path, workspace_dir, sample, service_time_model)
        finally:
            shutil.rmtree(workspace_dir, ignore_errors=True)

        error = predicted - sample["actual_seconds"]
        abs_error = abs(error)
        ape = abs_error / sample["actual_seconds"] * 100.0 if sample["actual_seconds"] != 0 else 0.0
        results.append(
            {
                "dataset": sample["dataset"],
                "operation": sample["operation"],
                "group": sample["group"],
                "label": sample["label"],
                "size_bytes": sample["size_bytes"],
                "actual_seconds": sample["actual_seconds"],
                "predicted_seconds": predicted,
                "error_seconds": error,
                "abs_error_seconds": abs_error,
                "ape_percent": ape,
            }
        )

    return results


def summarize(results):
    summaries = []
    operation_summaries = []
    grouped = defaultdict(list)
    operation_grouped = defaultdict(list)

    for row in results:
        grouped[(row["dataset"], row["operation"], row["group"], row["label"])].append(row)
        operation_grouped[(row["dataset"], row["operation"])].append(row)

    for (dataset, operation, group, label), rows in sorted(grouped.items()):
        errors = [row["error_seconds"] for row in rows]
        actual_pred_pairs = [(row["actual_seconds"], row["predicted_seconds"]) for row in rows]
        summaries.append(
            {
                "dataset": dataset,
                "operation": operation,
                "group": group,
                "label": label,
                "samples": len(rows),
                "mae_seconds": mae(errors),
                "rmse_seconds": rmse(errors),
                "mape_percent": mape(actual_pred_pairs),
            }
        )

    for (dataset, operation), rows in sorted(operation_grouped.items()):
        errors = [row["error_seconds"] for row in rows]
        actual_pred_pairs = [(row["actual_seconds"], row["predicted_seconds"]) for row in rows]
        operation_summaries.append(
            {
                "dataset": dataset,
                "operation": operation,
                "group": "all",
                "label": f"{dataset}:{operation}",
                "samples": len(rows),
                "mae_seconds": mae(errors),
                "rmse_seconds": rmse(errors),
                "mape_percent": mape(actual_pred_pairs),
            }
        )

    overall_errors = [row["error_seconds"] for row in results]
    overall_pairs = [(row["actual_seconds"], row["predicted_seconds"]) for row in results]
    overall = {
        "dataset": "overall",
        "operation": "all",
        "group": "all",
        "label": "overall",
        "samples": len(results),
        "mae_seconds": mae(overall_errors),
        "rmse_seconds": rmse(overall_errors),
        "mape_percent": mape(overall_pairs),
    }
    return summaries, operation_summaries, overall


def print_summary_table(summaries, operation_summaries, overall, mode):
    print(f"Interpolation validation mode: {mode}")
    print("")
    print("Dataset\tOperation\tGroup\tSamples\tMAE(s)\tRMSE(s)\tMAPE(%)")
    for row in operation_summaries:
        print(
            f"{row['dataset']}\t{row['operation']}\t{row['label']}\t{row['samples']}"
            f"\t{row['mae_seconds']:.6f}\t{row['rmse_seconds']:.6f}\t{row['mape_percent']:.2f}"
        )
    print("")
    print(
        f"OVERALL\t{overall['operation']}\t{overall['label']}\t{overall['samples']}"
        f"\t{overall['mae_seconds']:.6f}\t{overall['rmse_seconds']:.6f}\t{overall['mape_percent']:.2f}"
    )

    print("\nDetailed groups:")
    print("Dataset\tOperation\tGroup\tSamples\tMAE(s)\tRMSE(s)\tMAPE(%)")
    for row in summaries:
        print(
            f"{row['dataset']}\t{row['operation']}\t{row['label']}\t{row['samples']}"
            f"\t{row['mae_seconds']:.6f}\t{row['rmse_seconds']:.6f}\t{row['mape_percent']:.2f}"
        )


def write_results_csv(path, results):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "dataset",
        "operation",
        "group",
        "label",
        "size_bytes",
        "actual_seconds",
        "predicted_seconds",
        "error_seconds",
        "abs_error_seconds",
        "ape_percent",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def write_summary_csv(path, summaries, operation_summaries, overall):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "level",
        "dataset",
        "operation",
        "group",
        "label",
        "samples",
        "mae_seconds",
        "rmse_seconds",
        "mape_percent",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in operation_summaries:
            writer.writerow({"level": "operation", **row})
        for row in summaries:
            writer.writerow({"level": "group", **row})
        writer.writerow({"level": "overall", **overall})


def save_figure(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_actual_vs_predicted(results, plots_dir):
    fig, ax = plt.subplots(figsize=(7, 6))
    by_operation = defaultdict(list)
    for row in results:
        by_operation[row["operation"]].append(row)

    colors = {
        "compress": "#4C78A8",
        "decompress": "#F58518",
        "hash": "#54A24B",
        "encode": "#E45756",
        "decode": "#B279A2",
    }
    min_value = min(min(row["actual_seconds"], row["predicted_seconds"]) for row in results if row["actual_seconds"] > 0 and row["predicted_seconds"] > 0)
    max_value = max(max(row["actual_seconds"], row["predicted_seconds"]) for row in results)

    for operation, rows in sorted(by_operation.items()):
        ax.scatter(
            [row["actual_seconds"] for row in rows],
            [row["predicted_seconds"] for row in rows],
            label=operation,
            alpha=0.85,
            s=38,
            color=colors.get(operation),
        )

    ax.plot([min_value, max_value], [min_value, max_value], linestyle="--", color="#666666", linewidth=1.0, label="ideal")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Real time (s)")
    ax.set_ylabel("Predicted time (s)")
    ax.set_title("Interpolation Model: Predicted vs Real")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()
    save_figure(fig, plots_dir / "predicted_vs_real_scatter.png")


def plot_metrics_by_operation(operation_summaries, plots_dir):
    labels = [row["operation"] for row in operation_summaries]
    x = list(range(len(labels)))
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.5))
    metric_specs = [
        ("mae_seconds", "MAE (s)", "#4C78A8"),
        ("rmse_seconds", "RMSE (s)", "#F58518"),
        ("mape_percent", "MAPE (%)", "#54A24B"),
    ]
    for ax, (key, title, color) in zip(axes, metric_specs):
        ax.bar(x, [row[key] for row in operation_summaries], color=color)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=25, ha="right")
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Validation Error by Operation")
    save_figure(fig, plots_dir / "error_metrics_by_operation.png")


def plot_absolute_percentage_error(results, plots_dir):
    order = ["compress", "decompress", "hash", "encode", "decode"]
    series = [[row["ape_percent"] for row in results if row["operation"] == operation] for operation in order]
    labels = [operation for operation, values in zip(order, series) if values]
    filtered_series = [values for values in series if values]

    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    try:
        ax.boxplot(filtered_series, tick_labels=labels, showfliers=True)
    except TypeError:
        ax.boxplot(filtered_series, labels=labels, showfliers=True)
    ax.set_ylabel("Absolute Percentage Error (%)")
    ax.set_title("APE Distribution by Operation")
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, plots_dir / "ape_distribution_by_operation.png")


def plot_error_vs_size(results, plots_dir):
    fig, ax = plt.subplots(figsize=(8, 5.5))
    by_operation = defaultdict(list)
    for row in results:
        by_operation[row["operation"]].append(row)

    colors = {
        "compress": "#4C78A8",
        "decompress": "#F58518",
        "hash": "#54A24B",
        "encode": "#E45756",
        "decode": "#B279A2",
    }
    for operation, rows in sorted(by_operation.items()):
        ax.scatter(
            [row["size_bytes"] / 1048576.0 for row in rows],
            [row["abs_error_seconds"] for row in rows],
            label=operation,
            alpha=0.85,
            s=38,
            color=colors.get(operation),
        )
    ax.set_xscale("log")
    ax.set_xlabel("Input size (MB)")
    ax.set_ylabel("Absolute error (s)")
    ax.set_title("Absolute Error vs Input Size")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()
    save_figure(fig, plots_dir / "absolute_error_vs_size.png")


def generate_plots(results, operation_summaries, plots_dir):
    plots_dir.mkdir(parents=True, exist_ok=True)
    plot_actual_vs_predicted(results, plots_dir)
    plot_metrics_by_operation(operation_summaries, plots_dir)
    plot_absolute_percentage_error(results, plots_dir)
    plot_error_vs_size(results, plots_dir)


def main():
    args = parse_args()
    repo_dir = Path(__file__).resolve().parent
    real_values_dir = resolve_real_values_dir(
        repo_dir,
        args.real_values_dir,
        hardware_profile=args.hardware_profile,
        machine_datasets_root=args.machine_datasets_root,
    )
    helper_path = build_helper((repo_dir / args.helper) if not args.helper.is_absolute() else args.helper, repo_dir)

    samples = load_all_samples(real_values_dir)
    results = validate_samples(samples, helper_path, real_values_dir, args.mode, args.service_time_model)
    summaries, operation_summaries, overall = summarize(results)
    print_summary_table(summaries, operation_summaries, overall, f"{args.mode}, model={args.service_time_model}")

    if args.out:
        out_path = (repo_dir / args.out).resolve() if not args.out.is_absolute() else args.out.resolve()
        write_results_csv(out_path, results)
        print(f"\nDetailed results written to {out_path}")

    summary_out_path = (repo_dir / args.summary_out).resolve() if not args.summary_out.is_absolute() else args.summary_out.resolve()
    write_summary_csv(summary_out_path, summaries, operation_summaries, overall)
    print(f"Summary written to {summary_out_path}")

    plots_dir = (repo_dir / args.plots_dir).resolve() if not args.plots_dir.is_absolute() else args.plots_dir.resolve()
    generate_plots(results, operation_summaries, plots_dir)
    print(f"Plots written to {plots_dir}")


if __name__ == "__main__":
    main()
