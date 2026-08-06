#!/usr/bin/env python3
"""DagOnStar adapter: the workload as a real DagOnStar task graph.

DagOnStar schedules external commands and resolves data dependencies through
its ``workflow://`` staging scheme, so this adapter is process-based like the
Nextflow one: each stage is a ``DagonTask`` that invokes ``nfr_apply.py``, and
DagOnStar itself stages the protected artefacts between tasks. Dependencies are
never declared by hand -- they are inferred by DagOnStar from the
``workflow:///<task>/<file>`` references, which is precisely the engine
behaviour being exercised.

Note on packages: the real DagOnStar is distributed as ``dagonstar`` from
source (it provides the ``dagon`` import). An unrelated project owns the name
``dagon`` on PyPI; installing that one shadows this import and breaks it. See
setup_venv.sh.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import measure, site_power_w  # noqa: E402
from nfr_plan import artifact_classes, load_plan  # noqa: E402
from process_engine import (  # noqa: E402
    APPLY,
    add_common_arguments,
    pass_from_log,
    read_enforcement_log,
    summarize,
    write_run_result,
)

ENGINE = "dagonstar"

# DagOnStar launches each task in a fresh shell that has not activated our
# virtualenv, so a bare "python3" would resolve to the system interpreter and
# fail on the crypto dependencies. Every Python invocation is absolute.
PYTHON = sys.executable
MIXER = Path(__file__).resolve().parent / "mix_payload.py"


def _apply_command(plan_path: Path, artifact_class: str, direction: str,
                   source: str, out_name: str, steps_ref: str, log_path: Path,
                   stage: str) -> str:
    return (
        f"{PYTHON} {APPLY} "
        f"--plan {plan_path} "
        f"--artifact-class {artifact_class} "
        f"--direction {direction} "
        f"--in {source} "
        f"--out {out_name} "
        f"--steps {steps_ref} "
        f"--log {log_path} "
        f"--stage {stage}"
    )


def build_workflow(plan_path: Path, plan: dict[str, Any], payload_bytes: int,
                   scratch: Path, log_path: Path):
    """Assemble the DagOnStar task graph for one pass of the workload.

    Dependencies are never declared explicitly: every ``workflow:///`` reference
    in a command is what DagOnStar resolves into an edge plus a staging
    operation, so ``make_dependencies`` derives the DAG from the commands alone.
    """
    from dagon import Workflow
    from dagon.task import DagonTask, TaskType

    config = {"batch": {"scratch_dir_base": str(scratch), "remove_dir": "False"}}
    workflow = Workflow(f"NFR_Realization_{plan.get('selection', {}).get('candidate_id', '0')}",
                        config=config)

    classes = [c for c in ("raw", "derived") if c in artifact_classes(plan)]
    tasks = [DagonTask(TaskType.BATCH, "ingest",
                       f"head -c {payload_bytes} /dev/urandom > raw.bin")]
    previous, previous_file = "ingest", "raw.bin"

    for artifact_class in classes:
        protect = f"protect_{artifact_class}"
        unprotect = f"unprotect_{artifact_class}"
        tasks.append(DagonTask(TaskType.BATCH, protect, _apply_command(
            plan_path, artifact_class, "output",
            f"workflow:///{previous}/{previous_file}",
            f"{artifact_class}.nfr", f"{artifact_class}.steps.json",
            log_path, f"protect:{artifact_class}",
        )))
        tasks.append(DagonTask(TaskType.BATCH, unprotect, _apply_command(
            plan_path, artifact_class, "input",
            f"workflow:///{protect}/{artifact_class}.nfr",
            f"{artifact_class}.restored",
            f"workflow:///{protect}/{artifact_class}.steps.json",
            log_path, f"unprotect:{artifact_class}",
        )))
        previous, previous_file = unprotect, f"{artifact_class}.restored"

        if artifact_class == "raw" and len(classes) > 1:
            # The compute stage reads the restored artefact straight from the
            # upstream task; the reference is part of the command at creation
            # time, which is when DagOnStar parses it.
            tasks.append(DagonTask(TaskType.BATCH, "compute", (
                f"{PYTHON} {MIXER} "
                f"workflow:///{unprotect}/{artifact_class}.restored "
                f"derived.bin"
            )))
            previous, previous_file = "compute", "derived.bin"

    for task in tasks:
        workflow.add_task(task)
    workflow.make_dependencies()
    return workflow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_arguments(parser)
    args = parser.parse_args()

    try:
        import dagon  # noqa: F401
        from dagon.task import DagonTask  # noqa: F401
    except ImportError as exc:
        print(f"[{ENGINE}] DagOnStar is not importable ({exc}); skipping. "
              "Install with: pip install git+https://github.com/DagOnStar/dagonstar.git",
              file=sys.stderr)
        return 3

    plan = load_plan(args.plan)
    model_power_w = args.model_power_w
    if model_power_w is None and args.site and args.machine:
        model_power_w = site_power_w(args.site, args.machine)

    args.output.mkdir(parents=True, exist_ok=True)
    passes = []
    with measure(model_power_w=model_power_w, allow_slurm=True) as run_energy:
        for index in range(max(1, args.repeats)):
            pass_dir = args.output / f"pass{index}"
            pass_dir.mkdir(parents=True, exist_ok=True)
            log_path = (pass_dir / "enforcement_log.jsonl").resolve()

            workflow = build_workflow(
                Path(args.plan).resolve(), plan, args.payload_bytes,
                (pass_dir / "scratch").resolve(), log_path,
            )
            with measure(model_power_w=model_power_w) as m:
                workflow.run()
            records = read_enforcement_log(log_path)
            if not records:
                print(f"[{ENGINE}] pass {index} produced no enforcement records; "
                      f"see {pass_dir}", file=sys.stderr)
                return 1
            passes.append(pass_from_log(index, args.repeats, m, records))

    run = summarize(ENGINE, args, plan, passes,
                    model_power_w=model_power_w, run_energy=dict(run_energy))
    write_run_result(args.output, run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
