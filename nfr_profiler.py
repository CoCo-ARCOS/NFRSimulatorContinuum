#!/usr/bin/env python3
"""Simulation-backed NFR profiler for continuum DAG workflows.

The profiler consumes a DAG plus either workflow-engine candidate plans or
per-task allowed-machine sets. It chooses independent compression, encryption,
erasure-coding, and integrity mechanisms. Candidates are ranked by fulfilled
NFR clauses first, raw assurance level second, and energy/deadline cost third.

The companion C simulator writes an authoritative run_summary.json containing
DAG makespan, integrated machine energy, and network energy. Replications use
common deterministic seeds across all candidates.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import itertools
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

CONFIG_PREFIX = "profiler_candidate_"
SLOTS = ("compress", "encrypt", "erasure", "hash")
FAMILIES = ("compression", "confidentiality", "reliability", "integrity")
LEVELS = ("required", "optional", "none")

NFR_CATALOG: dict[str, dict[str, Any]] = {
    "compression": {
        "slot": "compress",
        "operation_type": "compress",
        "selectable": ["LZ4", "ZLIB", "ZSTD", "BZ2", "LZMA"],
        "strength": {"LZ4": 1, "ZLIB": 2, "ZSTD": 3, "BZ2": 4, "LZMA": 5},
        "semantics": "ordinal data-reduction class; validate ratios per data class",
    },
    "confidentiality": {
        "slot": "encrypt",
        "operation_type": "encrypt",
        "selectable": ["AES", "CHACHA20"],
        "strength": {"AES": 2, "CHACHA20": 2},
        "semantics": "approved symmetric protection mechanism",
    },
    "reliability": {
        "slot": "erasure",
        "operation_type": "erasure",
        "selectable": ["RS"],
        "strength": {},
        "semantics": "erasure tolerance; level equals m lost fragments",
    },
    "integrity": {
        "slot": "hash",
        "operation_type": "hash",
        "selectable": ["SHA256", "SHA3_256", "BLAKE3", "HMAC_SHA256"],
        "strength": {"SHA256": 1, "SHA3_256": 1, "BLAKE3": 1, "HMAC_SHA256": 2},
        "semantics": "1=digest, 2=keyed authenticity",
    },
}

# One-sided 95% Student-t critical values. The normal limit is used above 30 df.
T95_ONE_SIDED = {
    1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015,
    6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812,
    11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761, 15: 1.753,
    16: 1.746, 17: 1.740, 18: 1.734, 19: 1.729, 20: 1.725,
    21: 1.721, 22: 1.717, 23: 1.714, 24: 1.711, 25: 1.708,
    26: 1.706, 27: 1.703, 28: 1.701, 29: 1.699, 30: 1.697,
}


# ---------------------------------------------------------------------------
# Request validation and DAG handling
# ---------------------------------------------------------------------------


def _task_id(task: dict[str, Any]) -> str:
    value = task.get("id", task.get("name"))
    if not isinstance(value, str) or not value:
        raise ValueError("every workflow task needs a non-empty id or name")
    return value


def _edge_source(edge: dict[str, Any]) -> str:
    value = edge.get("from", edge.get("source"))
    if not isinstance(value, str) or not value:
        raise ValueError("every workflow edge needs from/source")
    return value


def _edge_target(edge: dict[str, Any]) -> str:
    value = edge.get("to", edge.get("target"))
    if not isinstance(value, str) or not value:
        raise ValueError("every workflow edge needs to/target")
    return value


def _edge_id(edge: dict[str, Any], index: int) -> str:
    value = edge.get("id")
    if value is None:
        value = f"{_edge_source(edge)}->{_edge_target(edge)}-{index}"
    if not isinstance(value, str) or not value:
        raise ValueError("every workflow edge id must be a non-empty string")
    return value


def policy_scope(request: dict[str, Any]) -> str:
    scope = request.get("objective", {}).get("policy_scope", "edge")
    if scope not in ("edge", "global"):
        raise ValueError("objective.policy_scope must be 'edge' or 'global'")
    return scope


def edge_policy_group(
    request: dict[str, Any], edge: dict[str, Any], index: int
) -> str:
    if policy_scope(request) == "global":
        return "global"
    group = edge.get("policy_group", _edge_id(edge, index))
    if not isinstance(group, str) or not group:
        raise ValueError(f"edge {_edge_id(edge, index)} has an invalid policy_group")
    return group


def validate_dag(workflow: dict[str, Any]) -> None:
    tasks = workflow.get("tasks")
    edges = workflow.get("edges")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("workflow.tasks must be a non-empty array")
    if not isinstance(edges, list):
        raise ValueError("workflow.edges must be an array; use [] for a one-task DAG")

    task_ids = [_task_id(task) for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("workflow task ids must be unique")

    indegree = {task_id: 0 for task_id in task_ids}
    adjacency = {task_id: [] for task_id in task_ids}
    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        source, target = _edge_source(edge), _edge_target(edge)
        if source not in indegree or target not in indegree:
            raise ValueError(f"edge {index} refers to an unknown task")
        if source == target:
            raise ValueError(f"edge {index} is a self-loop")
        edge_id = _edge_id(edge, index)
        if edge_id in edge_ids:
            raise ValueError(f"duplicate edge id '{edge_id}'")
        edge_ids.add(edge_id)
        indegree[target] += 1
        adjacency[source].append(target)

    queue = [task_id for task_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for successor in adjacency[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if visited != len(task_ids):
        raise ValueError("workflow contains a cycle; the simulator requires a DAG")


def load_request(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)

    for section in ("workflow", "infrastructure", "requirements"):
        if section not in request:
            raise ValueError(f"request is missing the '{section}' section")

    validate_dag(request["workflow"])
    machines = request["infrastructure"].get("machines")
    if not isinstance(machines, list) or not machines:
        raise ValueError("infrastructure.machines must be a non-empty array")
    machine_names = [machine.get("name") for machine in machines]
    if any(not isinstance(name, str) or not name for name in machine_names):
        raise ValueError("every infrastructure machine needs a name")
    if len(machine_names) != len(set(machine_names)):
        raise ValueError("machine names must be unique")

    for family, spec in request["requirements"].items():
        if family not in NFR_CATALOG:
            raise ValueError(f"unknown NFR family '{family}'")
        if not isinstance(spec, dict):
            raise ValueError(f"requirements.{family} must be an object")
        level = spec.get("level", "none")
        if level not in LEVELS:
            raise ValueError(f"requirements.{family}.level must be one of {LEVELS}")
        algorithms = spec.get("algorithms") or NFR_CATALOG[family]["selectable"]
        for algorithm in algorithms:
            if algorithm not in NFR_CATALOG[family]["selectable"]:
                raise ValueError(
                    f"algorithm '{algorithm}' is not selectable for {family}; "
                    f"available={NFR_CATALOG[family]['selectable']}"
                )
        weight = spec.get("weight", 1.0)
        minimum = spec.get("min_strength", 1.0)
        if not isinstance(weight, (int, float)) or weight < 0:
            raise ValueError(f"requirements.{family}.weight must be non-negative")
        if not isinstance(minimum, (int, float)) or minimum < 0:
            raise ValueError(f"requirements.{family}.min_strength must be non-negative")

    known_families = set(FAMILIES)
    for edge in request["workflow"]["edges"]:
        families = edge.get("nfr_families")
        if families is not None:
            unknown = set(families) - known_families
            if unknown:
                raise ValueError(f"edge {_edge_source(edge)}->{_edge_target(edge)} has unknown NFR families {sorted(unknown)}")

    for family in FAMILIES:
        if requirement_level(request, family) == "required" and not eligible_edges(request, family):
            raise ValueError(f"{family} is required but no workflow edge is eligible for it")

    policy_scope(request)
    policy_groups(request)

    constraints = request.get("constraints", {})
    if not any(
        constraints.get(key) is not None
        for key in ("global_energy_budget_j", "max_makespan_s")
    ) and not constraints.get("machine_energy_budgets_j"):
        print("WARNING: no energy budget or deadline was declared", file=sys.stderr)

    return request


def requirement_level(request: dict[str, Any], family: str) -> str:
    return request["requirements"].get(family, {}).get("level", "none")


def requirement_algorithms(request: dict[str, Any], family: str) -> list[str]:
    spec = request["requirements"].get(family, {})
    return list(spec.get("algorithms") or NFR_CATALOG[family]["selectable"])


def edge_allows_family(edge: dict[str, Any], family: str) -> bool:
    if edge.get("nfr_eligible", True) is False:
        return False
    families = edge.get("nfr_families")
    return families is None or family in families


def eligible_edge_entries(
    request: dict[str, Any], family: str
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, edge)
        for index, edge in enumerate(request["workflow"]["edges"])
        if edge_allows_family(edge, family)
    ]


def eligible_edges(request: dict[str, Any], family: str) -> list[dict[str, Any]]:
    return [edge for _, edge in eligible_edge_entries(request, family)]


def policy_groups(request: dict[str, Any]) -> list[str]:
    groups: list[str] = []
    for index, edge in enumerate(request["workflow"]["edges"]):
        if edge.get("nfr_eligible", True) is False:
            continue
        if not any(
            requirement_level(request, family) != "none"
            and edge_allows_family(edge, family)
            for family in FAMILIES
        ):
            continue
        group = edge_policy_group(request, edge, index)
        if group not in groups:
            groups.append(group)
    return groups


# ---------------------------------------------------------------------------
# NFR design space and coverage-first objective
# ---------------------------------------------------------------------------


def mechanism_strength(
    request: dict[str, Any], family: str, option: dict[str, Any] | None
) -> float:
    if option is None:
        return 0.0
    if family == "reliability":
        return float(option.get("m", 0))
    overrides = request["requirements"].get(family, {}).get("strength_overrides") or {}
    algorithm = option["algorithm"]
    if algorithm in overrides:
        return float(overrides[algorithm])
    return float(NFR_CATALOG[family]["strength"].get(algorithm, 0.0))


def build_family_options(request: dict[str, Any], family: str) -> list[dict[str, Any] | None]:
    level = requirement_level(request, family)
    if level == "none":
        return [None]

    options: list[dict[str, Any] | None] = []
    if level == "optional":
        options.append(None)

    if family == "reliability":
        spec = request["requirements"].get(family, {})
        pairs = spec.get("ida_km") or [[spec.get("ida_k", 8), spec.get("ida_m", 4)]]
        for algorithm in requirement_algorithms(request, family):
            for pair in pairs:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("requirements.reliability.ida_km entries must be [k,m]")
                k, m = int(pair[0]), int(pair[1])
                if k <= 0 or m < 0:
                    raise ValueError("reliability k must be >0 and m must be >=0")
                options.append({"algorithm": algorithm, "k": k, "m": m})
    else:
        options.extend({"algorithm": algorithm} for algorithm in requirement_algorithms(request, family))
    return options


def group_has_family(
    request: dict[str, Any], group: str, family: str
) -> bool:
    if requirement_level(request, family) == "none":
        return False
    return any(
        edge_policy_group(request, edge, index) == group
        and edge_allows_family(edge, family)
        for index, edge in enumerate(request["workflow"]["edges"])
    )


def build_group_combos(
    request: dict[str, Any], group: str
) -> list[dict[str, dict[str, Any] | None]]:
    options_by_family = {
        family: (
            build_family_options(request, family)
            if group_has_family(request, group, family)
            else [None]
        )
        for family in FAMILIES
    }
    combinations = []
    for values in itertools.product(*(options_by_family[family] for family in FAMILIES)):
        combinations.append({
            NFR_CATALOG[family]["slot"]: option
            for family, option in zip(FAMILIES, values)
        })
    return combinations


def build_nfr_policies(
    request: dict[str, Any]
) -> list[dict[str, dict[str, dict[str, Any] | None]]]:
    groups = policy_groups(request)
    if not groups:
        return [{}]
    combinations_by_group = [build_group_combos(request, group) for group in groups]
    policies = []
    for values in itertools.product(*combinations_by_group):
        policies.append({group: combo for group, combo in zip(groups, values)})
    return policies


def empty_combo() -> dict[str, None]:
    return {slot: None for slot in SLOTS}


def empty_policy(request: dict[str, Any]) -> dict[str, dict[str, None]]:
    return {group: empty_combo() for group in policy_groups(request)}


def is_reference(policy: dict[str, Any]) -> bool:
    return all(
        combo.get(slot) is None
        for combo in policy.values()
        for slot in SLOTS
    )


def family_option_for_edge(
    policy: dict[str, Any],
    request: dict[str, Any],
    edge: dict[str, Any],
    index: int,
    family: str,
) -> dict[str, Any] | None:
    group = edge_policy_group(request, edge, index)
    combo = policy.get(group) or empty_combo()
    return combo.get(NFR_CATALOG[family]["slot"])


def edge_family_satisfied(
    policy: dict[str, Any],
    request: dict[str, Any],
    edge: dict[str, Any],
    index: int,
    family: str,
) -> bool:
    option = family_option_for_edge(policy, request, edge, index, family)
    minimum = float(request["requirements"].get(family, {}).get("min_strength", 1.0))
    return option is not None and mechanism_strength(request, family, option) >= minimum


def is_admissible(policy: dict[str, Any], request: dict[str, Any]) -> bool:
    for family in FAMILIES:
        if requirement_level(request, family) != "required":
            continue
        for index, edge in eligible_edge_entries(request, family):
            if not edge_family_satisfied(policy, request, edge, index, family):
                return False
    return True


def annotate_coverage(policy: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    fulfilled_clauses: dict[str, int] = {}
    assurance_profile: dict[str, list[float]] = {}
    weighted_coverage = 0.0
    clause_count = 0

    for family in FAMILIES:
        if requirement_level(request, family) == "none":
            continue
        fulfilled = 0
        strengths: list[float] = []
        for index, edge in eligible_edge_entries(request, family):
            option = family_option_for_edge(policy, request, edge, index, family)
            strength = mechanism_strength(request, family, option)
            strengths.append(strength)
            if edge_family_satisfied(policy, request, edge, index, family):
                fulfilled += 1
        fulfilled_clauses[family] = fulfilled
        assurance_profile[family] = strengths
        clause_count += fulfilled
        weighted_coverage += fulfilled * float(
            request["requirements"].get(family, {}).get("weight", 1.0)
        )

    return {
        "coverage_count": clause_count,
        "coverage_weighted": weighted_coverage,
        "fulfilled_clauses": fulfilled_clauses,
        "assurance_profile": assurance_profile,
    }


def family_priorities(request: dict[str, Any]) -> list[str]:
    active = [family for family in FAMILIES if requirement_level(request, family) != "none"]
    return sorted(
        active,
        key=lambda family: (
            -float(request["requirements"].get(family, {}).get("weight", 1.0)),
            FAMILIES.index(family),
        ),
    )


def assurance_key(result: dict[str, Any], request: dict[str, Any]) -> tuple[float, ...]:
    values = [float(result["coverage_weighted"]), float(result["coverage_count"])]
    # After equal coverage, compare the weakest edge first within each family.
    # This is lexicographic use of ordinal levels, not arithmetic aggregation.
    for family in family_priorities(request):
        values.extend(sorted(float(value) for value in result["assurance_profile"].get(family, [])))
    return tuple(values)


def combo_label(combo: dict[str, Any]) -> str:
    labels = []
    for slot in SLOTS:
        option = combo.get(slot)
        if option is None:
            continue
        label = option["algorithm"]
        if slot == "erasure":
            label += f"(k={option['k']},m={option['m']})"
        labels.append(label)
    return "+".join(labels) if labels else "none"


def nfr_label(policy: dict[str, Any]) -> str:
    if not policy or is_reference(policy):
        return "no NFRs"
    if set(policy) == {"global"}:
        return combo_label(policy["global"])
    return "; ".join(
        f"{group}[{combo_label(combo)}]"
        for group, combo in policy.items()
        if not all(combo.get(slot) is None for slot in SLOTS)
    ) or "no NFRs"


# ---------------------------------------------------------------------------
# Workflow-engine plans / placement space
# ---------------------------------------------------------------------------


def build_placements(request: dict[str, Any]) -> list[dict[str, Any]]:
    workflow = request["workflow"]
    tasks = workflow["tasks"]
    task_ids = [_task_id(task) for task in tasks]
    machine_names = [machine["name"] for machine in request["infrastructure"]["machines"]]

    plans = workflow.get("plans")
    if plans:
        normalized = []
        for index, plan in enumerate(plans):
            placement = plan.get("placement", plan.get("tasks"))
            if not isinstance(placement, dict):
                raise ValueError("every workflow plan needs a placement object")
            missing = [task for task in task_ids if task not in placement]
            unknown = [machine for machine in placement.values() if machine not in machine_names]
            if missing or unknown:
                raise ValueError(f"invalid plan {index}: missing={missing}, unknown_machines={unknown}")
            normalized.append({
                "plan_id": str(plan.get("id", f"plan-{index}")),
                "placement": {task: placement[task] for task in task_ids},
            })
        return normalized

    choices = []
    for task in tasks:
        task_id = _task_id(task)
        if task.get("machine"):
            allowed = [task["machine"]]
        else:
            allowed = list(task.get("allowed_machines") or machine_names)
        unknown = [machine for machine in allowed if machine not in machine_names]
        if unknown:
            raise ValueError(f"task '{task_id}' allows unknown machines {unknown}")
        choices.append(allowed)

    placements = []
    for index, values in enumerate(itertools.product(*choices)):
        placements.append({
            "plan_id": f"enumerated-{index}",
            "placement": dict(zip(task_ids, values)),
        })
    return placements


# ---------------------------------------------------------------------------
# Simulator configuration generation
# ---------------------------------------------------------------------------


def mechanism_model(request: dict[str, Any], family: str, algorithm: str) -> dict[str, Any]:
    model: dict[str, Any] = {}
    global_models = request.get("mechanism_models") or {}
    if isinstance(global_models.get(algorithm), dict):
        model.update(global_models[algorithm])
    family_models = request["requirements"].get(family, {}).get("mechanism_models") or {}
    if isinstance(family_models.get(algorithm), dict):
        model.update(family_models[algorithm])
    return model


def option_to_operation(
    request: dict[str, Any], family: str, option: dict[str, Any]
) -> dict[str, Any]:
    operation = {
        "type": NFR_CATALOG[family]["operation_type"],
        "algorithm": option["algorithm"],
    }
    if family == "reliability":
        operation.update({"k": option["k"], "m": option["m"]})
    operation.update(mechanism_model(request, family, option["algorithm"]))
    return operation


def edge_pipeline(
    edge: dict[str, Any],
    edge_index: int,
    policy: dict[str, Any],
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    fixed = copy.deepcopy(edge.get("fixed_pipeline") or [])
    if edge.get("nfr_eligible", True) is False:
        return fixed

    allowed = set(edge.get("nfr_families") or FAMILIES)
    order = request.get("objective", {}).get("pipeline_order") or list(FAMILIES)
    if len(order) != len(FAMILIES) or set(order) != set(FAMILIES):
        raise ValueError(f"objective.pipeline_order must contain each family exactly once: {FAMILIES}")

    pipeline = fixed
    for family in order:
        if family not in allowed:
            continue
        option = family_option_for_edge(policy, request, edge, edge_index, family)
        if option is not None:
            pipeline.append(option_to_operation(request, family, option))
    return pipeline


def normalize_workload(workflow: dict[str, Any]) -> dict[str, Any]:
    source = workflow.get("workload") or workflow.get("trace") or {}
    return {
        "instances": int(source.get("instances", source.get("objects", 1))),
        "arrival_model": source.get("arrival_model", "fixed"),
        "mean_interarrival_s": float(source.get("mean_interarrival_s", source.get("inter_arrival", 0.0))),
        "input_size_bytes": float(source.get("input_size_bytes", source.get("size_bytes", 1048576))),
        "input_size_cv": float(source.get("input_size_cv", source.get("stddevS", 0.0))),
    }


def build_config(candidate: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    workflow = request["workflow"]
    placement = candidate["placement"]
    policy = candidate["nfr_policy"]

    tasks = []
    for task in workflow["tasks"]:
        task_id = _task_id(task)
        generated = {
            "id": task_id,
            "machine": placement[task_id],
            "service_time_s": float(task.get("service_time_s", task.get("service_time", 1.0))),
            "source_input_size_factor": float(task.get("source_input_size_factor", task.get("input_size_factor", 1.0))),
            "output_size_factor": float(task.get("output_size_factor", task.get("size_factor", 1.0))),
        }
        for key in ("read_bandwidth_Bps", "write_bandwidth_Bps", "b_fs_read", "b_fs_write"):
            if key in task:
                generated[key] = task[key]
        tasks.append(generated)

    edges = []
    for index, edge in enumerate(workflow["edges"]):
        source, target = _edge_source(edge), _edge_target(edge)
        edges.append({
            "id": edge.get("id", f"{source}->{target}-{index}"),
            "from": source,
            "to": target,
            "size_factor": float(edge.get("size_factor", 1.0)),
            "pipeline": edge_pipeline(edge, index, policy, request),
        })

    simulator = request.get("simulator", {})
    return {
        "schema_version": 2,
        "seed": 0,
        "config_hash": "",
        "service_time_model": simulator.get("service_time_model", "linear"),
        "real_values_dir": simulator.get("real_values_dir", request.get("real_values_dir", "")),
        "strict_calibration": simulator.get("strict_calibration", True),
        "allow_extrapolation": simulator.get("allow_extrapolation", False),
        "workload": normalize_workload(workflow),
        "workflow": {"tasks": tasks, "edges": edges},
        "infrastructure": copy.deepcopy(request["infrastructure"]),
    }


def config_fingerprint(config: dict[str, Any]) -> str:
    canonical = copy.deepcopy(config)
    canonical["seed"] = 0
    canonical["config_hash"] = ""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Simulator execution and statistical aggregation
# ---------------------------------------------------------------------------


def replication_seeds(args: argparse.Namespace, request: dict[str, Any]) -> list[int]:
    declared = request.get("simulator", {}).get("seeds")
    if declared:
        if len(declared) < args.replications:
            raise ValueError("simulator.seeds contains fewer entries than --replications")
        return [int(seed) for seed in declared[: args.replications]]
    return [args.base_seed + index * 104729 for index in range(args.replications)]


def run_simulator(
    simulator_cmd: str,
    simulator_dir: str,
    config: dict[str, Any],
    run_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, allow_nan=False)

    command = [simulator_cmd, str(config_path.resolve()), "--output-dir", str(run_dir.resolve())]
    try:
        completed = subprocess.run(
            command,
            cwd=simulator_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"simulator exceeded {timeout}s timeout") from exc

    (run_dir / "stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    summary_path = run_dir / "run_summary.json"
    if not summary_path.exists():
        raise RuntimeError(
            f"simulator produced no run_summary.json (exit={completed.returncode}): "
            f"{(completed.stderr or '').strip()}"
        )
    with summary_path.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    if completed.returncode != 0 or summary.get("status") != "ok":
        raise RuntimeError(summary.get("error") or (completed.stderr or "simulator failed").strip())
    return summary


def validate_summary(summary: dict[str, Any], config_hash: str, seed: int) -> None:
    if summary.get("config_hash") != config_hash:
        raise RuntimeError("simulator summary config_hash does not match the candidate")
    if int(summary.get("seed", -1)) != int(seed):
        raise RuntimeError("simulator summary seed does not match the requested replication")
    if summary.get("instances_completed") != summary.get("instances_expected"):
        raise RuntimeError("simulator returned an incomplete DAG execution")
    if int(summary.get("missing_predictions", 0)) != 0:
        raise RuntimeError("simulator reported missing calibration predictions")

    for key in ("makespan_s", "total_energy_j", "network_energy_j"):
        value = summary.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise RuntimeError(f"simulator returned invalid {key}={value!r}")
    machine_energy = summary.get("machine_energy_j")
    if not isinstance(machine_energy, dict) or not machine_energy:
        raise RuntimeError("simulator summary has no machine_energy_j object")
    for machine, value in machine_energy.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise RuntimeError(f"invalid energy for machine {machine}: {value!r}")


def sample_statistics(values: list[float]) -> dict[str, float]:
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"mean": mean, "stddev": 0.0, "ucb95": mean}
    stddev = statistics.stdev(values)
    critical = T95_ONE_SIDED.get(len(values) - 1, 1.645)
    ucb = mean + critical * stddev / math.sqrt(len(values))
    return {"mean": mean, "stddev": stddev, "ucb95": ucb}


def simulate(
    base_config: dict[str, Any],
    args: argparse.Namespace,
    request: dict[str, Any],
    cache: dict[str, dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, bool]:
    fingerprint = config_fingerprint(base_config)
    if fingerprint in cache:
        return cache[fingerprint], True

    makespans: list[float] = []
    total_energies: list[float] = []
    network_energies: list[float] = []
    per_machine: list[dict[str, float]] = []
    extrapolated = 0

    for seed in replication_seeds(args, request):
        config = copy.deepcopy(base_config)
        config["seed"] = int(seed)
        config["config_hash"] = fingerprint
        run_dir = Path(args.output_dir) / "runs" / fingerprint / str(seed)
        try:
            summary = run_simulator(
                args.simulator_cmd_abs,
                args.simulator_dir,
                config,
                run_dir,
                args.timeout,
            )
            validate_summary(summary, fingerprint, seed)
        except RuntimeError as exc:
            print(f"  simulator failure for seed {seed}: {exc}", file=sys.stderr)
            cache[fingerprint] = None
            return None, False

        makespans.append(float(summary["makespan_s"]))
        total_energies.append(float(summary["total_energy_j"]))
        network_energies.append(float(summary["network_energy_j"]))
        per_machine.append({name: float(value) for name, value in summary["machine_energy_j"].items()})
        extrapolated += int(summary.get("extrapolated_predictions", 0))

        if not args.keep_runs:
            shutil.rmtree(run_dir, ignore_errors=True)

    machines = sorted({machine for sample in per_machine for machine in sample})
    makespan_stats = sample_statistics(makespans)
    energy_stats = sample_statistics(total_energies)
    network_stats = sample_statistics(network_energies)
    machine_stats = {
        machine: sample_statistics([sample.get(machine, 0.0) for sample in per_machine])
        for machine in machines
    }

    metrics = {
        "config_hash": fingerprint,
        "makespan": makespan_stats["mean"],
        "makespan_stddev": makespan_stats["stddev"],
        "makespan_ucb95": makespan_stats["ucb95"],
        "total_energy": energy_stats["mean"],
        "total_energy_stddev": energy_stats["stddev"],
        "total_energy_ucb95": energy_stats["ucb95"],
        "network_energy": network_stats["mean"],
        "network_energy_ucb95": network_stats["ucb95"],
        "energy_by_machine": {machine: values["mean"] for machine, values in machine_stats.items()},
        "energy_by_machine_ucb95": {machine: values["ucb95"] for machine, values in machine_stats.items()},
        "replications": args.replications,
        "seeds": replication_seeds(args, request),
        "extrapolated_predictions": extrapolated,
    }
    cache[fingerprint] = metrics
    return metrics, False


def evaluate_candidate(
    candidate: dict[str, Any],
    candidate_id: int,
    request: dict[str, Any],
    args: argparse.Namespace,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    base_config = build_config(candidate, request)
    metrics, cached = simulate(base_config, args, request, cache)
    if metrics is None:
        return None

    result = {
        "id": candidate_id,
        "plan_id": candidate["plan_id"],
        "placement": candidate["placement"],
        "nfr_policy": candidate["nfr_policy"],
        "nfr_label": nfr_label(candidate["nfr_policy"]),
        "reference": is_reference(candidate["nfr_policy"]),
        "admissible": is_admissible(candidate["nfr_policy"], request),
        "cached": cached,
        "simulator_config": base_config,
    }
    result.update(metrics)
    result.update(annotate_coverage(candidate["nfr_policy"], request))
    return result


# ---------------------------------------------------------------------------
# Feasibility, ranking, and attribution
# ---------------------------------------------------------------------------


def check_constraints(result: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    violations = []
    use_ucb = constraints.get("risk_mode", "ucb95") != "mean"
    makespan = result["makespan_ucb95"] if use_ucb else result["makespan"]
    energy = result["total_energy_ucb95"] if use_ucb else result["total_energy"]
    machine_energy = result["energy_by_machine_ucb95"] if use_ucb else result["energy_by_machine"]
    label = "95% upper bound" if use_ucb else "mean"

    deadline = constraints.get("max_makespan_s")
    if deadline is not None and makespan > deadline:
        violations.append(f"{label} makespan {makespan:.3f}s > deadline {deadline:.3f}s")
    budget = constraints.get("global_energy_budget_j")
    if budget is not None and energy > budget:
        violations.append(f"{label} total energy {energy:.3f}J > budget {budget:.3f}J")
    for machine, machine_budget in (constraints.get("machine_energy_budgets_j") or {}).items():
        value = machine_energy.get(machine, 0.0)
        if value > machine_budget:
            violations.append(
                f"{label} {machine} energy {value:.3f}J > budget {machine_budget:.3f}J"
            )
    return violations


def cost_score(result: dict[str, Any], request: dict[str, Any]) -> float:
    constraints = request.get("constraints", {})
    objective = request.get("objective", {})
    energy_weight = float(objective.get("energy_weight", 0.5))
    budget = constraints.get("global_energy_budget_j")
    deadline = constraints.get("max_makespan_s")
    energy = result["total_energy_ucb95"]
    makespan = result["makespan_ucb95"]
    energy_normalized = energy / budget if budget else 0.0
    time_normalized = makespan / deadline if deadline else 0.0
    if budget and deadline:
        return energy_weight * energy_normalized + (1.0 - energy_weight) * time_normalized
    return energy_normalized or time_normalized


def rank_feasible(results: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
    feasible = [result for result in results if result["feasible"]]
    for result in feasible:
        result["cost_score"] = cost_score(result, request)
    return sorted(
        feasible,
        key=lambda result: (
            tuple(-value for value in assurance_key(result, request)),
            result["cost_score"],
        ),
    )


def pareto_front(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    front = []
    for candidate in results:
        dominated = any(
            other["coverage_weighted"] >= candidate["coverage_weighted"]
            and other["total_energy_ucb95"] <= candidate["total_energy_ucb95"]
            and other["makespan_ucb95"] <= candidate["makespan_ucb95"]
            and (
                other["coverage_weighted"] > candidate["coverage_weighted"]
                or other["total_energy_ucb95"] < candidate["total_energy_ucb95"]
                or other["makespan_ucb95"] < candidate["makespan_ucb95"]
            )
            for other in results
            if other["id"] != candidate["id"]
        )
        if not dominated:
            front.append(candidate)
    return sorted(front, key=lambda result: result["total_energy_ucb95"])


def energy_frontier(
    results: list[dict[str, Any]], request: dict[str, Any], steps: int = 50
) -> list[dict[str, Any]]:
    deadline = request.get("constraints", {}).get("max_makespan_s")
    pool = [
        result
        for result in results
        if result["admissible"]
        and (deadline is None or result["makespan_ucb95"] <= deadline)
    ]
    if not pool:
        return []
    low = min(result["total_energy_ucb95"] for result in pool)
    high = max(result["total_energy_ucb95"] for result in pool)
    budgets = [low] if high <= low else [low + (high - low) * index / (steps - 1) for index in range(steps)]
    frontier = []
    for budget in budgets:
        affordable = [result for result in pool if result["total_energy_ucb95"] <= budget]
        if not affordable:
            frontier.append({"budget_j": budget, "coverage_weighted": None})
            continue
        best = max(
            affordable,
            key=lambda result: (assurance_key(result, request), -result["total_energy_ucb95"]),
        )
        frontier.append({
            "budget_j": budget,
            "coverage_weighted": best["coverage_weighted"],
            "coverage_count": best["coverage_count"],
            "candidate_id": best["id"],
            "nfr_label": best["nfr_label"],
            "energy_ucb95_j": best["total_energy_ucb95"],
            "makespan_ucb95_s": best["makespan_ucb95"],
        })
    return frontier


def attribute_nfr_costs(
    recommended: dict[str, Any],
    request: dict[str, Any],
    args: argparse.Namespace,
    cache: dict[str, dict[str, Any] | None],
    next_id: int,
) -> dict[str, Any]:
    baseline_candidate = {
        "plan_id": recommended["plan_id"],
        "placement": recommended["placement"],
        "nfr_policy": empty_policy(request),
    }
    baseline = evaluate_candidate(baseline_candidate, next_id, request, args, cache)
    next_id += 1
    attribution: dict[str, Any] = {"marginal": {}}
    if baseline is None:
        return attribution

    attribution["no_nfr"] = {
        "makespan_s": baseline["makespan"],
        "total_energy_j": baseline["total_energy"],
        "makespan_overhead_s": recommended["makespan"] - baseline["makespan"],
        "energy_overhead_j": recommended["total_energy"] - baseline["total_energy"],
    }

    for family in FAMILIES:
        slot = NFR_CATALOG[family]["slot"]
        if not any(combo.get(slot) is not None for combo in recommended["nfr_policy"].values()):
            continue
        leave_one_out = copy.deepcopy(recommended["nfr_policy"])
        removed = []
        for group, combo in leave_one_out.items():
            option = combo.get(slot)
            if option is not None:
                removed.append(f"{group}:{option['algorithm']}")
                combo[slot] = None
        candidate = {
            "plan_id": recommended["plan_id"],
            "placement": recommended["placement"],
            "nfr_policy": leave_one_out,
        }
        result = evaluate_candidate(candidate, next_id, request, args, cache)
        next_id += 1
        if result is None:
            continue
        attribution["marginal"][family] = {
            "removed": removed,
            "makespan_delta_s": recommended["makespan"] - result["makespan"],
            "energy_delta_j": recommended["total_energy"] - result["total_energy"],
        }

    if attribution["marginal"]:
        sum_energy = sum(item["energy_delta_j"] for item in attribution["marginal"].values())
        sum_time = sum(item["makespan_delta_s"] for item in attribution["marginal"].values())
        attribution["interaction"] = {
            "energy_j": attribution["no_nfr"]["energy_overhead_j"] - sum_energy,
            "makespan_s": attribution["no_nfr"]["makespan_overhead_s"] - sum_time,
        }
    return attribution


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(results: list[dict[str, Any]], ranked: list[dict[str, Any]], args: argparse.Namespace) -> None:
    admissible = sum(result["admissible"] for result in results)
    feasible = sum(result["feasible"] for result in results)
    print("\n=== Continuum DAG NFR profile ===")
    print(f"evaluated={len(results)} admissible={admissible} feasible={feasible}")
    print(f"replications={args.replications}; constraints use 95% one-sided upper bounds")
    if not ranked:
        print("No candidate satisfies all mandatory NFRs and resource constraints.")
        return
    print("\nTop candidates:")
    for rank, result in enumerate(ranked[: args.top_n], 1):
        print(
            f"{rank}. id={result['id']} plan={result['plan_id']} "
            f"coverage={result['coverage_count']} ({result['coverage_weighted']:.2f} weighted) "
            f"NFR={result['nfr_label']} "
            f"T={result['makespan']:.3f}s/UCB={result['makespan_ucb95']:.3f}s "
            f"E={result['total_energy']:.3f}J/UCB={result['total_energy_ucb95']:.3f}J"
        )


def write_results_csv(results: list[dict[str, Any]], path: Path) -> None:
    machines = sorted({machine for result in results for machine in result["energy_by_machine"]})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "id", "plan_id", "placement", "nfr", "admissible", "feasible",
                "coverage_count", "coverage_weighted", "makespan_mean_s",
                "makespan_ucb95_s", "energy_mean_j", "energy_ucb95_j",
                "network_energy_mean_j", "extrapolated_predictions",
            ]
            + [f"energy_{machine}_mean_j" for machine in machines]
            + [f"energy_{machine}_ucb95_j" for machine in machines]
            + ["violations", "pareto"]
        )
        for result in results:
            placement = ";".join(f"{task}={machine}" for task, machine in result["placement"].items())
            writer.writerow(
                [
                    result["id"], result["plan_id"], placement, result["nfr_label"],
                    int(result["admissible"]), int(result["feasible"]),
                    result["coverage_count"], f"{result['coverage_weighted']:.6f}",
                    f"{result['makespan']:.9f}", f"{result['makespan_ucb95']:.9f}",
                    f"{result['total_energy']:.9f}", f"{result['total_energy_ucb95']:.9f}",
                    f"{result['network_energy']:.9f}", result["extrapolated_predictions"],
                ]
                + [f"{result['energy_by_machine'].get(machine, 0.0):.9f}" for machine in machines]
                + [f"{result['energy_by_machine_ucb95'].get(machine, 0.0):.9f}" for machine in machines]
                + [" | ".join(result["violations"]), int(result.get("pareto", False))]
            )


def write_frontier_csv(frontier: list[dict[str, Any]], path: Path) -> None:
    if not frontier:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "budget_j", "coverage_weighted", "coverage_count", "candidate_id",
            "nfr", "energy_ucb95_j", "makespan_ucb95_s",
        ])
        for point in frontier:
            writer.writerow([
                point.get("budget_j"), point.get("coverage_weighted"),
                point.get("coverage_count"), point.get("candidate_id"),
                point.get("nfr_label"), point.get("energy_ucb95_j"),
                point.get("makespan_ucb95_s"),
            ])


def serializable_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key not in {"nfr_policy", "simulator_config"}}


# ---------------------------------------------------------------------------
# CLI and orchestration
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Maximize fulfilled NFR clauses for a continuum DAG under energy and deadline constraints"
    )
    parser.add_argument("--request", required=True, help="Profiling request JSON")
    parser.add_argument("--simulator-cmd", required=True, help="Path to nfr_dag_sim")
    parser.add_argument("--simulator-dir", default=".", help="Simulator working directory")
    parser.add_argument("--output-dir", default="profiler-output", help="Profiler output directory")
    parser.add_argument("--replications", type=int, default=5)
    parser.add_argument("--base-seed", type=int, default=2026001)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-attribution", action="store_true")
    parser.add_argument("--keep-runs", action="store_true")
    args = parser.parse_args()
    if args.replications < 1:
        parser.error("--replications must be >= 1")
    if args.max_candidates < 1:
        parser.error("--max-candidates must be >= 1")
    args.simulator_cmd_abs = str(Path(args.simulator_cmd).resolve())
    args.simulator_dir = str(Path(args.simulator_dir).resolve())
    args.output_dir = str(Path(args.output_dir).resolve())
    return args


def build_candidates(
    request: dict[str, Any], args: argparse.Namespace
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    placements = build_placements(request)
    policies = build_nfr_policies(request)
    baseline_per_plan = 0 if any(is_reference(policy) for policy in policies) else 1
    total = len(placements) * (len(policies) + baseline_per_plan)
    print(
        f"Search space: {len(placements)} workflow plans x "
        f"({len(policies)} edge-policy assignments + {baseline_per_plan} reference) = {total}"
    )
    if total > args.max_candidates and not args.dry_run:
        raise ValueError(
            f"search space {total} exceeds --max-candidates={args.max_candidates}; "
            "group edges with policy_group, use objective.policy_scope='global', "
            "provide workflow.plans, or reduce algorithms"
        )

    candidates = []
    for plan in placements:
        for policy in policies:
            candidates.append({
                "plan_id": plan["plan_id"],
                "placement": plan["placement"],
                "nfr_policy": policy,
            })
        if not any(is_reference(policy) for policy in policies):
            candidates.append({
                "plan_id": plan["plan_id"],
                "placement": plan["placement"],
                "nfr_policy": empty_policy(request),
            })
    return candidates, placements, policies


def main() -> int:
    args = parse_args()
    try:
        request = load_request(args.request)
        candidates, placements, policies = build_candidates(request, args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Request error: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for index, candidate in enumerate(candidates):
            coverage = annotate_coverage(candidate["nfr_policy"], request)
            print(
                f"candidate={index} plan={candidate['plan_id']} "
                f"placement={candidate['placement']} NFR={nfr_label(candidate['nfr_policy'])} "
                f"coverage={coverage['coverage_count']} admissible={is_admissible(candidate['nfr_policy'], request)}"
            )
        return 0

    cache: dict[str, dict[str, Any] | None] = {}
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        result = evaluate_candidate(candidate, index, request, args, cache)
        if result is None:
            print(f"candidate {index}: failed and skipped", file=sys.stderr)
            continue
        result["violations"] = check_constraints(result, request.get("constraints", {}))
        result["feasible"] = result["admissible"] and not result["violations"]
        status = "ok" if result["feasible"] else ("violated" if result["admissible"] else "inadmissible")
        cached = " cached" if result["cached"] else ""
        print(
            f"candidate {index} [{status}{cached}] plan={result['plan_id']} "
            f"NFR={result['nfr_label']} coverage={result['coverage_count']} "
            f"T_ucb={result['makespan_ucb95']:.3f}s E_ucb={result['total_energy_ucb95']:.3f}J"
        )
        results.append(result)

    if not results:
        print("Every simulator run failed", file=sys.stderr)
        return 3

    ranked = rank_feasible(results, request)
    front = pareto_front([result for result in results if result["admissible"]])
    front_ids = {result["id"] for result in front}
    for result in results:
        result["pareto"] = result["id"] in front_ids
    frontier = energy_frontier(results, request)
    recommended = ranked[0] if ranked else None

    attribution = None
    if recommended and not args.skip_attribution:
        attribution = attribute_nfr_costs(
            recommended, request, args, cache, len(candidates)
        )

    print_report(results, ranked, args)
    write_results_csv(results, output_dir / "profiler_results.csv")
    write_frontier_csv(frontier, output_dir / "profiler_frontier.csv")

    if recommended:
        recommended_config = copy.deepcopy(recommended["simulator_config"])
        recommended_config["seed"] = replication_seeds(args, request)[0]
        recommended_config["config_hash"] = recommended["config_hash"]
        with (output_dir / "recommended_config.json").open("w", encoding="utf-8") as handle:
            json.dump(recommended_config, handle, indent=2, allow_nan=False)

    report = {
        "request_file": str(Path(args.request).resolve()),
        "objective": "maximize fulfilled DAG-edge NFR clauses, then assurance, under risk-aware constraints",
        "policy_scope": policy_scope(request),
        "policy_groups": policy_groups(request),
        "search_space": {
            "plans": len(placements),
            "nfr_policies": len(policies),
            "evaluated": len(results),
            "distinct_simulated": sum(value is not None for value in cache.values()),
            "replications": args.replications,
            "common_seeds": replication_seeds(args, request),
        },
        "constraints": request.get("constraints", {}),
        "candidates": [serializable_result(result) for result in results],
        "pareto_ids": sorted(front_ids),
        "energy_frontier": frontier,
        "recommended_id": recommended["id"] if recommended else None,
        "nfr_cost_attribution": attribution,
    }
    with (output_dir / "profiler_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)

    print(f"Results written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
