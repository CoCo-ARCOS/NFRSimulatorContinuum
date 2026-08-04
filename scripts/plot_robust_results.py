#!/usr/bin/env python3
"""Create paper figures for the robust evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt


def savefig(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.savefig(path.with_suffix(".pdf"))
    plt.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    ms_path = args.analysis / "method_summary_robust.csv"
    if ms_path.exists() and ms_path.stat().st_size > 0:
        ms = pd.read_csv(ms_path)
        sub = ms[(ms["power_scenario"] == "nominal") & (ms["scale"] == "medium")]
        if not sub.empty:
            # Contract-aware vs global-ish baselines by contract. Use the main four methods.
            keep = ["contract-aware-edge", "greedy-coverage-per-cost", "minimum-energy", "required-only", "strongest"]
            sub = sub[sub["method"].isin(keep)]
            pivot = sub.pivot_table(index="contract", columns="method", values="mean_coverage_fraction", aggfunc="mean")
            ax = pivot.plot(kind="bar", figsize=(8, 4))
            ax.set_ylabel("Mean admitted weighted coverage")
            ax.set_xlabel("Contract profile")
            ax.set_title("Coverage by contract profile")
            ax.legend(loc="best", fontsize=8)
            savefig(args.output / "fig_method_by_contract.png")

    cs_path = args.analysis / "constraint_sweep_robust.csv"
    if cs_path.exists() and cs_path.stat().st_size > 0:
        cs = pd.read_csv(cs_path)
        sub = cs[(cs["method"] == "contract-aware-edge") &
                 (cs["power_scenario"] == "nominal") &
                 (cs["scale"] == "medium")]
        if not sub.empty:
            # Use a representative scenario: prefer realistic-14/bandwidth-first, otherwise first.
            pref = sub[(sub["workflow"] == "realistic-14") & (sub["contract"] == "bandwidth-first")]
            if pref.empty:
                first = sub.iloc[0]
                pref = sub[(sub["workflow"] == first["workflow"]) &
                           (sub["plan"] == first["plan"]) &
                           (sub["contract"] == first["contract"])]
            title = f"{pref.iloc[0]['workflow']} / {pref.iloc[0]['plan']} / {pref.iloc[0]['contract']}"
            piv = pref.pivot_table(index="energy_multiplier", columns="deadline_multiplier",
                                   values="coverage_fraction", aggfunc="max")
            plt.figure(figsize=(5, 4))
            plt.imshow(piv.values, origin="lower", aspect="auto")
            plt.colorbar(label="Admitted coverage")
            plt.xticks(range(len(piv.columns)), [str(x) for x in piv.columns])
            plt.yticks(range(len(piv.index)), [str(x) for x in piv.index])
            plt.xlabel("Deadline budget / mandatory baseline")
            plt.ylabel("Energy budget / mandatory baseline")
            plt.title(title)
            savefig(args.output / "fig_coverage_envelope.png")

    rr_path = args.analysis / "residual_risk_by_category.csv"
    if rr_path.exists() and rr_path.stat().st_size > 0:
        rr = pd.read_csv(rr_path)
        if not rr.empty:
            sub = rr[(rr["power_scenario"] == "nominal") & (rr["scale"] == "medium")]
            piv = sub.pivot_table(index="contract", columns="risk_category",
                                  values="residual_weight", aggfunc="sum", fill_value=0)
            ax = piv.plot(kind="bar", stacked=True, figsize=(7, 4))
            ax.set_ylabel("Residual optional-risk weight")
            ax.set_xlabel("Contract profile")
            ax.set_title("Residual optional risk at 1.30x budgets")
            ax.legend(loc="best", fontsize=8)
            savefig(args.output / "fig_residual_risk.png")

    ps_path = args.analysis / "power_stability.csv"
    if ps_path.exists() and ps_path.stat().st_size > 0:
        ps = pd.read_csv(ps_path)
        if not ps.empty:
            agg = ps.groupby("contract").agg(
                mean_coverage_loss=("coverage_loss", "mean"),
                same_pipeline_rate=("same_pipeline", "mean")
            ).reset_index()
            fig, ax1 = plt.subplots(figsize=(6, 4))
            ax1.bar(agg["contract"], agg["mean_coverage_loss"])
            ax1.set_ylabel("Mean coverage loss under high-power scenario")
            ax1.set_xlabel("Contract profile")
            ax1.set_title("Energy-model uncertainty sensitivity")
            savefig(args.output / "fig_power_stability.png")

    print(f"Wrote figures to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
