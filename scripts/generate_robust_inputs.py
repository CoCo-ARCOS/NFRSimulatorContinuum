#!/usr/bin/env python3
"""Generate a robust evaluation matrix for contract-driven NFR realization.

The generator reads a site file and a workflow file, then creates:
  - low, nominal, and high power site files;
  - a robust workflow file with heterogeneous edge requirements and extra plans;
  - explicit artifact-scoped contracts;
  - a manifest of profiler requests.

The generated profiler requests use loose constraints so every candidate catalog
is collected once. The analysis script performs offline admission under budgets
defined relative to mandatory-compliant baselines.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


BUDGET_MULTIPLIERS = [1.05, 1.15, 1.30, 1.60, 2.00]


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def machine_idle_max(machine: dict[str, Any]) -> tuple[float, float]:
    max_w = float(machine.get("max_power_w", machine.get("max_power", 1.0)))
    frac = float(machine.get("static_power_fraction", machine.get("static_power_percent", 0.5)))
    idle_w = float(machine.get("power_metadata", {}).get("idle_power_w", max_w * frac))
    return idle_w, max_w


def set_machine_idle_max(machine: dict[str, Any], idle_w: float, max_w: float) -> None:
    frac = idle_w / max_w if max_w > 0 else 0.0
    machine["max_power_w"] = max_w
    machine["static_power_fraction"] = frac
    # The legacy profiler expects these names.
    machine["max_power"] = max_w
    machine["static_power_percent"] = frac
    md = machine.setdefault("power_metadata", {})
    md["idle_power_w_effective"] = idle_w
    md["max_power_w_effective"] = max_w


def tier_uncertainty(machine: dict[str, Any]) -> float:
    name = str(machine.get("name", "")).lower()
    md = machine.get("power_metadata", {})
    if "uncertainty_fraction" in md:
        return float(md["uncertainty_fraction"])
    if "edge" in name:
        return 0.20
    if "fog" in name:
        return 0.30
    if "cloud" in name or "hpc" in name:
        return 0.25
    return 0.20


def power_site(site: dict[str, Any], scenario: str) -> dict[str, Any]:
    out = copy.deepcopy(site)
    for machine in out["infrastructure"]["machines"]:
        idle, max_w = machine_idle_max(machine)
        u = tier_uncertainty(machine)
        mult = {"low": 1.0 - u, "nominal": 1.0, "high": 1.0 + u}[scenario]
        set_machine_idle_max(machine, idle * mult, max_w * mult)
        machine.setdefault("power_metadata", {})["scenario"] = scenario
        machine["power_scenario"] = scenario
    out["power_scenario"] = scenario
    out["description"] = out.get("description", "") + f" Power scenario: {scenario}."
    return out


def normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    tid = task.get("id", task.get("name"))
    return {
        "id": tid,
        "service_time_s": float(task.get("service_time_s", task.get("service_time", 1.0))),
        "output_size_factor": float(task.get("output_size_factor", task.get("size_factor", 1.0))),
    }


def heterogenize_edges(workflow: dict[str, Any]) -> dict[str, Any]:
    """Make edge risks less uniform while preserving edge IDs and DAG structure."""
    wf = copy.deepcopy(workflow)
    edges = wf.get("edges", [])
    n = len(edges)
    for i, e in enumerate(edges):
        eid = str(e.get("id", f"edge-{i}")).lower()
        # Raw/source edges: all protections may be relevant.
        if i == 0 or any(k in eid for k in ("raw", "source", "ingest")):
            e["policy_group"] = "raw"
            e["risk_tags"] = ["sensitive", "critical", "bulk", "resilient"]
            e["nfr_families"] = ["compression", "confidentiality", "reliability", "integrity"]
        # Final/result/archive edges: emphasize authenticity/reliability, usually smaller artifacts.
        elif i >= n - 2 or any(k in eid for k in ("result", "merged", "aggregate", "archive", "store", "sink")):
            e["policy_group"] = "result"
            e["risk_tags"] = ["critical", "authentic", "resilient"]
            e["nfr_families"] = ["reliability", "integrity", "confidentiality"]
        # Branch/intermediate bulk edges: compression can matter but reliability is optional.
        elif any(k in eid for k in ("prepared", "clean", "features", "curated", "branch")):
            e["policy_group"] = "intermediate"
            e["risk_tags"] = ["sensitive", "bulk", "authentic"]
            e["nfr_families"] = ["compression", "confidentiality", "integrity"]
        else:
            e["policy_group"] = "intermediate"
            e["risk_tags"] = ["bulk", "authentic"]
            e["nfr_families"] = ["compression", "integrity"]
    return wf


def nodes_by_tier(site: dict[str, Any]) -> dict[str, list[str]]:
    names = [m["name"] for m in site["infrastructure"]["machines"]]
    tiers = {
        "edge": [n for n in names if n.lower().startswith("edge")],
        "fog": [n for n in names if n.lower().startswith("fog")],
        "cloud": [n for n in names if n.lower().startswith("cloud") or n.lower().startswith("hpc")],
    }
    # Defensive fallback: partition by order.
    if not tiers["edge"]:
        tiers["edge"] = names[:1]
    if not tiers["fog"]:
        tiers["fog"] = names[1:2] or names[:1]
    if not tiers["cloud"]:
        tiers["cloud"] = names[2:] or names[-1:]
    return tiers


def add_extra_plans(workflow: dict[str, Any], site: dict[str, Any]) -> dict[str, Any]:
    wf = copy.deepcopy(workflow)
    tiers = nodes_by_tier(site)
    edge0 = tiers["edge"][0]
    edge1 = tiers["edge"][-1]
    fog0 = tiers["fog"][0]
    fog1 = tiers["fog"][-1]
    clouds = tiers["cloud"]

    tasks = [t["id"] for t in wf["tasks"]]
    if not tasks:
        return wf

    existing_ids = {p["id"] for p in wf.get("plans", [])}
    new_plans = []

    # Edge-heavy: source/prep at edge, rest fog to avoid excessive cloud transfer.
    if "edge-heavy" not in existing_ids:
        placement = {}
        for i, t in enumerate(tasks):
            if i == 0:
                placement[t] = edge0
            elif i == 1 and len(tiers["edge"]) > 1:
                placement[t] = edge1
            else:
                placement[t] = fog0 if i % 2 == 0 else fog1
        new_plans.append({"id": "edge-heavy", "placement": placement})

    # Cloud-balanced: fan-out across cloud nodes but keep source/prep close to edge/fog.
    if "cloud-balanced" not in existing_ids:
        placement = {}
        for i, t in enumerate(tasks):
            if i == 0:
                placement[t] = edge0
            elif i == 1:
                placement[t] = fog0
            else:
                placement[t] = clouds[(i - 2) % len(clouds)]
        new_plans.append({"id": "cloud-balanced", "placement": placement})

    # Data-locality: alternate fog nodes for intermediate/aggregate tasks, cloud only for heavy workers.
    if "data-locality" not in existing_ids:
        placement = {}
        for i, t in enumerate(tasks):
            name = t.lower()
            if i == 0 or "source" in name or "ingest" in name or "acquire" in name:
                placement[t] = edge0
            elif any(k in name for k in ("analyse", "analysis", "infer", "worker-4")):
                placement[t] = clouds[i % len(clouds)]
            elif any(k in name for k in ("archive", "store", "sink")):
                placement[t] = fog0
            else:
                placement[t] = fog0 if i % 2 == 0 else fog1
        new_plans.append({"id": "data-locality", "placement": placement})

    wf.setdefault("plans", []).extend(new_plans)
    return wf


def realistic_14_workflow(site: dict[str, Any]) -> dict[str, Any]:
    tiers = nodes_by_tier(site)
    e0 = tiers["edge"][0]
    f0 = tiers["fog"][0]
    f1 = tiers["fog"][-1]
    clouds = tiers["cloud"]
    tasks = [
        ("source", 2.0, 1.0),
        ("quality-check", 8.0, 0.95),
        ("prepare", 25.0, 0.75),
        ("split", 5.0, 1.0),
        ("prep-a", 35.0, 0.50),
        ("prep-b", 30.0, 0.45),
        ("prep-c", 40.0, 0.40),
        ("prep-d", 45.0, 0.35),
        ("infer-a", 120.0, 0.20),
        ("infer-b", 150.0, 0.18),
        ("infer-c", 180.0, 0.15),
        ("infer-d", 210.0, 0.12),
        ("aggregate", 45.0, 0.10),
        ("validate-archive", 20.0, 1.0),
    ]
    edges = [
        ("raw-data", "source", "quality-check", ["sensitive", "critical", "bulk", "resilient"], ["compression", "confidentiality", "reliability", "integrity"], "raw"),
        ("quality-data", "quality-check", "prepare", ["sensitive", "bulk", "authentic"], ["compression", "confidentiality", "integrity"], "intermediate"),
        ("prepared-data", "prepare", "split", ["bulk", "authentic"], ["compression", "integrity"], "intermediate"),
        ("split-a", "split", "prep-a", ["sensitive", "bulk"], ["compression", "confidentiality", "integrity"], "branch"),
        ("split-b", "split", "prep-b", ["bulk", "authentic"], ["compression", "integrity"], "branch"),
        ("split-c", "split", "prep-c", ["sensitive", "authentic"], ["confidentiality", "integrity"], "branch"),
        ("split-d", "split", "prep-d", ["bulk", "resilient"], ["compression", "reliability", "integrity"], "branch"),
        ("prep-a-features", "prep-a", "infer-a", ["sensitive", "bulk", "authentic"], ["compression", "confidentiality", "integrity"], "features"),
        ("prep-b-features", "prep-b", "infer-b", ["bulk", "authentic"], ["compression", "integrity"], "features"),
        ("prep-c-features", "prep-c", "infer-c", ["sensitive", "critical"], ["confidentiality", "integrity"], "features"),
        ("prep-d-features", "prep-d", "infer-d", ["resilient", "critical"], ["reliability", "integrity"], "features"),
        ("infer-a-result", "infer-a", "aggregate", ["critical", "authentic", "resilient"], ["reliability", "integrity"], "result"),
        ("infer-b-result", "infer-b", "aggregate", ["critical", "authentic"], ["integrity"], "result"),
        ("infer-c-result", "infer-c", "aggregate", ["critical", "authentic", "resilient"], ["reliability", "integrity"], "result"),
        ("infer-d-result", "infer-d", "aggregate", ["critical", "authentic", "resilient"], ["reliability", "integrity"], "result"),
        ("validated-result", "aggregate", "validate-archive", ["critical", "authentic", "resilient"], ["reliability", "integrity", "confidentiality"], "result"),
    ]
    placements = {
        "continuum-balanced": {
            "source": e0, "quality-check": f0, "prepare": f0, "split": f1,
            "prep-a": f0, "prep-b": f1, "prep-c": f0, "prep-d": f1,
            "infer-a": clouds[0 % len(clouds)], "infer-b": clouds[1 % len(clouds)],
            "infer-c": f1, "infer-d": clouds[2 % len(clouds)],
            "aggregate": f1, "validate-archive": f0,
        },
        "fog-heavy": {t[0]: (e0 if i == 0 else (f0 if i % 2 == 0 else f1)) for i, t in enumerate(tasks)},
        "hpc-heavy": {
            **{tasks[0][0]: e0, tasks[1][0]: f0},
            **{t[0]: clouds[i % len(clouds)] for i, t in enumerate(tasks[2:])},
        },
        "data-locality": {
            "source": e0, "quality-check": f0, "prepare": f0, "split": f0,
            "prep-a": f0, "prep-b": f0, "prep-c": f1, "prep-d": f1,
            "infer-a": clouds[0 % len(clouds)], "infer-b": f0,
            "infer-c": clouds[1 % len(clouds)], "infer-d": f1,
            "aggregate": f1, "validate-archive": f0,
        },
        "cloud-balanced": {
            **{tasks[0][0]: e0, tasks[1][0]: f0, tasks[2][0]: f0, tasks[3][0]: f1},
            **{t[0]: clouds[i % len(clouds)] for i, t in enumerate(tasks[4:])},
        },
    }
    return {
        "id": "realistic-14",
        "description": "Fourteen-task realistic fan-out/fan-in continuum workflow with heterogeneous artifact risks.",
        "tasks": [{"id": n, "service_time_s": s, "output_size_factor": sf} for n, s, sf in tasks],
        "edges": [
            {"id": eid, "from": src, "to": dst, "policy_group": pg, "risk_tags": tags, "nfr_families": fams}
            for eid, src, dst, tags, fams, pg in edges
        ],
        "plans": [{"id": pid, "placement": p} for pid, p in placements.items()],
    }


CONTRACT_RULES = {
    "balanced": [
        ("sensitive", "unauthorized_disclosure", "confidentiality", "mandatory", "assurance_level", 2, 6),
        ("critical", "corruption", "integrity", "mandatory", "assurance_level", 1, 6),
        ("authentic", "unauthorized_modification", "integrity", "optional", "assurance_level", 2, 4),
        ("resilient", "artifact_unavailability", "reliability", "optional", "tolerated_fragment_losses", 2, 4),
        ("bulk", "excessive_volume", "data_reduction", "optional", "compression_ratio", 1.3, 3),
    ],
    "security-first": [
        ("sensitive", "unauthorized_disclosure", "confidentiality", "mandatory", "assurance_level", 2, 9),
        ("critical", "corruption", "integrity", "mandatory", "assurance_level", 1, 7),
        ("authentic", "unauthorized_modification", "integrity", "mandatory", "assurance_level", 2, 8),
        ("resilient", "artifact_unavailability", "reliability", "optional", "tolerated_fragment_losses", 2, 2),
        ("bulk", "excessive_volume", "data_reduction", "optional", "compression_ratio", 1.3, 1),
    ],
    "resilience-first": [
        ("resilient", "artifact_unavailability", "reliability", "mandatory", "tolerated_fragment_losses", 2, 9),
        ("critical", "corruption", "integrity", "mandatory", "assurance_level", 1, 7),
        ("authentic", "unauthorized_modification", "integrity", "optional", "assurance_level", 2, 5),
        ("sensitive", "unauthorized_disclosure", "confidentiality", "optional", "assurance_level", 2, 3),
        ("bulk", "excessive_volume", "data_reduction", "optional", "compression_ratio", 1.3, 2),
    ],
    "bandwidth-first": [
        ("bulk", "excessive_volume", "data_reduction", "mandatory", "compression_ratio", 1.3, 9),
        ("sensitive", "unauthorized_disclosure", "confidentiality", "mandatory", "assurance_level", 2, 5),
        ("authentic", "unauthorized_modification", "integrity", "optional", "assurance_level", 2, 4),
        ("critical", "corruption", "integrity", "optional", "assurance_level", 1, 3),
        ("resilient", "artifact_unavailability", "reliability", "optional", "tolerated_fragment_losses", 2, 2),
    ],
}


def clauses_for_workflow(workflow: dict[str, Any], contract_id: str) -> list[dict[str, Any]]:
    clauses = []
    rules = CONTRACT_RULES[contract_id]
    for edge in workflow["edges"]:
        tags = set(edge.get("risk_tags", []))
        families = set(edge.get("nfr_families", []))
        for tag, risk, prop, modality, metric, minimum, weight in rules:
            prop_family = "compression" if prop == "data_reduction" else prop
            if tag in tags and prop_family in families:
                clauses.append({
                    "id": f"{edge['id']}-{risk}",
                    "scope": {"edge": edge["id"]},
                    "risk": risk,
                    "property": prop,
                    "modality": modality,
                    "metric": metric,
                    "minimum": minimum,
                    "weight": weight,
                })
    return clauses


def request_for(site: dict[str, Any], workflow: dict[str, Any], plan: dict[str, Any],
                contract_id: str, scale: dict[str, Any], max_candidates: int,
                policy_scope: str = "edge") -> dict[str, Any]:
    tasks = []
    for t in workflow["tasks"]:
        name = t["id"]
        tasks.append({
            "name": name,
            "service_time": t["service_time_s"],
            "size_factor": t["output_size_factor"],
            "allowed_machines": [plan["placement"][name]],
        })
    edges = []
    for e in workflow["edges"]:
        edges.append({
            "id": e["id"],
            "from": e["from"],
            "to": e["to"],
            "policy_group": e.get("policy_group", e["id"]),
            "risk_tags": e.get("risk_tags", []),
            "nfr_families": e.get("nfr_families", []),
        })
    reqs = copy.deepcopy(site.get("requirements", {}))
    for family in ["compression", "confidentiality", "integrity", "reliability"]:
        reqs.setdefault(family, {})
        reqs[family].setdefault("level", "optional")
        reqs[family].setdefault("weight", 1.0)

    return {
        "workflow": {
            "id": workflow["id"],
            "plan_id": plan["id"],
            "tasks": tasks,
            "edges": edges,
            "workers": max(1, min(8, len(tasks))),
            "trace": {
                "objects": scale["instances"],
                "inter_arrival": scale["mean_interarrival_s"],
                "distribution": 3 if scale.get("arrival_model") == "exponential" else 1,
                "size_bytes": scale["input_size_bytes"],
                "stddevS": scale.get("input_size_cv", 0.1),
            },
        },
        "infrastructure": copy.deepcopy(site["infrastructure"]),
        "requirements": reqs,
        "mechanism_models": copy.deepcopy(site.get("mechanism_models", {})),
        "simulator": copy.deepcopy(site.get("simulator", {})),
        "contract": {
            "id": contract_id,
            "clauses": clauses_for_workflow(workflow, contract_id),
        },
        "objective": {
            "assurance_mode": "weighted",
            "energy_weight": 0.5,
            "policy_scope": policy_scope,
        },
        # Loose constraints; offline analysis evaluates meaningful budget grids.
        "constraints": {
            "global_energy_budget_j": 1e18,
            "max_makespan_s": 1e12,
        },
        "runner": {
            "max_candidates": max_candidates,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, type=Path)
    ap.add_argument("--workflows", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--max-candidates", type=int, default=5000)
    ap.add_argument("--replications", type=int, default=5)
    args = ap.parse_args()

    site0 = load_json(args.site)
    workflows0 = load_json(args.workflows)["workflows"]

    out = args.output
    cfg = out / "configs"
    reqdir = out / "requests"
    cfg.mkdir(parents=True, exist_ok=True)
    reqdir.mkdir(parents=True, exist_ok=True)

    sites = {s: power_site(site0, s) for s in ("low", "nominal", "high")}
    for s, obj in sites.items():
        write_json(cfg / f"site.{s}.json", obj)

    robust_workflows = []
    for wf in workflows0:
        wf2 = add_extra_plans(heterogenize_edges(wf), site0)
        robust_workflows.append(wf2)
    robust_workflows.append(realistic_14_workflow(site0))
    write_json(cfg / "workflows.robust.json", {"workflows": robust_workflows})

    suite = {
        "budget_multipliers": BUDGET_MULTIPLIERS,
        "contracts": list(CONTRACT_RULES.keys()),
        "power_scenarios": ["nominal", "high"],
        "workload_scales": {
            "medium": {
                "instances": 24,
                "arrival_model": "exponential",
                "mean_interarrival_s": 0.5,
                "input_size_bytes": 500000000,
                "input_size_cv": 0.10,
            },
            "large": {
                "instances": 12,
                "arrival_model": "exponential",
                "mean_interarrival_s": 2.0,
                "input_size_bytes": 800000000,
                "input_size_cv": 0.10,
            },
        },
        "policy_scopes": ["edge", "global"],
        "replications": args.replications,
        "max_candidates": args.max_candidates,
    }
    write_json(cfg / "evaluation_suite.robust.json", suite)

    manifest = {
        "generated_by": "generate_robust_inputs.py",
        "site_sha256": sha256_path(args.site),
        "workflows_sha256": sha256_path(args.workflows),
        "replications": args.replications,
        "max_candidates": args.max_candidates,
        "budget_multipliers": BUDGET_MULTIPLIERS,
        "scenarios": [],
    }

    for power_scenario in ["nominal", "high"]:
        site = sites[power_scenario]
        for wf in robust_workflows:
            for plan in wf["plans"]:
                for contract_id in CONTRACT_RULES:
                    for scale_name, scale in suite["workload_scales"].items():
                        base_id = f"{wf['id']}__{plan['id']}__{contract_id}__{scale_name}__{power_scenario}"
                        for policy_scope in suite["policy_scopes"]:
                            scenario_id = base_id if policy_scope == "edge" else f"{base_id}__global"
                            request = request_for(site, wf, plan, contract_id, scale,
                                                  args.max_candidates, policy_scope)
                            path = reqdir / f"{scenario_id}.json"
                            write_json(path, request)
                            manifest["scenarios"].append({
                                "id": scenario_id,
                                "workflow": wf["id"],
                                "plan": plan["id"],
                                "contract": contract_id,
                                "scale": scale_name,
                                "power_scenario": power_scenario,
                                "policy_scope": policy_scope,
                                "request": str(path),
                            })

    write_json(out / "manifest.json", manifest)
    print(f"Wrote {len(manifest['scenarios'])} scenarios to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
