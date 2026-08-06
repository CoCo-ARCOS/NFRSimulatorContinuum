#!/usr/bin/env python3
"""Shared support for engines that enforce the plan out of process.

Nextflow and DagOnStar both run tasks as external commands, so neither imports
:mod:`nfr_plan`. They invoke ``nfr_apply.py`` instead and the mechanism trace is
reconstructed from the enforcement log it appends to. That indirection is the
point of these adapters: an engine that never touches our Python API still
produces a trace directly comparable with the in-process engines.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
APPLY = HERE / "nfr_apply.py"


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """The argument surface every engine adapter shares.

    Defined once so the adapters cannot drift apart: the driver invokes them
    all with the same flags, and an option missing from one engine is an
    immediate argparse failure rather than a silent behavioural difference.
    """
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--payload-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--repeats", type=int, default=3,
                        help="Measured passes; the first is discarded as warm-up")
    parser.add_argument("--min-seconds", type=float, default=0.0,
                        help="Keep repeating passes until the run has lasted this "
                             "long, so coarse energy accounting has several "
                             "samples to attribute")
    parser.add_argument("--max-repeats", type=int, default=200,
                        help="Ceiling on passes added by --min-seconds")
    parser.add_argument("--site", type=Path, default=None,
                        help="Site file supplying the modelled power fallback")
    parser.add_argument("--machine", default="",
                        help="Machine in the site file whose power model to use")
    parser.add_argument("--model-power-w", type=float, default=None,
                        help="Explicit active power for the modelled fallback")
    # Random payloads are incompressible, which silently disables the
    # volume-reduction clauses; see payload.py.
    parser.add_argument("--payload-kind", choices=("synthetic", "random", "file"),
                        default="synthetic", help="Payload realism")
    parser.add_argument("--payload-ratio", type=float, default=3.0,
                        help="Target compression ratio for --payload-kind synthetic")
    parser.add_argument("--payload-seed", type=int, default=0)
    parser.add_argument("--payload-source", type=Path, default=None,
                        help="File to tile for --payload-kind file")


def payload_provenance(args: argparse.Namespace) -> dict:
    """Measured description of the payload these runs process."""
    from payload import provenance
    return provenance(args.payload_bytes, kind=args.payload_kind,
                      ratio=args.payload_ratio, seed=args.payload_seed,
                      source=args.payload_source)


def payload_command(python: str, generator: Path, args: argparse.Namespace,
                    out_name: str) -> str:
    """Shell command generating the payload, for engines that run tasks as
    processes. They must use the same generator as the in-process engines, or
    the compression results would not be comparable."""
    command = (
        f"{python} {generator} --bytes {args.payload_bytes} "
        f"--kind {args.payload_kind} --ratio {args.payload_ratio} "
        f"--seed {args.payload_seed} --out {out_name}"
    )
    if args.payload_source:
        command += f" --source {Path(args.payload_source).resolve()}"
    return command


def keep_going(index: int, elapsed: float, args: argparse.Namespace) -> bool:
    """Whether another pass is due, honouring --repeats then --min-seconds."""
    if index < max(1, args.repeats):
        return True
    return elapsed < args.min_seconds and index < args.max_repeats


def read_enforcement_log(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def trace_from_log(records: list[dict[str, Any]]) -> str:
    """Rebuild the canonical mechanism trace from output-direction records.

    Only the output direction counts, so the trace lines up with the in-process
    engines, which record mechanisms when applied rather than when inverted.
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


def pass_from_log(index: int, repeats: int, measurement: dict[str, Any],
                  records: list[dict[str, Any]]) -> dict[str, Any]:
    """One measured pass, summarised from its enforcement log."""
    return {
        "pass": index,
        "warmup": index == 0 and repeats > 1,
        "total_seconds": measurement["seconds"],
        "total_energy_j": measurement["energy_j"],
        "energy_source": measurement["energy_source"],
        "nfr_seconds": sum(float(r.get("seconds", 0.0)) for r in records),
        "mechanism_trace": trace_from_log(records),
        "enforcement_records": len(records),
        # An input-direction record means a consumer inverted the transformation,
        # which is where integrity digests are checked.
        "integrity_verified": any(r.get("direction") == "input" for r in records),
    }


def summarize(engine: str, args: argparse.Namespace, plan: dict[str, Any],
              passes: list[dict[str, Any]], *, model_power_w: float | None,
              run_energy: dict[str, Any] | None = None,
              extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the ``run_result.json`` record shared by every engine adapter."""
    from measure import probe  # Imported here to keep this module import-light.

    measured = [p for p in passes if not p["warmup"]] or passes
    seconds = [p["total_seconds"] for p in measured]
    energies = [p["total_energy_j"] for p in measured if p["total_energy_j"] is not None]
    traces = {p["mechanism_trace"] for p in measured}

    run_energy = run_energy or {}
    run_energy_j = run_energy.get("energy_j")
    if run_energy_j is not None and run_energy.get("energy_source") in ("rapl", "slurm"):
        share = sum(seconds) / sum(p["total_seconds"] for p in passes)
        energy_mean = run_energy_j * share / len(measured)
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
        "measured": {
            "seconds_mean": sum(seconds) / len(seconds),
            "seconds_min": min(seconds),
            "seconds_max": max(seconds),
            "energy_j_mean": energy_mean,
            "nfr_seconds_mean": sum(p["nfr_seconds"] for p in measured) / len(measured),
            "energy_source": energy_source,
            "run_energy_j": run_energy_j,
            "run_seconds": run_energy.get("seconds"),
            "energy_coarse": bool(run_energy.get("coarse", False)),
            "passes_total": len(passes),
            "passes_measured": len(measured),
        },
        "mechanism_trace": sorted(traces)[0] if len(traces) == 1 else "INCONSISTENT",
        "mechanism_trace_stable": len(traces) == 1,
        "integrity_verified": all(p["integrity_verified"] for p in measured),
        "passes": passes,
    }
    if extra:
        run.update(extra)
    return run


def write_run_result(output: Path, run: dict[str, Any]) -> Path:
    Path(output).mkdir(parents=True, exist_ok=True)
    path = Path(output) / "run_result.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(run, handle, indent=2)
        handle.write("\n")

    engine = run["engine"]
    measured = run["measured"]
    print(f"[{engine}] {measured['seconds_mean']:.4f} s "
          f"({measured['nfr_seconds_mean']:.4f} s in mechanisms)")
    print(f"[{engine}] mechanisms: {run['mechanism_trace']}")
    print(f"[{engine}] wrote {path}")
    return path
