# Real Pipeline Reference

This folder contains a small runnable reference pipeline that performs real byte-level processing similar to the simulator:

- per-stage input requirements
- per-stage application processing
- per-stage application size transformation
- per-stage output requirements
- timing of read, compute, and write work

It is meant as a concrete baseline for experiments and demonstrations, not as a replacement for the simulator.

## What it does

For each file, each stage executes:

1. input pipeline requirements
2. application processing
3. output pipeline requirements

The application step:

- reads the current data
- transforms it
- changes its size using `application_size_factor`
- writes the transformed data

The output is a new file plus sidecar metadata used by inverse input operations such as decompression, verification, and parity decoding.

## Supported requirement types

- `compress`
  - `ZLIB`, `BZ2`, `LZMA`
  - `LZ4` via `lz4`
  - `ZSTD` via `zstandard`
- `hash`
  - `SHA256`, `SHA3_256`
  - `BLAKE3` via `blake3`
  - `HMAC_SHA256`
- `cipher`
  - `AES` and `CHACHA20` via `pycryptodome`
  - `ASCON` via `ascon`
  - `RS` via `zfec`

The implementation now mirrors the benchmark repository more closely:

- original payload write/read before each NFR
- transformed payload write/read for compression, confidentiality, and reliability
- true `zfec` encoding/decoding for `RS`
- actual encryption and hashing libraries instead of fallbacks

## Dependencies

To run the full benchmark-aligned pipeline you will want:

```bash
pip install pycryptodome lz4 zstandard blake3 ascon zfec
```

If one of those is missing, the runner raises a clear message when that algorithm is requested.

## Files

- [pipeline_runner.py](/home/domizzi/Documents/GitHub/SimulatorContinuum/real_pipeline_reference/pipeline_runner.py)
- [sample_config.json](/home/domizzi/Documents/GitHub/SimulatorContinuum/real_pipeline_reference/sample_config.json)
- [sync_from_simulator_config.py](/home/domizzi/Documents/GitHub/SimulatorContinuum/real_pipeline_reference/sync_from_simulator_config.py)

## Run

From the repository root:

```bash
python3 real_pipeline_reference/pipeline_runner.py \
  --config real_pipeline_reference/sample_config.json \
  --workers 8
```

To refresh the reference config from the simulator config:

```bash
python3 real_pipeline_reference/sync_from_simulator_config.py \
  --simulator-config proxy_dd/config_distributed_example.json \
  --out real_pipeline_reference/sample_config.json
```

The runner creates:

- `generated_input/`
- `work/`
- `output/`
- `results/`

## Outputs

Inside the configured `results_dir`:

- `stage_summary.csv`
- `stage_totals_by_workers.csv`
- `file_stage_metrics.csv`
- `overall_summary.json`

These report:

- input, application, and output times per stage
- simulator-style per-stage totals broken down by worker count and requirement family
- application read/write/compute timing
- per-file processing metrics
- final pipeline totals

## Compare with the simulator

Use [compare_real_pipeline_vs_simulator.py](/home/domizzi/Documents/GitHub/SimulatorContinuum/real_pipeline_reference/compare_real_pipeline_vs_simulator.py)
to compare the real pipeline stage timings against simulator outputs.

From the repository root:

```bash
python3 real_pipeline_reference/compare_real_pipeline_vs_simulator.py \
  --real-results real_pipeline_reference/results/stage_summary.csv \
  --simulator-results proxy_dd/results
```

If you want to compare against an aggregated worker-scaling summary, pass the CSV and
select the worker count:

```bash
python3 real_pipeline_reference/compare_real_pipeline_vs_simulator.py \
  --real-results real_pipeline_reference/results/stage_summary.csv \
  --simulator-results proxy_dd/paper_evaluation/sensitivity/workers_detailed_summary.csv \
  --workers 8 \
  --out-dir real_pipeline_reference/comparison_results
```

The comparison writes:

- `stage_time_comparison.csv`
- `overall_comparison.json`
- `comparison_summary.md`
- `stage_total_comparison.png`
- `stage_component_comparison.png`

Current note:

- the comparison matches stage timing structure, but it does not yet verify that the
  simulator and real pipeline used exactly the same file sizes or file counts. Keep the
  workload definitions aligned when using these plots for reporting.
