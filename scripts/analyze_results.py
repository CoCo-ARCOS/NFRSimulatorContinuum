#!/usr/bin/env python3
"""Analyze profiler catalogs, sweep constraints, compare baselines, and plot results."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

FAMILIES = ("compression", "confidentiality", "reliability", "integrity")
PROPERTY_TO_FAMILY = {
    "compression": "compression",
    "data_reduction": "compression",
    "data-reduction": "compression",
    "confidentiality": "confidentiality",
    "reliability": "reliability",
    "integrity": "integrity",
    "authenticity": "integrity",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def family_weights(request: dict[str, Any]) -> dict[str, float]:
    weights = {family: 0.0 for family in FAMILIES}
    for clause in (request.get("contract") or {}).get("clauses", []):
        prop = str(clause.get("property", clause.get("family", ""))).lower()
        family = PROPERTY_TO_FAMILY.get(prop)
        if family:
            weights[family] = max(weights[family], float(clause.get("weight", 1.0)))
    return weights


def optional_totals(request: dict[str, Any]) -> tuple[int, float]:
    clauses = [
        clause for clause in (request.get("contract") or {}).get("clauses", [])
        if str(clause.get("modality", "optional")).lower() == "optional"
    ]
    return len(clauses), sum(float(clause.get("weight", 1.0)) for clause in clauses)


def assurance_key(candidate: dict[str, Any], request: dict[str, Any]) -> tuple[float, ...]:
    values = [float(candidate.get("coverage_weighted", 0.0)), float(candidate.get("coverage_count", 0))]
    weights = family_weights(request)
    priorities = sorted(FAMILIES, key=lambda family: (-weights[family], FAMILIES.index(family)))
    profile = candidate.get("assurance_profile") or {}
    for family in priorities:
        values.extend(sorted(float(value) for value in profile.get(family, [])))
    return tuple(values)


def metric(candidate: dict[str, Any], kind: str, risk_mode: str) -> float:
    if kind == "energy":
        return float(candidate["total_energy_ucb95"] if risk_mode == "ucb95" else candidate["total_energy"])
    if kind == "time":
        return float(candidate["makespan_ucb95"] if risk_mode == "ucb95" else candidate["makespan"])
    raise ValueError(kind)


def is_feasible(candidate: dict[str, Any], budget: float, deadline: float, risk_mode: str) -> bool:
    return bool(candidate.get("admissible")) and metric(candidate, "energy", risk_mode) <= budget and metric(candidate, "time", risk_mode) <= deadline


def normalized_cost(candidate: dict[str, Any], budget: float, deadline: float, risk_mode: str) -> float:
    return 0.5 * metric(candidate, "energy", risk_mode) / budget + 0.5 * metric(candidate, "time", risk_mode) / deadline


def select_proposed(
    candidates: list[dict[str, Any]], request: dict[str, Any], budget: float, deadline: float, risk_mode: str
) -> dict[str, Any] | None:
    feasible = [candidate for candidate in candidates if is_feasible(candidate, budget, deadline, risk_mode)]
    if not feasible:
        return None
    return min(
        feasible,
        key=lambda candidate: (
            tuple(-value for value in assurance_key(candidate, request)),
            normalized_cost(candidate, budget, deadline, risk_mode),
        ),
    )


def select_strongest(candidates: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, Any] | None:
    admissible = [candidate for candidate in candidates if candidate.get("admissible")]
    if not admissible:
        return None
    return min(
        admissible,
        key=lambda candidate: (
            tuple(-value for value in assurance_key(candidate, request)),
            float(candidate["total_energy_ucb95"]) + float(candidate["makespan_ucb95"]),
        ),
    )


def select_minimum(candidates: list[dict[str, Any]], kind: str, risk_mode: str) -> dict[str, Any] | None:
    admissible = [candidate for candidate in candidates if candidate.get("admissible")]
    return min(admissible, key=lambda candidate: metric(candidate, kind, risk_mode), default=None)


def select_required_only(
    candidates: list[dict[str, Any]], budget: float, deadline: float, risk_mode: str
) -> dict[str, Any] | None:
    admissible = [candidate for candidate in candidates if candidate.get("admissible")]
    if not admissible:
        return None
    minimum_coverage = min(float(candidate.get("coverage_weighted", 0.0)) for candidate in admissible)
    pool = [candidate for candidate in admissible if float(candidate.get("coverage_weighted", 0.0)) == minimum_coverage]
    return min(pool, key=lambda candidate: normalized_cost(candidate, budget, deadline, risk_mode))


def select_greedy(
    candidates: list[dict[str, Any]], budget: float, deadline: float, risk_mode: str
) -> dict[str, Any] | None:
    feasible = [candidate for candidate in candidates if is_feasible(candidate, budget, deadline, risk_mode)]
    if not feasible:
        return None
    def score(candidate: dict[str, Any]) -> tuple[float, float, float]:
        cost = max(normalized_cost(candidate, budget, deadline, risk_mode), 1e-12)
        return (
            float(candidate.get("coverage_weighted", 0.0)) / cost,
            float(candidate.get("coverage_weighted", 0.0)),
            -cost,
        )
    return max(feasible, key=score)


def coverage_fraction(candidate: dict[str, Any] | None, total_weight: float) -> float:
    if candidate is None:
        return 0.0
    if total_weight <= 0:
        return 1.0
    return float(candidate.get("coverage_weighted", 0.0)) / total_weight


def constraint_anchors(candidates: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, float]:
    admissible = [candidate for candidate in candidates if candidate.get("admissible")]
    if not admissible:
        raise ValueError("catalog has no admissible candidates")
    min_energy = min(metric(candidate, "energy", "ucb95") for candidate in admissible)
    min_time = min(metric(candidate, "time", "ucb95") for candidate in admissible)
    max_assurance = max(assurance_key(candidate, request) for candidate in admissible)
    full = [candidate for candidate in admissible if assurance_key(candidate, request) == max_assurance]
    max_energy = max(metric(candidate, "energy", "ucb95") for candidate in admissible)
    max_time = max(metric(candidate, "time", "ucb95") for candidate in admissible)
    energy_span = max(max_energy - min_energy, 1e-12)
    time_span = max(max_time - min_time, 1e-12)
    full_reference = min(
        full,
        key=lambda candidate: (
            (metric(candidate, "energy", "ucb95") - min_energy) / energy_span
            + (metric(candidate, "time", "ucb95") - min_time) / time_span
        ),
    )
    full_energy = metric(full_reference, "energy", "ucb95")
    full_time = metric(full_reference, "time", "ucb95")
    return {
        "min_energy": min_energy,
        "full_energy": full_energy,
        "min_time": min_time,
        "full_time": full_time,
    }


def bound(low: float, high: float, level: float) -> float:
    span = max(high - low, max(abs(low), 1.0) * 1e-6)
    return low + level * span


def selection_row(
    base: dict[str, Any], method: str, candidate: dict[str, Any] | None,
    budget: float, deadline: float, total_count: int, total_weight: float, risk_mode: str
) -> dict[str, Any]:
    feasible = candidate is not None and is_feasible(candidate, budget, deadline, risk_mode)
    return {
        **base,
        "method": method,
        "candidate_id": candidate.get("id") if candidate else None,
        "plan_id": candidate.get("plan_id") if candidate else None,
        "nfr": candidate.get("nfr_label") if candidate else None,
        "selected": int(candidate is not None),
        "feasible": int(feasible),
        "coverage_count": int(candidate.get("coverage_count", 0)) if candidate else 0,
        "coverage_weighted": float(candidate.get("coverage_weighted", 0.0)) if candidate else 0.0,
        "coverage_fraction": coverage_fraction(candidate, total_weight),
        "optional_clause_count": total_count,
        "optional_weight_total": total_weight,
        "energy_j": metric(candidate, "energy", risk_mode) if candidate else math.nan,
        "makespan_s": metric(candidate, "time", risk_mode) if candidate else math.nan,
        "energy_ratio": metric(candidate, "energy", risk_mode) / budget if candidate else math.nan,
        "deadline_ratio": metric(candidate, "time", risk_mode) / deadline if candidate else math.nan,
        "admitted_coverage_fraction": coverage_fraction(candidate, total_weight) if feasible else 0.0,
    }


def bootstrap_ci(values: list[float], replications: int = 2000, seed: int = 2026001) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    samples = []
    for _ in range(replications):
        sample = [values[rng.randrange(len(values))] for _ in values]
        samples.append(statistics.fmean(sample))
    samples.sort()
    low = samples[int(0.025 * (len(samples) - 1))]
    high = samples[int(0.975 * (len(samples) - 1))]
    return low, high


def summarize_methods(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for method in sorted({row["method"] for row in rows}):
        subset = [row for row in rows if row["method"] == method]
        by_scenario: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in subset:
            by_scenario[(row["workflow_id"], row["contract_id"], row["scale_id"])].append(row)
        scenario_coverage = [statistics.fmean(item["admitted_coverage_fraction"] for item in group) for group in by_scenario.values()]
        scenario_violation = [statistics.fmean(1.0 - item["feasible"] for item in group) for group in by_scenario.values()]
        coverage_low, coverage_high = bootstrap_ci(scenario_coverage)
        violation_low, violation_high = bootstrap_ci(scenario_violation, seed=2026002)
        summaries.append({
            "method": method,
            "scenario_count": len(by_scenario),
            "mean_admitted_coverage": statistics.fmean(scenario_coverage) if scenario_coverage else math.nan,
            "coverage_ci95_low": coverage_low,
            "coverage_ci95_high": coverage_high,
            "mean_violation_rate": statistics.fmean(scenario_violation) if scenario_violation else math.nan,
            "violation_ci95_low": violation_low,
            "violation_ci95_high": violation_high,
        })
    return summaries


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%")


def write_baseline_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    methods = sorted({row["method"] for row in rows})
    lines = [
        "\\begin{tabular}{lrr}",
        "\\toprule",
        "Method & Admitted coverage & Violation rate \\\\",
        "\\midrule",
    ]
    for method in methods:
        subset = [row for row in rows if row["method"] == method]
        admitted = statistics.fmean(row["admitted_coverage_fraction"] for row in subset)
        violation = statistics.fmean(1.0 - row["feasible"] for row in subset)
        lines.append(f"{latex_escape(method)} & {admitted:.3f} & {violation:.3f} \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_scalability_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "\\begin{tabular}{rrrr}",
        "\\toprule",
        "Policy groups & Candidates & Simulations & Wall time (s) \\\\",
        "\\midrule",
    ]
    for row in sorted(rows, key=lambda item: int(item["policy_groups"])):
        lines.append(
            f"{int(row['policy_groups'])} & {int(row['candidate_count'])} & "
            f"{int(row['distinct_simulated'])} & {float(row['elapsed_wall_s']):.2f} \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_plots(output: Path, grid_rows: list[dict[str, Any]], baseline_rows: list[dict[str, Any]], scalability_rows: list[dict[str, Any]], representative_rows: list[dict[str, Any]]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed; CSV and LaTeX outputs were still generated")
        return

    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    
    plt.rcParams.update({'axes.labelsize': 11, 'legend.fontsize': 10, 'xtick.labelsize': 9, 'ytick.labelsize': 9})

    # 1. Combined Heatmaps (1xN grid)
    contracts = sorted({row["contract_id"] for row in grid_rows if row["method"] == "contract-aware-edge" and row["scale_id"] == "medium"})
    if contracts:
        fig_hm, axes_hm = plt.subplots(1, len(contracts), figsize=(3.5 * len(contracts), 3.5), sharey=True)
        if len(contracts) == 1: axes_hm = [axes_hm]
        
        for i, contract_id in enumerate(contracts):
            subset = [
                row for row in grid_rows
                if row["method"] == "contract-aware-edge" and row["contract_id"] == contract_id and row["scale_id"] == "medium"
            ]
            e_levels = sorted({float(row["energy_level"]) for row in subset})
            d_levels = sorted({float(row["deadline_level"]) for row in subset})
            matrix = []
            for deadline_level in d_levels:
                line = []
                for energy_level in e_levels:
                    values = [
                        row["admitted_coverage_fraction"] for row in subset
                        if float(row["energy_level"]) == energy_level and float(row["deadline_level"]) == deadline_level
                    ]
                    line.append(statistics.fmean(values) if values else math.nan)
                matrix.append(line)
            
            ax = axes_hm[i]
            im = ax.imshow(matrix, origin="lower", aspect="auto", vmin=0.0, vmax=1.0, cmap="viridis")
            ax.set_xticks(range(len(e_levels)))
            ax.set_xticklabels([f"{value:.1f}" for value in e_levels])
            ax.set_yticks(range(len(d_levels)))
            ax.set_yticklabels([f"{value:.1f}" for value in d_levels])
            ax.set_xlabel("Energy tightness level")
            if i == 0:
                ax.set_ylabel("Deadline tightness level")
            ax.set_title(contract_id)
            
        fig_hm.subplots_adjust(right=0.85)
        cbar_ax = fig_hm.add_axes([0.88, 0.15, 0.02, 0.7])
        fig_hm.colorbar(im, cax=cbar_ax, label="Weighted optional-clause coverage")
        fig_hm.savefig(figures / "combined_heatmaps.pdf", bbox_inches='tight')
        fig_hm.savefig(figures / "combined_heatmaps.png", dpi=200, bbox_inches='tight')
        plt.close(fig_hm)

    # 2. Combined Baselines (1x2 grid)
    if baseline_rows:
        methods = sorted({row["method"] for row in baseline_rows})
        # Style definition for highlighting our method
        colors = ['#2ca02c' if 'contract-aware' in m else '#7f7f7f' for m in methods]
        
        fig_bl, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
        
        admitted = [statistics.fmean(row["admitted_coverage_fraction"] for row in baseline_rows if row["method"] == method) for method in methods]
        ax1.bar(range(len(methods)), admitted, color=colors)
        ax1.set_xticks(range(len(methods)))
        ax1.set_xticklabels(methods, rotation=35, ha="right")
        ax1.set_ylabel("Mean admitted coverage")
        ax1.set_ylim(0.0, 1.05)
        
        violations = [statistics.fmean(1.0 - row["feasible"] for row in baseline_rows if row["method"] == method) for method in methods]
        ax2.bar(range(len(methods)), violations, color=colors)
        ax2.set_xticks(range(len(methods)))
        ax2.set_xticklabels(methods, rotation=35, ha="right")
        ax2.set_ylabel("Constraint-violation rate")
        ax2.set_ylim(0.0, 1.05)
        
        fig_bl.tight_layout()
        fig_bl.savefig(figures / "combined_baselines.pdf")
        fig_bl.savefig(figures / "combined_baselines.png", dpi=200)
        plt.close(fig_bl)

    # 2.5 Combined Baselines Per Topology (1xN grid)
    if baseline_rows:
        workflows = sorted({row["workflow_id"] for row in baseline_rows})
        methods = sorted({row["method"] for row in baseline_rows})
        colors = ['#2ca02c' if 'contract-aware' in m else '#7f7f7f' for m in methods]
        
        if workflows:
            fig_top, axes_top = plt.subplots(1, len(workflows), figsize=(4.0 * len(workflows), 4), sharey=True)
            if len(workflows) == 1: axes_top = [axes_top]
            
            for i, workflow_id in enumerate(workflows):
                subset = [row for row in baseline_rows if row["workflow_id"] == workflow_id]
                admitted = [statistics.fmean(row["admitted_coverage_fraction"] for row in subset if row["method"] == method) if any(row["method"] == method for row in subset) else 0.0 for method in methods]
                
                ax = axes_top[i]
                ax.bar(range(len(methods)), admitted, color=colors)
                ax.set_xticks(range(len(methods)))
                ax.set_xticklabels(methods, rotation=35, ha="right")
                if i == 0:
                    ax.set_ylabel("Mean admitted coverage")
                ax.set_ylim(0.0, 1.05)
                ax.set_title(f"Topology: {workflow_id}")
                
            fig_top.tight_layout()
            fig_top.savefig(figures / "combined_topologies.pdf")
            fig_top.savefig(figures / "combined_topologies.png", dpi=200)
            plt.close(fig_top)


    # 3. Combined Scalability (1x2 grid)
    if scalability_rows:
        ordered = sorted(scalability_rows, key=lambda row: int(row["policy_groups"]))
        fig_sc, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3.5))
        
        ax1.plot([int(row["policy_groups"]) for row in ordered], [int(row["candidate_count"]) for row in ordered], marker="o", color="#1f77b4", linewidth=2)
        ax1.set_xlabel("Policy groups")
        ax1.set_ylabel("Candidate configurations")
        ax1.set_yscale("log")
        ax1.grid(True, linestyle="--", alpha=0.5)
        
        ax2.plot([int(row["candidate_count"]) for row in ordered], [float(row["elapsed_wall_s"]) for row in ordered], marker="o", color="#d62728", linewidth=2)
        ax2.set_xlabel("Candidate configurations")
        ax2.set_ylabel("Profiler wall time (s)")
        ax2.grid(True, linestyle="--", alpha=0.5)
        
        fig_sc.tight_layout()
        fig_sc.savefig(figures / "combined_scalability.pdf")
        fig_sc.savefig(figures / "combined_scalability.png", dpi=200)
        plt.close(fig_sc)

    # 4. Priority Sensitivity
    if representative_rows:
        contracts_rep = sorted({row["contract_id"] for row in representative_rows})
        values = [statistics.fmean(row["admitted_coverage_fraction"] for row in representative_rows if row["contract_id"] == contract) for contract in contracts_rep]
        fig_rep, ax = plt.subplots(figsize=(5, 4))
        ax.bar(range(len(contracts_rep)), values, color='#2ca02c')
        ax.set_xticks(range(len(contracts_rep)))
        ax.set_xticklabels(contracts_rep, rotation=25, ha="right")
        ax.set_ylabel("Mean admitted coverage")
        ax.set_ylim(0.0, 1.05)
        fig_rep.tight_layout()
        fig_rep.savefig(figures / "contract_priority_sensitivity.pdf")
        fig_rep.savefig(figures / "contract_priority_sensitivity.png", dpi=200)
        plt.close(fig_rep)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    sweep = manifest["constraint_sweep"]
    energy_levels = [float(value) for value in sweep["energy_levels"]]
    deadline_levels = [float(value) for value in sweep["deadline_levels"]]
    representative_level = float(sweep["representative_level"])

    catalogs: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    catalog_rows: list[dict[str, Any]] = []
    scalability_rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for scenario in manifest["scenarios"]:
        scenario_dir = args.results / scenario["scenario_id"]
        report_path = scenario_dir / "profiler_report.json"
        record_path = scenario_dir / "run_record.json"
        if not report_path.exists():
            failures.append(scenario["scenario_id"])
            continue
        report = load_json(report_path)
        request = load_json(Path(scenario["request"]))
        record = load_json(record_path) if record_path.exists() else {}
        metadata = request.get("metadata") or {}
        candidates = report.get("candidates", [])
        optional_count, optional_weight = optional_totals(request)
        row = {
            "scenario_id": scenario["scenario_id"],
            "kind": scenario["kind"],
            "workflow_id": scenario["workflow_id"],
            "contract_id": scenario["contract_id"],
            "scale_id": scenario["scale_id"],
            "policy_scope": scenario["policy_scope"],
            "candidate_count": len(candidates),
            "admissible_count": sum(int(candidate.get("admissible", False)) for candidate in candidates),
            "optional_clause_count": optional_count,
            "optional_weight_total": optional_weight,
            "distinct_simulated": int(report.get("search_space", {}).get("distinct_simulated", len(candidates))),
            "replications": int(report.get("search_space", {}).get("replications", scenario["replications"])),
            "elapsed_wall_s": float(record.get("elapsed_wall_s", math.nan)),
            "max_coverage_weighted": max((float(candidate.get("coverage_weighted", 0.0)) for candidate in candidates if candidate.get("admissible")), default=0.0),
        }
        catalog_rows.append(row)
        if scenario["kind"] == "scalability":
            scalability_rows.append({
                **row,
                "policy_groups": int(scenario.get("policy_groups", metadata.get("policy_groups", 0))),
            })
        else:
            key = (scenario["workflow_id"], scenario["contract_id"], scenario["scale_id"], scenario["policy_scope"])
            catalogs[key] = {"scenario": scenario, "request": request, "report": report, "record": record}

    grid_rows: list[dict[str, Any]] = []
    selected_configs: list[dict[str, Any]] = []
    base_keys = sorted({key[:3] for key in catalogs})
    for workflow_id, contract_id, scale_id in base_keys:
        edge_catalog = catalogs.get((workflow_id, contract_id, scale_id, "edge"))
        global_catalog = catalogs.get((workflow_id, contract_id, scale_id, "global"))
        anchor_catalog = edge_catalog or global_catalog
        if anchor_catalog is None:
            continue
        request = anchor_catalog["request"]
        edge_candidates = edge_catalog["report"]["candidates"] if edge_catalog else []
        global_candidates = global_catalog["report"]["candidates"] if global_catalog else []
        anchors = constraint_anchors(edge_candidates or global_candidates, request)
        total_count, total_weight = optional_totals(request)

        for energy_level in energy_levels:
            budget = bound(anchors["min_energy"], anchors["full_energy"], energy_level)
            for deadline_level in deadline_levels:
                deadline = bound(anchors["min_time"], anchors["full_time"], deadline_level)
                base = {
                    "workflow_id": workflow_id,
                    "contract_id": contract_id,
                    "scale_id": scale_id,
                    "energy_level": energy_level,
                    "deadline_level": deadline_level,
                    "energy_budget_j": budget,
                    "deadline_s": deadline,
                }

                methods: list[tuple[str, dict[str, Any] | None, str]] = []
                if edge_candidates:
                    methods.extend([
                        ("contract-aware-edge", select_proposed(edge_candidates, request, budget, deadline, "ucb95"), "ucb95"),
                        ("strongest", select_strongest(edge_candidates, request), "ucb95"),
                        ("minimum-energy", select_minimum(edge_candidates, "energy", "ucb95"), "ucb95"),
                        ("minimum-makespan", select_minimum(edge_candidates, "time", "ucb95"), "ucb95"),
                        ("required-only", select_required_only(edge_candidates, budget, deadline, "ucb95"), "ucb95"),
                        ("greedy-coverage-per-cost", select_greedy(edge_candidates, budget, deadline, "ucb95"), "ucb95"),
                    ])
                if global_candidates:
                    methods.append(("global-uniform", select_proposed(global_candidates, global_catalog["request"], budget, deadline, "ucb95"), "ucb95"))

                for method, candidate, mode in methods:
                    grid_rows.append(selection_row(base, method, candidate, budget, deadline, total_count, total_weight, mode))

                if energy_level == representative_level and deadline_level == representative_level and edge_candidates:
                    for mode in ("ucb95", "mean"):
                        candidate = select_proposed(edge_candidates, request, budget, deadline, mode)
                        if candidate is not None:
                            selected_configs.append({
                                **base,
                                "risk_mode": mode,
                                "candidate_id": candidate["id"],
                                "plan_id": candidate["plan_id"],
                                "nfr": candidate["nfr_label"],
                                "simulator_config": candidate.get("simulator_config"),
                                "request": edge_catalog["scenario"]["request"],
                            })

    baseline_rows = [row for row in grid_rows if row["scale_id"] == "medium"]
    method_summary_rows = summarize_methods(baseline_rows)
    representative_rows = [
        row for row in grid_rows
        if row["method"] == "contract-aware-edge"
        and row["scale_id"] == "medium"
        and float(row["energy_level"]) == representative_level
        and float(row["deadline_level"]) == representative_level
    ]

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "catalog_summary.csv", catalog_rows)
    write_csv(args.output / "constraint_sweep.csv", grid_rows)
    write_csv(args.output / "baseline_comparison.csv", baseline_rows)
    write_csv(args.output / "method_summary.csv", method_summary_rows)
    write_csv(args.output / "representative_contracts.csv", representative_rows)
    write_csv(args.output / "scalability.csv", scalability_rows)
    write_json(args.output / "selected_configs.json", {"selected": selected_configs})
    write_json(args.output / "analysis_summary.json", {
        "missing_scenarios": failures,
        "catalogs_analyzed": len(catalog_rows),
        "constraint_rows": len(grid_rows),
        "representative_level": representative_level,
    })
    write_baseline_latex(args.output / "tables" / "baseline_summary.tex", baseline_rows)
    write_scalability_latex(args.output / "tables" / "scalability.tex", scalability_rows)
    make_plots(args.output, grid_rows, baseline_rows, scalability_rows, representative_rows)
    print(f"Analysis written to {args.output}; missing scenarios={len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
