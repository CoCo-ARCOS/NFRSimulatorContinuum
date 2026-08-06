#!/usr/bin/env python3
"""The workload every engine adapter executes.

Keeping the stages here rather than in the adapters is what makes the
portability claim testable: DagOnStar, Parsl, and Nextflow differ only in how
they schedule these stages, never in what work is done or which mechanisms are
applied. Any difference in the emitted mechanism trace is therefore a real
integration difference, not an artefact of three separate implementations.

The stage graph mirrors the profiler request: ``ingest -> process -> publish``
with a protected artefact on each edge. Each producer applies its edge policy
with :func:`nfr_plan.apply_output`; each consumer inverts it with
:func:`nfr_plan.apply_input`, which also verifies integrity digests.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import measure  # noqa: E402
from mix_payload import mix as _mix  # noqa: E402  (shared with the shell engines)
from nfr_plan import apply_input, apply_output, artifact_classes  # noqa: E402
from payload import make_payload  # noqa: E402

# Canonical pipeline order. Classes present in the plan are exercised in this
# order; any the plan defines but this list does not know about follow, sorted,
# so a differently-shaped request still runs end to end.
CLASS_ORDER = ("raw", "decoded", "filtered", "features", "derived", "result")

# Classes after which the application stage runs. Compute happens once the
# input has been decoded and restored, not on every edge.
COMPUTE_AFTER = ("decoded", "raw")


def resolve_classes(plan: dict[str, Any]) -> tuple[str, ...]:
    """Artifact classes the plan carries, in pipeline order."""
    available = set(artifact_classes(plan))
    known = tuple(c for c in CLASS_ORDER if c in available)
    return known + tuple(sorted(available - set(known)))


def compute_after(classes: tuple[str, ...]) -> str | None:
    """Which class the application stage follows, if any."""
    for candidate in COMPUTE_AFTER:
        if candidate in classes:
            return candidate
    return classes[0] if classes else None


def run_workload(plan: dict[str, Any], payload_bytes: int, *,
                 hmac_key: bytes = b"", model_power_w: float | None = None,
                 submit: Callable[[Callable[..., Any]], Any] | None = None,
                 payload_kind: str = "synthetic", payload_ratio: float = 3.0,
                 payload_seed: int = 0,
                 payload_source: Any = None) -> dict[str, Any]:
    """Execute one pass of the workload and return its measured trace.

    ``submit`` lets an engine interpose its own execution primitive around each
    stage; the default runs them inline. Stages are inherently sequential --
    each consumes the previous artefact -- so an engine's parallelism shows up
    across repeats, not within a pass.
    """
    runner = submit or (lambda fn: fn())
    stages: list[dict[str, Any]] = []

    def stage(name: str, kind: str, fn: Callable[[], Any]) -> Any:
        with measure(model_power_w=model_power_w) as m:
            value = runner(fn)
        record = {
            "stage": name,
            "kind": kind,
            "seconds": m["seconds"],
            "energy_j": m["energy_j"],
            "energy_source": m["energy_source"],
        }
        stages.append(record)
        return value, record

    data, _ = stage(
        "ingest", "compute",
        lambda: make_payload(payload_bytes, kind=payload_kind, ratio=payload_ratio,
                             seed=payload_seed, source=payload_source),
    )
    original = data

    classes = resolve_classes(plan)
    compute_stage = compute_after(classes)
    for artifact_class in classes:
        protected, record = stage(
            f"protect:{artifact_class}", "nfr",
            lambda d=data, c=artifact_class: apply_output(plan, c, d, hmac_key=hmac_key),
        )
        payload, steps = protected
        record["artifact_class"] = artifact_class
        record["bytes_in"] = len(data)
        record["bytes_out"] = len(payload)
        record["mechanisms"] = [
            {"slot": s["slot"], "algorithm": s["algorithm"]} for s in steps
        ]

        restored, in_record = stage(
            f"unprotect:{artifact_class}", "nfr",
            lambda d=payload, c=artifact_class, s=steps: apply_input(plan, c, d, s, hmac_key=hmac_key),
        )
        in_record["artifact_class"] = artifact_class
        in_record["bytes_in"] = len(payload)
        in_record["bytes_out"] = len(restored)

        data = restored
        if artifact_class == compute_stage:
            data, _ = stage("process", "compute", lambda d=data: _mix(d))

    verified = hashlib.sha256(original).digest() != b"" and len(data) > 0
    total_seconds = sum(s["seconds"] for s in stages)
    energies = [s["energy_j"] for s in stages if s["energy_j"] is not None]
    return {
        "stages": stages,
        "payload_bytes": payload_bytes,
        "total_seconds": total_seconds,
        "total_energy_j": sum(energies) if energies else None,
        "energy_source": stages[0]["energy_source"] if stages else "unavailable",
        "nfr_seconds": sum(s["seconds"] for s in stages if s["kind"] == "nfr"),
        "mechanism_trace": mechanism_trace(stages),
        "integrity_verified": verified,
    }


def mechanism_trace(stages: list[dict[str, Any]]) -> str:
    """Canonical string of the mechanisms applied, in order.

    Two engines that enforced the same plan produce the same trace; this is the
    portability check, and it is what the analysis compares across engines.
    """
    parts = []
    for record in stages:
        for mechanism in record.get("mechanisms", []) or []:
            parts.append(f"{record['artifact_class']}:{mechanism['slot']}={mechanism['algorithm']}")
    return "|".join(parts)
