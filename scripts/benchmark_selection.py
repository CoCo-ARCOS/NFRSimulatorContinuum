#!/usr/bin/env python3
"""Benchmark online admission + selection latency over profiled catalogs.

The catalog is built offline; this measures the request-time cost only:
filtering the mandatory-admissible candidates against an (energy, deadline)
budget pair and selecting the contract-aware realization.
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

from analyze_robust_catalogs import (
    BUDGET_MULTIPLIERS,
    cand_energy,
    cand_time,
    load_json,
    mandatory_ok,
    select_contract_aware,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--catalogs", required=True, type=Path)
    ap.add_argument("--repeats", type=int, default=5,
                    help="Timing repetitions per scenario (best-of is reported per selection)")
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    catalog_sizes = []
    per_selection_us = []

    for sc in manifest["scenarios"]:
        if sc.get("policy_scope", "edge") == "global":
            continue
        rep_path = args.catalogs / sc["id"] / "profiler_report.json"
        if not rep_path.exists():
            continue
        report = load_json(rep_path)
        candidates = report.get("candidates", [])
        admissible = [c for c in candidates if mandatory_ok(c)]
        if not admissible:
            continue
        catalog_sizes.append(len(candidates))

        e_ref = min(cand_energy(c) for c in admissible)
        t_ref = min(cand_time(c) for c in admissible)
        pairs = [(e_ref * em, t_ref * dm)
                 for em in BUDGET_MULTIPLIERS for dm in BUDGET_MULTIPLIERS]

        best = None
        for _ in range(args.repeats):
            start = time.perf_counter()
            for budget, deadline in pairs:
                feasible = [c for c in admissible
                            if cand_energy(c) <= budget and cand_time(c) <= deadline]
                select_contract_aware(feasible)
            elapsed = time.perf_counter() - start
            best = elapsed if best is None else min(best, elapsed)
        per_selection_us.append(best / len(pairs) * 1e6)

    if not per_selection_us:
        print("No catalogs found.")
        return 1

    lat = sorted(per_selection_us)
    print(f"scenarios: {len(per_selection_us)}")
    print(f"catalog size: mean {statistics.mean(catalog_sizes):.1f}, "
          f"min {min(catalog_sizes)}, max {max(catalog_sizes)}")
    print(f"selection latency per request (us): "
          f"mean {statistics.mean(lat):.1f}, median {lat[len(lat) // 2]:.1f}, "
          f"p95 {lat[int(len(lat) * 0.95)]:.1f}, max {lat[-1]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
