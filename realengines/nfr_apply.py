#!/usr/bin/env python3
"""Apply a realization plan to a file, from any workflow engine.

Python-native engines can import :mod:`nfr_plan` directly. Engines that run
tasks as external processes -- Nextflow, Snakemake, shell-based orchestrators --
use this command instead, which is the same enforcement path behind a file
interface:

    nfr_apply.py --plan plan.json --artifact-class raw --direction output \\
        --in payload.bin --out payload.nfr --steps payload.steps.json

``--direction input`` inverts the transformation using the emitted step file.
Every invocation appends one record to the enforcement log, which is the audit
trail linking contract clauses to the mechanisms actually executed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nfr_plan import apply_input, apply_output, load_plan, policy_for  # noqa: E402


def append_log(log_path: Path, record: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--artifact-class", required=True)
    parser.add_argument("--direction", required=True, choices=("output", "input"))
    parser.add_argument("--in", dest="input_path", required=True, type=Path)
    parser.add_argument("--out", dest="output_path", required=True, type=Path)
    parser.add_argument("--steps", type=Path, default=None,
                        help="Step metadata: written on output, read on input")
    parser.add_argument("--log", type=Path, default=None,
                        help="Enforcement log (default: alongside the plan)")
    parser.add_argument("--hmac-key-hex", default=os.environ.get("NFR_HMAC_KEY", ""),
                        help="Key for HMAC integrity mechanisms")
    parser.add_argument("--stage", default="", help="Stage label recorded in the log")
    args = parser.parse_args()

    plan = load_plan(args.plan)
    hmac_key = bytes.fromhex(args.hmac_key_hex) if args.hmac_key_hex else b""
    steps_path = args.steps or args.output_path.with_suffix(args.output_path.suffix + ".steps.json")
    log_path = args.log or (args.plan.parent / "enforcement_log.jsonl")

    data = args.input_path.read_bytes()
    size_in = len(data)
    started = time.perf_counter()

    if args.direction == "output":
        data, steps = apply_output(plan, args.artifact_class, data, hmac_key=hmac_key)
        steps_path.parent.mkdir(parents=True, exist_ok=True)
        with steps_path.open("w", encoding="utf-8") as handle:
            json.dump(steps, handle, indent=2)
    else:
        if not steps_path.exists():
            raise FileNotFoundError(f"missing step metadata {steps_path}; cannot invert")
        with steps_path.open("r", encoding="utf-8") as handle:
            steps = json.load(handle)
        data = apply_input(plan, args.artifact_class, data, steps, hmac_key=hmac_key)

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_bytes(data)
    elapsed = time.perf_counter() - started

    append_log(log_path, {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stage": args.stage,
        "artifact_class": args.artifact_class,
        "direction": args.direction,
        "contract_label": plan.get("selection", {}).get("label", ""),
        "mechanisms": [
            {"slot": slot, "algorithm": spec["algorithm"]}
            for slot, spec in policy_for(plan, args.artifact_class).items()
        ],
        "bytes_in": size_in,
        "bytes_out": len(data),
        "seconds": elapsed,
        "steps": [
            {k: v for k, v in step.items() if k != "metadata"}
            for step in steps
        ],
    })

    mechanisms = ", ".join(
        f"{slot}:{spec['algorithm']}" for slot, spec in policy_for(plan, args.artifact_class).items()
    )
    print(f"{args.artifact_class} {args.direction}: [{mechanisms}] "
          f"{size_in} -> {len(data)} bytes in {elapsed:.4f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
