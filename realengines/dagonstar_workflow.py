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
from workload import compute_after, resolve_classes  # noqa: E402
from nfr_plan import load_plan  # noqa: E402
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

ENGINE = "dagonstar"

# DagOnStar launches each task in a fresh shell that has not activated our
# virtualenv, so a bare "python3" would resolve to the system interpreter and
# fail on the crypto dependencies. Every Python invocation is absolute.
PYTHON = sys.executable
MIXER = Path(__file__).resolve().parent / "mix_payload.py"
GENERATOR = Path(__file__).resolve().parent / "payload.py"


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


def build_workflow(plan_path: Path, plan: dict[str, Any], args,
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

    classes = resolve_classes(plan)
    compute_stage = compute_after(classes)
    tasks = []

    # One chain per object, all inside a single workflow: DagOnStar schedules
    # them itself (up to its thread pool), and the per-run setup cost is paid
    # once rather than once per object.
    for index in range(max(1, getattr(args, "objects", 1))):
        suffix = f"_o{index}"
        ingest = f"ingest{suffix}"
        tasks.append(DagonTask(TaskType.BATCH, ingest,
                               payload_command(PYTHON, GENERATOR, args, "raw.bin",
                                               seed_offset=index)))
        previous, previous_file = ingest, "raw.bin"

        for artifact_class in classes:
            protect = f"protect_{artifact_class}{suffix}"
            unprotect = f"unprotect_{artifact_class}{suffix}"
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

            if artifact_class == compute_stage:
                compute = f"compute{suffix}"
                tasks.append(DagonTask(TaskType.BATCH, compute, (
                    f"{PYTHON} {MIXER} "
                    f"workflow:///{unprotect}/{artifact_class}.restored "
                    f"computed.bin"
                )))
                previous, previous_file = compute, "computed.bin"

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
    objects = max(1, args.objects)
    passes = []
    with measure(model_power_w=model_power_w, allow_slurm=True) as run_energy:
        index = 0
        elapsed = 0.0
        while keep_going(index, elapsed, args):
            pass_dir = args.output / f"pass{index}"
            pass_dir.mkdir(parents=True, exist_ok=True)
            log_path = (pass_dir / "enforcement_log.jsonl").resolve()

            workflow = build_workflow(
                Path(args.plan).resolve(), plan, args,
                (pass_dir / "scratch").resolve(), log_path,
            )
            # DagOnStar reports nothing per task and this adapter summarises
            # only at the end, so a run killed by a timeout would otherwise
            # leave a log indistinguishable from a hang. Announce each pass and
            # the work it represents before starting it.
            n_tasks = len(getattr(workflow, "tasks", []) or [])
            print(f"[{ENGINE}] pass {index}: {objects} object(s), "
                  f"{n_tasks} tasks, elapsed {elapsed:.0f}s", flush=True)
            with measure(model_power_w=model_power_w) as m:
                workflow.run()
            print(f"[{ENGINE}] pass {index} done in {m['seconds']:.1f}s", flush=True)
            records = read_enforcement_log(log_path)
            if not records:
                print(f"[{ENGINE}] pass {index} produced no enforcement records; "
                      f"see {pass_dir}", file=sys.stderr)
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
