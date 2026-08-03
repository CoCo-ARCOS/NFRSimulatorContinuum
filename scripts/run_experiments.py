#!/usr/bin/env python3
"""Run generated evaluation scenarios and record reproducible run metadata."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shlex
import subprocess
import time
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


def select_scenarios(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    scenarios = manifest["scenarios"]
    if args.kind:
        allowed = set(args.kind)
        scenarios = [scenario for scenario in scenarios if scenario["kind"] in allowed]
    if args.scenario:
        selected = set(args.scenario)
        scenarios = [scenario for scenario in scenarios if scenario["scenario_id"] in selected]
    if args.smoke:
        core = next((scenario for scenario in scenarios if scenario["kind"] == "core"), None)
        scale = next((scenario for scenario in scenarios if scenario["kind"] == "scalability" and scenario.get("policy_groups") == 2), None)
        scenarios = [scenario for scenario in (core, scale) if scenario is not None]
    return scenarios


def run_one(
    scenario: dict[str, Any],
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    scenario_id = scenario["scenario_id"]
    output_dir = args.output / scenario_id
    report_path = output_dir / "profiler_report.json"
    record_path = output_dir / "run_record.json"
    if report_path.exists() and not args.force:
        record = load_json(record_path) if record_path.exists() else {}
        record.update({"scenario_id": scenario_id, "status": "skipped-existing"})
        return record

    output_dir.mkdir(parents=True, exist_ok=True)
    replications = 1 if args.smoke else (args.replications if args.replications is not None else int(scenario["replications"]))
    command = [
        str(args.profiler.resolve()),
        "--request", str(Path(scenario["request"]).resolve()),
        "--simulator-cmd", str(args.simulator.resolve()),
        "--simulator-dir", str(args.simulator_dir.resolve()),
        "--output-dir", str(output_dir.resolve()),
        "--replications", str(replications),
        "--base-seed", str(int(manifest["base_seed"])),
        "--timeout", str(float(manifest["timeout_s"])),
        "--max-candidates", str(int(scenario["max_candidates"])),
        "--skip-attribution",
    ]
    if args.keep_runs:
        command.append("--keep-runs")

    environment = os.environ.copy()
    profiler_parent = str(args.profiler.resolve().parent)
    environment["PYTHONPATH"] = profiler_parent + os.pathsep + environment.get("PYTHONPATH", "")

    started = time.time()
    completed = subprocess.run(
        command,
        cwd=args.simulator_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=environment,
    )
    elapsed = time.time() - started
    (output_dir / "stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (output_dir / "stderr.log").write_text(completed.stderr or "", encoding="utf-8")

    record: dict[str, Any] = {
        "scenario_id": scenario_id,
        "kind": scenario["kind"],
        "request": str(Path(scenario["request"]).resolve()),
        "output_dir": str(output_dir.resolve()),
        "command": shlex.join(command),
        "replications": replications,
        "started_unix_s": started,
        "elapsed_wall_s": elapsed,
        "returncode": completed.returncode,
        "status": "ok" if completed.returncode == 0 and report_path.exists() else "failed",
    }
    if report_path.exists():
        report = load_json(report_path)
        record["search_space"] = report.get("search_space", {})
        record["recommended_id"] = report.get("recommended_id")
        record["candidate_count"] = len(report.get("candidates", []))
    write_json(record_path, record)
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profiler", type=Path, required=True)
    parser.add_argument("--simulator", type=Path, required=True)
    parser.add_argument("--simulator-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--replications", type=int, help="Override the manifest replication count")
    parser.add_argument("--kind", action="append", help="Run only a scenario kind; may be repeated")
    parser.add_argument("--scenario", action="append", help="Run only an exact scenario id; may be repeated")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-runs", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Run one core and one small scalability scenario with one replication")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    if args.replications is not None and args.replications < 1:
        parser.error("--replications must be >= 1")
    if not args.profiler.exists():
        parser.error(f"profiler not found: {args.profiler}")
    if not args.simulator.exists():
        parser.error(f"simulator not found: {args.simulator}")
    return args


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    scenarios = select_scenarios(manifest, args)
    if not scenarios:
        print("No scenarios selected")
        return 2
    args.output.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    if args.jobs == 1:
        for index, scenario in enumerate(scenarios, 1):
            print(f"[{index}/{len(scenarios)}] {scenario['scenario_id']}")
            record = run_one(scenario, manifest, args)
            print(f"  {record['status']} in {record.get('elapsed_wall_s', 0.0):.2f}s")
            records.append(record)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(run_one, scenario, manifest, args): scenario
                for scenario in scenarios
            }
            for future in concurrent.futures.as_completed(futures):
                scenario = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - preserve failure in run manifest
                    record = {
                        "scenario_id": scenario["scenario_id"],
                        "kind": scenario["kind"],
                        "status": "runner-exception",
                        "error": str(exc),
                    }
                print(f"{record['scenario_id']}: {record['status']}")
                records.append(record)

    records.sort(key=lambda item: item["scenario_id"])
    summary = {
        "manifest": str(args.manifest.resolve()),
        "profiler": str(args.profiler.resolve()),
        "simulator": str(args.simulator.resolve()),
        "records": records,
    }
    write_json(args.output / "run_manifest.json", summary)
    failures = [record for record in records if record.get("status") not in {"ok", "skipped-existing"}]
    print(f"Completed {len(records)} scenarios; failures={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
