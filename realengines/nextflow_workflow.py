#!/usr/bin/env python3
"""Nextflow adapter: drives ``nextflow/main.nf`` and collects its measurements.

Unlike the Python-native engines, Nextflow enforces the plan out of process
through ``nfr_apply.py``. The mechanism trace is therefore reconstructed from
the enforcement log rather than from in-process step records, which is the
point: an engine that never imports our Python API still produces a trace the
analysis can compare against the others.

Wall time here covers the whole Nextflow run, so it includes the engine's own
scheduling and staging overhead. That overhead is reported separately from the
mechanism time taken from the enforcement log.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import measure, probe, site_power_w  # noqa: E402
from nfr_plan import load_plan  # noqa: E402

ENGINE = "nextflow"
HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "nextflow" / "main.nf"
APPLY = HERE / "nfr_apply.py"


def read_enforcement_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def trace_from_log(records: list[dict[str, Any]]) -> str:
    """Rebuild the canonical mechanism trace from output-direction records.

    Only the output direction is used so the trace matches the in-process
    engines, which record mechanisms when they are applied, not when inverted.
    """
    parts = []
    for record in records:
        if record.get("direction") != "output":
            continue
        for mechanism in record.get("mechanisms", []):
            parts.append(
                f"{record['artifact_class']}:{mechanism['slot']}={mechanism['algorithm']}"
            )
    return "|".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--payload-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--site", type=Path, default=None)
    parser.add_argument("--machine", default="")
    parser.add_argument("--model-power-w", type=float, default=None)
    parser.add_argument("--nextflow", default="nextflow", help="Nextflow executable")
    args = parser.parse_args()

    if shutil.which(args.nextflow) is None:
        print(f"[{ENGINE}] '{args.nextflow}' not found on PATH; skipping", file=sys.stderr)
        return 3

    args.output.mkdir(parents=True, exist_ok=True)
    plan = load_plan(args.plan)

    model_power_w = args.model_power_w
    if model_power_w is None and args.site and args.machine:
        model_power_w = site_power_w(args.site, args.machine)

    passes = []
    for index in range(max(1, args.repeats)):
        work_dir = args.output / f"pass{index}"
        work_dir.mkdir(parents=True, exist_ok=True)
        log_path = work_dir / "enforcement_log.jsonl"
        command = [
            args.nextflow, "run", str(PIPELINE),
            "--plan", str(Path(args.plan).resolve()),
            "--apply", str(APPLY.resolve()),
            "--payload_bytes", str(args.payload_bytes),
            "--outdir", str((work_dir / "results").resolve()),
            "--log", str(log_path.resolve()),
            "-work-dir", str((work_dir / "work").resolve()),
        ]
        with measure(model_power_w=model_power_w) as m:
            completed = subprocess.run(
                command, cwd=str(work_dir), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
        (work_dir / "nextflow.log").write_text(completed.stdout, encoding="utf-8")
        if completed.returncode != 0:
            print(f"[{ENGINE}] run failed (exit {completed.returncode}); "
                  f"see {work_dir / 'nextflow.log'}", file=sys.stderr)
            return 1

        records = read_enforcement_log(log_path)
        passes.append({
            "pass": index,
            "warmup": index == 0 and args.repeats > 1,
            "total_seconds": m["seconds"],
            "total_energy_j": m["energy_j"],
            "energy_source": m["energy_source"],
            "nfr_seconds": sum(float(r.get("seconds", 0.0)) for r in records),
            "mechanism_trace": trace_from_log(records),
            "enforcement_records": len(records),
            "integrity_verified": any(r.get("direction") == "input" for r in records),
        })

    measured = [p for p in passes if not p["warmup"]] or passes
    seconds = [p["total_seconds"] for p in measured]
    energies = [p["total_energy_j"] for p in measured if p["total_energy_j"] is not None]
    traces = {p["mechanism_trace"] for p in measured}

    selection = plan.get("selection", {})
    predicted = selection.get("predicted", {})
    run = {
        "engine": ENGINE,
        "host": platform.node(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plan": str(args.plan),
        "payload_bytes": args.payload_bytes,
        "repeats": args.repeats,
        "energy_probe": probe(),
        "model_power_w": model_power_w,
        "selection": {
            "method": selection.get("method"),
            "candidate_id": selection.get("candidate_id"),
            "label": selection.get("label", ""),
            "coverage_weighted": predicted.get("coverage_weighted"),
        },
        "predicted": {
            "energy_j": predicted.get("energy_j"),
            "makespan_s": predicted.get("makespan_s"),
        },
        "budgets": plan.get("budgets", {}),
        "measured": {
            "seconds_mean": sum(seconds) / len(seconds),
            "seconds_min": min(seconds),
            "seconds_max": max(seconds),
            "energy_j_mean": (sum(energies) / len(energies)) if energies else None,
            "nfr_seconds_mean": sum(p["nfr_seconds"] for p in measured) / len(measured),
            "energy_source": measured[0]["energy_source"],
        },
        "mechanism_trace": sorted(traces)[0] if len(traces) == 1 else "INCONSISTENT",
        "mechanism_trace_stable": len(traces) == 1,
        "integrity_verified": all(p["integrity_verified"] for p in measured),
        "passes": passes,
    }

    result_path = args.output / "run_result.json"
    with result_path.open("w", encoding="utf-8") as handle:
        json.dump(run, handle, indent=2)
        handle.write("\n")

    print(f"[{ENGINE}] {run['measured']['seconds_mean']:.4f} s "
          f"({run['measured']['nfr_seconds_mean']:.4f} s in mechanisms)")
    print(f"[{ENGINE}] mechanisms: {run['mechanism_trace']}")
    print(f"[{ENGINE}] wrote {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
