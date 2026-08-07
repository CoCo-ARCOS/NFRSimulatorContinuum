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
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import measure, site_power_w  # noqa: E402
from nfr_plan import load_plan  # noqa: E402
from workload import compute_after, resolve_classes  # noqa: E402
from nextflow_pipeline import write as write_pipeline  # noqa: E402
from process_engine import (  # noqa: E402
    APPLY,
    add_common_arguments,
    keep_going,
    payload_command,
    pass_from_log,
    read_enforcement_log,
    summarize,
    write_run_result,
)

ENGINE = "nextflow"
HERE = Path(__file__).resolve().parent
MIXER = HERE / "mix_payload.py"
GENERATOR = HERE / "payload.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_arguments(parser)
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

    classes = resolve_classes(plan)
    compute_stage = compute_after(classes)
    print(f"[{ENGINE}] artifact classes: {', '.join(classes)}")

    passes = []
    with measure(model_power_w=model_power_w, allow_slurm=True) as run_energy:
        index = 0
        elapsed = 0.0
        while keep_going(index, elapsed, args):
            work_dir = args.output / f"pass{index}"
            work_dir.mkdir(parents=True, exist_ok=True)
            log_path = work_dir / "enforcement_log.jsonl"
            pipeline = write_pipeline(
                work_dir / "pipeline.nf", list(classes), compute_stage,
                # Nextflow interpolates the per-object seed itself, so one
                # generated pipeline serves every object in the channel.
                re.sub(r"--seed \d+",
                       lambda _: "--seed ${params.payload_seed + idx}",
                       payload_command(sys.executable, GENERATOR, args, "raw.bin")),
            )
            command = [
                # Absolute: nextflow runs with cwd set to the pass directory,
                # and a relative path that does not resolve there is taken as a
                # remote "owner/repo" project identifier rather than a file.
                args.nextflow, "run", str(pipeline.resolve()),
                "--plan", str(Path(args.plan).resolve()),
                "--apply", str(APPLY.resolve()),
                "--mixer", str(MIXER.resolve()),
                # Nextflow tasks do not inherit our virtualenv, so the
                # interpreter is passed through explicitly.
                "--python", sys.executable,
                "--payload_bytes", str(args.payload_bytes),
                "--objects", str(args.objects),
                "--payload_seed", str(args.payload_seed),
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
            if not records:
                print(f"[{ENGINE}] pass {index} produced no enforcement records; "
                      f"see {work_dir / 'nextflow.log'}", file=sys.stderr)
                return 1
            passes.append(pass_from_log(index, args.repeats, m, records))
            elapsed += m["seconds"]
            index += 1

    run = summarize(ENGINE, args, plan, passes,
                    model_power_w=model_power_w, run_energy=dict(run_energy))
    write_run_result(args.output, run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
