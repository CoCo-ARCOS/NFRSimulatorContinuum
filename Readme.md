

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

Go to the ```proxy_dd``` directory, compile the code, and execute the main program:

```bash
cd proxy_dd
make
./main config_distributed_example.json
./main config_distributed_example.json apptainer ../stages/single_queue.sif
```

#### Configurations

In ```proxy_dd``` directory, edit the file ```config.cfg``` specifying the following:

* ```workers```: number of parallel workers to simulate.
* ```traces_number```: number of input traces for each worker.
* ```traces_fileName```: configuration file containing the parameters of each trace.
* ```service_time_model```: service-time model for benchmark tables. Use ```linear``` for the previous linear interpolation or ```log-log``` for a power-law fit in log-log space.
* ```container_platform```: container runtime for the queue estimator. Use ```docker``` (default) or ```apptainer```.
* ```queue_container_image```: Docker image or Apptainer SIF for the queue estimator. Defaults to ```single:queue``` with Docker and ```../stages/single_queue.sif``` with Apptainer.
* ```application_mean_service_time```: per-stage average application execution time, in seconds, configured inside each stage and simulated between that stage's input and output pipelines with the ```single:queue``` estimator.

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
