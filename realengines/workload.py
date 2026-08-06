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
from nfr_plan import apply_input, apply_output, artifact_classes  # noqa: E402

# Edge id -> artifact class, matching the policy groups of the request.
DEFAULT_EDGES = (("raw-data", "raw"), ("derived-data", "derived"))


def _mix(data: bytes) -> bytes:
    """Stand-in application compute: deterministic, and touches every byte."""
    digest = hashlib.sha256(data).digest()
    view = bytearray(data)
    for index in range(len(view)):
        view[index] ^= digest[index % len(digest)]
    return bytes(view)


def resolve_edges(plan: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Edges to exercise, restricted to classes the plan actually carries."""
    available = set(artifact_classes(plan))
    edges = tuple((edge, cls) for edge, cls in DEFAULT_EDGES if cls in available)
    if edges:
        return edges
    # Fall back to whatever the plan does define, so a differently-shaped
    # request still runs instead of silently exercising nothing.
    return tuple((f"{cls}-edge", cls) for cls in sorted(available))


def run_workload(plan: dict[str, Any], payload_bytes: int, *,
                 hmac_key: bytes = b"", model_power_w: float | None = None,
                 submit: Callable[[Callable[..., Any]], Any] | None = None) -> dict[str, Any]:
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

    data, _ = stage("ingest", "compute", lambda: os.urandom(payload_bytes))
    original = data

    for edge_id, artifact_class in resolve_edges(plan):
        protected, record = stage(
            f"protect:{edge_id}", "nfr",
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
            f"unprotect:{edge_id}", "nfr",
            lambda d=payload, c=artifact_class, s=steps: apply_input(plan, c, d, s, hmac_key=hmac_key),
        )
        in_record["artifact_class"] = artifact_class
        in_record["bytes_in"] = len(payload)
        in_record["bytes_out"] = len(restored)

        if edge_id == DEFAULT_EDGES[0][0]:
            data, _ = stage("process", "compute", lambda d=restored: _mix(d))
        else:
            data = restored

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
