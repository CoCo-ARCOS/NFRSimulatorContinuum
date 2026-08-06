

### Virtual Container Image construction

First, you have to build two virtual container images: 

```bash
docker build -t single:queue ./stages
docker build -t trace:generator ./TRACE_GENERATOR
```

For Apptainer, build or provide the queue estimator SIF. The simulator defaults to:

```bash
../stages/single_queue.sif
```

### Simulator execution

Go to the `proxy_dd` directory, compile the code, and execute the main program:

```bash
cd proxy_dd
make
./main config_distributed_example.json
./main config_distributed_example.json log-log docker single:queue
```

The simulator outputs extensive performance metrics into the console and generates CSV reports, including Stage Totals, Manager Metrics, Link Metrics, and Machine Energy Estimations.

#### Configurations

In the `proxy_dd` directory, the simulation is configured through a JSON file (e.g., `config_distributed_example.json`). Key fields include:

* `workers`: Number of parallel workers to simulate.
* `traces`: List of trace distributions and sizes.
* `stages`: List of simulated pipeline stages. Each stage specifies its I/O throughput (`b_fs`, `b_fs_read`, `b_fs_write`), `application_mean_service_time`, and an array of `input_requirements` and `output_requirements` (e.g., `compress`, `hash`, `cipher`).
* `machines`: Defines physical or virtual machines, mapping each to specific `stages`, and defining a `hardware_profile` to enable energy consumption estimations.
* `links`: Defines network connections between machines (`b_net`, `latency_ms`).

Command-line arguments can optionally be provided to override defaults:
* `service_time_model`: Use `linear` for interpolation or `log-log` for a power-law fit.
* `container_platform`: Container runtime for the queue estimator (`docker` or `apptainer`).
* `queue_container_image`: Docker image or Apptainer SIF for the queue estimator.

### NFR Cost Profiler

`nfr_profiler.py` turns the simulator into a profiler for the cost of adding NFRs
(confidentiality, integrity, compression, reliability) to data in continuum
workflows: given a declarative request with requirement levels and time/energy
constraints, it proposes compliant configurations, ranks them on the
makespan/energy Pareto front, and attributes the marginal cost of each NFR. See
`PROFILER_README.md`:

```bash
python3 nfr_profiler.py --request profiler_request_example.json \
    --simulator-cmd proxy_dd/main --simulator-dir proxy_dd --plot
```

### Single Requirement Benchmarks

The repository includes a comprehensive benchmark runner designed to compare real pipeline performance against the simulator's predicted models dynamically.

#### Running Locally
To test a single requirement constraint algorithm (e.g., LZ4 compression):
```bash
source venv/bin/activate
python3 benchmark_single_requirement.py --requirement-type compress --algorithm LZ4 --objects 10 --size-mb 100.0 --hardware-profile dantelap
```

To run a multi-dimensional sweep across all algorithms, sizes, and parameters:
```bash
python3 benchmark_single_requirement.py --sweep --all-requirements --objects-list 10 --size-mb-list 1.0,10.0,100.0 --hardware-profile dantelap
```

Running all the tests:

```bash
python3 benchmark_single_requirement.py \
  --sweep \
  --requirement-type cipher \
  --algorithm RS \
  --ida-k-list 4,8,10 \
  --ida-m-list 2,4,4 \
  --workers-list 1,2,4,8,16 \
  --hardware-profile dantelap \
  --objects-list 100 \
  --size-mb-list 1,10,100 \
  --service-time-models all
```

#### Running on Slurm
For large parameter sweeps, you can dispatch the benchmark runner to a Slurm cluster using the provided job script. Just pass the normal arguments to `sbatch`:
```bash
sbatch run_sweep_slurm.sh --sweep --all-requirements --objects-list 10 --size-mb-list 1.0,10.0,100.0 --hardware-profile dantelap
```
Logs will be automatically aggregated into the `slurm_logs/` directory.

### Real Workflow-Engine Experiments

The approach can be executed on real workflow engines (DagOnStar, Parsl, Nextflow)
to demonstrate that realization plans are engine-agnostic and to measure their
behaviour outside the simulator. See [`realengines/README.md`](realengines/README.md)
for the full execution steps, SLURM job scripts, and expected results.

```bash
./setup_venv.sh
./run_realengines_all.sh proxy_dd realengines-output
```
