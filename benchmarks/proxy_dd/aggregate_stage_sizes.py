#!/usr/bin/env python3
"""
Aggregate per-run stage_sizes.csv into a summary table
with average input/output (MB) per algorithm, payload and stage.

Writes: proxy_dd/sensitivity_results*/pipeline_stage_sizes_summary.csv
"""
import csv
from pathlib import Path
from collections import defaultdict
import argparse


def aggregate(root_dir: Path, out_csv: Path):
    root = root_dir / "pipeline_architectures"
    if not root.exists():
        raise FileNotFoundError(f"Pipeline architectures folder not found: {root}")

    # key: (alg, payload_mb, stage, stage_name) -> [sum_in_bytes, sum_out_bytes, count]
    agg = defaultdict(lambda: [0.0, 0.0, 0])

    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.split("_")
        if len(parts) < 3:
            continue
        alg = parts[1]
        payload_label = parts[-1]
        try:
            payload_mb = int(payload_label.replace("MB", ""))
        except Exception:
            payload_mb = None

        for run in sorted(d.glob('run_*')):
            sizes_csv = run / 'results' / 'stage_sizes.csv'
            if not sizes_csv.exists():
                continue
            try:
                with sizes_csv.open('r', encoding='utf-8') as fp:
                    reader = csv.DictReader(fp)
                    for r in reader:
                        try:
                            stage = int(r.get('stage', -1))
                        except Exception:
                            stage = -1
                        stage_name = r.get('stage_name', '')
                        try:
                            in_b = float(r.get('input_bytes', 0.0))
                        except Exception:
                            in_b = 0.0
                        try:
                            out_b = float(r.get('output_bytes', 0.0))
                        except Exception:
                            out_b = 0.0

                        key = (alg, payload_mb, stage, stage_name)
                        agg[key][0] += in_b
                        agg[key][1] += out_b
                        agg[key][2] += 1
            except Exception:
                continue

    # write summary
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open('w', newline='', encoding='utf-8') as fp:
        fieldnames = ['algorithm', 'payload_mb', 'stage', 'stage_name', 'avg_input_mb', 'avg_output_mb', 'runs']
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for (alg, payload_mb, stage, stage_name), (sum_in, sum_out, count) in sorted(agg.items()):
            if count <= 0:
                continue
            avg_in_mb = (sum_in / count) / 1e6
            avg_out_mb = (sum_out / count) / 1e6
            writer.writerow({
                'algorithm': alg,
                'payload_mb': payload_mb,
                'stage': stage,
                'stage_name': stage_name,
                'avg_input_mb': f"{avg_in_mb:.6f}",
                'avg_output_mb': f"{avg_out_mb:.6f}",
                'runs': count,
            })
    print(f'Wrote aggregated stage sizes to: {out_csv}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=Path('proxy_dd/sensitivity_results2'), help='Sensitivity results root containing pipeline_architectures')
    p.add_argument('--out', type=Path, default=Path('proxy_dd/sensitivity_results2/pipeline_stage_sizes_summary.csv'))
    args = p.parse_args()
    aggregate(args.root, args.out)
