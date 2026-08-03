#!/usr/bin/env python3
"""Contract-clause front end for the continuum DAG NFR profiler.

This wrapper preserves the simulator-driving, statistical, and reporting code in
``nfr_profiler_core.py`` while replacing the original family-global requirement
logic with artifact-scoped clauses. It is backward compatible: when no
``contract.clauses`` array is present, clauses are synthesized from the legacy
``requirements.<family>.level`` fields.
"""

from __future__ import annotations

import copy
import json
import math
import sys
from typing import Any

import nfr_profiler_core as core

PROPERTY_ALIASES = {
    "compression": "compression",
    "data_reduction": "compression",
    "data-reduction": "compression",
    "volume": "compression",
    "confidentiality": "confidentiality",
    "disclosure": "confidentiality",
    "reliability": "reliability",
    "unavailability": "reliability",
    "integrity": "integrity",
    "authenticity": "integrity",
    "authenticated_integrity": "integrity",
    "authenticated-integrity": "integrity",
    "corruption": "integrity",
    "modification": "integrity",
}

CATEGORICAL_THRESHOLDS = {
    "confidentiality": {
        "none": 0.0,
        "encrypted": 2.0,
        "encrypted_in_transit": 2.0,
        "approved_cipher": 2.0,
    },
    "integrity": {
        "none": 0.0,
        "corruption_detection": 1.0,
        "digest": 1.0,
        "authenticated_verification": 2.0,
        "authenticated_integrity": 2.0,
        "mac": 2.0,
    },
}


def _edge_index(request: dict[str, Any]) -> dict[str, int]:
    return {
        core._edge_id(edge, index): index
        for index, edge in enumerate(request["workflow"]["edges"])
    }


def _normalize_family(value: Any) -> str:
    key = str(value or "").strip().lower().replace(" ", "_")
    family = PROPERTY_ALIASES.get(key)
    if family not in core.FAMILIES:
        raise ValueError(f"unknown clause property/family '{value}'")
    return family


def _resolve_scope(request: dict[str, Any], raw_clause: dict[str, Any]) -> list[int]:
    edges = request["workflow"]["edges"]
    by_id = _edge_index(request)
    scope = raw_clause.get("scope", raw_clause.get("edge", raw_clause.get("edges")))

    if scope is None or scope == "workflow":
        indices = list(range(len(edges)))
    elif isinstance(scope, str):
        if scope not in by_id:
            raise ValueError(f"clause scope refers to unknown edge '{scope}'")
        indices = [by_id[scope]]
    elif isinstance(scope, list):
        indices = []
        for edge_id in scope:
            if edge_id not in by_id:
                raise ValueError(f"clause scope refers to unknown edge '{edge_id}'")
            indices.append(by_id[edge_id])
    elif isinstance(scope, dict):
        if scope.get("workflow") is True:
            indices = list(range(len(edges)))
        elif "edge" in scope:
            edge_id = scope["edge"]
            if edge_id not in by_id:
                raise ValueError(f"clause scope refers to unknown edge '{edge_id}'")
            indices = [by_id[edge_id]]
        elif "edges" in scope:
            indices = []
            for edge_id in scope["edges"]:
                if edge_id not in by_id:
                    raise ValueError(f"clause scope refers to unknown edge '{edge_id}'")
                indices.append(by_id[edge_id])
        elif "policy_group" in scope:
            group = str(scope["policy_group"])
            indices = [
                index
                for index, edge in enumerate(edges)
                if edge.get("policy_group", core._edge_id(edge, index)) == group
            ]
        else:
            raise ValueError("clause scope must define workflow, edge, edges, or policy_group")
    else:
        raise ValueError("invalid clause scope")

    if not indices:
        raise ValueError(f"clause '{raw_clause.get('id', '<unnamed>')}' has an empty scope")
    return sorted(set(indices))


def _threshold(raw_clause: dict[str, Any], family: str) -> tuple[str, float]:
    threshold = raw_clause.get("threshold")
    metric = raw_clause.get("metric")
    value: Any = raw_clause.get("min_strength", raw_clause.get("minimum", raw_clause.get("theta")))

    if isinstance(threshold, dict):
        metric = threshold.get("metric", metric)
        value = threshold.get("minimum", threshold.get("value", value))
    elif threshold is not None:
        value = threshold

    metric = str(metric or "assurance_level")
    if value is None:
        value = 1.0
    if isinstance(value, str):
        mapping = CATEGORICAL_THRESHOLDS.get(family, {})
        key = value.strip().lower().replace(" ", "_")
        if key not in mapping:
            raise ValueError(
                f"unsupported categorical threshold '{value}' for {family}; "
                "use a numeric threshold or a supported label"
            )
        value = mapping[key]
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError("clause threshold must be a finite non-negative value")
    return metric, float(value)


def _legacy_clauses(request: dict[str, Any]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    for family in core.FAMILIES:
        spec = request["requirements"].get(family, {})
        modality = spec.get("level", "none")
        if modality == "none":
            continue
        for index, edge in enumerate(request["workflow"]["edges"]):
            if not core.edge_allows_family(edge, family):
                continue
            clauses.append({
                "id": f"legacy-{core._edge_id(edge, index)}-{family}",
                "edge_indices": [index],
                "family": family,
                "risk": family,
                "modality": "mandatory" if modality == "required" else "optional",
                "metric": "assurance_level",
                "minimum": float(spec.get("min_strength", 1.0)),
                "weight": float(spec.get("weight", 1.0)),
            })
    return clauses


def contract_clauses(request: dict[str, Any]) -> list[dict[str, Any]]:
    cached = request.get("_normalized_contract_clauses")
    if isinstance(cached, list):
        return cached

    raw_clauses = (request.get("contract") or {}).get("clauses")
    if raw_clauses is None:
        normalized = _legacy_clauses(request)
        request["_normalized_contract_clauses"] = normalized
        return normalized
    if not isinstance(raw_clauses, list) or not raw_clauses:
        raise ValueError("contract.clauses must be a non-empty array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, raw in enumerate(raw_clauses):
        if not isinstance(raw, dict):
            raise ValueError("every contract clause must be an object")
        clause_id = str(raw.get("id", f"clause-{position}"))
        if not clause_id or clause_id in seen_ids:
            raise ValueError(f"duplicate or empty contract clause id '{clause_id}'")
        seen_ids.add(clause_id)
        family = _normalize_family(raw.get("family", raw.get("property", raw.get("risk"))))
        modality = str(raw.get("modality", raw.get("level", "optional"))).lower()
        if modality == "required":
            modality = "mandatory"
        if modality not in {"mandatory", "optional"}:
            raise ValueError(f"clause '{clause_id}' modality must be mandatory or optional")
        weight = raw.get("weight", raw.get("priority", 1.0))
        if not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or float(weight) <= 0:
            raise ValueError(f"clause '{clause_id}' weight must be positive")
        metric, minimum = _threshold(raw, family)
        edge_indices = _resolve_scope(request, raw)
        for index in edge_indices:
            edge = request["workflow"]["edges"][index]
            if not core.edge_allows_family(edge, family):
                raise ValueError(
                    f"clause '{clause_id}' requests {family} on edge "
                    f"'{core._edge_id(edge, index)}', but that family is not enabled on the edge"
                )
        normalized.append({
            "id": clause_id,
            "edge_indices": edge_indices,
            "family": family,
            "risk": raw.get("risk", family),
            "modality": modality,
            "metric": metric,
            "minimum": minimum,
            "weight": float(weight),
        })

    request["_normalized_contract_clauses"] = normalized
    return normalized


def clauses_for_group_family(
    request: dict[str, Any], group: str, family: str
) -> list[dict[str, Any]]:
    matches = []
    for clause in contract_clauses(request):
        if clause["family"] != family:
            continue
        if any(
            core.edge_policy_group(request, request["workflow"]["edges"][index], index) == group
            for index in clause["edge_indices"]
        ):
            matches.append(clause)
    return matches


def patched_requirement_level(request: dict[str, Any], family: str) -> str:
    matches = [clause for clause in contract_clauses(request) if clause["family"] == family]
    if any(clause["modality"] == "mandatory" for clause in matches):
        return "required"
    return "optional" if matches else "none"


def patched_eligible_edge_entries(
    request: dict[str, Any], family: str
) -> list[tuple[int, dict[str, Any]]]:
    indices = sorted({
        index
        for clause in contract_clauses(request)
        if clause["family"] == family
        for index in clause["edge_indices"]
    })
    return [(index, request["workflow"]["edges"][index]) for index in indices]


def patched_eligible_edges(request: dict[str, Any], family: str) -> list[dict[str, Any]]:
    return [edge for _, edge in patched_eligible_edge_entries(request, family)]


def patched_policy_groups(request: dict[str, Any]) -> list[str]:
    groups: list[str] = []
    for clause in contract_clauses(request):
        for index in clause["edge_indices"]:
            edge = request["workflow"]["edges"][index]
            group = core.edge_policy_group(request, edge, index)
            if group not in groups:
                groups.append(group)
    return groups


def patched_group_has_family(request: dict[str, Any], group: str, family: str) -> bool:
    return bool(clauses_for_group_family(request, group, family))


def patched_build_group_combos(
    request: dict[str, Any], group: str
) -> list[dict[str, dict[str, Any] | None]]:
    options_by_family: dict[str, list[dict[str, Any] | None]] = {}
    for family in core.FAMILIES:
        matches = clauses_for_group_family(request, group, family)
        if not matches:
            options_by_family[family] = [None]
            continue
        options = core.build_family_options(request, family)
        mandatory = [clause for clause in matches if clause["modality"] == "mandatory"]
        if mandatory:
            options = [
                option
                for option in options
                if option is not None
                and all(
                    delivered_value(request, family, option, clause["metric"]) >= clause["minimum"]
                    for clause in mandatory
                )
            ]
            if not options:
                raise ValueError(
                    f"no configured {family} mechanism can satisfy the mandatory clauses in policy group '{group}'"
                )
        elif options and options[0] is not None:
            options = [None] + options
        options_by_family[family] = options

    combinations = []
    import itertools
    for values in itertools.product(*(options_by_family[family] for family in core.FAMILIES)):
        combinations.append({
            core.NFR_CATALOG[family]["slot"]: option
            for family, option in zip(core.FAMILIES, values)
        })
    return combinations


def delivered_value(
    request: dict[str, Any], family: str, option: dict[str, Any] | None, metric: str
) -> float:
    if option is None:
        return 0.0
    normalized_metric = metric.strip().lower().replace("-", "_")
    if normalized_metric in {"assurance", "assurance_level", "protection_level"}:
        return core.mechanism_strength(request, family, option)
    if normalized_metric in {"tolerated_fragment_losses", "fragment_losses", "m"}:
        return float(option.get("m", 0))

    model = core.mechanism_model(request, family, option["algorithm"])
    if normalized_metric in {"key_bits", "key_strength_bits"}:
        return float(model.get("key_bits", 0.0))
    ratio = float(model.get("ratio", 0.0))
    if normalized_metric in {"compression_ratio", "reduction_ratio"}:
        return ratio
    if normalized_metric in {"reduction_fraction", "size_reduction_fraction"}:
        return 1.0 - 1.0 / ratio if ratio > 0 else 0.0
    raise ValueError(f"unsupported clause metric '{metric}'")


def clause_satisfied(
    policy: dict[str, Any], request: dict[str, Any], clause: dict[str, Any]
) -> bool:
    family = clause["family"]
    for index in clause["edge_indices"]:
        edge = request["workflow"]["edges"][index]
        option = core.family_option_for_edge(policy, request, edge, index, family)
        if delivered_value(request, family, option, clause["metric"]) < clause["minimum"]:
            return False
    return True


def patched_is_admissible(policy: dict[str, Any], request: dict[str, Any]) -> bool:
    return all(
        clause_satisfied(policy, request, clause)
        for clause in contract_clauses(request)
        if clause["modality"] == "mandatory"
    )


def patched_annotate_coverage(policy: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    fulfilled_by_family = {family: 0 for family in core.FAMILIES}
    assurance_profile = {family: [] for family in core.FAMILIES}
    weighted = 0.0
    count = 0
    mandatory_count = 0
    mandatory_satisfied = 0
    clause_status: dict[str, bool] = {}

    for clause in contract_clauses(request):
        satisfied = clause_satisfied(policy, request, clause)
        clause_status[clause["id"]] = satisfied
        values = []
        for index in clause["edge_indices"]:
            edge = request["workflow"]["edges"][index]
            option = core.family_option_for_edge(policy, request, edge, index, clause["family"])
            values.append(delivered_value(request, clause["family"], option, clause["metric"]))
        assurance_profile[clause["family"]].append(min(values) if values else 0.0)

        if clause["modality"] == "mandatory":
            mandatory_count += 1
            mandatory_satisfied += int(satisfied)
        elif satisfied:
            count += 1
            weighted += clause["weight"]
            fulfilled_by_family[clause["family"]] += 1

    return {
        "coverage_count": count,
        "coverage_weighted": weighted,
        "fulfilled_clauses": fulfilled_by_family,
        "mandatory_count": mandatory_count,
        "mandatory_satisfied": mandatory_satisfied,
        "clause_status": clause_status,
        "assurance_profile": assurance_profile,
    }


def patched_family_priorities(request: dict[str, Any]) -> list[str]:
    weights = {
        family: max(
            [clause["weight"] for clause in contract_clauses(request) if clause["family"] == family]
            or [0.0]
        )
        for family in core.FAMILIES
    }
    active = [family for family in core.FAMILIES if weights[family] > 0]
    return sorted(active, key=lambda family: (-weights[family], core.FAMILIES.index(family)))


def patched_serializable_result(result: dict[str, Any]) -> dict[str, Any]:
    # Candidate policies and simulator configs are retained so that the
    # evaluation scripts can re-select plans under many constraints and run
    # independent holdout seeds without repeating the catalog search.
    return copy.deepcopy(result)


def patched_load_request(path: str) -> dict[str, Any]:
    request = original_load_request(path)
    clauses = contract_clauses(request)
    if not clauses:
        raise ValueError("the request defines no active NFR clauses")
    return request


original_load_request = core.load_request
core.requirement_level = patched_requirement_level
core.eligible_edge_entries = patched_eligible_edge_entries
core.eligible_edges = patched_eligible_edges
core.policy_groups = patched_policy_groups
core.group_has_family = patched_group_has_family
core.build_group_combos = patched_build_group_combos
core.is_admissible = patched_is_admissible
core.annotate_coverage = patched_annotate_coverage
core.family_priorities = patched_family_priorities
core.serializable_result = patched_serializable_result
core.load_request = patched_load_request


if __name__ == "__main__":
    raise SystemExit(core.main())
