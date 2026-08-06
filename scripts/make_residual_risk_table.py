#!/usr/bin/env python3
"""Emit the residual optional-risk figure as a LaTeX table.

Replaces fig_residual_risk (and optionally fig_residual_vs_budget) with a
compact booktabs table sized for an IEEE single column.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

CONTRACT_ORDER = ["balanced", "bandwidth-first", "resilience-first", "security-first"]
CONTRACT_LABELS = {
    "balanced": "Balanced",
    "bandwidth-first": "Bandwidth",
    "resilience-first": "Resilience",
    "security-first": "Security",
}
RISK_ORDER = ["corruption", "disclosure", "modification", "unavailability", "volume"]
RISK_LABELS = {
    "corruption": "Corruption",
    "disclosure": "Disclosure",
    "modification": "Modification",
    "unavailability": "Unavailability",
    "volume": "Volume",
}


def slice_figure(rr: pd.DataFrame, budget: float | None) -> pd.DataFrame:
    """Rows behind the figure. ``budget=None`` keeps every budget level, so the
    downstream pivots average over the sweep once divided by the level count."""
    sub = rr[(rr["power_scenario"] == "nominal") & (rr["scale"] == "medium")].copy()
    if budget is not None and "energy_multiplier" in sub.columns:
        sub = sub[abs(sub["energy_multiplier"] - budget) < 1e-9]
    return sub


def pivot(sub: pd.DataFrame, value: str, divisor: int = 1) -> pd.DataFrame:
    p = sub.pivot_table(
        index="risk_category", columns="contract", values=value, aggfunc="sum", fill_value=0.0
    )
    rows = [r for r in RISK_ORDER if r in p.index]
    cols = [c for c in CONTRACT_ORDER if c in p.columns]
    return p.reindex(index=rows, columns=cols, fill_value=0.0) / divisor


def main_table(sub: pd.DataFrame, budget: float | None) -> str:
    levels = sorted(sub["energy_multiplier"].unique()) if budget is None else []
    divisor = len(levels) or 1
    decimals = 1 if budget is None else 0

    res = pivot(sub, "residual_weight", divisor)
    off = pivot(sub, "clause_weight", divisor)

    if budget is None:
        span = ", ".join(f"$\\times{v:g}$" for v in levels)
        caption = (
            r"Residual optional-risk weight by contract and risk category, averaged "
            rf"over the budget sweep ({span}) at nominal power and medium scale. "
        )
    else:
        caption = (
            r"Residual optional-risk weight by contract and risk category "
            rf"(nominal power, medium scale, budget $\times{budget:g}$). "
        )
    caption += (
        r"Entries are the summed weight of optional clauses left unmet by the "
        r"selected pipeline; ``--'' marks a category the contract does not price. "
        r"\emph{Offered} is the total optional weight available, so \emph{unmet "
        r"share} normalises across contracts that price clauses on different scales."
    )

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{" + caption + "}",
        r"\label{tab:residual-risk}",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{l" + "r" * len(res.columns) + "}",
        r"\toprule",
        "Risk category & " + " & ".join(CONTRACT_LABELS[c] for c in res.columns) + r" \\",
        r"\midrule",
    ]
    for risk in res.index:
        cells = []
        for c in res.columns:
            cells.append("--" if off.loc[risk, c] == 0 else f"{res.loc[risk, c]:.{decimals}f}")
        lines.append(f"{RISK_LABELS[risk]} & " + " & ".join(cells) + r" \\")
    lines += [
        r"\midrule",
        r"Residual (total) & "
        + " & ".join(f"{v:.{decimals}f}" for v in res.sum())
        + r" \\",
    ]
    if budget is None:
        per_budget = sub.pivot_table(
            index="energy_multiplier", columns="contract", values="residual_weight",
            aggfunc="sum", fill_value=0.0,
        ).reindex(columns=res.columns, fill_value=0.0)
        lines.append(
            r"\quad range over budgets & "
            + " & ".join(
                f"{per_budget[c].min():.0f}--{per_budget[c].max():.0f}" for c in res.columns
            )
            + r" \\"
        )
    lines += [
        r"Offered (total) & " + " & ".join(f"{v:.0f}" for v in off.sum()) + r" \\",
        r"Unmet share & "
        + " & ".join(f"{100 * r / o:.1f}\\%" for r, o in zip(res.sum(), off.sum()))
        + r" \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def budget_table(rr: pd.DataFrame) -> str:
    if "energy_multiplier" not in rr.columns:
        return ""
    sub = rr[(rr["power_scenario"] == "nominal") & (rr["scale"] == "medium")]
    res = sub.pivot_table(
        index="contract", columns="energy_multiplier", values="residual_weight",
        aggfunc="sum", fill_value=0.0,
    )
    off = sub.pivot_table(
        index="contract", columns="energy_multiplier", values="clause_weight",
        aggfunc="sum", fill_value=0.0,
    )
    res = res.reindex([c for c in CONTRACT_ORDER if c in res.index])
    off = off.reindex(res.index)
    share = 100 * res / off

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Residual optional-risk weight (and share of offered optional weight) "
        r"as the energy/deadline budget grows, nominal power and medium scale.}",
        r"\label{tab:residual-budget}",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{l" + "r" * len(res.columns) + "}",
        r"\toprule",
        r"\multirow{2}{*}{Contract} & \multicolumn{" + str(len(res.columns))
        + r"}{c}{Budget ($\times$ mandatory baseline)} \\",
        r"\cmidrule(lr){2-" + str(len(res.columns) + 1) + "}",
        " & " + " & ".join(f"{v:g}" for v in res.columns) + r" \\",
        r"\midrule",
    ]
    for c in res.index:
        cells = [f"{res.loc[c, b]:.0f} ({share.loc[c, b]:.0f}\\%)" for b in res.columns]
        lines.append(f"{CONTRACT_LABELS[c]} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path, help="residual_risk_by_category.csv")
    ap.add_argument("--budget", type=float, default=1.30)
    ap.add_argument(
        "--average-budgets",
        action="store_true",
        help="average the main table over every budget level instead of slicing at --budget",
    )
    ap.add_argument("--with-budget-table", action="store_true")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    rr = pd.read_csv(args.csv)
    budget = None if args.average_budgets else args.budget
    if budget is None and "energy_multiplier" not in rr.columns:
        raise SystemExit("--average-budgets needs a CSV carrying energy_multiplier")
    sub = slice_figure(rr, budget)
    if sub.empty:
        raise SystemExit("no rows for the requested slice")

    out = main_table(sub, budget)
    if args.with_budget_table:
        out += "\n\n" + budget_table(rr)
    if args.output:
        args.output.write_text(out + "\n")
    else:
        print(out)


if __name__ == "__main__":
    main()
