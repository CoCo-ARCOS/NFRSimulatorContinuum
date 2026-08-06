#!/usr/bin/env python3
"""Parsl adapter: executes the shared workload through Parsl apps.

Parsl is Python-native, so it consumes the realization plan in process through
:mod:`nfr_plan`. Each workload stage is submitted as a ``python_app`` and
resolved before the next one starts, because the stages form a data dependency
chain -- the point being measured is that the mechanisms Parsl executes are the
ones the profiler selected, not that Parsl can run them concurrently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_cli import execute, parse_args  # noqa: E402

ENGINE = "parsl"


def main() -> int:
    args = parse_args(ENGINE)

    import parsl
    from parsl.config import Config
    from parsl.executors.threads import ThreadPoolExecutor

    parsl.load(Config(executors=[ThreadPoolExecutor(max_threads=4, label="local_threads")]))
    try:
        from parsl.app.app import python_app

        @python_app
        def _stage_app(fn):
            return fn()

        # Each stage is dispatched through Parsl and joined before the next.
        execute(ENGINE, args, submit=lambda fn: _stage_app(fn).result())
    finally:
        parsl.dfk().cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
