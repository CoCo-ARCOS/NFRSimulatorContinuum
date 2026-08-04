#!/usr/bin/env python3
"""Run robust profiler catalogs from a manifest."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
from pathlib import Path


def load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def run_one(scenario, args):
    outdir = args.output / "catalogs" / scenario["id"]
    report = outdir / "profiler_report.json"
    if report.exists() and not args.force:
        return scenario["id"], 0, "skipped"
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(args.profiler),
        "--request", scenario["request"],
        "--simulator-cmd", str(args.simulator),
        "--simulator-dir", str(args.simulator_dir),
        "--output-dir", str(outdir),
        "--replications", str(args.replications),
        "--timeout", str(args.timeout),
        "--max-candidates", str(args.max_candidates),
        "--skip-attribution",
        "--keep-runs",
    ]
    if args.plot:
        cmd.append("--plot")
    log_path = outdir / "run.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
    return scenario["id"], proc.returncode, "ok" if proc.returncode == 0 else f"failed: {log_path}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--profiler", required=True, type=Path)
    ap.add_argument("--simulator", required=True, type=Path)
    ap.add_argument("--simulator-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--replications", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--max-candidates", type=int, default=5000)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="Run only first two scenarios with one replication")
    args = ap.parse_args()

    manifest = load(args.manifest)
    scenarios = manifest["scenarios"][:2] if args.smoke else manifest["scenarios"]
    if args.smoke:
        args.replications = 1
    args.output.mkdir(parents=True, exist_ok=True)

    failures = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = [ex.submit(run_one, sc, args) for sc in scenarios]
        for fut in cf.as_completed(futs):
            sid, rc, msg = fut.result()
            print(f"{sid}: {msg}")
            if rc != 0:
                failures.append((sid, msg))
    if failures:
        print("Failures:")
        for sid, msg in failures:
            print(f"  {sid}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
