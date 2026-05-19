#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calibrate a simulator config from real pipeline outputs."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("proxy_dd/config_distributed_example.json"),
        help="Base simulator config to calibrate.",
    )
    parser.add_argument(
        "--real-results",
        type=Path,
        default=Path("real_pipeline_reference/results"),
        help="Real pipeline results directory containing file_stage_metrics.csv, stage_queue_summary.csv, and stage_timeline.csv.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("proxy_dd/config_distributed_calibrated.json"),
        help="Output calibrated simulator config.",
    )
    parser.add_argument(
        "--application-mode",
        choices=("compute", "total"),
        default="compute",
        help="Use only measured application compute time, or the full application read+compute+write time.",
    )
    parser.add_argument(
        "--source-arrival-strategy",
        choices=("throughput", "downstream_mean"),
        default="throughput",
        help="How to estimate stage1/source interarrival when the real pipeline uses a burst source.",
    )
    return parser.parse_args()


def read_csv_rows(path: Path):
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


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
        fp.write("\n")


def stage_application_means(file_stage_metrics, application_mode):
    per_stage = {}
    for row in file_stage_metrics:
        stage = row["stage"]
        metric = "application_compute_seconds" if application_mode == "compute" else "application_total_seconds"
        per_stage.setdefault(stage, []).append(float_value(row, metric))
    return {
        stage: (sum(values) / len(values) if values else 0.0)
        for stage, values in per_stage.items()
    }


def stage_elapsed_by_timeline(stage_timeline):
    by_stage = {}
    for row in stage_timeline:
        stage = row["stage"]
        arrival = float_value(row, "arrival_time_seconds")
        completion = float_value(row, "completion_time_seconds")
        metrics = by_stage.setdefault(stage, {"first_arrival": None, "last_completion": None, "objects": 0})
        metrics["objects"] += 1
        if metrics["first_arrival"] is None or arrival < metrics["first_arrival"]:
            metrics["first_arrival"] = arrival
        if metrics["last_completion"] is None or completion > metrics["last_completion"]:
            metrics["last_completion"] = completion
    return {
        stage: {
            "elapsed": max(0.0, values["last_completion"] - values["first_arrival"]),
            "objects": values["objects"],
        }
        for stage, values in by_stage.items()
    }


def choose_source_interarrival(queue_summary_rows, elapsed_map, strategy):
    by_stage = {row["stage"]: row for row in queue_summary_rows}
    stage1 = by_stage.get("stage1")
    if stage1 is None:
        return None, "stage1 missing in queue summary"

    stage1_interarrival = float_value(stage1, "mean_interarrival_seconds")
    if stage1_interarrival > 0.0:
        return stage1_interarrival, "used measured stage1 mean interarrival"

    if strategy == "downstream_mean":
        downstream = [
            float_value(row, "mean_interarrival_seconds")
            for row in queue_summary_rows
            if float_value(row, "mean_interarrival_seconds") > 0.0
        ]
        if downstream:
            return sum(downstream) / len(downstream), "stage1 burst approximated with downstream mean interarrival"

    stage1_elapsed = elapsed_map.get("stage1", {}).get("elapsed", 0.0)
    stage1_objects = elapsed_map.get("stage1", {}).get("objects", 0)
    if stage1_elapsed > 0.0 and stage1_objects > 0:
        return stage1_elapsed / stage1_objects, "stage1 burst approximated with stage1 throughput spacing"

    return None, "could not infer source interarrival"


def calibrate_config(config, application_means, source_interarrival, notes):
    calibrated = json.loads(json.dumps(config))

    for stage in calibrated.get("stages", []):
        stage_name = stage.get("name")
        if stage_name in application_means:
            stage["application_mean_service_time"] = round(application_means[stage_name], 6)

    traces = calibrated.get("traces")
    if isinstance(traces, list) and traces:
        traces[0]["inter_arrival"] = round(source_interarrival, 6)

    calibrated.setdefault("_calibration", {})
    calibrated["_calibration"].update(notes)
    return calibrated


def main():
    args = parse_args()
    config = load_json(args.config.resolve())
    real_results = args.real_results.resolve()

    file_stage_metrics = read_csv_rows(real_results / "file_stage_metrics.csv")
    queue_summary_rows = read_csv_rows(real_results / "stage_queue_summary.csv")
    stage_timeline = read_csv_rows(real_results / "stage_timeline.csv")

    application_means = stage_application_means(file_stage_metrics, args.application_mode)
    elapsed_map = stage_elapsed_by_timeline(stage_timeline)
    source_interarrival, source_note = choose_source_interarrival(
        queue_summary_rows,
        elapsed_map,
        args.source_arrival_strategy,
    )
    if source_interarrival is None:
        raise RuntimeError(f"Unable to determine source interarrival: {source_note}")

    notes = {
        "application_mode": args.application_mode,
        "source_arrival_strategy": args.source_arrival_strategy,
        "source_interarrival_note": source_note,
        "derived_stage_application_mean_service_time": application_means,
        "derived_source_interarrival_seconds": source_interarrival,
    }
    calibrated = calibrate_config(config, application_means, source_interarrival, notes)
    write_json(args.out.resolve(), calibrated)

    print(f"Wrote calibrated config to {args.out.resolve()}")
    print(f"Source interarrival: {source_interarrival:.6f} s ({source_note})")
    for stage_name, value in application_means.items():
        print(f"{stage_name}: application_mean_service_time={value:.6f}")


if __name__ == "__main__":
    main()
