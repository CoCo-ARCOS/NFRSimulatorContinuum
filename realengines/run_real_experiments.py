#!/usr/bin/env python3
"""Drive the real-engine experiments.

For every (contract profile, budget level) the tuner resolves a realization
plan, and every available engine then executes that same plan. Three claims
fall out of the resulting matrix:

  E1 portability  one plan, several engines, identical mechanism traces;
  E2 fidelity     predicted cost against measured cost;
  E3 adaptivity   budget level steering which realization actually runs.

Catalogs are cached per request by the tuner, so re-running with more engines
or more repeats does not re-profile. Engines that are not installed are
recorded as skipped rather than failing the sweep, which keeps a partial run
on a cluster node useful.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from nfr_plan import write_plan  # noqa: E402
from nfr_tuner import resolve_calibration_paths, resolve_realization  # noqa: E402

# Contract profiles as family-level requirement overrides on the base request.
# These mirror the profiles of the offline evaluation so the two are comparable.
CONTRACT_PROFILES: dict[str, dict[str, Any]] = {
    "balanced": {},
    "security-first": {
        "confidentiality": {"level": "required", "weight": 4.0, "min_strength": 2},
        "integrity": {"level": "required", "weight": 3.0},
    },
    "resilience-first": {
        "reliability": {"level": "required", "weight": 4.0, "min_strength": 2},
        "integrity": {"level": "required", "weight": 2.0},
    },
    "bandwidth-first": {
        "compression": {"level": "required", "weight": 4.0},
    },
}

DEFAULT_BUDGETS = [1.05, 1.30, 2.00]

ENGINES = {
    "parsl": HERE / "parsl_workflow.py",
    "dagonstar": HERE / "dagonstar_workflow.py",
    "nextflow": HERE / "nextflow_workflow.py",
}


def load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def request_for_profile(base: dict[str, Any], profile: str) -> dict[str, Any]:
    """Apply a contract profile's overrides to the base request."""
    request = copy.deepcopy(base)
    requirements = request.setdefault("requirements", {})
    for family, overrides in CONTRACT_PROFILES[profile].items():
        requirements.setdefault(family, {}).update(overrides)
    return request


def run_engine(engine: str, script: Path, plan_path: Path, run_dir: Path,
               args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable, str(script),
        "--plan", str(plan_path),
        "--output", str(run_dir),
        "--payload-bytes", str(args.payload_bytes),
        "--repeats", str(args.repeats),
    ]
    if args.min_seconds:
        command += ["--min-seconds", str(args.min_seconds)]
    command += ["--payload-kind", args.payload_kind,
                "--payload-ratio", str(args.payload_ratio),
                "--payload-seed", str(args.payload_seed)]
    if args.payload_source:
        command += ["--payload-source", str(Path(args.payload_source).resolve())]
    if args.site:
        command += ["--site", str(args.site)]
    if args.machine:
        command += ["--machine", args.machine]
    if args.model_power_w is not None:
        command += ["--model-power-w", str(args.model_power_w)]
    if engine == "nextflow" and args.nextflow:
        nextflow = Path(args.nextflow)
        if nextflow.exists():
            nextflow = nextflow.resolve()
        command += ["--nextflow", str(nextflow)]

    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "engine.log"
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command, cwd=str(REPO_ROOT), stdout=log,
            stderr=subprocess.STDOUT, text=True, timeout=args.timeout,
        )
    result_path = run_dir / "run_result.json"
    if completed.returncode == 3:
        return {"engine": engine, "status": "skipped", "reason": "engine not installed"}
    if completed.returncode != 0 or not result_path.exists():
        return {
            "engine": engine, "status": "failed",
            "reason": f"exit {completed.returncode}; see {log_path}",
        }
    result = load_json(result_path)
    result["status"] = "ok"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", type=Path, default=HERE / "demo_request.json")
    parser.add_argument("--simulator", type=Path, default=REPO_ROOT / "proxy_dd" / "nfr_dag_sim")
    parser.add_argument("--simulator-dir", type=Path, default=REPO_ROOT / "proxy_dd")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "realengines-output")
    parser.add_argument("--engines", default="parsl,dagonstar,nextflow",
                        help="Comma-separated subset of: " + ", ".join(ENGINES))
    parser.add_argument("--profiles", default=",".join(CONTRACT_PROFILES))
    parser.add_argument("--budgets", default=",".join(str(b) for b in DEFAULT_BUDGETS),
                        help="Budget multipliers applied to energy and deadline")
    parser.add_argument("--payload-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--payload-kind", choices=("synthetic", "random", "file"),
                        default="synthetic",
                        help="Payload realism; 'random' is incompressible and "
                             "makes volume-reduction clauses unachievable")
    parser.add_argument("--payload-ratio", type=float, default=3.0,
                        help="Target compression ratio for --payload-kind synthetic")
    parser.add_argument("--payload-seed", type=int, default=0)
    parser.add_argument("--payload-source", type=Path, default=None)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--min-seconds", type=float, default=0.0,
                        help="Extend each engine run to at least this duration, so "
                             "coarse energy accounting has several samples to "
                             "attribute")
    parser.add_argument("--replications", type=int, default=3,
                        help="Simulator replications when profiling a catalog")
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--site", type=Path, default=None)
    parser.add_argument("--machine", default="")
    parser.add_argument("--model-power-w", type=float, default=None)
    parser.add_argument("--nextflow", default=os.environ.get("NEXTFLOW", ""),
                        help="Nextflow executable; point this at a self-contained "
                             "bundle from fetch_nextflow.sh on an offline cluster")
    parser.add_argument("--force", action="store_true", help="Re-profile cached catalogs")
    args = parser.parse_args()

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in ENGINES]
    if unknown:
        parser.error(f"unknown engine(s): {', '.join(unknown)}")
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    budgets = [float(b) for b in args.budgets.split(",") if b.strip()]

    base_request = load_json(args.request)
    output = Path(args.output)
    (output / "requests").mkdir(parents=True, exist_ok=True)

    # Fail before the sweep rather than once per configuration: a missing
    # simulator or missing calibration data would otherwise surface as twelve
    # identical "no feasible realization" lines.
    if not Path(args.simulator).exists():
        parser.error(f"simulator not found at {args.simulator}; build it with "
                     f"'make -C {args.simulator_dir}'")
    try:
        resolve_calibration_paths(Path(args.request), output)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    records: list[dict[str, Any]] = []
    started = time.time()
    for profile in profiles:
        request = request_for_profile(base_request, profile)
        request_path = output / "requests" / f"{profile}.json"
        with request_path.open("w", encoding="utf-8") as handle:
            json.dump(request, handle, indent=2)
            handle.write("\n")

        for budget in budgets:
            tag = f"{profile}__b{budget:g}"
            try:
                plan = resolve_realization(
                    request_path, simulator=args.simulator,
                    simulator_dir=args.simulator_dir,
                    output_dir=output / "tuner",
                    energy_multiplier=budget, deadline_multiplier=budget,
                    replications=args.replications, max_candidates=args.max_candidates,
                    force=args.force,
                )
            except RuntimeError as exc:
                # An infeasible budget is a result, not an error: record the
                # rejection and continue with the rest of the sweep.
                print(f"{tag}: no feasible realization ({exc})")
                records.append({
                    "profile": profile, "budget_multiplier": budget,
                    "engine": "", "status": "rejected", "reason": str(exc),
                })
                continue

            plan_path = output / "plans" / f"{tag}.json"
            write_plan(plan_path, plan)
            print(f"{tag}: {plan['selection']['label']}")

            for engine in engines:
                run_dir = output / "runs" / tag / engine
                result = run_engine(engine, ENGINES[engine], plan_path, run_dir, args)
                result.update({"profile": profile, "budget_multiplier": budget})
                records.append(result)
                if result["status"] != "ok":
                    print(f"  {engine}: {result['status']} ({result.get('reason', '')})")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": time.time() - started,
        "request": str(args.request),
        "engines": engines,
        "profiles": profiles,
        "budgets": budgets,
        "payload_bytes": args.payload_bytes,
        "repeats": args.repeats,
        "records": records,
    }
    summary_path = output / "real_engine_runs.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")

    ok = sum(1 for r in records if r.get("status") == "ok")
    print(f"\n{ok}/{len(records)} runs succeeded; wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
