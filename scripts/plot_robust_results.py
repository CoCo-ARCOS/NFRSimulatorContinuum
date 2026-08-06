#!/usr/bin/env python3
"""Create paper figures for the robust evaluation.

The plots intentionally foreground the edge-contract method and keep baselines
muted so the figures read well in a paper column or appendix.
"""

from __future__ import annotations

import argparse
import os
import warnings
from pathlib import Path

if "MPLCONFIGDIR" not in os.environ:
    mpl_cache = Path(os.environ.get("TMPDIR", "/tmp")) / "nfrsim-matplotlib"
    mpl_cache.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mpl_cache)

import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


EDGE_METHOD = "contract-aware-edge"
GREEDY_METHOD = "greedy-coverage-per-cost"
GLOBAL_METHOD = "global-uniform"

METHOD_ORDER = [
    EDGE_METHOD,
    GLOBAL_METHOD,
    "strongest",
    "minimum-energy",
    "minimum-makespan",
    "required-only",
]

METHOD_LABELS = {
    EDGE_METHOD: "Edge-contract",
    GLOBAL_METHOD: "Global uniform",
    "strongest": "Strongest",
    "minimum-energy": "Min energy",
    "minimum-makespan": "Min makespan",
    "required-only": "Required only",
}

METHOD_COLORS = {
    EDGE_METHOD: "#0072B2",
    GLOBAL_METHOD: "#E69F00",
    "strongest": "#5F6368",
    "minimum-energy": "#8E8E8E",
    "minimum-makespan": "#B0B0B0",
    "required-only": "#D0D0D0",
}

METHOD_MARKERS = {
    EDGE_METHOD: "o",
    GLOBAL_METHOD: "s",
    "strongest": "^",
    "minimum-energy": "v",
    "minimum-makespan": "D",
    "required-only": "X",
}

METHOD_LINESTYLES = {
    EDGE_METHOD: "-",
    GLOBAL_METHOD: "-",
    "strongest": "--",
    "minimum-energy": "-.",
    "minimum-makespan": ":",
    "required-only": "--",
}

COVERAGE_LOSS_COLOR = "#E69F00"

CONTRACT_ORDER = ["balanced", "bandwidth-first", "resilience-first", "security-first"]
CONTRACT_LABELS = {
    "balanced": "Balanced",
    "bandwidth-first": "Bandwidth-first",
    "resilience-first": "Resilience-first",
    "security-first": "Security-first",
}

WORKFLOW_ORDER = ["linear-5", "diamond-6", "fork-join-8", "realistic-14"]
WORKFLOW_LABELS = {
    "linear-5": "Linear-5",
    "diamond-6": "Diamond-6",
    "fork-join-8": "Fork-join-8",
    "realistic-14": "Realistic-14",
}

RISK_LABELS = {
    "corruption": "Corruption",
    "disclosure": "Disclosure",
    "modification": "Modification",
    "unavailability": "Unavailability",
    "volume": "Volume",
}

RISK_COLORS = {
    "corruption": "#CC79A7",
    "disclosure": "#E69F00",
    "modification": "#009E73",
    "unavailability": "#D55E00",
    "volume": "#56B4E9",
}

BUDGET_XLABEL = "Energy and deadline budget (x mandatory baseline)"

# Figures are rendered at the true IEEE single-column width so fonts print at
# their nominal point size under \includegraphics[width=\linewidth].
COLUMN_WIDTH_IN = 3.5


def configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 400,
            "font.family": "DejaVu Sans",
            "font.size": 8.0,
            "axes.titlesize": 9,
            "axes.labelsize": 8.0,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "legend.fontsize": 7.0,
            "axes.linewidth": 0.6,
            "lines.linewidth": 1.4,
            "lines.markersize": 3.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def savefig(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="This figure includes Axes that are not compatible with tight_layout.*",
        )
        fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def nominal_medium(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["power_scenario"] == "nominal") & (df["scale"] == "medium")].copy()


def drop_greedy(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "method" not in df.columns:
        return df
    return df[df["method"] != GREEDY_METHOD].copy()


def ordered(values, preferred) -> list[str]:
    seen = list(dict.fromkeys(values))
    return [v for v in preferred if v in seen] + [v for v in seen if v not in preferred]


def method_label(method: str) -> str:
    return METHOD_LABELS.get(method, method.replace("-", " ").title())


def contract_label(contract: str) -> str:
    return CONTRACT_LABELS.get(contract, contract.replace("-", " ").title())


def workflow_label(workflow: str) -> str:
    return WORKFLOW_LABELS.get(workflow, workflow.replace("-", " ").title())


def risk_label(risk: str) -> str:
    return RISK_LABELS.get(risk, risk.replace("-", " ").title())


def method_color(method: str) -> str:
    return METHOD_COLORS.get(method, "#6F6F6F")


def to_bool(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def feasible_series(df: pd.DataFrame) -> pd.Series:
    raw = df["selected_feasible"] if "selected_feasible" in df else df["admitted"]
    raw = raw.where(raw.notna(), df["admitted"])
    return raw.map(to_bool)


def style_axes(ax, grid_axis: str = "y") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis=grid_axis, color="#D8D8D8", linewidth=0.6)
    ax.set_axisbelow(True)


def plot_method_by_contract(ms: pd.DataFrame, output: Path) -> None:
    sub = nominal_medium(ms)
    if sub.empty:
        return
    
    print(f"Plotting method by contract for {len(sub)} records")
    print(sub)
    methods = ordered(sub["method"].unique(), METHOD_ORDER)
    contracts = ordered(sub["contract"].unique(), CONTRACT_ORDER)
    pivot = sub.pivot_table(
        index="contract",
        columns="method",
        values="mean_coverage_fraction",
        aggfunc="mean",
    ).reindex(index=contracts, columns=methods)

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 3.1))
    y = np.arange(len(pivot.index))
    height = min(0.12, 0.78 / max(len(methods), 1))
    offsets = (np.arange(len(methods)) - (len(methods) - 1) / 2) * height

    for i, method in enumerate(methods):
        values = pivot[method] * 100
        is_edge = method == EDGE_METHOD
        is_comparator = method == GLOBAL_METHOD
        ax.barh(
            y + offsets[i],
            values,
            height=height * 0.85,
            label=method_label(method),
            color=method_color(method),
            edgecolor="#222222" if is_edge else "white",
            linewidth=0.8 if is_edge else 0.35,
            alpha=1.0 if is_edge or is_comparator else 0.72,
        )

    if EDGE_METHOD in pivot:
        edge_offset = offsets[methods.index(EDGE_METHOD)]
        for j, value in enumerate(pivot[EDGE_METHOD] * 100):
            if pd.notna(value):
                ax.text(
                    min(value + 1.2, 103.5),
                    y[j] + edge_offset,
                    f"{value:.0f}%",
                    va="center",
                    ha="left",
                    color=method_color(EDGE_METHOD),
                    fontsize=7.5,
                    fontweight="bold",
                )

    ax.set_yticks(y)
    ax.set_yticklabels([contract_label(c) for c in pivot.index])
    ax.invert_yaxis()
    ax.set_xlim(0, 105)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel("Mean admitted optional coverage")
    style_axes(ax, "x")
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.1,
        columnspacing=0.8,
    )
    savefig(fig, output / "fig_method_by_contract.png")


def plot_method_by_workflow(cs: pd.DataFrame, output: Path) -> None:
    sub = nominal_medium(cs)
    if sub.empty or "workflow" not in sub.columns:
        return

    methods = ordered(sub["method"].unique(), METHOD_ORDER)
    workflows = ordered(sub["workflow"].unique(), WORKFLOW_ORDER)
    pivot = sub.pivot_table(
        index="workflow",
        columns="method",
        values="coverage_fraction",
        aggfunc="mean",
    ).reindex(index=workflows, columns=methods)

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 3.1))
    y = np.arange(len(pivot.index))
    height = min(0.12, 0.78 / max(len(methods), 1))
    offsets = (np.arange(len(methods)) - (len(methods) - 1) / 2) * height

    for i, method in enumerate(methods):
        values = pivot[method] * 100
        is_edge = method == EDGE_METHOD
        is_comparator = method == GLOBAL_METHOD
        ax.barh(
            y + offsets[i],
            values,
            height=height * 0.85,
            label=method_label(method),
            color=method_color(method),
            edgecolor="#222222" if is_edge else "white",
            linewidth=0.8 if is_edge else 0.35,
            alpha=1.0 if is_edge or is_comparator else 0.72,
        )

    if EDGE_METHOD in pivot:
        edge_offset = offsets[methods.index(EDGE_METHOD)]
        for j, value in enumerate(pivot[EDGE_METHOD] * 100):
            if pd.notna(value):
                ax.text(
                    min(value + 1.2, 103.5),
                    y[j] + edge_offset,
                    f"{value:.0f}%",
                    va="center",
                    ha="left",
                    color=method_color(EDGE_METHOD),
                    fontsize=7.5,
                    fontweight="bold",
                )

    ax.set_yticks(y)
    ax.set_yticklabels([workflow_label(w) for w in pivot.index])
    ax.invert_yaxis()
    ax.set_xlim(0, 105)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel("Mean admitted optional coverage")
    style_axes(ax, "x")
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.1,
        columnspacing=0.8,
    )
    savefig(fig, output / "fig_method_by_workflow.png")


def plot_coverage_envelope(cs: pd.DataFrame, output: Path) -> None:
    sub = nominal_medium(cs)
    sub = sub[sub["method"] == EDGE_METHOD]
    if sub.empty:
        return

    grouped = (
        sub.groupby(["id", "workflow", "plan", "contract"], dropna=False)["coverage_fraction"]
        .agg(["std", "mean", "min", "max"])
        .reset_index()
    )
    grouped["std"] = grouped["std"].fillna(0.0)
    chosen = grouped.sort_values(["std", "mean"], ascending=[False, False]).iloc[0]
    rep = sub[sub["id"] == chosen["id"]]
    pivot = rep.pivot_table(
        index="energy_multiplier",
        columns="deadline_multiplier",
        values="coverage_fraction",
        aggfunc="max",
    ).sort_index().sort_index(axis=1)

    if pivot.empty:
        return

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.75))
    values = pivot.values * 100
    im = ax.imshow(values, origin="lower", aspect="auto", vmin=0, vmax=100, cmap="YlGnBu")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    cbar.set_label("Coverage")

    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            value = values[row, col]
            color = "white" if value >= 72 else "#1F1F1F"
            ax.text(col, row, f"{value:.0f}", ha="center", va="center", fontsize=7, color=color)

    ax.set_xticks(np.arange(len(pivot.columns)))
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_xticklabels([f"{x:g}" for x in pivot.columns])
    ax.set_yticklabels([f"{x:g}" for x in pivot.index])
    ax.set_xlabel("Deadline budget (x mandatory baseline)")
    ax.set_ylabel("Energy budget (x mandatory baseline)")
    scenario = (
        f"{chosen['workflow']} / {chosen['plan']} / "
        f"{contract_label(str(chosen['contract']))}"
    )
    ax.set_title(f"Edge-contract coverage envelope\n{scenario}")
    savefig(fig, output / "fig_coverage_envelope.png")


def plot_coverage_envelope_panels(cs: pd.DataFrame, output: Path) -> None:
    sub = nominal_medium(cs)
    sub = sub[sub["method"] == EDGE_METHOD]
    if sub.empty:
        return

    mean_pivot = sub.pivot_table(
        index="energy_multiplier",
        columns="deadline_multiplier",
        values="coverage_fraction",
        aggfunc="mean",
    ).sort_index().sort_index(axis=1)

    grouped = (
        sub.groupby(["id", "workflow", "plan", "contract"], dropna=False)["coverage_fraction"]
        .agg(["std", "mean"])
        .reset_index()
    )
    grouped["std"] = grouped["std"].fillna(0.0)
    chosen = grouped.sort_values(["std", "mean"], ascending=[False, False]).iloc[0]
    rep = sub[sub["id"] == chosen["id"]]
    rep_pivot = rep.pivot_table(
        index="energy_multiplier",
        columns="deadline_multiplier",
        values="coverage_fraction",
        aggfunc="max",
    ).sort_index().sort_index(axis=1)

    if mean_pivot.empty or rep_pivot.empty:
        return

    n_scenarios = sub["id"].nunique()
    scenario = f"{chosen['workflow']} / {chosen['plan']} / {contract_label(str(chosen['contract']))}"
    print(f"Envelope panels: most budget-sensitive scenario is {scenario}")

    fig, axes = plt.subplots(
        1, 2, figsize=(COLUMN_WIDTH_IN, 2.1), sharey=True, gridspec_kw={"wspace": 0.12}
    )
    panels = [
        (axes[0], mean_pivot, f"(a) Mean, {n_scenarios} scenarios"),
        (axes[1], rep_pivot, "(b) Most-sensitive"),
    ]
    im = None
    for ax, pivot, title in panels:
        values = pivot.values * 100
        im = ax.imshow(values, origin="lower", aspect="auto", vmin=0, vmax=100, cmap="YlGnBu")
        for row in range(values.shape[0]):
            for col in range(values.shape[1]):
                value = values[row, col]
                color = "white" if value >= 72 else "#1F1F1F"
                ax.text(col, row, f"{value:.0f}", ha="center", va="center", fontsize=6, color=color)
        ax.set_xticks(np.arange(len(pivot.columns)))
        ax.set_yticks(np.arange(len(pivot.index)))
        ax.set_xticklabels([f"{x:g}" for x in pivot.columns], fontsize=6)
        ax.set_yticklabels([f"{x:g}" for x in pivot.index], fontsize=6.5)
        ax.set_title(title, fontsize=7)
        ax.set_xlabel("Deadline (x baseline)", fontsize=7)
    axes[0].set_ylabel("Energy (x baseline)", fontsize=7)
    cbar = fig.colorbar(im, ax=axes[1], fraction=0.046, pad=0.06)
    cbar.ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    cbar.ax.tick_params(labelsize=6.5)
    cbar.set_label("Coverage", fontsize=7)
    savefig(fig, output / "fig_coverage_envelope_panels.png")


def plot_feasibility_and_reachable(cs: pd.DataFrame, analysis: Path, output: Path) -> None:
    sweep = nominal_medium(cs)
    sweep = sweep[sweep["energy_multiplier"] == sweep["deadline_multiplier"]].copy()
    if sweep.empty:
        return

    sweep["feasible"] = feasible_series(sweep)
    rates = sweep.groupby(["method", "energy_multiplier"])["feasible"].mean().unstack(0)
    methods = ordered(rates.columns, METHOD_ORDER)

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.7))
    draw_order = [m for m in methods if m not in {EDGE_METHOD, GLOBAL_METHOD}]
    draw_order += [GLOBAL_METHOD, EDGE_METHOD]
    for method in draw_order:
        if method not in rates:
            continue
        is_edge = method == EDGE_METHOD
        is_comparator = method == GLOBAL_METHOD
        ax.plot(
            rates.index,
            rates[method] * 100,
            label=method_label(method),
            color=method_color(method),
            linestyle=METHOD_LINESTYLES.get(method, "-"),
            marker=METHOD_MARKERS.get(method, "o"),
            linewidth=2.1 if is_edge else 1.5,
            alpha=1.0 if is_edge or is_comparator else 0.62,
            zorder=5 if is_edge else 4 if is_comparator else 2,
        )
    ax.set_xlabel(BUDGET_XLABEL)
    ax.set_ylabel("Feasible selection rate")
    ax.set_ylim(0, 105)
    ax.set_xticks(rates.index)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    style_axes(ax, "y")
    handles, labels = ax.get_legend_handles_labels()
    label_to_handle = dict(zip(labels, handles))
    legend_labels = [method_label(m) for m in methods if method_label(m) in label_to_handle]
    ax.legend(
        [label_to_handle[l] for l in legend_labels],
        legend_labels,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.2,
        columnspacing=0.8,
    )
    savefig(fig, output / "fig_feasibility.png")

    diag = read_csv(analysis / "catalog_diagnostics_robust.csv")
    if diag.empty:
        return
    diag_cols = ["id", "max_reachable_optional_fraction", "mandatory_ref_energy_j"]
    if not set(diag_cols).issubset(diag.columns):
        return

    dsweep = sweep.merge(diag[diag_cols], on="id", how="inner")
    dsweep = dsweep[dsweep["max_reachable_optional_fraction"] > 0].copy()
    if dsweep.empty:
        return

    dsweep["frac_of_max"] = (
        dsweep["coverage_fraction"] / dsweep["max_reachable_optional_fraction"]
    ).clip(upper=1.0)
    plot_optimality_gap(dsweep, output)
    plot_coverage_energy_tradeoff(dsweep, output)


def plot_optimality_gap(dsweep: pd.DataFrame, output: Path) -> None:
    gap = dsweep.groupby(["method", "energy_multiplier"])["frac_of_max"].mean().unstack(0)
    methods = ordered(gap.columns, METHOD_ORDER)

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.7))
    draw_order = [m for m in methods if m not in {EDGE_METHOD, GLOBAL_METHOD}]
    draw_order += [GLOBAL_METHOD, EDGE_METHOD]
    for method in draw_order:
        if method not in gap:
            continue
        is_edge = method == EDGE_METHOD
        is_comparator = method == GLOBAL_METHOD
        ax.plot(
            gap.index,
            gap[method] * 100,
            label=method_label(method),
            color=method_color(method),
            linestyle=METHOD_LINESTYLES.get(method, "-"),
            marker=METHOD_MARKERS.get(method, "o"),
            linewidth=2.1 if is_edge else 1.5,
            alpha=1.0 if is_edge or is_comparator else 0.62,
            zorder=5 if is_edge else 4 if is_comparator else 2,
        )
    ax.set_xlabel(BUDGET_XLABEL)
    ax.set_ylabel("Coverage attained vs. catalog maximum")
    ax.set_ylim(0, 105)
    ax.set_xticks(gap.index)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    style_axes(ax, "y")
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.2,
        columnspacing=0.8,
    )
    savefig(fig, output / "fig_optimality_gap.png")


def plot_coverage_energy_tradeoff(dsweep: pd.DataFrame, output: Path) -> None:
    rep = dsweep[(abs(dsweep["energy_multiplier"] - 1.30) < 1e-9) & dsweep["feasible"]].copy()
    if rep.empty:
        return
    rep["energy_j"] = pd.to_numeric(rep["energy_j"], errors="coerce")
    rep = rep.dropna(subset=["energy_j"])
    if rep.empty:
        return

    rep["energy_ratio"] = rep["energy_j"] / rep["mandatory_ref_energy_j"]
    summary = (
        rep.groupby("method")
        .agg(
            energy_mean=("energy_ratio", "mean"),
            energy_q25=("energy_ratio", lambda s: s.quantile(0.25)),
            energy_q75=("energy_ratio", lambda s: s.quantile(0.75)),
            coverage_mean=("coverage_fraction", "mean"),
            coverage_q25=("coverage_fraction", lambda s: s.quantile(0.25)),
            coverage_q75=("coverage_fraction", lambda s: s.quantile(0.75)),
            n=("coverage_fraction", "size"),
        )
        .reset_index()
    )
    methods = ordered(summary["method"].unique(), METHOD_ORDER)

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.9))
    for method in [m for m in methods if m not in {EDGE_METHOD, GLOBAL_METHOD}] + [
        GLOBAL_METHOD,
        EDGE_METHOD,
    ]:
        row = summary[summary["method"] == method]
        if row.empty:
            continue
        row = row.iloc[0]
        is_edge = method == EDGE_METHOD
        color = method_color(method)
        x = row["energy_mean"]
        y = row["coverage_mean"] * 100
        xerr = np.array(
            [[max(0.0, x - row["energy_q25"])], [max(0.0, row["energy_q75"] - x)]]
        )
        yerr = np.array(
            [
                [max(0.0, y - row["coverage_q25"] * 100)],
                [max(0.0, row["coverage_q75"] * 100 - y)],
            ]
        )
        ax.errorbar(
            x,
            y,
            xerr=xerr,
            yerr=yerr,
            fmt=METHOD_MARKERS.get(method, "o"),
            color=color,
            ecolor=color,
            elinewidth=1.1 if is_edge else 0.8,
            capsize=2.5,
            markersize=7 if is_edge else 5.5,
            markerfacecolor=color if is_edge else "white",
            markeredgecolor=color,
            markeredgewidth=1.3,
            alpha=1.0 if is_edge else 0.72,
            label=method_label(method),
            zorder=5 if is_edge else 3,
        )

    ax.set_xlabel("Energy / mandatory-baseline energy")
    ax.set_ylabel("Coverage")
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    style_axes(ax, "both")
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.1,
        columnspacing=0.8,
    )
    savefig(fig, output / "fig_coverage_energy_tradeoff.png")


def plot_residual_risk(rr: pd.DataFrame, output: Path) -> None:
    sub = nominal_medium(rr)
    if "energy_multiplier" in sub.columns:
        sub = sub[abs(sub["energy_multiplier"] - 1.30) < 1e-9]
    if sub.empty:
        return
    pivot = sub.pivot_table(
        index="contract",
        columns="risk_category",
        values="residual_weight",
        aggfunc="sum",
        fill_value=0,
    )
    contracts = ordered(pivot.index, CONTRACT_ORDER)
    risks = ordered(pivot.columns, list(RISK_LABELS))
    pivot = pivot.reindex(index=contracts, columns=risks, fill_value=0)

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.4))
    y = np.arange(len(pivot.index))
    left = np.zeros(len(pivot.index))
    for risk in risks:
        values = pivot[risk].values
        ax.barh(
            y,
            values,
            left=left,
            label=risk_label(risk),
            color=RISK_COLORS.get(risk, "#8E8E8E"),
            edgecolor="white",
            linewidth=0.5,
        )
        left += values

    for j, total in enumerate(left):
        ax.text(total + max(left.max() * 0.015, 0.4), y[j], f"{total:.0f}", va="center", fontsize=7.5)

    ax.set_yticks(y)
    ax.set_yticklabels([contract_label(c) for c in pivot.index])
    ax.invert_yaxis()
    ax.set_xlabel("Residual optional-risk weight")
    style_axes(ax, "x")
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.0,
        columnspacing=0.7,
    )
    savefig(fig, output / "fig_residual_risk.png")


def plot_residual_vs_budget(rr: pd.DataFrame, output: Path) -> None:
    if "energy_multiplier" not in rr.columns:
        return
    sub = nominal_medium(rr)
    if sub.empty:
        return

    pivot = sub.pivot_table(
        index="energy_multiplier",
        columns="risk_category",
        values="residual_weight",
        aggfunc="sum",
        fill_value=0,
    ).sort_index()
    risks = [r for r in ordered(pivot.columns, list(RISK_LABELS)) if pivot[r].sum() > 0]
    if not risks:
        return

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.4))
    x = np.arange(len(pivot.index))
    bottom = np.zeros(len(pivot.index))
    for risk in risks:
        values = pivot[risk].values
        ax.bar(
            x,
            values,
            bottom=bottom,
            label=risk_label(risk),
            color=RISK_COLORS.get(risk, "#8E8E8E"),
            edgecolor="white",
            linewidth=0.5,
            width=0.62,
        )
        bottom += values

    for j, total in enumerate(bottom):
        ax.text(x[j], total + bottom.max() * 0.02, f"{total:.0f}", ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{v:g}" for v in pivot.index])
    ax.set_xlabel(BUDGET_XLABEL)
    ax.set_ylabel("Residual optional-risk weight")
    ax.set_ylim(0, bottom.max() * 1.12 if bottom.max() > 0 else 1)
    style_axes(ax, "y")
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        handlelength=1.0,
        columnspacing=0.7,
    )
    savefig(fig, output / "fig_residual_vs_budget.png")


def plot_power_sensitivity(ps: pd.DataFrame, output: Path) -> None:
    """Budget violations when a nominal-power plan meets high-power reality."""
    sub = ps[ps["admitted_nominal"].map(to_bool)].copy()
    sub = sub[sub["actual_energy_j"].notna()]
    if sub.empty:
        return

    agg = sub.groupby("energy_multiplier").agg(
        violation_rate=("energy_violation", "mean"),
        replanned_feasible=("replanned_feasible", lambda s: s.map(to_bool).mean()),
    ).sort_index()
    agg *= 100
    shift = float(sub["energy_ratio"].mean())

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.7))
    x = agg.index.to_numpy()

    # The cost shift itself: budgets with less slack than this cannot absorb it.
    ax.axvline(shift, color="#555555", linestyle=":", linewidth=1.0)
    ax.annotate(f"cost shift\n{(shift - 1) * 100:.0f}%", xy=(shift, 50),
                xytext=(3, 0), textcoords="offset points",
                fontsize=6.5, color="#555555", va="center")

    ax.plot(x, agg["violation_rate"], marker="o", color=method_color(EDGE_METHOD),
            linewidth=2.0, label="Budget violated")
    ax.plot(x, agg["replanned_feasible"], marker="s", color=COVERAGE_LOSS_COLOR,
            linestyle="--", linewidth=1.5, label="Re-planning feasible")

    ax.set_xlabel(BUDGET_XLABEL)
    ax.set_ylabel("Share of configurations")
    ax.set_ylim(0, 105)
    ax.set_xticks(x)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    style_axes(ax, "y")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
              frameon=False, handlelength=1.4, columnspacing=1.0)
    savefig(fig, output / "fig_power_sensitivity.png")


def plot_power_stability(ps: pd.DataFrame, output: Path) -> None:
    if ps.empty:
        return
    agg = (
        ps.groupby("contract")
        .agg(
            mean_coverage_loss=("coverage_loss", "mean"),
            same_pipeline_rate=("same_pipeline", "mean"),
        )
        .reset_index()
    )
    contracts = ordered(agg["contract"], CONTRACT_ORDER)
    agg = agg.set_index("contract").reindex(contracts).reset_index()
    agg["coverage_change_pp"] = agg["mean_coverage_loss"] * 100

    fig, ax = plt.subplots(figsize=(COLUMN_WIDTH_IN, 2.4))
    y = np.arange(len(agg))
    colors = [
        method_color(EDGE_METHOD) if v >= 0 else COVERAGE_LOSS_COLOR
        for v in agg["coverage_change_pp"]
    ]
    ax.barh(y, agg["coverage_change_pp"], color=colors, edgecolor="#222222", linewidth=0.5)
    ax.axvline(0, color="#555555", linewidth=0.8)
    max_abs = max(0.5, agg["coverage_change_pp"].abs().max() * 1.35)
    for j, row in agg.iterrows():
        x = row["coverage_change_pp"]
        # Bars extend left for losses, so the right half of the axes is free.
        ax.text(
            max(x, 0) + max_abs * 0.05,
            j,
            f"{x:+.1f} pp ({row['same_pipeline_rate'] * 100:.0f}% same pipeline)",
            va="center",
            ha="left",
            fontsize=7,
        )

    ax.set_yticks(y)
    ax.set_yticklabels([contract_label(c) for c in agg["contract"]])
    ax.invert_yaxis()
    ax.set_xlim(-max_abs, max_abs)
    ax.set_xlabel("Coverage change: nominal - high-power (percentage points)")
    ax.set_title("Energy-model sensitivity of edge-contract selection")
    style_axes(ax, "x")
    savefig(fig, output / "fig_power_stability.png")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    configure_style()

    method_summary = drop_greedy(read_csv(args.analysis / "method_summary_robust.csv"))
    if not method_summary.empty:
        plot_method_by_contract(method_summary, args.output)

    constraint_sweep = drop_greedy(read_csv(args.analysis / "constraint_sweep_robust.csv"))
    if not constraint_sweep.empty:
        plot_method_by_workflow(constraint_sweep, args.output)
        plot_coverage_envelope(constraint_sweep, args.output)
        plot_coverage_envelope_panels(constraint_sweep, args.output)
        plot_feasibility_and_reachable(constraint_sweep, args.analysis, args.output)

    residual_risk = read_csv(args.analysis / "residual_risk_by_category.csv")
    if not residual_risk.empty:
        plot_residual_risk(residual_risk, args.output)
        plot_residual_vs_budget(residual_risk, args.output)

    power_sensitivity = read_csv(args.analysis / "power_sensitivity.csv")
    if not power_sensitivity.empty:
        plot_power_sensitivity(power_sensitivity, args.output)

    # Older analysis directories still carry the superseded stability table.
    power_stability = read_csv(args.analysis / "power_stability.csv")
    if not power_stability.empty:
        plot_power_stability(power_stability, args.output)

    print(f"Wrote figures to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
