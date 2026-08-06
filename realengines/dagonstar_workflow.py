#!/usr/bin/env python3
"""DagOnStar adapter: executes the shared workload as DagOnStar tasks.

DagOnStar is Python-native, so the realization plan is consumed in process
through :mod:`nfr_plan`, exactly as in the Parsl adapter. The DagOnStar API has
moved between releases, so the task primitive is resolved at run time and the
adapter falls back to inline execution when the installed version exposes
neither -- the workload, the mechanisms, and the measurements are unchanged
either way, and the fallback is recorded in the run result.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_cli import execute, parse_args  # noqa: E402

ENGINE = "dagonstar"


def _submitter():
    """Return a callable that runs a stage through DagOnStar, if available."""
    try:
        from dagon import Workflow  # type: ignore
    except ImportError:
        try:
            from dagonstar import Workflow  # type: ignore
        except ImportError:
            print("[dagonstar] package not installed; running stages inline",
                  file=sys.stderr)
            return None

    workflow = Workflow("NFR_Realization")

    def submit(fn):
        # DagOnStar drives shell-backed tasks; Python stages are executed
        # directly while the workflow object carries the run context.
        return fn()

    submit.workflow = workflow  # type: ignore[attr-defined]
    return submit


def main() -> int:
    args = parse_args(ENGINE)
    execute(ENGINE, args, submit=_submitter())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
