#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


SENSITIVITY_EXPECTATIONS = {
    "payload_size": "increasing",
    "devices": "increasing",
    "workers": "decreasing",
    "network_bandwidth": "decreasing",
    "storage_bandwidth": "decreasing",
    "nfr_pipeline": "increasing_ordered",
}

NFR_ORDER = {"minimal": 0, "baseline": 1, "heavy": 2, "maximal": 3}
HARDWARE_ORDER = {"c3": 0, "dianalap": 1, "toge": 2}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize interpolation, queueing, and sensitivity outputs into a paper-friendly report."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        required=True,
        help="Root directory produced by run_paper_evaluation.sh.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=None,
        help="Markdown file to write. Defaults to <input-root>/paper_evaluation_summary.md.",
    )
    return parser.parse_args()


def read_csv_rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def float_value(row, key, default=0.0):
    value = row.get(key, "")
    if value in ("", None):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def interpolation_section(input_root):
    rows = []
    interpolation_root = input_root / "interpolation"
    for profile_dir in sorted(interpolation_root.iterdir()) if interpolation_root.exists() else []:
        if not profile_dir.is_dir():
            continue
        summary_rows = read_csv_rows(profile_dir / "summary.csv")
        overall = next((row for row in summary_rows if row.get("level") == "overall"), None)
        if not overall:
            continue
        operation_rows = [row for row in summary_rows if row.get("level") == "operation"]
        worst_operation = max(operation_rows, key=lambda row: float_value(row, "rmse_seconds"), default=None)
        rows.append(
            {
                "profile": profile_dir.name,
                "mae": float_value(overall, "mae_seconds"),
                "rmse": float_value(overall, "rmse_seconds"),
                "mape": float_value(overall, "mape_percent"),
                "samples": int(float_value(overall, "samples")),
                "worst_operation": worst_operation.get("operation", "") if worst_operation else "",
                "worst_rmse": float_value(worst_operation, "rmse_seconds") if worst_operation else 0.0,
            }
        )
    return rows


def queueing_section(input_root):
    rows = []
    queueing_root = input_root / "queueing"
    for profile_dir in sorted(queueing_root.iterdir()) if queueing_root.exists() else []:
        if not profile_dir.is_dir():
            continue
        overall_rows = read_csv_rows(profile_dir / "overall.csv")
        summary_rows = read_csv_rows(profile_dir / "summary.csv")
        overall = next((row for row in overall_rows if row.get("scope") == "reference:overall"), None)
        mgc_rows = [row for row in summary_rows if row.get("model") == "mgc"]
        worst_mgc = max(mgc_rows, key=lambda row: float_value(row, "reference_response_error_percent"), default=None)
        rows.append(
            {
                "profile": profile_dir.name,
                "mean_waiting_error": float_value(overall, "mean_waiting_error_percent") if overall else 0.0,
                "mean_response_error": float_value(overall, "mean_response_error_percent") if overall else 0.0,
                "worst_mgc_scenario": worst_mgc.get("scenario", "") if worst_mgc else "",
                "worst_mgc_response_error": float_value(worst_mgc, "reference_response_error_percent") if worst_mgc else 0.0,
            }
        )
    return rows


def monotonic_direction(values):
    if len(values) < 2:
        return True
    return all(values[i] >= values[i - 1] for i in range(1, len(values)))


def monotonic_reverse(values):
    if len(values) < 2:
        return True
    return all(values[i] <= values[i - 1] for i in range(1, len(values)))


def parse_numeric_label(label):
    text = label.replace("MBps", "").replace("MB", "").replace("-devices", "").replace("-workers", "").replace("-worker", "")
    try:
        return float(text)
    except ValueError:
        return None


def sort_factor_rows(factor, rows):
    if factor == "nfr_pipeline":
        return sorted(rows, key=lambda row: NFR_ORDER.get(row.get("value_label", ""), 999))
    if factor == "hardware_profile":
        return sorted(rows, key=lambda row: HARDWARE_ORDER.get(row.get("value_label", ""), 999))

    annotated = []
    for row in rows:
        numeric = parse_numeric_label(row.get("value_label", ""))
        annotated.append((numeric if numeric is not None else float_value(row, "value"), row))
    return [row for _, row in sorted(annotated, key=lambda item: item[0])]


def sensitivity_section(input_root):
    summary_rows = read_csv_rows(input_root / "sensitivity" / "sensitivity_summary.csv")
    grouped = {}
    for row in summary_rows:
        grouped.setdefault(row.get("factor", ""), []).append(row)

    findings = []
    for factor, rows in sorted(grouped.items()):
        ordered = sort_factor_rows(factor, rows)
        totals = [float_value(row, "pipeline_total_seconds") for row in ordered]
        labels = [row.get("value_label", "") for row in ordered]

        expectation = SENSITIVITY_EXPECTATIONS.get(factor)
        monotonic_ok = None
        if expectation == "increasing":
            monotonic_ok = monotonic_direction(totals)
        elif expectation == "decreasing":
            monotonic_ok = monotonic_reverse(totals)
        elif expectation == "increasing_ordered":
            monotonic_ok = monotonic_direction(totals)

        best_label = ""
        best_total = 0.0
        if ordered:
            best_row = min(ordered, key=lambda row: float_value(row, "pipeline_total_seconds"))
            best_label = best_row.get("value_label", "")
            best_total = float_value(best_row, "pipeline_total_seconds")

        finding = {
            "factor": factor,
            "labels": labels,
            "totals": totals,
            "monotonic_ok": monotonic_ok,
            "best_label": best_label,
            "best_total": best_total,
        }

        if factor == "workers" and ordered:
            finding["highest_worker_total"] = totals[-1]
            finding["lowest_worker_total"] = totals[0]
        if factor == "hardware_profile" and ordered:
            finding["fastest"] = min(ordered, key=lambda row: float_value(row, "pipeline_total_seconds")).get("value_label", "")
            finding["slowest"] = max(ordered, key=lambda row: float_value(row, "pipeline_total_seconds")).get("value_label", "")

        findings.append(finding)
    return findings


def build_report(interpolation_rows, queueing_rows, sensitivity_findings, input_root):
    lines = [
        "# Overall Simulator Evaluation",
        "",
        f"Generated from `{input_root}`.",
        "",
        "## Interpolation Validation",
        "",
        "| Profile | Samples | MAE (s) | RMSE (s) | MAPE (%) | Worst operation by RMSE | Worst RMSE (s) |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]

    for row in interpolation_rows:
        lines.append(
            f"| {row['profile']} | {row['samples']} | {row['mae']:.6f} | {row['rmse']:.6f} | "
            f"{row['mape']:.2f} | {row['worst_operation']} | {row['worst_rmse']:.6f} |"
        )

    lines.extend(
        [
            "",
            "## Queueing Validation",
            "",
            "| Profile | Mean waiting error (%) | Mean response error (%) | Worst M/G/c scenario | Worst M/G/c response error (%) |",
            "|---|---:|---:|---|---:|",
        ]
    )

    for row in queueing_rows:
        lines.append(
            f"| {row['profile']} | {row['mean_waiting_error']:.2f} | {row['mean_response_error']:.2f} | "
            f"{row['worst_mgc_scenario']} | {row['worst_mgc_response_error']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Sensitivity Analysis",
            "",
            "| Factor | Expected trend | Monotonic / plausible | Best setting | Notes |",
            "|---|---|---|---|---|",
        ]
    )

    for finding in sensitivity_findings:
        factor = finding["factor"]
        expectation = SENSITIVITY_EXPECTATIONS.get(factor, "qualitative")
        monotonic = finding["monotonic_ok"]
        if monotonic is None:
            status = "qualitative"
        else:
            status = "yes" if monotonic else "mixed"

        notes = ""
        if factor == "workers":
            notes = (
                f"best total at {finding['best_label']} ({finding['best_total']:.3f}s); "
                f"start={finding.get('lowest_worker_total', 0.0):.3f}s, end={finding.get('highest_worker_total', 0.0):.3f}s"
            )
        elif factor == "hardware_profile":
            notes = f"fastest={finding.get('fastest', '')}, slowest={finding.get('slowest', '')}"
        elif finding["labels"]:
            notes = f"range {finding['labels'][0]} -> {finding['labels'][-1]}"

        lines.append(
            f"| {factor} | {expectation} | {status} | {finding['best_label']} | {notes} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Interpolation validation reports how closely the service-time model reproduces measured operation times for each hardware profile.",
            "- Queueing validation reports agreement between analytical/approximate baselines and the controlled queueing implementation.",
            "- Sensitivity analysis checks whether system behavior follows expected monotonic trends under workload, bandwidth, worker-count, hardware-profile, and NFR changes.",
            "",
            "These tables are intended as the paper-level entry point; the detailed CSVs and plots remain in the corresponding subdirectories.",
        ]
    )

    return "\n".join(lines) + "\n"


def main():
    args = parse_args()
    input_root = args.input_root.resolve()
    output_md = args.output_md.resolve() if args.output_md else (input_root / "paper_evaluation_summary.md").resolve()

    interpolation_rows = interpolation_section(input_root)
    queueing_rows = queueing_section(input_root)
    sensitivity_findings = sensitivity_section(input_root)
    report = build_report(interpolation_rows, queueing_rows, sensitivity_findings, input_root)

    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(report, encoding="utf-8")
    print(f"Paper summary written to {output_md}")


if __name__ == "__main__":
    main()
