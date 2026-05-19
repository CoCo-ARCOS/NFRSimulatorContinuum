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
        choices=("io_total", "output_only", "split_io"),
        default="split_io",
        help="Use input+output stage times together, only output stage time, or derive separate read/write bandwidths.",
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
            real_in = sim_in = 0.0
            real_out = real_io
            sim_out = sim_io
        else:
            real_io = float_value(row, "real_input_stage_seconds") + float_value(row, "real_output_stage_seconds")
            sim_io = float_value(row, "sim_input_stage_seconds") + float_value(row, "sim_output_stage_seconds")
            real_in = float_value(row, "real_input_stage_seconds")
            sim_in = float_value(row, "sim_input_stage_seconds")
            real_out = float_value(row, "real_output_stage_seconds")
            sim_out = float_value(row, "sim_output_stage_seconds")

        old_bfs = float(stage.get("b_fs", config.get("b_fs", 0.0)))
        old_bfs_read = float(stage.get("b_fs_read", stage.get("b_fs", config.get("b_fs_read", config.get("b_fs", 0.0)))))
        old_bfs_write = float(stage.get("b_fs_write", stage.get("b_fs", config.get("b_fs_write", config.get("b_fs", 0.0)))))
        if old_bfs <= 0.0 and old_bfs_read > 0.0 and old_bfs_write > 0.0:
            old_bfs = (old_bfs_read + old_bfs_write) / 2.0
        if real_io <= 0.0 or sim_io <= 0.0 or old_bfs <= 0.0:
            continue

        if mode == "split_io" and old_bfs_read > 0.0 and old_bfs_write > 0.0 and real_in > 0.0 and sim_in > 0.0 and real_out > 0.0 and sim_out > 0.0:
            read_scale = sim_in / real_in
            write_scale = sim_out / real_out
            new_bfs_read = old_bfs_read * read_scale
            new_bfs_write = old_bfs_write * write_scale
            updates[stage_name] = {
                "b_fs_read": round(new_bfs_read, 6),
                "b_fs_write": round(new_bfs_write, 6),
            }
            notes[stage_name] = {
                "old_b_fs_read": old_bfs_read,
                "old_b_fs_write": old_bfs_write,
                "real_input_seconds": real_in,
                "sim_input_seconds": sim_in,
                "real_output_seconds": real_out,
                "sim_output_seconds": sim_out,
                "scale_sim_over_real_read": read_scale,
                "scale_sim_over_real_write": write_scale,
                "new_b_fs_read": new_bfs_read,
                "new_b_fs_write": new_bfs_write,
            }
        else:
            scale = sim_io / real_io
            new_bfs = old_bfs * scale
            updates[stage_name] = {"b_fs": round(new_bfs, 6)}
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
            for key, value in updates[stage_name].items():
                stage[key] = value
            if "b_fs_read" in updates[stage_name] and "b_fs_write" in updates[stage_name]:
                stage["b_fs"] = round((updates[stage_name]["b_fs_read"] + updates[stage_name]["b_fs_write"]) / 2.0, 6)

    calibrated.setdefault("_calibration", {})
    calibrated["_calibration"]["b_fs_mode"] = args.mode
    calibrated["_calibration"]["derived_b_fs"] = notes

    write_json(args.out.resolve(), calibrated)
    print(f"Wrote b_fs calibrated config to {args.out.resolve()}")
    for stage_name, info in notes.items():
        if args.mode == "split_io" and "new_b_fs_read" in info:
            print(
                f"{stage_name}: b_fs_read {info['old_b_fs_read']:.6f} -> {info['new_b_fs_read']:.6f} "
                f"(sim_in={info['sim_input_seconds']:.6f}, real_in={info['real_input_seconds']:.6f}); "
                f"b_fs_write {info['old_b_fs_write']:.6f} -> {info['new_b_fs_write']:.6f} "
                f"(sim_out={info['sim_output_seconds']:.6f}, real_out={info['real_output_seconds']:.6f})"
            )
        else:
            print(
                f"{stage_name}: b_fs {info['old_b_fs']:.6f} -> {info['new_b_fs']:.6f} "
                f"(sim_io={info['sim_io_seconds']:.6f}, real_io={info['real_io_seconds']:.6f})"
            )


if __name__ == "__main__":
    main()
