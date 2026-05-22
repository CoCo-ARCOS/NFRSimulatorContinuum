#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a real pipeline config from a simulator config."
    )
    parser.add_argument(
        "--simulator-config",
        type=Path,
        default=Path("proxy_dd/config_distributed_example.json"),
        help="Path to the simulator JSON config.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("real_pipeline_reference/sample_config.json"),
        help="Output path for the translated real pipeline config.",
    )
    parser.add_argument(
        "--files",
        type=int,
        default=4,
        help="Number of generated input files for the real pipeline.",
    )
    parser.add_argument(
        "--size-mb",
        type=float,
        default=1.0,
        help="Generated input size in MB for the real pipeline.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for generated input data.",
    )
    parser.add_argument(
        "--mode",
        choices=("random", "compressible"),
        default="random",
        help="Input data generation mode.",
    )
    return parser.parse_args()


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def transform_stage(stage):
    return {
        "name": stage["name"],
        "application_size_factor": float(stage.get("application_size_factor", 1.0)),
        "input_requirements": list(stage.get("input_requirements", [])),
        "output_requirements": list(stage.get("output_requirements", [])),
    }


def translate(simulator_config, args):
    return {
        "source_simulator_config": str(args.simulator_config),
        "input_dir": "generated_input",
        "work_dir": "work",
        "output_dir": "output",
        "results_dir": "results",
        "generate_input": {
            "files": int(args.files),
            "size_mb": float(args.size_mb),
            "seed": int(args.seed),
            "mode": args.mode,
        },
        "ida_k": int(simulator_config.get("ida_k", 8)),
        "ida_m": int(simulator_config.get("ida_m", 4)),
        "aes_key_bits": int(simulator_config.get("aes_key_bits", 256)),
        "stages": [transform_stage(stage) for stage in simulator_config.get("stages", [])],
    }


def main():
    args = parse_args()
    simulator_config = load_json(args.simulator_config.resolve())
    translated = translate(simulator_config, args)
    out_path = args.out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(translated, fp, indent=2)
        fp.write("\n")


if __name__ == "__main__":
    main()
