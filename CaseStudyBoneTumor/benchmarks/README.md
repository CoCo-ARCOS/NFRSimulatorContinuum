# Bone Tumor Benchmark Helpers

This folder contains the result aggregation helper used by the bone tumor case-study Slurm benchmark.

From `CaseStudyBoneTumor/`, run the full worker sweep:

```bash
./run_all_experiments.sh
```

The sweep writes experiment folders under `benchmark_results/` and submits the final aggregation job automatically.

To aggregate completed results manually:

```bash
python benchmarks/aggregate_results.py --base_dir benchmark_results
```

The aggregator writes:

```text
benchmark_results/final_benchmark.csv
```
