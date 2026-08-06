#!/usr/bin/env python3
"""Resolve a contract into an executable NFR realization plan.

This is the bridge between the offline profiler and a workflow engine. It
profiles the candidate catalog once per request (results are cached), admits
candidates under an energy and deadline budget derived from the
mandatory-compliant baseline, and selects the contract-aware realization.

The admission and selection rules are imported from
``scripts/analyze_robust_catalogs`` so that what a workflow engine executes is
chosen by exactly the same code as the offline evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for _path in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from analyze_robust_catalogs import (  # noqa: E402  (path set up above)
    cand_energy,
    cand_time,
    mandatory_ok,
    select_contract_aware,
)

from nfr_plan import build_plan, describe, legacy_view, load_plan, write_plan  # noqa: E402

DEFAULT_PLAN_PATH = REPO_ROOT / "realengines" / "tuner-output" / "plan.json"
SELECTION_METHOD = "contract-aware-edge"


def _load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fingerprint(request_path: Path, replications: int, max_candidates: int) -> str:
    digest = hashlib.sha256()
    digest.update(Path(request_path).read_bytes())
    digest.update(f"|reps={replications}|cands={max_candidates}".encode())
    return digest.hexdigest()[:16]


def resolve_calibration_paths(request_path: Path, output_dir: Path) -> Path:
    """Make ``real_values_dir`` entries absolute, relative to the repository.

    Requests store these paths relative so they survive being checked out
    anywhere -- a cluster home directory is not the author's laptop. Without
    the calibration data the simulator has no profile for any mechanism, every
    candidate using one fails, and the catalog degenerates to the "no NFRs"
    reference, which cannot satisfy a mandatory clause. Resolving here, and
    checking existence, turns that into an immediate error instead of an
    inscrutable one twelve profiler runs later.
    """
    request = _load_json(request_path)
    machines = (request.get("infrastructure") or {}).get("machines") or []
    missing: list[str] = []
    changed = False

    for machine in machines:
        raw = machine.get("real_values_dir")
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
            machine["real_values_dir"] = str(path) + ("/" if raw.endswith("/") else "")
            changed = True
        if not path.is_dir():
            missing.append(f"{machine.get('name', '?')}: {path}")

    if missing:
        raise FileNotFoundError(
            "calibration data is missing for:\n  " + "\n  ".join(missing)
            + "\nThe simulator needs these per-machine measurements to profile "
              "mechanisms; without them no candidate can satisfy a mandatory "
              "clause. Check that the repository was checked out completely "
              "(the directories are tracked in git)."
        )

    if not changed:
        return request_path
    # Write the resolved request beside the catalogs so the profiler and the
    # recorded provenance refer to the same file.
    resolved_dir = Path(output_dir) / "resolved-requests"
    resolved_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolved_dir / request_path.name
    with resolved.open("w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2)
        handle.write("\n")
    return resolved


def profile_catalog(request_path: Path, *, simulator: Path, simulator_dir: Path,
                    output_dir: Path, replications: int, max_candidates: int,
                    timeout: float, force: bool) -> dict[str, Any]:
    """Run the profiler for a request, reusing a cached catalog when present."""
    fingerprint = _fingerprint(request_path, replications, max_candidates)
    catalog_dir = Path(output_dir) / "catalogs" / fingerprint
    report_path = catalog_dir / "profiler_report.json"
    if report_path.exists() and not force:
        return _load_json(report_path)

    catalog_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(SCRIPTS_DIR / "nfr_contract_profiler.py"),
        "--request", str(Path(request_path).resolve()),
        "--simulator-cmd", str(Path(simulator).resolve()),
        "--simulator-dir", str(Path(simulator_dir).resolve()),
        "--output-dir", str(catalog_dir.resolve()),
        "--replications", str(replications),
        "--max-candidates", str(max_candidates),
        "--timeout", str(timeout),
        "--skip-attribution",
    ]
    log_path = catalog_dir / "profiler.log"
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command, cwd=str(SCRIPTS_DIR), stdout=log, stderr=subprocess.STDOUT, text=True
        )
    if completed.returncode != 0 or not report_path.exists():
        raise RuntimeError(f"profiler failed (exit {completed.returncode}); see {log_path}")
    return _load_json(report_path)


def select_from_catalog(report: dict[str, Any], *, energy_multiplier: float,
                        deadline_multiplier: float, energy_budget_j: float | None = None,
                        deadline_s: float | None = None) -> tuple[Any, dict, dict]:
    """Admit under budgets and select the contract-aware realization."""
    candidates = report.get("candidates", [])
    admissible = [c for c in candidates if mandatory_ok(c)]
    if not admissible:
        # Distinguish a genuinely over-constrained contract from a catalog that
        # never got off the ground: if the only survivors carry no mechanisms,
        # the profiler was unable to evaluate any, which is an environment
        # problem rather than a selection outcome.
        with_mechanisms = [c for c in candidates if c.get("nfr_policy")
                           and any(c["nfr_policy"].values())]
        if not candidates:
            raise RuntimeError(
                "the profiler produced no candidates at all; see profiler.log "
                "in the catalog directory"
            )
        if not with_mechanisms:
            raise RuntimeError(
                f"the catalog holds {len(candidates)} candidate(s), none carrying any "
                "mechanism: the simulator could not evaluate a single realization. "
                "This is usually missing calibration data (real_values_dir) -- check "
                "profiler.log for 'no calibration profile is available'"
            )
        raise RuntimeError(
            f"none of the {len(candidates)} candidates satisfies every mandatory "
            "clause; the contract may be stricter than the available mechanisms"
        )

    # Baseline references are per-metric minima over the mandatory-compliant
    # candidates; they need not come from the same candidate.
    e_ref = min(cand_energy(c) for c in admissible)
    t_ref = min(cand_time(c) for c in admissible)
    budget = energy_budget_j if energy_budget_j is not None else e_ref * energy_multiplier
    deadline = deadline_s if deadline_s is not None else t_ref * deadline_multiplier

    feasible = [c for c in admissible if cand_energy(c) <= budget and cand_time(c) <= deadline]
    chosen = select_contract_aware(feasible)
    if chosen is None:
        raise RuntimeError(
            f"no realization fits the budget ({budget:.3f} J, {deadline:.3f} s); "
            f"baseline needs at least {e_ref:.3f} J and {t_ref:.3f} s"
        )
    budgets = {
        "energy_j": budget,
        "deadline_s": deadline,
        "energy_multiplier": energy_multiplier if energy_budget_j is None else None,
        "deadline_multiplier": deadline_multiplier if deadline_s is None else None,
    }
    baseline = {"energy_j": e_ref, "makespan_s": t_ref, "admissible_candidates": len(admissible)}
    return chosen, budgets, baseline


def resolve_realization(request_path: Path, *, simulator: Path, simulator_dir: Path,
                        output_dir: Path = DEFAULT_PLAN_PATH.parent,
                        energy_multiplier: float = 1.30, deadline_multiplier: float = 1.30,
                        energy_budget_j: float | None = None, deadline_s: float | None = None,
                        replications: int = 3, max_candidates: int = 500,
                        timeout: float = 600.0, force: bool = False) -> dict[str, Any]:
    """Profile, admit, select, and return the realization plan."""
    request_path = resolve_calibration_paths(Path(request_path), Path(output_dir))
    report = profile_catalog(
        request_path, simulator=simulator, simulator_dir=simulator_dir,
        output_dir=Path(output_dir), replications=replications,
        max_candidates=max_candidates, timeout=timeout, force=force,
    )
    chosen, budgets, baseline = select_from_catalog(
        report, energy_multiplier=energy_multiplier, deadline_multiplier=deadline_multiplier,
        energy_budget_j=energy_budget_j, deadline_s=deadline_s,
    )
    request_ref = {
        "path": str(request_path),
        "sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
    }
    return build_plan(
        chosen, request_ref=request_ref, budgets=budgets,
        baseline=baseline, method=SELECTION_METHOD,
    )


def get_tuned_nfrs(plan: dict[str, Any] | None = None, artifact_class: str = "raw") -> dict[str, Any]:
    """Legacy flat view of a resolved plan, for the engine adapters.

    Reads ``$NFR_PLAN`` (or the default tuner output) when no plan is passed.
    There is deliberately no hard-coded fallback: an engine must execute a
    realization that the profiler actually selected.
    """
    if plan is None:
        plan_path = Path(os.environ.get("NFR_PLAN", DEFAULT_PLAN_PATH))
        if not plan_path.exists():
            raise FileNotFoundError(
                f"no realization plan at {plan_path}. Resolve one first, e.g.:\n"
                f"  python3 realengines/nfr_tuner.py --request realengines/demo_request.json \\\n"
                f"      --simulator proxy_dd/nfr_dag_sim --simulator-dir proxy_dd"
            )
        plan = load_plan(plan_path)
    return legacy_view(plan, artifact_class)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", required=True, type=Path, help="Profiler request JSON")
    parser.add_argument("--simulator", required=True, type=Path, help="Path to nfr_dag_sim")
    parser.add_argument("--simulator-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_PLAN_PATH.parent,
                        help="Directory for cached catalogs and the emitted plan")
    parser.add_argument("--plan", type=Path, default=None, help="Plan output path")
    parser.add_argument("--energy-multiplier", type=float, default=1.30)
    parser.add_argument("--deadline-multiplier", type=float, default=1.30)
    parser.add_argument("--energy-budget-j", type=float, default=None,
                        help="Absolute energy budget (overrides the multiplier)")
    parser.add_argument("--deadline-s", type=float, default=None,
                        help="Absolute deadline (overrides the multiplier)")
    parser.add_argument("--replications", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--force", action="store_true", help="Re-profile even if cached")
    args = parser.parse_args()

    plan = resolve_realization(
        args.request, simulator=args.simulator, simulator_dir=args.simulator_dir,
        output_dir=args.output, energy_multiplier=args.energy_multiplier,
        deadline_multiplier=args.deadline_multiplier, energy_budget_j=args.energy_budget_j,
        deadline_s=args.deadline_s, replications=args.replications,
        max_candidates=args.max_candidates, timeout=args.timeout, force=args.force,
    )
    plan_path = args.plan or (Path(args.output) / "plan.json")
    write_plan(plan_path, plan)
    print(describe(plan))
    print(f"\nWrote plan to {plan_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
