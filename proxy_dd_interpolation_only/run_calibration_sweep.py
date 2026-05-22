#!/usr/bin/env python3
import argparse
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the simulator for several configs and compare each run against the real pipeline."
    )
    parser.add_argument(
        "--proxy-dir",
        type=Path,
        default=Path("proxy_dd"),
        help="Simulator directory containing main, results, and config files.",
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
        help="Simulator binary path, relative to --proxy-dir unless absolute.",
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


def write_index(out_dir: Path, run_summaries):
    payload = {"runs": run_summaries}
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Calibration Sweep",
        "",
        "| Config | Queue APE (%) | Service Total APE (%) | Run Dir |",
        "|---|---:|---:|---|",
    ]
    for summary in run_summaries:
        queue_ape = summary.get("comparisons", {}).get("queue", {}).get("ape_stage_elapsed_percent", 0.0)
        service_ape = summary.get("comparisons", {}).get("service", {}).get("ape_total_seconds_percent", 0.0)
        lines.append(
            f"| {summary['config']} | {queue_ape:.2f} | {service_ape:.2f} | `{summary['run_dir']}` |"
        )
    (out_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    args = parse_args()
    proxy_dir = args.proxy_dir.resolve()
    real_results = args.real_results.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    binary = args.binary if args.binary.is_absolute() else (proxy_dir / args.binary).resolve()
    compare_script = (proxy_dir.parent / "real_pipeline_reference" / "compare_real_pipeline_vs_simulator.py").resolve()
    results_dir = (proxy_dir / "results").resolve()

    if not args.skip_build:
        run_command(["make"], cwd=proxy_dir)

    run_summaries = []
    for config_arg in args.configs:
        config_path = resolve_under(proxy_dir, config_arg)
        config_name = config_path.stem
        run_dir = out_dir / config_name
        run_dir.mkdir(parents=True, exist_ok=True)

        if results_dir.exists():
            shutil.rmtree(results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)

        run_command(
            [str(binary), config_path.name if config_path.parent == proxy_dir else str(config_path)],
            cwd=proxy_dir,
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
                cwd=proxy_dir.parent,
            )

        run_summaries.append(
            {
                "config": config_path.name,
                "run_dir": str(run_dir),
                "comparisons": comparison_overview(run_dir),
            }
        )

    write_index(out_dir, run_summaries)
    print(f"Calibration sweep written to {out_dir}")


if __name__ == "__main__":
    main()
