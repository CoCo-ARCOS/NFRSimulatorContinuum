#!/usr/bin/env python3
import argparse
import csv
import json
import shutil
import subprocess
from pathlib import Path


DEFAULT_CONFIGS = [
    "config_distributed_example.json",
    "config_distributed_calibrated.json",
    "config_distributed_calibrated_totalapp.json",
    "config_distributed_calibrated_bfs.json",
    "config_distributed_calibrated_bfs_v2.json",
]

DEFAULT_SIMULATOR_DIRS = [
    "proxy_dd",
    "proxy_dd_interpolation_only",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the simulator for several configs and compare each run against the real pipeline."
    )
    parser.add_argument(
        "--simulator-dirs",
        nargs="+",
        default=DEFAULT_SIMULATOR_DIRS,
        help="Simulator directories to evaluate.",
    )
    parser.add_argument(
        "--real-results",
        type=Path,
        default=Path("real_pipeline_reference/results"),
        help="Real pipeline results directory.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("proxy_dd/calibration_runs"),
        help="Directory where run outputs and comparisons will be stored.",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path("./main"),
        help="Simulator binary path, relative to each simulator directory unless absolute.",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=DEFAULT_CONFIGS,
        help="Config files to evaluate, relative to --proxy-dir unless absolute.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Worker count to pass to the comparison script.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip `make` before running the sweep.",
    )
    return parser.parse_args()


def resolve_under(base: Path, maybe_relative: str | Path):
    path = Path(maybe_relative)
    return path if path.is_absolute() else (base / path).resolve()


def run_command(command, cwd: Path, stdout_path: Path | None = None, stderr_path: Path | None = None):
    stdout_handle = stdout_path.open("w", encoding="utf-8") if stdout_path else subprocess.DEVNULL
    stderr_handle = stderr_path.open("w", encoding="utf-8") if stderr_path else subprocess.DEVNULL
    try:
        subprocess.run(command, cwd=cwd, check=True, stdout=stdout_handle, stderr=stderr_handle)
    finally:
        if stdout_path:
            stdout_handle.close()
        if stderr_path:
            stderr_handle.close()


def copy_results(source: Path, destination: Path):
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def comparison_overview(run_dir: Path):
    overview = {}
    for mode in ("queue", "service"):
        overall_path = run_dir / f"comparison_{mode}" / "overall_comparison.json"
        if overall_path.exists():
            overview[mode] = json.loads(overall_path.read_text(encoding="utf-8"))
    return overview


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


def max_requirement_index(row, prefix):
    index = 1
    while f"real_{prefix}_requirement_{index}" in row or f"sim_{prefix}_requirement_{index}" in row:
        index += 1
    return index - 1


def summarize_requirements_from_service_csv(path: Path):
    rows = read_csv_rows(path)
    aggregated = {}
    for row in rows:
        stage_name = row["stage_name"]
        for prefix in ("input", "output"):
            for index in range(1, max_requirement_index(row, prefix) + 1):
                real_label = row.get(f"real_{prefix}_requirement_{index}", "")
                sim_label = row.get(f"sim_{prefix}_requirement_{index}", "")
                label = real_label or sim_label
                if not label:
                    continue
                key = (stage_name, prefix, label)
                entry = aggregated.setdefault(
                    key,
                    {
                        "stage_name": stage_name,
                        "direction": prefix,
                        "requirement": label,
                        "real_seconds": 0.0,
                        "sim_seconds": 0.0,
                    },
                )
                entry["real_seconds"] += float_value(row, f"real_{prefix}_requirement_{index}_seconds")
                entry["sim_seconds"] += float_value(row, f"sim_{prefix}_requirement_{index}_seconds")

    summary_rows = []
    for key in sorted(aggregated):
        entry = aggregated[key]
        error = entry["sim_seconds"] - entry["real_seconds"]
        ape = abs(error) / entry["real_seconds"] * 100.0 if entry["real_seconds"] > 0 else 0.0
        summary_rows.append(
            {
                **entry,
                "error_seconds": error,
                "ape_percent": ape,
            }
        )
    return summary_rows


def write_requirement_run_summary(run_dir: Path, requirement_rows):
    csv_path = run_dir / "requirement_comparison.csv"
    md_path = run_dir / "requirement_comparison.md"
    fieldnames = [
        "stage_name",
        "direction",
        "requirement",
        "real_seconds",
        "sim_seconds",
        "error_seconds",
        "ape_percent",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(requirement_rows)

    lines = [
        "# Requirement Comparison",
        "",
        "| Stage | Direction | Requirement | Real (s) | Simulator (s) | Error (s) | APE (%) |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in requirement_rows:
        lines.append(
            f"| {row['stage_name']} | {row['direction']} | {row['requirement']} | "
            f"{row['real_seconds']:.6f} | {row['sim_seconds']:.6f} | "
            f"{row['error_seconds']:.6f} | {row['ape_percent']:.2f} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_global_requirement_summary(out_dir: Path, run_summaries):
    rows = []
    for summary in run_summaries:
        run_dir = Path(summary["run_dir"])
        requirement_csv = run_dir / "requirement_comparison.csv"
        if not requirement_csv.exists():
            continue
        for row in read_csv_rows(requirement_csv):
            rows.append(
                {
                    "variant": summary["variant"],
                    "config": summary["config"],
                    **row,
                }
            )

    fieldnames = [
        "variant",
        "config",
        "stage_name",
        "direction",
        "requirement",
        "real_seconds",
        "sim_seconds",
        "error_seconds",
        "ape_percent",
    ]
    with (out_dir / "requirements_summary.csv").open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Requirement-Level Comparison",
        "",
        "| Variant | Config | Stage | Direction | Requirement | Real (s) | Simulator (s) | Error (s) | APE (%) |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['variant']} | {row['config']} | {row['stage_name']} | {row['direction']} | {row['requirement']} | "
            f"{float(row['real_seconds']):.6f} | {float(row['sim_seconds']):.6f} | "
            f"{float(row['error_seconds']):.6f} | {float(row['ape_percent']):.2f} |"
        )
    (out_dir / "requirements_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(out_dir: Path, run_summaries):
    payload = {"runs": run_summaries}
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Calibration Sweep",
        "",
        "| Variant | Config | Queue APE (%) | Service Total APE (%) | Run Dir |",
        "|---|---|---:|---:|---|",
    ]
    for summary in run_summaries:
        queue_ape = summary.get("comparisons", {}).get("queue", {}).get("ape_stage_elapsed_percent", 0.0)
        service_ape = summary.get("comparisons", {}).get("service", {}).get("ape_total_seconds_percent", 0.0)
        lines.append(
            f"| {summary['variant']} | {summary['config']} | {queue_ape:.2f} | {service_ape:.2f} | `{summary['run_dir']}` |"
        )
    (out_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_global_requirement_summary(out_dir, run_summaries)


def variant_name(simulator_dir: Path):
    name = simulator_dir.name
    if name == "proxy_dd":
        return "queue_model"
    if name == "proxy_dd_interpolation_only":
        return "interpolation_only"
    return name


def main():
    args = parse_args()
    simulator_dirs = [Path(path).resolve() for path in args.simulator_dirs]
    real_results = args.real_results.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / "real_pipeline_reference").exists() and repo_root.name == "benchmarks":
        repo_root = repo_root.parent
    compare_script = (repo_root / "real_pipeline_reference" / "compare_real_pipeline_vs_simulator.py").resolve()

    run_summaries = []
    for simulator_dir in simulator_dirs:
        binary = args.binary if args.binary.is_absolute() else (simulator_dir / args.binary).resolve()
        results_dir = (simulator_dir / "results").resolve()
        variant = variant_name(simulator_dir)

        if not args.skip_build:
            run_command(["make"], cwd=simulator_dir)

        for config_arg in args.configs:
            config_path = resolve_under(simulator_dir, config_arg)
            config_name = config_path.stem
            run_dir = out_dir / f"{variant}__{config_name}"
            run_dir.mkdir(parents=True, exist_ok=True)

            if results_dir.exists():
                shutil.rmtree(results_dir)
            results_dir.mkdir(parents=True, exist_ok=True)

            run_command(
                [
                    str(binary),
                    config_path.name if config_path.parent == simulator_dir else str(config_path),
                ],
                cwd=simulator_dir,
                stdout_path=run_dir / "sim_stdout.txt",
                stderr_path=run_dir / "sim_stderr.txt",
            )

            simulator_results_dir = run_dir / "simulator_results"
            copy_results(results_dir, simulator_results_dir)

            for mode in ("queue", "service"):
                comparison_dir = run_dir / f"comparison_{mode}"
                run_command(
                    [
                        "python3",
                        str(compare_script),
                        "--real-results",
                        str(real_results),
                        "--simulator-results",
                        str(simulator_results_dir),
                        "--workers",
                        str(args.workers),
                        "--mode",
                        mode,
                        "--out-dir",
                        str(comparison_dir),
                    ],
                    cwd=repo_root,
                )

            requirement_rows = summarize_requirements_from_service_csv(
                run_dir / "comparison_service" / "stage_time_comparison.csv"
            )
            write_requirement_run_summary(run_dir, requirement_rows)

            run_summaries.append(
                {
                    "variant": variant,
                    "config": config_path.name,
                    "run_dir": str(run_dir),
                    "comparisons": comparison_overview(run_dir),
                }
            )

    write_index(out_dir, run_summaries)
    print(f"Calibration sweep written to {out_dir}")


if __name__ == "__main__":
    main()
