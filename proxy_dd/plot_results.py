#!/usr/bin/env python3
"""
Generate PNG plots from SimulatorContinuum result CSVs.

Usage:
    python plot_results.py
    python plot_results.py --results results --out results/plots

The script expects matplotlib and uses only the Python standard library for CSV
loading. Missing CSV files are skipped.
"""

import argparse
import csv
import os
import sys
from collections import OrderedDict
from pathlib import Path


def read_csv(results_dir, file_name):
    path = results_dir / file_name
    if not path.exists():
        return []

    with path.open(newline="") as fp:
        return list(csv.DictReader(fp))


def number(row, key, default=0.0):
    value = row.get(key, "")
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def number_any(row, keys, default=0.0):
    if isinstance(keys, str):
        keys = (keys,)
    for key in keys:
        value = row.get(key, "")
        if value in (None, ""):
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return default


def text(row, key, default=""):
    value = row.get(key)
    return value if value not in (None, "") else default


def stage_label(row):
    stage = text(row, "stage", "?")
    name = text(row, "stage_name")
    return "S%s %s" % (stage, name) if name else "S%s" % stage


def worker_stage_label(row):
    return "W%s-S%s" % (text(row, "worker", "?"), text(row, "stage", "?"))


def sort_by_numeric(rows, *keys):
    return sorted(rows, key=lambda row: tuple(number(row, key) for key in keys))


def series_has_values(rows, columns):
    return [column for column in columns if any(number(row, column) != 0.0 for row in rows)]


def save_figure(fig, out_path):
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    return out_path


def set_labels(ax, title, ylabel):
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.25)


def rotate_xlabels(ax):
    for label in ax.get_xticklabels():
        label.set_rotation(35)
        label.set_horizontalalignment("right")


def stacked_bar(plt, labels, series, title, ylabel, out_path):
    if not labels or not series:
        return None

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.1), 5))
    x = list(range(len(labels)))
    bottom = [0.0] * len(labels)

    for name, values in series.items():
        ax.bar(x, values, bottom=bottom, label=name)
        bottom = [bottom[i] + values[i] for i in range(len(values))]

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    rotate_xlabels(ax)
    set_labels(ax, title, ylabel)
    ax.legend()
    return save_figure(fig, out_path)


def bar(plt, labels, values, title, ylabel, out_path):
    if not labels:
        return None

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 0.9), 5))
    x = list(range(len(labels)))
    ax.bar(x, values)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    rotate_xlabels(ax)
    set_labels(ax, title, ylabel)
    return save_figure(fig, out_path)


def grouped_bar(plt, labels, series, title, ylabel, out_path):
    if not labels or not series:
        return None

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.0), 5))
    x = list(range(len(labels)))
    names = list(series.keys())
    width = min(0.8 / len(names), 0.35)

    for idx, name in enumerate(names):
        offset = (idx - (len(names) - 1) / 2.0) * width
        ax.bar([pos + offset for pos in x], series[name], width=width, label=name)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    rotate_xlabels(ax)
    set_labels(ax, title, ylabel)
    ax.legend()
    return save_figure(fig, out_path)


def duration_label(seconds):
    if seconds >= 3600.0:
        return "%.1fh" % (seconds / 3600.0)
    if seconds >= 60.0:
        return "%.1fm" % (seconds / 60.0)
    return "%.1fs" % seconds


def parse_stage_requirement_segments(row):
    segments = []
    index = 1

    while True:
        input_label_key = "input_requirement_%d" % index
        input_seconds_key = "input_requirement_%d_seconds" % index
        output_label_key = "output_requirement_%d" % index
        output_seconds_key = "output_requirement_%d_seconds" % index

        has_keys = (
            input_label_key in row
            or input_seconds_key in row
            or output_label_key in row
            or output_seconds_key in row
        )
        if not has_keys:
            break

        input_label = text(row, input_label_key)
        input_seconds = number(row, input_seconds_key)
        if input_label or input_seconds > 0.0:
            segments.append(("input", input_label or "input_%d" % index, input_seconds))

        index += 1

    application_seconds = number(row, "application_seconds")
    if application_seconds > 0.0:
        segments.append(("application", "application", application_seconds))

    index = 1
    while True:
        output_label_key = "output_requirement_%d" % index
        output_seconds_key = "output_requirement_%d_seconds" % index
        has_keys = output_label_key in row or output_seconds_key in row
        if not has_keys:
            break

        output_label = text(row, output_label_key)
        output_seconds = number(row, output_seconds_key)
        if output_label or output_seconds > 0.0:
            segments.append(("output", output_label or "output_%d" % index, output_seconds))

        index += 1

    if not segments:
        fallback = [
            ("input", "input", number_any(row, ("input_stage_seconds", "input_seconds"))),
            ("application", "application", number(row, "application_seconds")),
            ("output", "output", number_any(row, ("output_stage_seconds", "output_seconds"))),
        ]
        segments = [segment for segment in fallback if segment[2] > 0.0]

    return segments


def stage_segment_color(kind, index):
    palettes = {
        "input": ["#4C78A8", "#72B7B2", "#9ecae9", "#6baed6", "#3182bd"],
        "application": ["#F58518"],
        "output": ["#54A24B", "#8CD17D", "#B5CF6B", "#59A14F", "#2E8B57"],
    }
    colors = palettes.get(kind, ["#999999"])
    return colors[index % len(colors)]


def stage_segment_label(stage_name, kind, label):
    if kind == "application":
        return "%s application" % stage_name
    return "%s %s %s" % (stage_name, kind, label)


def plot_stage_timeline(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    segment_columns = [
        (("input_stage_seconds", "input_seconds"), "input"),
        ("application_seconds", "application"),
        (("output_stage_seconds", "output_seconds"), "output"),
        (("transfer_seconds",), "transfer"),
    ]
    colors = {
        "input": "#4C78A8",
        "application": "#F58518",
        "output": "#54A24B",
        "transfer": "#E45756",
    }

    stage_totals = []
    for row in rows:
        total = sum(number_any(row, columns) for columns, _ in segment_columns)
        if total <= 0.0:
            total = number(row, "total_seconds")
        stage_totals.append(total)

    if not any(total > 0.0 for total in stage_totals):
        return []

    total_pipeline_seconds = sum(stage_totals)
    fig_height = max(4, len(rows) * 0.65 + 1.5)
    fig, ax = plt.subplots(figsize=(12, fig_height))
    legend_seen = set()

    cumulative_start = 0.0
    for y, (row, total) in enumerate(zip(rows, stage_totals)):
        segment_start = cumulative_start
        ax.axvline(segment_start, color="#dddddd", linewidth=0.8, zorder=0)

        for column, name in segment_columns:
            duration = number_any(row, column)
            if duration <= 0.0:
                continue

            label = name if name not in legend_seen else None
            ax.barh(
                y,
                duration,
                left=segment_start,
                height=0.5,
                color=colors[name],
                edgecolor="white",
                linewidth=0.8,
                label=label,
            )
            legend_seen.add(name)

            if total_pipeline_seconds > 0.0 and duration / total_pipeline_seconds >= 0.015:
                ax.text(
                    segment_start + duration / 2.0,
                    y,
                    duration_label(duration),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white",
                )

            segment_start += duration

        ax.text(
            segment_start + total_pipeline_seconds * 0.01,
            y,
            "  %s" % duration_label(total),
            va="center",
            fontsize=8,
        )
        cumulative_start += total

    ax.axvline(cumulative_start, color="#bbbbbb", linewidth=1.0, zorder=0)
    ax.set_yticks(list(range(len(rows))))
    ax.set_yticklabels([stage_label(row) for row in rows])
    ax.invert_yaxis()
    ax.set_xlabel("cumulative seconds")
    ax.set_title("Stage Execution Timeline")
    ax.grid(axis="x", alpha=0.25)
    if legend_seen:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=len(legend_seen))
    ax.set_xlim(0.0, cumulative_start * 1.08 if cumulative_start > 0.0 else 1.0)
    path = save_figure(fig, out_dir / ("stage_execution_timeline.%s" % image_format))
    return [path]


def plot_stage_requirement_breakdown(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    labels = [stage_label(row) for row in rows]
    series = OrderedDict()

    for row in rows:
        stage_name = stage_label(row)
        for kind, segment_label, _seconds in parse_stage_requirement_segments(row):
            label = stage_segment_label(stage_name, kind, segment_label)
            if label not in series:
                series[label] = [0.0] * len(rows)

    if not series:
        return []

    for row_index, row in enumerate(rows):
        stage_name = stage_label(row)
        for kind, segment_label, seconds in parse_stage_requirement_segments(row):
            label = stage_segment_label(stage_name, kind, segment_label)
            series[label][row_index] = seconds

    fig, ax = plt.subplots(figsize=(max(10, len(labels) * 1.4), 7))
    x = list(range(len(labels)))
    bottom = [0.0] * len(labels)

    for series_index, (name, values) in enumerate(series.items()):
        if not any(value != 0.0 for value in values):
            continue
        color = stage_segment_color(
            "application" if " application" in name else ("input" if " input " in name else "output"),
            series_index,
        )
        ax.bar(x, values, bottom=bottom, label=name, color=color)
        bottom = [bottom[i] + values[i] for i in range(len(values))]

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    rotate_xlabels(ax)
    set_labels(ax, "Stage Requirement Breakdown", "seconds")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)
    return [save_figure(fig, out_dir / ("stage_requirement_breakdown.%s" % image_format))]


def plot_pipeline_stage_breakdown(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    labels = ["pipeline"]
    series = OrderedDict()

    for row in rows:
        stage_name = stage_label(row)
        for kind, segment_label, seconds in parse_stage_requirement_segments(row):
            label = stage_segment_label(stage_name, kind, segment_label)
            series[label] = [seconds]

    if not series:
        return []

    fig, ax = plt.subplots(figsize=(12, 8))
    bottom = [0.0]
    for series_index, (name, values) in enumerate(series.items()):
        color = stage_segment_color(
            "application" if " application" in name else ("input" if " input " in name else "output"),
            series_index,
        )
        ax.bar([0], values, bottom=bottom, label=name, color=color, width=0.6)
        bottom = [bottom[0] + values[0]]

    ax.set_xticks([0])
    ax.set_xticklabels(labels)
    set_labels(ax, "Pipeline Breakdown By Stage", "seconds")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)
    return [save_figure(fig, out_dir / ("pipeline_stage_breakdown.%s" % image_format))]


def plot_stage_requirement_timeline(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    fig_height = max(4, len(rows) * 0.8 + 1.5)
    fig, ax = plt.subplots(figsize=(14, fig_height))
    legend_seen = set()
    cumulative_start = 0.0
    total_pipeline_seconds = sum(number(row, "total_seconds") for row in rows)

    for y, row in enumerate(rows):
        stage_segments = parse_stage_requirement_segments(row)
        stage_total = sum(seconds for _kind, _label, seconds in stage_segments)
        segment_start = cumulative_start
        ax.axvline(segment_start, color="#dddddd", linewidth=0.8, zorder=0)

        for segment_index, (kind, segment_label, seconds) in enumerate(stage_segments):
            if seconds <= 0.0:
                continue
            legend_label = kind if kind not in legend_seen else None
            ax.barh(
                y,
                seconds,
                left=segment_start,
                height=0.55,
                color=stage_segment_color(kind, segment_index),
                edgecolor="white",
                linewidth=0.8,
                label=legend_label,
            )
            legend_seen.add(kind)
            if total_pipeline_seconds > 0.0 and seconds / total_pipeline_seconds >= 0.012:
                ax.text(
                    segment_start + seconds / 2.0,
                    y,
                    duration_label(seconds),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white",
                )
            segment_start += seconds

        ax.text(
            segment_start + max(total_pipeline_seconds * 0.008, 1e-9),
            y,
            duration_label(stage_total),
            va="center",
            fontsize=8,
        )
        cumulative_start += stage_total

    ax.set_yticks(list(range(len(rows))))
    ax.set_yticklabels([stage_label(row) for row in rows])
    ax.invert_yaxis()
    ax.set_xlabel("cumulative seconds")
    ax.set_title("Stage Requirement Timeline")
    ax.grid(axis="x", alpha=0.25)
    if legend_seen:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=len(legend_seen))
    ax.set_xlim(0.0, cumulative_start * 1.08 if cumulative_start > 0.0 else 1.0)
    return [save_figure(fig, out_dir / ("stage_requirement_timeline.%s" % image_format))]


def plot_pipeline_timeline(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    fig, ax = plt.subplots(figsize=(16, 4.5))
    legend_seen = set()
    segment_start = 0.0
    total_seconds = 0.0

    for row in rows:
        stage_name = stage_label(row)
        for segment_index, (kind, segment_label, seconds) in enumerate(parse_stage_requirement_segments(row)):
            if seconds <= 0.0:
                continue
            label = stage_segment_label(stage_name, kind, segment_label)
            legend_label = label if label not in legend_seen else None
            ax.barh(
                0,
                seconds,
                left=segment_start,
                height=0.5,
                color=stage_segment_color(kind, segment_index),
                edgecolor="white",
                linewidth=0.8,
                label=legend_label,
            )
            if seconds > 0.0 and (total_seconds + seconds) > 0.0:
                ax.text(
                    segment_start + seconds / 2.0,
                    0,
                    duration_label(seconds),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white",
                )
            legend_seen.add(label)
            segment_start += seconds
            total_seconds += seconds

    if total_seconds <= 0.0:
        return []

    ax.set_yticks([0])
    ax.set_yticklabels(["pipeline"])
    ax.set_xlabel("cumulative seconds")
    ax.set_title("Pipeline Timeline")
    ax.grid(axis="x", alpha=0.25)
    ax.set_xlim(0.0, total_seconds * 1.05)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    return [save_figure(fig, out_dir / ("pipeline_timeline.%s" % image_format))]


def plot_stage_totals(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    labels = [stage_label(row) for row in rows]
    series = OrderedDict()

    stage_series = [
        ("input", ("input_stage_seconds", "input_seconds")),
        ("application", ("application_seconds",)),
        ("output", ("output_stage_seconds", "output_seconds")),
        ("transfer", ("transfer_seconds",)),
    ]
    for label, columns in stage_series:
        values = [number_any(row, columns) for row in rows]
        if any(value != 0.0 for value in values):
            series[label] = values

    plots = []
    path = stacked_bar(
        plt,
        labels,
        series,
        "Stage Time Breakdown",
        "seconds",
        out_dir / ("stage_time_breakdown.%s" % image_format),
    )
    if path:
        plots.append(path)

    path = bar(
        plt,
        labels,
        [number(row, "total_seconds") for row in rows],
        "Total Time By Stage",
        "seconds",
        out_dir / ("stage_total_seconds.%s" % image_format),
    )
    if path:
        plots.append(path)
    return plots


def plot_worker_stage_times(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "worker_stage_times.csv"), "worker", "stage")
    if not rows:
        return []

    labels = [worker_stage_label(row) for row in rows]
    columns = series_has_values(
        rows,
        ["input_seconds", "application_seconds", "output_seconds", "transfer_seconds"],
    )
    pretty = {
        "input_seconds": "input",
        "application_seconds": "application",
        "output_seconds": "output",
        "transfer_seconds": "transfer",
    }
    series = OrderedDict(
        (pretty[column], [number(row, column) for row in rows])
        for column in columns
    )

    path = stacked_bar(
        plt,
        labels,
        series,
        "Worker Stage Time Breakdown",
        "seconds",
        out_dir / ("worker_stage_time_breakdown.%s" % image_format),
    )
    return [path] if path else []


def plot_application_times(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "worker_stage_application_times.csv"), "worker", "stage")
    if not rows:
        return []

    labels = [worker_stage_label(row) for row in rows]
    plots = []
    path = bar(
        plt,
        labels,
        [number(row, "application_seconds") for row in rows],
        "Application Time By Worker And Stage",
        "seconds",
        out_dir / ("application_time_by_worker_stage.%s" % image_format),
    )
    if path:
        plots.append(path)

    path = bar(
        plt,
        labels,
        [number(row, "avg_object_application_seconds") for row in rows],
        "Average Application Time Per Object",
        "seconds/object",
        out_dir / ("application_avg_object_time.%s" % image_format),
    )
    if path:
        plots.append(path)
    return plots


def plot_stage_nfr_totals(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "stage_nfr_totals_by_workers.csv"), "stage")
    if not rows:
        return []

    labels = [
        "%s %s %s" % (stage_label(row), text(row, "requirement_pipeline"), text(row, "nfr"))
        for row in rows
    ]
    path = bar(
        plt,
        labels,
        [number(row, "total_seconds") for row in rows],
        "NFR Time By Stage",
        "seconds",
        out_dir / ("stage_nfr_total_seconds.%s" % image_format),
    )
    return [path] if path else []


def plot_worker_totals(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "worker_totals.csv"), "worker")
    if not rows:
        return []

    labels = ["W%s" % text(row, "worker", "?") for row in rows]
    columns = series_has_values(
        rows,
        [
            "compression_decompression_seconds",
            "hashing_seconds",
            "indexing_seconds",
            "crypto_seconds",
            "application_seconds",
        ],
    )
    pretty = {
        "compression_decompression_seconds": "compression",
        "hashing_seconds": "hashing",
        "indexing_seconds": "indexing",
        "crypto_seconds": "crypto",
        "application_seconds": "application",
    }
    series = OrderedDict(
        (pretty[column], [number(row, column) for row in rows])
        for column in columns
    )

    path = stacked_bar(
        plt,
        labels,
        series,
        "Worker Total Time Breakdown",
        "seconds",
        out_dir / ("worker_total_time_breakdown.%s" % image_format),
    )
    return [path] if path else []


def plot_pipeline_metrics(plt, results_dir, out_dir, image_format):
    rows = read_csv(results_dir, "pipeline_metrics.csv")
    if not rows:
        return []

    labels = [text(row, "metric", "?") for row in rows]
    plots = []
    path = bar(
        plt,
        labels,
        [number(row, "total_seconds") for row in rows],
        "Pipeline Total Time By Metric",
        "seconds",
        out_dir / ("pipeline_metric_total_seconds.%s" % image_format),
    )
    if path:
        plots.append(path)

    path = bar(
        plt,
        labels,
        [number(row, "avg_per_task_seconds") for row in rows],
        "Pipeline Average Time By Metric",
        "seconds/task",
        out_dir / ("pipeline_metric_avg_seconds.%s" % image_format),
    )
    if path:
        plots.append(path)
    return plots


def plot_manager_metrics(plt, results_dir, out_dir, image_format):
    rows = [
        row for row in sort_by_numeric(read_csv(results_dir, "manager_metrics.csv"), "stage", "task_index")
        if text(row, "pipeline") != "combined"
    ]
    if not rows:
        return []

    avg_key = "avg_simulated_seconds"
    if avg_key not in rows[0]:
        avg_key = "avg_job_seconds"

    labels = [
        "%s %s %s" % (stage_label(row), text(row, "pipeline"), text(row, "task_name", "task"))
        for row in rows
    ]
    path = bar(
        plt,
        labels,
        [number(row, avg_key) for row in rows],
        "Average Manager Time Per Job",
        "seconds/job",
        out_dir / ("manager_avg_job_seconds.%s" % image_format),
    )
    return [path] if path else []


def plot_workload(plt, results_dir, out_dir, image_format):
    rows = sort_by_numeric(read_csv(results_dir, "worker_workload.csv"), "worker")
    if not rows:
        return []

    labels = ["W%s" % text(row, "worker", "?") for row in rows]
    mb = 1048576.0
    series = OrderedDict(
        [
            ("input MB", [number(row, "total_input_bytes") / mb for row in rows]),
            ("output MB", [number(row, "total_output_bytes") / mb for row in rows]),
        ]
    )
    path = grouped_bar(
        plt,
        labels,
        series,
        "Worker Workload",
        "MB",
        out_dir / ("worker_workload_mb.%s" % image_format),
    )
    return [path] if path else []


def plot_links(plt, results_dir, out_dir, image_format):
    rows = read_csv(results_dir, "link_metrics.csv")
    if not rows:
        return []

    labels = ["%s->%s" % (text(row, "from", "?"), text(row, "to", "?")) for row in rows]
    plots = []
    path = bar(
        plt,
        labels,
        [number(row, "total_seconds") for row in rows],
        "Link Transfer Time",
        "seconds",
        out_dir / ("link_transfer_seconds.%s" % image_format),
    )
    if path:
        plots.append(path)

    path = bar(
        plt,
        labels,
        [number(row, "bytes") / 1048576.0 for row in rows],
        "Link Bytes Transferred",
        "MB",
        out_dir / ("link_bytes_mb.%s" % image_format),
    )
    if path:
        plots.append(path)
    return plots


def parse_args():
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Generate plots from simulator result CSVs.")
    parser.add_argument(
        "--results",
        type=Path,
        default=script_dir / "results",
        help="Directory containing result CSVs. Default: proxy_dd/results",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory for plot images. Default: <results>/plots",
    )
    parser.add_argument(
        "--format",
        choices=["png", "pdf", "svg"],
        default="png",
        help="Image format. Default: png",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    results_dir = args.results.resolve()
    out_dir = (args.out or (results_dir / "plots")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    mpl_cache_dir = out_dir / ".matplotlib-cache"
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_cache_dir))

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is required. Install it with: python -m pip install matplotlib", file=sys.stderr)
        return 1

    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        pass

    plotters = [
        plot_stage_timeline,
        plot_stage_requirement_timeline,
        plot_pipeline_timeline,
        plot_stage_totals,
        plot_stage_requirement_breakdown,
        plot_pipeline_stage_breakdown,
        plot_worker_stage_times,
        plot_application_times,
        plot_stage_nfr_totals,
        plot_worker_totals,
        plot_pipeline_metrics,
        plot_manager_metrics,
        plot_workload,
        plot_links,
    ]

    created = []
    for plotter in plotters:
        created.extend(plotter(plt, results_dir, out_dir, args.format))

    if not created:
        print("No plots generated. Check that CSV files exist in %s." % results_dir)
        return 1

    print("Generated %d plots in %s:" % (len(created), out_dir))
    for path in created:
        print("  %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
