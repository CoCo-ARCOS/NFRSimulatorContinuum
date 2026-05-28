# Benchmark Scripts

This folder collects runnable copies of the benchmark and benchmark-helper scripts from the repository root, `proxy_dd/`, and `CaseStudyBoneTumor/`.

Run the commands below from the repository root:

```bash
cd /home/domizzi/Documents/GitHub/NFRSimulatorContinuum
```

## Layout

- `nfr_simulator/`: single-requirement benchmark sweep scripts and plotting helpers copied from the repository root.
- `proxy_dd/`: simulator worker-scaling, sensitivity, paper-evaluation, validation, calibration, and plotting helpers copied from `proxy_dd/`.
- `case_study_bone_tumor/`: Slurm benchmark launchers and result aggregation copied from `CaseStudyBoneTumor/`.

Some copied scripts need the full simulator or case-study assets. Those entry points intentionally run against the original `proxy_dd/` and `CaseStudyBoneTumor/` directories.

## Setup

Install the Python requirements and build the simulator before running simulator benchmarks:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd proxy_dd
make
cd ..
```

If queue estimation uses Docker, also build the queue image:

```bash
docker build -t single:queue ./stages
```

## Single Requirement Benchmarks

Run one requirement benchmark:

```bash
python3 benchmarks/nfr_simulator/benchmark_single_requirement.py \
  --requirement-type compress \
  --algorithm LZ4 \
  --objects 10 \
  --size-mb 100.0 \
  --hardware-profile dantelap
```

Run a sweep:

```bash
python3 benchmarks/nfr_simulator/benchmark_single_requirement.py \
  --sweep \
  --all-requirements \
  --objects-list 10 \
  --size-mb-list 1.0,10.0,100.0 \
  --hardware-profile dantelap
```

Submit the same sweep through Slurm:

```bash
sbatch benchmarks/nfr_simulator/run_sweep_slurm.sh \
  --sweep \
  --all-requirements \
  --objects-list 10 \
  --size-mb-list 1.0,10.0,100.0 \
  --hardware-profile dantelap
```

Plot a generated sweep summary:

```bash
python3 benchmarks/nfr_simulator/plot_sweep_results.py \
  single_requirement_benchmarks/<sweep_name>/sweep_summary.csv
```

## proxy_dd Benchmarks

Run worker-scaling benchmarks:

```bash
python3 benchmarks/proxy_dd/benchmark_workers.py \
  --config proxy_dd/config_distributed_example.json \
  --workers 1 2 4 8 \
  --repeats 1 \
  --output proxy_dd/benchmark_results \
  --with-stage-breakdown \
  --with-timelines
```

Run sensitivity benchmarks:

```bash
python3 benchmarks/proxy_dd/sensitivity_analysis.py \
  --config proxy_dd/config_distributed_example.json \
  --output proxy_dd/sensitivity_results \
  --repeats 1
```

Run the paper-evaluation workflow:

```bash
bash benchmarks/proxy_dd/run_paper_evaluation.sh \
  --output-dir "$PWD/proxy_dd/paper_evaluation"
```

Run a calibration sweep:

```bash
python3 benchmarks/proxy_dd/run_calibration_sweep.py \
  --simulator-dirs proxy_dd \
  --out-dir proxy_dd/calibration_runs
```

## Bone Tumor Case Study Benchmarks

Run the case-study Slurm worker sweep:

```bash
bash benchmarks/case_study_bone_tumor/run_all_experiments.sh
```

Aggregate completed case-study results manually:

```bash
python3 CaseStudyBoneTumor/benchmarks/aggregate_results.py \
  --base_dir CaseStudyBoneTumor/benchmark_results
```

The case-study scripts assume the dataset path and Slurm partition in `CaseStudyBoneTumor/deploy_distributed.sh` are valid for the cluster.
