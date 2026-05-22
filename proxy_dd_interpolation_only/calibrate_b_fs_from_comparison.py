#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calibrate per-stage b_fs values from a real-vs-simulator service comparison."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("proxy_dd/config_distributed_calibrated.json"),
        help="Base simulator config to update.",
    )
    parser.add_argument(
        "--comparison",
        type=Path,
        default=Path("real_pipeline_reference/comparison_results_service/stage_time_comparison.csv"),
        help="Service-mode comparison CSV produced by compare_real_pipeline_vs_simulator.py.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("proxy_dd/config_distributed_calibrated_bfs.json"),
        help="Output config path.",
    )
    parser.add_argument(
        "--mode",
        choices=("io_total", "output_only"),
        default="io_total",
        help="Use input+output stage times together, or only output stage time.",
    )
    return parser.parse_args()


def read_csv_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
        fp.write("\n")


def float_value(row, key, default=0.0):
    value = row.get(key, "")
    if value in ("", None):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def derive_bfs_updates(rows, config, mode):
    stage_config = {stage["name"]: stage for stage in config.get("stages", [])}
    updates = {}
    notes = {}
    for row in rows:
        stage_name = row["stage_name"]
        stage = stage_config.get(stage_name)
        if not stage:
            continue

        if mode == "output_only":
            real_io = float_value(row, "real_output_stage_seconds")
            sim_io = float_value(row, "sim_output_stage_seconds")
        else:
            real_io = float_value(row, "real_input_stage_seconds") + float_value(row, "real_output_stage_seconds")
            sim_io = float_value(row, "sim_input_stage_seconds") + float_value(row, "sim_output_stage_seconds")

        old_bfs = float(stage.get("b_fs", config.get("b_fs", 0.0)))
        if real_io <= 0.0 or sim_io <= 0.0 or old_bfs <= 0.0:
            continue

        scale = sim_io / real_io
        new_bfs = old_bfs * scale
        updates[stage_name] = round(new_bfs, 6)
        notes[stage_name] = {
            "old_b_fs": old_bfs,
            "real_io_seconds": real_io,
            "sim_io_seconds": sim_io,
            "scale_sim_over_real": scale,
            "new_b_fs": new_bfs,
        }
    return updates, notes


def main():
    args = parse_args()
    config = load_json(args.config.resolve())
    rows = read_csv_rows(args.comparison.resolve())
    updates, notes = derive_bfs_updates(rows, config, args.mode)

    calibrated = json.loads(json.dumps(config))
    for stage in calibrated.get("stages", []):
        stage_name = stage["name"]
        if stage_name in updates:
            stage["b_fs"] = updates[stage_name]

    calibrated.setdefault("_calibration", {})
    calibrated["_calibration"]["b_fs_mode"] = args.mode
    calibrated["_calibration"]["derived_b_fs"] = notes

    write_json(args.out.resolve(), calibrated)
    print(f"Wrote b_fs calibrated config to {args.out.resolve()}")
    for stage_name, info in notes.items():
        print(
            f"{stage_name}: b_fs {info['old_b_fs']:.6f} -> {info['new_b_fs']:.6f} "
            f"(sim_io={info['sim_io_seconds']:.6f}, real_io={info['real_io_seconds']:.6f})"
        )


if __name__ == "__main__":
    main()
