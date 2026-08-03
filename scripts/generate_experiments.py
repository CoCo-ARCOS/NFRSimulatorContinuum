#!/usr/bin/env python3
"""Generate contract-profiler requests for the evaluation matrix."""

from __future__ import annotations

import argparse
import copy
import itertools
import json
from pathlib import Path
from typing import Any

PROPERTY_TO_FAMILY = {
    "confidentiality": "confidentiality",
    "integrity": "integrity",
    "authenticity": "integrity",
    "reliability": "reliability",
    "data_reduction": "compression",
    "data-reduction": "compression",
    "compression": "compression",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def validate_site(site: dict[str, Any]) -> None:
    infrastructure = site.get("infrastructure") or {}
    machines = infrastructure.get("machines") or []
    links = infrastructure.get("links") or []
    if not machines:
        raise ValueError("site configuration has no machines")
    names = {machine.get("name") for machine in machines}
    if not names:
        raise ValueError("site machine names cannot be empty")
    for machine in machines:
        for key in ("slots", "speed_factor", "read_bandwidth_Bps", "write_bandwidth_Bps", "max_power_w"):
            if float(machine.get(key, 0)) <= 0:
                raise ValueError(f"machine {machine.get('name')} requires a positive {key}")
        path = machine.get("real_values_dir")
        if isinstance(path, str) and "/ABSOLUTE/PATH/" in path:
            raise ValueError("replace all placeholder real_values_dir paths in the calibrated site template")
    if not links:
        raise ValueError("site configuration has no links")
    for link in links:
        if float(link.get("bandwidth_Bps", 0)) <= 0:
            raise ValueError(f"link {link.get('id')} requires positive bandwidth_Bps")


def clauses_for(workflow: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    for edge in workflow["edges"]:
        tags = set(edge.get("risk_tags") or [])
        allowed = set(edge.get("nfr_families") or [])
        for rule_index, rule in enumerate(contract["rules"]):
            if rule["tag"] not in tags:
                continue
            family = PROPERTY_TO_FAMILY.get(rule["property"])
            if family is None:
                raise ValueError(f"unknown contract property {rule['property']}")
            if allowed and family not in allowed:
                raise ValueError(
                    f"contract rule {rule['tag']} requests {family} on edge {edge['id']}, "
                    "but the edge does not enable that family"
                )
            clause = {
                "id": f"{edge['id']}-{rule['risk']}-{rule_index}",
                "scope": {"edge": edge["id"]},
                "risk": rule["risk"],
                "property": rule["property"],
                "modality": rule["modality"],
                "metric": rule["metric"],
                "minimum": rule["minimum"],
                "weight": rule["weight"],
            }
            clauses.append(clause)
    if not clauses:
        raise ValueError(f"contract produced no clauses for workflow {workflow['id']}")
    return clauses


def make_request(
    workflow: dict[str, Any],
    contract_id: str,
    contract: dict[str, Any],
    scale_id: str,
    workload: dict[str, Any],
    scope: str,
    site: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    edges = copy.deepcopy(workflow["edges"])
    for edge in edges:
        edge.pop("risk_tags", None)
    request = {
        "metadata": {
            "scenario_kind": kind,
            "workflow_id": workflow["id"],
            "contract_id": contract_id,
            "scale_id": scale_id,
            "policy_scope": scope,
            "site_id": site.get("site_id", "site"),
        },
        "workflow": {
            "workload": copy.deepcopy(workload),
            "tasks": copy.deepcopy(workflow["tasks"]),
            "edges": edges,
            "plans": copy.deepcopy(workflow["plans"]),
        },
        "infrastructure": copy.deepcopy(site["infrastructure"]),
        "requirements": copy.deepcopy(site["requirements"]),
        "contract": {
            "id": f"{workflow['id']}-{contract_id}",
            "description": contract.get("description", ""),
            "clauses": clauses_for(workflow, contract),
        },
        "constraints": {"risk_mode": "ucb95"},
        "objective": {
            "energy_weight": 0.5,
            "pipeline_order": ["compression", "confidentiality", "reliability", "integrity"],
            "policy_scope": scope,
        },
        "simulator": copy.deepcopy(site.get("simulator") or {}),
    }
    if site.get("mechanism_models"):
        request["mechanism_models"] = copy.deepcopy(site["mechanism_models"])
    return request


def scalability_request(groups: int, site: dict[str, Any], suite: dict[str, Any]) -> dict[str, Any]:
    tasks = []
    edges = []
    placement = {}
    clauses = []
    machines = site["infrastructure"]["machines"]
    fog_node = next((m["name"] for m in machines if "fog" in m["name"].lower()), "fog-1")
    hpc_node = next((m["name"] for m in machines if "cloud" in m["name"].lower() or "hpc" in m["name"].lower()), "hpc-1")
    
    for index in range(groups + 1):
        task_id = f"task-{index}"
        tasks.append({"id": task_id, "service_time_s": 0.2 + index * 0.02, "output_size_factor": 1.0})
        placement[task_id] = fog_node if index % 2 == 0 else hpc_node
    for index in range(groups):
        edge_id = f"edge-{index}"
        edges.append({
            "id": edge_id,
            "from": f"task-{index}",
            "to": f"task-{index + 1}",
            "policy_group": f"group-{index}",
            "nfr_families": ["confidentiality", "integrity"],
        })
        clauses.extend([
            {
                "id": f"{edge_id}-confidentiality",
                "scope": {"edge": edge_id},
                "risk": "unauthorized_disclosure",
                "property": "confidentiality",
                "modality": "optional",
                "metric": "assurance_level",
                "minimum": 2,
                "weight": 1,
            },
            {
                "id": f"{edge_id}-integrity",
                "scope": {"edge": edge_id},
                "risk": "corruption",
                "property": "integrity",
                "modality": "optional",
                "metric": "assurance_level",
                "minimum": 1,
                "weight": 1,
            },
        ])

    requirements = copy.deepcopy(site["requirements"])
    requirements["confidentiality"]["algorithms"] = ["AES"]
    requirements["integrity"]["algorithms"] = ["SHA256"]
    requirements["compression"]["algorithms"] = []
    requirements["reliability"]["algorithms"] = []
    scalability = suite["scalability"]
    request = {
        "metadata": {
            "scenario_kind": "scalability",
            "workflow_id": f"scalability-{groups}-groups",
            "contract_id": "optional-confidentiality-integrity",
            "scale_id": "scalability",
            "policy_scope": "edge",
            "site_id": site.get("site_id", "site"),
            "policy_groups": groups,
        },
        "workflow": {
            "workload": {
                "instances": int(scalability["instances"]),
                "arrival_model": "fixed",
                "mean_interarrival_s": 0.1,
                "input_size_bytes": int(scalability["input_size_bytes"]),
                "input_size_cv": 0.0,
            },
            "tasks": tasks,
            "edges": edges,
            "plans": [{"id": "fixed-plan", "placement": placement}],
        },
        "infrastructure": copy.deepcopy(site["infrastructure"]),
        "requirements": requirements,
        "contract": {"id": f"scalability-{groups}", "clauses": clauses},
        "constraints": {"risk_mode": "mean"},
        "objective": {
            "energy_weight": 0.5,
            "pipeline_order": ["compression", "confidentiality", "reliability", "integrity"],
            "policy_scope": "edge",
        },
        "simulator": copy.deepcopy(site.get("simulator") or {}),
    }
    if site.get("mechanism_models"):
        request["mechanism_models"] = copy.deepcopy(site["mechanism_models"])
    return request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    site = load_json(args.site)
    workflows_doc = load_json(args.workflows)
    suite = load_json(args.suite)
    validate_site(site)
    workflows = {item["id"]: item for item in workflows_doc["workflows"]}
    contracts = suite["contracts"]
    scales = suite["workload_scales"]

    requests_dir = args.output / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    scenarios: list[dict[str, Any]] = []
    seen: set[str] = set()

    for matrix in suite["catalog_matrix"]:
        for workflow_id, contract_id, scale_id, scope in itertools.product(
            matrix["workflows"], matrix["contracts"], matrix["scales"], matrix["policy_scopes"]
        ):
            scenario_id = f"{matrix['kind']}__{workflow_id}__{contract_id}__{scale_id}__{scope}"
            if scenario_id in seen:
                continue
            seen.add(scenario_id)
            request = make_request(
                workflows[workflow_id], contract_id, contracts[contract_id], scale_id,
                scales[scale_id], scope, site, matrix["kind"]
            )
            request_path = requests_dir / f"{scenario_id}.json"
            write_json(request_path, request)
            scenarios.append({
                "scenario_id": scenario_id,
                "kind": matrix["kind"],
                "workflow_id": workflow_id,
                "contract_id": contract_id,
                "scale_id": scale_id,
                "policy_scope": scope,
                "request": str(request_path.resolve()),
                "replications": int(suite["replications"]),
                "max_candidates": int(suite["max_candidates"]),
            })

    if suite.get("scalability", {}).get("enabled", False):
        for groups in suite["scalability"]["policy_groups"]:
            scenario_id = f"scalability__groups-{groups}"
            request = scalability_request(int(groups), site, suite)
            request_path = requests_dir / f"{scenario_id}.json"
            write_json(request_path, request)
            scenarios.append({
                "scenario_id": scenario_id,
                "kind": "scalability",
                "workflow_id": f"scalability-{groups}-groups",
                "contract_id": "optional-confidentiality-integrity",
                "scale_id": "scalability",
                "policy_scope": "edge",
                "policy_groups": int(groups),
                "request": str(request_path.resolve()),
                "replications": int(suite["scalability"]["replications"]),
                "max_candidates": max(int(suite["max_candidates"]), 4 ** int(groups)),
            })

    manifest = {
        "schema_version": 1,
        "site": str(args.site.resolve()),
        "workflows": str(args.workflows.resolve()),
        "suite": str(args.suite.resolve()),
        "base_seed": int(suite["base_seed"]),
        "timeout_s": float(suite["timeout_s"]),
        "constraint_sweep": suite["constraint_sweep"],
        "holdout": suite.get("holdout", {}),
        "scenarios": scenarios,
    }
    write_json(args.output / "manifest.json", manifest)
    print(f"Generated {len(scenarios)} scenarios in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
