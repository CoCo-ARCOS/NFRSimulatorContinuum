#!/usr/bin/env python3
"""Analyze robust contract-realization catalogs.

This script performs offline admission using budgets relative to a mandatory-
compliant baseline. It is intentionally defensive because profiler report fields
may differ slightly between wrapper versions.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


BUDGET_MULTIPLIERS = [1.05, 1.15, 1.30, 1.60, 2.00]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def cand_energy(c):
    return float(c.get("total_energy", c.get("energy_j", 0.0)))


def cand_time(c):
    return float(c.get("makespan", c.get("makespan_s", 0.0)))


def mandatory_ok(c):
    if "mandatory_count" in c and "mandatory_satisfied" in c:
        return int(c["mandatory_satisfied"]) >= int(c["mandatory_count"])
    return bool(c.get("admissible", True))


def coverage_weight(c):
    for k in ("coverage_weighted", "optional_coverage_weighted", "weighted_coverage"):
        if k in c:
            return float(c[k])
    # Fall back to normalized utility. This is less ideal but keeps the script usable.
    return float(c.get("utility", 0.0))


def optional_total_from_request(req):
    total = 0.0
    for cl in req.get("contract", {}).get("clauses", []):
        if str(cl.get("modality", "optional")).lower() == "optional":
            total += float(cl.get("weight", 1.0))
    return total


def risk_category(clause):
    risk = str(clause.get("risk", clause.get("property", "unknown")))
    if "disclosure" in risk or "confidential" in risk:
        return "disclosure"
    if "modification" in risk or "authentic" in risk:
        return "modification"
    if "corruption" in risk or "integrity" in risk:
        return "corruption"
    if "unavailability" in risk or "reliability" in risk:
        return "unavailability"
    if "volume" in risk or "reduction" in risk or "compression" in risk:
        return "volume"
    return risk


def fulfilled_clause_ids(c):
    status = c.get("clause_status", {})
    if isinstance(status, dict):
        return {cid for cid, v in status.items() if bool(v)}
    return set()


def pipeline_label(c):
    return str(c.get("nfr_label", c.get("pipeline", "")))


def select_contract_aware(feasible):
    return max(feasible, key=lambda c: (coverage_weight(c), -cand_energy(c), -cand_time(c))) if feasible else None


def select_greedy(feasible, e_ref, t_ref):
    if not feasible:
        return None
    def key(c):
        cost = (cand_energy(c) / max(e_ref, 1e-9)) + (cand_time(c) / max(t_ref, 1e-9))
        return (coverage_weight(c) / max(cost, 1e-9), coverage_weight(c), -cost)
    return max(feasible, key=key)


def summarize_scenario(scenario, report, request, budget_multipliers, global_report=None):
    candidates = report.get("candidates", [])
    opt_total = optional_total_from_request(request)
    admissible = [c for c in candidates if mandatory_ok(c)]
    if not admissible:
        return [], []
    global_admissible = [
        c for c in (global_report or {}).get("candidates", []) if mandatory_ok(c)
    ]

    e_ref = min(cand_energy(c) for c in admissible)
    t_ref = min(cand_time(c) for c in admissible)
    max_cov = max(coverage_weight(c) for c in admissible)
    fullish = [c for c in admissible if abs(coverage_weight(c) - max_cov) < 1e-9]
    e_full = min(cand_energy(c) for c in fullish)
    t_full = min(cand_time(c) for c in fullish)

    sweep_rows = []
    for em in budget_multipliers:
        for dm in budget_multipliers:
            budget = e_ref * em
            deadline = t_ref * dm
            feasible = [c for c in admissible if cand_energy(c) <= budget and cand_time(c) <= deadline]
            chosen = select_contract_aware(feasible)
            greedy = select_greedy(feasible, e_ref, t_ref)
            budgeted = [("contract-aware-edge", chosen), ("greedy-coverage-per-cost", greedy)]
            if global_admissible:
                # Same selection rule, restricted to the uniform (global-scope) catalog.
                global_feasible = [
                    c for c in global_admissible
                    if cand_energy(c) <= budget and cand_time(c) <= deadline
                ]
                budgeted.append(("global-uniform", select_contract_aware(global_feasible)))
            for method, cand in budgeted:
                row = {
                    **{k: scenario[k] for k in ("id", "workflow", "plan", "contract", "scale", "power_scenario")},
                    "method": method,
                    "energy_multiplier": em,
                    "deadline_multiplier": dm,
                    "energy_budget_j": budget,
                    "deadline_s": deadline,
                    "admitted": cand is not None,
                    "coverage_weighted": coverage_weight(cand) if cand else 0.0,
                    "coverage_fraction": coverage_weight(cand) / opt_total if cand and opt_total > 0 else 0.0,
                    "energy_j": cand_energy(cand) if cand else "",
                    "makespan_s": cand_time(cand) if cand else "",
                    "pipeline": pipeline_label(cand) if cand else "",
                }
                sweep_rows.append(row)

            # Fixed baselines evaluated at the same budget.
            fixed_defs = {
                "minimum-energy": min(admissible, key=lambda c: cand_energy(c)),
                "minimum-makespan": min(admissible, key=lambda c: cand_time(c)),
                "strongest": max(admissible, key=lambda c: (coverage_weight(c), -cand_energy(c))),
                "required-only": min([c for c in admissible if coverage_weight(c) == 0.0] or admissible, key=lambda c: cand_energy(c) + cand_time(c)),
            }
            for method, cand in fixed_defs.items():
                feas = cand_energy(cand) <= budget and cand_time(cand) <= deadline
                sweep_rows.append({
                    **{k: scenario[k] for k in ("id", "workflow", "plan", "contract", "scale", "power_scenario")},
                    "method": method,
                    "energy_multiplier": em,
                    "deadline_multiplier": dm,
                    "energy_budget_j": budget,
                    "deadline_s": deadline,
                    "admitted": True,
                    "selected_feasible": feas,
                    "coverage_weighted": coverage_weight(cand) if feas else 0.0,
                    "coverage_fraction": coverage_weight(cand) / opt_total if feas and opt_total > 0 else 0.0,
                    "raw_coverage_weighted": coverage_weight(cand),
                    "energy_j": cand_energy(cand),
                    "makespan_s": cand_time(cand),
                    "pipeline": pipeline_label(cand),
                })

    diag_rows = [{
        **{k: scenario[k] for k in ("id", "workflow", "plan", "contract", "scale", "power_scenario")},
        "candidates": len(candidates),
        "mandatory_admissible_candidates": len(admissible),
        "optional_weight_total": opt_total,
        "max_reachable_optional_weight": max_cov,
        "max_reachable_optional_fraction": max_cov / opt_total if opt_total > 0 else 0.0,
        "mandatory_ref_energy_j": e_ref,
        "mandatory_ref_makespan_s": t_ref,
        "max_coverage_ref_energy_j": e_full,
        "max_coverage_ref_makespan_s": t_full,
    }]
    return sweep_rows, diag_rows


def aggregate(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["method"], r["contract"], r["scale"], r["power_scenario"])].append(r)
    out = []
    for key, vals in groups.items():
        method, contract, scale, ps = key
        cov = [float(v["coverage_fraction"]) for v in vals]
        admitted = [bool(v.get("admitted", False)) for v in vals]
        selected_feasible = [bool(v.get("selected_feasible", v.get("admitted", False))) for v in vals]
        out.append({
            "method": method,
            "contract": contract,
            "scale": scale,
            "power_scenario": ps,
            "mean_coverage_fraction": sum(cov) / len(cov),
            "admission_rate": sum(admitted) / len(admitted),
            "selected_feasible_rate": sum(selected_feasible) / len(selected_feasible),
            "n": len(vals),
        })
    return out


def residual_risk_rows(rows, manifest, catalogs_dir):
    # Contract-aware selections along the diagonal of the budget grid
    # (equal energy and deadline multipliers).
    reps = [r for r in rows if r["method"] == "contract-aware-edge"
            and abs(float(r["energy_multiplier"]) - float(r["deadline_multiplier"])) < 1e-9
            and r.get("admitted")]
    by_id = {s["id"]: s for s in manifest["scenarios"]}
    cache = {}
    out = []
    for r in reps:
        sc = by_id[r["id"]]
        if r["id"] not in cache:
            cache[r["id"]] = (
                load_json(Path(sc["request"])),
                load_json(catalogs_dir / sc["id"] / "profiler_report.json"),
            )
        request, report = cache[r["id"]]
        # Find matching candidate by energy/time/pipeline.
        chosen = None
        for c in report.get("candidates", []):
            if abs(cand_energy(c) - float(r["energy_j"])) < 1e-6 and abs(cand_time(c) - float(r["makespan_s"])) < 1e-6:
                chosen = c
                break
        fulfilled = fulfilled_clause_ids(chosen or {})
        for cl in request.get("contract", {}).get("clauses", []):
            if str(cl.get("modality", "optional")).lower() != "optional":
                continue
            out.append({
                "scenario": r["id"],
                "workflow": r["workflow"],
                "plan": r["plan"],
                "contract": r["contract"],
                "scale": r["scale"],
                "power_scenario": r["power_scenario"],
                "energy_multiplier": float(r["energy_multiplier"]),
                "deadline_multiplier": float(r["deadline_multiplier"]),
                "risk_category": risk_category(cl),
                "clause_weight": float(cl.get("weight", 1.0)),
                "fulfilled": cl.get("id") in fulfilled,
                "residual_weight": 0.0 if cl.get("id") in fulfilled else float(cl.get("weight", 1.0)),
            })
    return out


def realization_key(candidate):
    """Identify a realization independently of its index in the catalog, which
    is assigned during sampling and is not stable between profiler runs."""
    return (str(candidate.get("plan_id", "")), pipeline_label(candidate))


def power_multipliers(nominal_request, high_request):
    """Per-machine power scaling between two power scenarios.

    Taken from the two requests rather than recomputed, so the analysis always
    reflects the scaling that was actually generated.
    """
    def by_name(request):
        machines = (request.get("infrastructure") or {}).get("machines") or []
        return {
            str(m.get("name")): float(m.get("max_power_w", m.get("max_power", 0.0)) or 0.0)
            for m in machines
        }

    nominal, high = by_name(nominal_request), by_name(high_request)
    return {
        name: (high[name] / nominal[name])
        for name in nominal
        if name in high and nominal[name] > 0
    }


def energy_under(candidate, multipliers):
    """Recost a candidate under a different power model.

    Scaling machine power changes no timing -- the schedule and therefore the
    busy intervals are identical -- so the energy integral scales per machine
    and the network term is untouched. This reproduces the simulator's own
    figure exactly, which is what lets the test recost a realization instead of
    hunting for its twin in a separately sampled catalog.
    """
    per_machine = candidate.get("energy_by_machine") or {}
    if not per_machine:
        return None
    total = sum(value * multipliers.get(machine, 1.0) for machine, value in per_machine.items())
    return total + float(candidate.get("network_energy", 0.0) or 0.0)


def power_sensitivity(manifest, catalogs_dir, budget_multipliers):
    """Do selections planned under nominal power survive a high-power reality?

    Deriving each scenario's budgets from its own baseline compares two
    self-consistent worlds: under the high-power model both the costs and the
    baseline they are normalised against rise together, so almost nothing
    changes and the test reports a sensitivity that is not there.

    The test here instead fixes the budgets. Absolute budgets come from the
    nominal baseline, the selection is made under nominal costs, and only then
    are the costs recomputed with the high-power model and admission repeated
    against those same unchanged budgets. That answers the question a
    practitioner actually has: if the energy model underestimates the machines,
    does the plan still fit the budget it was admitted under?
    """
    by_key = defaultdict(dict)
    for scenario in manifest["scenarios"]:
        if scenario.get("policy_scope", "edge") == "global":
            continue
        key = (scenario["workflow"], scenario["plan"], scenario["contract"], scenario["scale"])
        by_key[key][scenario["power_scenario"]] = scenario

    rows = []
    for key, scenarios in sorted(by_key.items()):
        if "nominal" not in scenarios or "high" not in scenarios:
            continue
        nominal_path = catalogs_dir / scenarios["nominal"]["id"] / "profiler_report.json"
        if not nominal_path.exists():
            continue

        nominal = [c for c in load_json(nominal_path).get("candidates", []) if mandatory_ok(c)]
        if not nominal:
            continue
        multipliers = power_multipliers(
            load_json(Path(scenarios["nominal"]["request"])),
            load_json(Path(scenarios["high"]["request"])),
        )
        if not multipliers:
            continue

        # Budgets come from the nominal baseline and are never rescaled: the
        # whole point is to hold them fixed while the costs move.
        e_ref = min(cand_energy(c) for c in nominal)
        t_ref = min(cand_time(c) for c in nominal)
        recosted = {realization_key(c): energy_under(c, multipliers) for c in nominal}
        opt_total = max((coverage_weight(c) for c in nominal), default=0.0)

        for em in budget_multipliers:
            for dm in budget_multipliers:
                budget = e_ref * em
                deadline = t_ref * dm
                planned = select_contract_aware([
                    c for c in nominal
                    if cand_energy(c) <= budget and cand_time(c) <= deadline
                ])
                row = {
                    "workflow": key[0], "plan": key[1], "contract": key[2], "scale": key[3],
                    "energy_multiplier": em, "deadline_multiplier": dm,
                    "energy_budget_j": budget, "deadline_s": deadline,
                    "baseline_energy_j": e_ref, "baseline_makespan_s": t_ref,
                    "admitted_nominal": planned is not None,
                }
                if planned is None:
                    rows.append(row)
                    continue

                planned_energy = cand_energy(planned)
                actual_energy = recosted.get(realization_key(planned))
                # Re-admit every candidate at its high-power cost, against the
                # same budgets the nominal plan was admitted under. Makespan is
                # unaffected by the power model, so only energy moves.
                replanned = select_contract_aware([
                    c for c in nominal
                    if (recosted.get(realization_key(c)) or cand_energy(c)) <= budget
                    and cand_time(c) <= deadline
                ])
                replanned_coverage = coverage_weight(replanned) if replanned else 0.0

                row.update({
                    "pipeline": pipeline_label(planned),
                    "planned_energy_j": planned_energy,
                    "planned_makespan_s": cand_time(planned),
                    "planned_coverage": coverage_weight(planned),
                    "planned_coverage_fraction": (coverage_weight(planned) / opt_total
                                                  if opt_total > 0 else 0.0),
                    "replanned_feasible": replanned is not None,
                    "replanned_coverage": replanned_coverage,
                    "replanned_coverage_fraction": (replanned_coverage / opt_total
                                                    if opt_total > 0 else 0.0),
                    "coverage_loss": coverage_weight(planned) - replanned_coverage,
                    "same_pipeline": (replanned is not None and
                                      realization_key(replanned) == realization_key(planned)),
                })
                if actual_energy is not None:
                    row.update({
                        "actual_energy_j": actual_energy,
                        "energy_ratio": actual_energy / planned_energy if planned_energy else "",
                        "energy_violation": int(actual_energy > budget),
                        "energy_overrun": ((actual_energy - budget) / budget
                                           if budget and actual_energy > budget else 0.0),
                        "energy_headroom": (budget - actual_energy) / budget if budget else "",
                    })
                rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--catalogs", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--budget-multipliers", default=",".join(map(str, BUDGET_MULTIPLIERS)))
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    multipliers = [float(x) for x in args.budget_multipliers.split(",") if x.strip()]
    sweep = []
    diag = []
    missing = []
    for sc in manifest["scenarios"]:
        if sc.get("policy_scope", "edge") == "global":
            continue  # Consumed as the sibling catalog of the edge scenario.
        rep_path = args.catalogs / sc["id"] / "profiler_report.json"
        if not rep_path.exists():
            missing.append(sc["id"])
            continue
        report = load_json(rep_path)
        request = load_json(Path(sc["request"]))
        global_rep_path = args.catalogs / f"{sc['id']}__global" / "profiler_report.json"
        global_report = load_json(global_rep_path) if global_rep_path.exists() else None
        srows, drows = summarize_scenario(sc, report, request, multipliers, global_report)
        sweep.extend(srows)
        diag.extend(drows)

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "constraint_sweep_robust.csv", sweep)
    write_csv(args.output / "catalog_diagnostics_robust.csv", diag)
    write_csv(args.output / "method_summary_robust.csv", aggregate(sweep))
    write_csv(args.output / "residual_risk_by_category.csv", residual_risk_rows(sweep, manifest, args.catalogs))
    write_csv(args.output / "power_sensitivity.csv",
              power_sensitivity(manifest, args.catalogs, multipliers))
    if missing:
        (args.output / "missing_catalogs.txt").write_text("\n".join(missing) + "\n", encoding="utf-8")
        print(f"Warning: {len(missing)} missing catalogs. See missing_catalogs.txt.")
    print(f"Wrote analysis to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
