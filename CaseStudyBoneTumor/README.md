# Bone Tumor Case Study

<object data="casestudy.pdf" type="application/pdf" width="100%" height="500px">
    <p>Your browser does not support PDFs. <a href="casestudy.pdf">Download the PDF</a> instead.</p>
</object>

This directory contains the multiprocessing workflow for the bone tumor case study and the Slurm scripts used to run benchmark experiments.

The commands below assume you are in this directory:

```bash
cd CaseStudyBoneTumor
```

## Files

- `workflow.py`: main edge/fog/cloud workflow.
- `nfr_functions.py`: compression, encryption, encoding, preprocessing, and inference functions used by the workflow.
- `requirements.txt`: Python dependencies.
- `deploy_distributed.sh`: submits one staged Slurm run: edge, then fog, then cloud.
- `run_all_experiments.sh`: submits several Slurm runs with different worker counts and then submits an aggregation job.
- `casestudy.pdf`: case study description.

## Setup

Create and activate a Python environment:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The workflow expects a dataset directory containing DICOM files. By default, `workflow.py` looks in:

```text
/home/domizzi/Downloads/medicalimages/dicoms/
```

Pass `--dataset` explicitly if your files are somewhere else.

## Run The Workflow Locally

Run the full edge, fog, and cloud pipeline:

```bash
python workflow.py \
  --dataset /path/to/dicoms \
  --studies 1 \
  --workers 2 \
  --output_dir results/local_all \
  --stage all
```

Useful arguments:

- `--dataset`: path to the input DICOM directory.
- `--studies`: number of mocked studies to process. The workflow repeats the input file list this many times.
- `--workers`: number of parallel worker processes.
- `--output_dir`: directory where temporary stage outputs, keys, and timing logs are written.
- `--stage`: one of `all`, `edge`, `fog`, or `cloud`.

The main timing output is written to:

```text
<output_dir>/workflow_timing.log
```

The workflow creates these stage directories inside `--output_dir`:

```text
tmp_edge/
tmp_fog/
tmp_cloud/
```

## Run Stages Manually

To run the distributed stages by hand, use the same `--output_dir` for all stages so the later stages can find the previous outputs and shared key:

```bash
python workflow.py --stage edge  --dataset /path/to/dicoms --studies 10 --workers 4 --output_dir results/staged
python workflow.py --stage fog   --dataset /path/to/dicoms --studies 10 --workers 4 --output_dir results/staged
python workflow.py --stage cloud --dataset /path/to/dicoms --studies 10 --workers 4 --output_dir results/staged
```

## Run One Slurm Experiment

`deploy_distributed.sh` submits three dependent Slurm jobs for one experiment:

1. edge stage
2. fog stage, after edge succeeds
3. cloud stage, after fog succeeds

Before running it, check these settings in `deploy_distributed.sh`:

- `STUDIES_DIR`: set this to the DICOM dataset path available on the cluster.
- `PARTITION`: set this to the target Slurm partition.
- Python environment: make sure the batch jobs run with the dependencies from `requirements.txt`.

Then run:

```bash
chmod +x deploy_distributed.sh
./deploy_distributed.sh 10 4 benchmark_results/exp_w4
```

Arguments:

```text
./deploy_distributed.sh <studies> <workers> <output_dir>
```

If omitted, the script defaults to:

```text
studies=10
workers=4
output_dir=.
```

The script prints the cloud-stage Slurm job ID. That ID is used by `run_all_experiments.sh` to create dependencies between experiment jobs and the final aggregation job.

## Run All Slurm Experiments

`run_all_experiments.sh` launches the configured worker-count sweep:

```bash
chmod +x run_all_experiments.sh
./run_all_experiments.sh
```

Current defaults in the script:

```text
studies=100
workers=1 2 4 8
base output directory=benchmark_results
```

Outputs are written under:

```text
benchmark_results/exp_w1/
benchmark_results/exp_w2/
benchmark_results/exp_w4/
benchmark_results/exp_w8/
```



## Troubleshooting

- If no `.dcm` files are found, `workflow.py` falls back to processing every file under `--dataset`. Use a clean DICOM directory to avoid preprocessing errors.
- If fog or cloud stages fail, confirm that they use the same `--output_dir` as the edge stage.
- If Slurm jobs fail on imports, install `requirements.txt` in the environment used by the compute nodes or update the Slurm `--wrap` command to run the correct Python executable.
- If CUDA is unavailable, the cloud inference step falls back to CPU automatically.
