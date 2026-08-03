#!/usr/bin/env python3
"""Validate mean- and UCB-selected plans on independent holdout seeds."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def config_fingerprint(config: dict[str, Any]) -> str:
    canonical = json.loads(json.dumps(config))
    canonical["seed"] = 0
    canonical["config_hash"] = ""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_simulator(simulator: Path, simulator_dir: Path, config: dict[str, Any], run_dir: Path, timeout: float) -> dict[str, Any]:
    if run_dir.exists():
        summary_path = run_dir / "run_summary.json"
        if summary_path.exists():
            return load_json(summary_path)
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    write_json(config_path, config)
    completed = subprocess.run(
        [str(simulator.resolve()), str(config_path.resolve()), "--output-dir", str(run_dir.resolve())],
        cwd=simulator_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    (run_dir / "stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    summary_path = run_dir / "run_summary.json"
    if completed.returncode != 0 or not summary_path.exists():
        raise RuntimeError((completed.stderr or completed.stdout or "simulator failed").strip())
    summary = load_json(summary_path)
    if summary.get("status") != "ok":
        raise RuntimeError(summary.get("error", "simulator failed"))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--selected-configs", type=Path, required=True)
    parser.add_argument("--simulator", type=Path, required=True)
    parser.add_argument("--simulator-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replications", type=int)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    selected = load_json(args.selected_configs).get("selected", [])
    holdout = manifest.get("holdout", {})
    replications = args.replications or int(holdout.get("replications", 30))
    if args.smoke:
        replications = min(replications, 3)
        selected = selected[:2]
    base_seed = int(holdout.get("base_seed", 9100001))
    args.output.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for selected_index, item in enumerate(selected):
        base_config = item.get("simulator_config")
        if not isinstance(base_config, dict):
            failures.append({"item": selected_index, "error": "selected candidate has no simulator_config"})
            continue
        fingerprint = config_fingerprint(base_config)
        for replication in range(replications):
            seed = base_seed + replication * 104729
            config = json.loads(json.dumps(base_config))
            config["seed"] = seed
            config["config_hash"] = fingerprint
            run_dir = args.output / "runs" / fingerprint / str(seed)
            try:
                summary = run_simulator(args.simulator, args.simulator_dir, config, run_dir, args.timeout)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                failures.append({"item": selected_index, "seed": seed, "error": str(exc)})
                continue
            energy = float(summary["total_energy_j"])
            makespan = float(summary["makespan_s"])
            budget = float(item["energy_budget_j"])
            deadline = float(item["deadline_s"])
            rows.append({
                "workflow_id": item["workflow_id"],
                "contract_id": item["contract_id"],
                "scale_id": item["scale_id"],
                "risk_mode": item["risk_mode"],
                "candidate_id": item["candidate_id"],
                "plan_id": item["plan_id"],
                "seed": seed,
                "energy_budget_j": budget,
                "deadline_s": deadline,
                "energy_j": energy,
                "makespan_s": makespan,
                "energy_violation": int(energy > budget),
                "deadline_violation": int(makespan > deadline),
                "joint_violation": int(energy > budget or makespan > deadline),
            })

    summary_rows: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["workflow_id"], row["contract_id"], row["scale_id"], row["risk_mode"])].append(row)
    for key, values in sorted(grouped.items()):
        workflow_id, contract_id, scale_id, risk_mode = key
        summary_rows.append({
            "workflow_id": workflow_id,
            "contract_id": contract_id,
            "scale_id": scale_id,
            "risk_mode": risk_mode,
            "replications": len(values),
            "energy_violation_rate": sum(row["energy_violation"] for row in values) / len(values),
            "deadline_violation_rate": sum(row["deadline_violation"] for row in values) / len(values),
            "joint_violation_rate": sum(row["joint_violation"] for row in values) / len(values),
            "mean_energy_ratio": sum(row["energy_j"] / row["energy_budget_j"] for row in values) / len(values),
            "mean_deadline_ratio": sum(row["makespan_s"] / row["deadline_s"] for row in values) / len(values),
        })

    write_csv(args.output / "holdout_runs.csv", rows)
    write_csv(args.output / "holdout_summary.csv", summary_rows)
    write_json(args.output / "holdout_failures.json", {"failures": failures})

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None
    if plt is not None and summary_rows:
        modes = sorted({row["risk_mode"] for row in summary_rows})
        values = [
            sum(row["joint_violation_rate"] for row in summary_rows if row["risk_mode"] == mode)
            / sum(1 for row in summary_rows if row["risk_mode"] == mode)
            for mode in modes
        ]
        fig, ax = plt.subplots()
        ax.bar(range(len(modes)), values)
        ax.set_xticks(range(len(modes)), modes)
        ax.set_ylabel("Mean holdout violation rate")
        ax.set_ylim(0.0, 1.05)
        fig.tight_layout()
        fig.savefig(args.output / "holdout_violation_rate.pdf")
        fig.savefig(args.output / "holdout_violation_rate.png", dpi=200)
        plt.close(fig)

    print(f"Holdout validation wrote {len(rows)} runs; failures={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
