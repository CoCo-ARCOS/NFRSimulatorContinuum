#!/usr/bin/env python3
"""Common command-line surface for the engine adapters.

Every adapter accepts the same arguments and writes the same ``run_result.json``,
so the experiment driver can treat engines interchangeably and the analysis can
compare them without per-engine special cases.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import measure, probe, site_power_w  # noqa: E402
from nfr_plan import load_plan  # noqa: E402
from process_engine import (  # noqa: E402
    add_common_arguments,
    keep_going,
    payload_provenance,
)
from workload import run_workload  # noqa: E402


def parse_args(engine: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Run the NFR workload under {engine}")
    add_common_arguments(parser)
    # In-process engines apply mechanisms directly, so they alone need the key.
    parser.add_argument("--hmac-key-hex", default="")
    return parser.parse_args()


def execute(engine: str, args: argparse.Namespace,
            submit: Callable[[Callable[..., Any]], Any] | None = None) -> dict[str, Any]:
    """Run the workload ``repeats`` times and summarise the measured passes."""
    plan = load_plan(args.plan)
    hmac_key = bytes.fromhex(args.hmac_key_hex) if args.hmac_key_hex else b""

    model_power_w = args.model_power_w
    if model_power_w is None and args.site and args.machine:
        model_power_w = site_power_w(args.site, args.machine)

    passes = []
    # The whole set of passes is bracketed as one region: node-level accounting
    # is far too coarse per stage, but attributable across a long enough run.
    with measure(model_power_w=model_power_w, allow_slurm=True) as run_energy:
        index = 0
        elapsed = 0.0
        while keep_going(index, elapsed, args):
            result = run_workload(
                plan, args.payload_bytes, hmac_key=hmac_key,
                model_power_w=model_power_w, submit=submit,
                payload_kind=args.payload_kind, payload_ratio=args.payload_ratio,
                payload_seed=args.payload_seed, payload_source=args.payload_source,
                objects=args.objects,
            )
            result["pass"] = index
            result["warmup"] = index == 0 and args.repeats > 1
            passes.append(result)
            elapsed += result["total_seconds"]
            index += 1

    measured = [p for p in passes if not p["warmup"]] or passes
    seconds = [p["total_seconds"] for p in measured]
    energies = [p["total_energy_j"] for p in measured if p["total_energy_j"] is not None]
    traces = {p["mechanism_trace"] for p in measured}

    # Per-pass energy from the run-level region, apportioned over the measured
    # passes by their share of the total time.
    run_energy_j = run_energy["energy_j"]
    measured_share = sum(seconds) / sum(p["total_seconds"] for p in passes)
    if run_energy_j is not None and run_energy["energy_source"] in ("rapl", "slurm"):
        energy_mean = run_energy_j * measured_share / len(measured)
        energy_source = run_energy["energy_source"]
    else:
        energy_mean = (sum(energies) / len(energies)) if energies else None
        energy_source = measured[0]["energy_source"]

    selection = plan.get("selection", {})
    predicted = selection.get("predicted", {})
    run = {
        "engine": engine,
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
        "payload": payload_provenance(args),
        "objects": args.objects,
        "measured": {
            "seconds_mean": sum(seconds) / len(seconds),
            "seconds_min": min(seconds),
            "seconds_max": max(seconds),
            "energy_j_mean": energy_mean,
            "nfr_seconds_mean": sum(p["nfr_seconds"] for p in measured) / len(measured),
            "energy_source": energy_source,
            "run_energy_j": run_energy_j,
            "run_seconds": run_energy["seconds"],
            "energy_coarse": bool(run_energy.get("coarse", False)),
            "passes_total": len(passes),
            "passes_measured": len(measured),
        },
        # One trace means every measured pass enforced the same mechanisms.
        "mechanism_trace": sorted(traces)[0] if len(traces) == 1 else "INCONSISTENT",
        "mechanism_trace_stable": len(traces) == 1,
        "integrity_verified": all(p["integrity_verified"] for p in measured),
        "passes": passes,
    }

    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "run_result.json"
    with result_path.open("w", encoding="utf-8") as handle:
        json.dump(run, handle, indent=2)
        handle.write("\n")

    energy = run["measured"]["energy_j_mean"]
    energy_text = f"{energy:.3f} J ({run['measured']['energy_source']})" if energy else "energy n/a"
    print(f"[{engine}] {run['measured']['seconds_mean']:.4f} s, {energy_text}")
    print(f"[{engine}] mechanisms: {run['mechanism_trace']}")
    print(f"[{engine}] wrote {result_path}")
    return run
