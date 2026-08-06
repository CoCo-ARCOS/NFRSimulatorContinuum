#!/usr/bin/env python3
"""Engine-agnostic NFR realization plan.

A plan is the profiler's selected ``nfr_policy`` carried verbatim, wrapped with
the provenance needed to audit the decision: the budgets it was selected under,
the predicted cost, and the contract clauses it fulfills.

Workflow engines consume a plan through :func:`policy_for` and apply mechanisms
with :func:`apply_output` / :func:`apply_input`; nothing here depends on a
particular engine.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import real_pipeline_reference.pipeline_runner as runner

SCHEMA = "nfr-realization-plan/1"

# Canonical slot order, matching nfr_profiler_core.SLOTS. Output-side mechanisms
# are applied in this order; input-side inverts them in reverse.
SLOTS = ("compress", "encrypt", "erasure", "hash")

# The profiler names the confidentiality slot "encrypt"; pipeline_runner groups
# both encryption and erasure coding under its "cipher" requirement type.
SLOT_TO_RUNNER_TYPE = {
    "compress": "compress",
    "encrypt": "cipher",
    "erasure": "cipher",
    "hash": "hash",
}

DEFAULT_AES_KEY_BITS = 256


def load_plan(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    if plan.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} document")
    return plan


def write_plan(path: Path, plan: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2)
        handle.write("\n")


def build_plan(candidate, *, request_ref, budgets, baseline, method) -> dict[str, Any]:
    """Wrap a selected profiler candidate as a realization plan."""
    policy = candidate.get("nfr_policy") or {}
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": request_ref,
        "budgets": budgets,
        "selection": {
            "method": method,
            "candidate_id": candidate.get("id"),
            "plan_id": candidate.get("plan_id"),
            "label": candidate.get("nfr_label", ""),
            "predicted": {
                "energy_j": candidate.get("total_energy"),
                "makespan_s": candidate.get("makespan"),
                "coverage_weighted": candidate.get("coverage_weighted"),
                "coverage_count": candidate.get("coverage_count"),
            },
            "baseline": baseline,
            "mandatory": {
                "satisfied": candidate.get("mandatory_satisfied"),
                "required": candidate.get("mandatory_count"),
            },
            "clause_status": candidate.get("clause_status", {}),
        },
        "policy": policy,
        "runtime": {"aes_key_bits": DEFAULT_AES_KEY_BITS},
    }


def artifact_classes(plan: dict[str, Any]) -> list[str]:
    return list((plan.get("policy") or {}).keys())


def policy_for(plan: dict[str, Any], artifact_class: str) -> dict[str, Any]:
    """Return the active slots for one artifact class, in canonical order."""
    policy = (plan.get("policy") or {}).get(artifact_class)
    if policy is None:
        raise KeyError(
            f"artifact class {artifact_class!r} not in plan "
            f"(have: {', '.join(artifact_classes(plan)) or 'none'})"
        )
    return {slot: policy[slot] for slot in SLOTS if policy.get(slot)}


def legacy_view(plan: dict[str, Any], artifact_class: str) -> dict[str, Any]:
    """Flat ``{compress,cipher,erasure,hash}`` view used by the engine adapters."""
    active = policy_for(plan, artifact_class)
    key_bits = int((plan.get("runtime") or {}).get("aes_key_bits", DEFAULT_AES_KEY_BITS))
    view: dict[str, Any] = {}
    for slot, spec in active.items():
        name = "cipher" if slot == "encrypt" else slot
        entry = {"algorithm": spec["algorithm"], "enabled": True, "config": {}}
        if slot == "encrypt":
            entry["config"] = {"aes_key_bits": key_bits}
        elif slot == "erasure":
            entry["config"] = {"ida_k": int(spec.get("k", 8)), "ida_m": int(spec.get("m", 4))}
        view[name] = entry
    return view


def _slot_config(slot: str, spec: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if slot == "encrypt":
        key_bits = (plan.get("runtime") or {}).get("aes_key_bits", DEFAULT_AES_KEY_BITS)
        return {"aes_key_bits": int(key_bits)}
    if slot == "erasure":
        return {"ida_k": int(spec.get("k", 8)), "ida_m": int(spec.get("m", 4))}
    return {}


def apply_output(plan: dict[str, Any], artifact_class: str, data: bytes,
                 hmac_key: bytes = b"") -> tuple[bytes, list[dict[str, Any]]]:
    """Apply every mechanism of one artifact class to outbound data.

    Returns the transformed payload and a step log; the log carries the
    metadata required to invert the transformation in :func:`apply_input`.
    """
    steps: list[dict[str, Any]] = []
    for slot, spec in policy_for(plan, artifact_class).items():
        algorithm = spec["algorithm"]
        config = _slot_config(slot, spec, plan)
        size_in = len(data)
        started = time.perf_counter()
        metadata: dict[str, Any] = {}
        if slot == "compress":
            data = runner.compress_payload(data, algorithm)
        elif slot in ("encrypt", "erasure"):
            data, metadata = runner.encrypt_payload(data, algorithm, config)
        elif slot == "hash":
            # Integrity does not transform the payload; the digest travels with it.
            metadata = {"algorithm": algorithm, "digest": runner.hash_digest(data, algorithm, hmac_key)}
        steps.append({
            "slot": slot,
            "algorithm": algorithm,
            "runner_type": SLOT_TO_RUNNER_TYPE[slot],
            "bytes_in": size_in,
            "bytes_out": len(data),
            "seconds": time.perf_counter() - started,
            "metadata": metadata,
        })
    return data, steps


def apply_input(plan: dict[str, Any], artifact_class: str, data: bytes,
                steps: list[dict[str, Any]], hmac_key: bytes = b"") -> bytes:
    """Invert :func:`apply_output` using its step log."""
    for step in reversed(steps):
        slot = step["slot"]
        algorithm = step["algorithm"]
        metadata = step.get("metadata") or {}
        if slot == "compress":
            data = runner.decompress_payload(data, algorithm)
        elif slot in ("encrypt", "erasure"):
            data = runner.decrypt_payload(data, metadata)
        elif slot == "hash":
            digest = runner.hash_digest(data, algorithm, hmac_key)
            if metadata.get("digest") and digest != metadata["digest"]:
                raise RuntimeError(f"{artifact_class}: {algorithm} integrity check failed")
    return data


def describe(plan: dict[str, Any]) -> str:
    sel = plan.get("selection", {})
    predicted = sel.get("predicted", {})
    budgets = plan.get("budgets", {})
    lines = [
        f"selection: {sel.get('method')} -> candidate {sel.get('candidate_id')} "
        f"(plan {sel.get('plan_id')})",
        f"  label:     {sel.get('label')}",
        f"  predicted: {predicted.get('energy_j')} J, {predicted.get('makespan_s')} s, "
        f"coverage {predicted.get('coverage_weighted')}",
        f"  budgets:   {budgets.get('energy_j')} J, {budgets.get('deadline_s')} s",
    ]
    for artifact_class in artifact_classes(plan):
        active = policy_for(plan, artifact_class)
        rendered = ", ".join(f"{slot}={spec['algorithm']}" for slot, spec in active.items())
        lines.append(f"  {artifact_class}: {rendered or 'none'}")
    return "\n".join(lines)
