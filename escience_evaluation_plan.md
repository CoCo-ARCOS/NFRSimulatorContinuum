# Evaluation Plan for an eScience Submission

## Evaluation Goal

The paper should evaluate the simulator as a practical tool for studying distributed data-processing pipelines across the computing continuum. The central claim is not only that the simulator can predict execution time, but that it enables experiments that are too expensive, slow, or hard to reproduce with only real deployments.

The evaluation should therefore demonstrate five benefits:

1. Accuracy: simulator predictions are close enough to measured executions to support design decisions.
2. Portability: the same simulator framework works across heterogeneous hardware profiles.
3. Scalability: the simulator explores larger workloads, worker counts, and pipeline designs than the real reference pipeline can cheaply execute.
4. Insight: the simulator exposes bottlenecks and non-functional requirement overheads by stage, component, and resource.
5. Decision support: simulator-selected configurations are near the best configurations measured in the real pipeline.

## Research Questions

| ID | Question | Benefit Captured |
|---|---|---|
| RQ1 | How accurately does the simulator predict service times for compression, integrity, confidentiality, and reliability operations? | Validates the foundation of the simulator. |
| RQ2 | How accurately does the simulator predict queueing behavior and end-to-end stage timing? | Validates the pipeline execution model, not just individual operations. |
| RQ3 | Does the simulator remain accurate across heterogeneous machines such as HPC, laptop, GPU laptop, and Raspberry Pi profiles? | Shows continuum portability. |
| RQ4 | How much faster and cheaper is simulator-based design-space exploration than running the real pipeline for every configuration? | Shows practical research productivity. |
| RQ5 | Can the simulator identify bottlenecks, scaling limits, and useful worker counts under changing workloads and network/storage constraints? | Shows insight and scalability. |
| RQ6 | Can simulator-driven recommendations choose near-optimal configurations when validated against the real pipeline? | Shows decision-support value. |
| RQ7 | Which simulator components matter most: interpolation model, queueing model, size transformations, and hardware-specific calibration? | Shows why the simulator design is necessary. |

## Experimental Assets Already in the Repository

Use these existing assets as the backbone of the evaluation:

| Asset | Role in Evaluation |
|---|---|
| `proxy_dd/results_different_machines/organized/` | Measured operation datasets for C3, Toge, DianaLap, and DanteLap. |
| `proxy_dd/validate_interpolation.py` | Leave-one-out validation for service-time prediction. |
| `proxy_dd/validate_queueing.py` | Queueing validation against analytical and controlled simulation baselines. |
| `proxy_dd/sensitivity_analysis.py` | One-factor sensitivity sweeps for workload, workers, bandwidth, hardware, and NFR complexity. |
| `proxy_dd/run_paper_evaluation.sh` | Reproducible driver for interpolation, queueing, sensitivity, and summary generation. |
| `real_pipeline_reference/` | Real byte-level reference pipeline for end-to-end validation. |
| `benchmark_single_requirement.py` | Real-versus-simulator benchmark driver for individual NFR operations. |
| `proxy_dd_interpolation_only/` | Useful baseline for ablation against the fuller queue-aware simulator. |

Note: the organized data includes `dantelap`, but some validation scripts currently restrict hardware profile choices to `c3`, `toge`, and `dianalap`. Either include only those three in the initial paper run, or extend the profile choices to include `dantelap` before claiming four-machine validation.

## Core Experiments

### E1. Service-Time Model Accuracy

Purpose: show that the simulator can predict real execution time for individual NFR operations when compared against the byte-level reference pipeline. Use `benchmark_single_requirement.py` as the primary experiment because it runs the real operation and the simulator under the same generated workload.

Test design:

| Factor | Values |
|---|---|
| Machines | At least DianaLap for real-vs-simulator runs; C3, Toge, and DanteLap can be used when equivalent real execution is available. |
| Data sizes | 1 MB, 10 MB, 100 MB, with 50 MB and 1000 MB for simulator-only or smaller object-count runs if feasible. |
| Compression | ZLIB, BZ2, LZMA, LZ4, ZSTD. |
| Integrity | SHA256, SHA3_256, BLAKE3, HMAC_SHA256. |
| Confidentiality | AES, CHACHA20. |
| Reliability | Reed-Solomon / IDA with available `k,m` settings. |
| Models | Linear interpolation, log-log model. |

Method:

1. Run `benchmark_single_requirement.py` in sweep mode so every case executes both the real byte-level operation and the simulator.
2. Sweep operation family, algorithm, direction, object count, payload size, worker count, input mode, and service-time model.
3. Use the generated `sweep_error_metrics.csv` as the paper-ready aggregate table.
4. Report error overall, by operation family, by algorithm, by direction, and by service-time model.
5. Optionally keep `validate_interpolation.py` as a secondary leave-one-out check for the interpolation tables.

Metrics:

| Metric | Meaning |
|---|---|
| MAE seconds | Absolute timing error. |
| RMSE seconds | Penalizes large outliers. |
| MAPE percent | Mean absolute percentage error across small and large operations. |
| P50/P90/P95 absolute percentage error | Shows typical and worst-case behavior. |
| Worst operation by RMSE | Identifies where the model needs improvement. |

Suggested command:

```bash
python3 benchmark_single_requirement.py \
  --sweep \
  --all-requirements \
  --directions output \
  --objects-list 100 \
  --size-mb-list 1,10,100 \
  --workers-list 1 \
  --hardware-profile dantelap \
  --service-time-models all \
  --input-modes compressible \
  --sweep-name service_time_accuracy
```

Paper figure/table:

- Table: `sweep_error_metrics.csv` rows for `metric=requirement_compute_seconds`, reporting MAE, RMSE, MAPE, P50 APE, P90 APE, and P95 APE.
- Figure: simulator versus real requirement time on log scale, colored by operation family.
- Figure: error distribution by operation family.

Acceptance target:

- Median relative error should be low enough to preserve ranking decisions among algorithms.
- Outliers should be named and explained, especially compression at large payload sizes or random/compressible input differences if they dominate RMSE.

### E2. Queueing Model Validation

Purpose: show that the simulator's queueing layer behaves correctly under controlled workloads and under service-time distributions derived from real operation measurements.

Test design:

| Scenario Type | Values |
|---|---|
| M/M/1 | Utilization rho = 0.2, 0.4, 0.6, 0.8, 0.9. |
| M/D/1 | Utilization rho = 0.2, 0.4, 0.6, 0.8, 0.9. |
| M/M/c | 2 servers, rho = 0.4, 0.6, 0.8, 0.9. |
| M/G/c | Real profiles: ZLIB compression, SHA256 hash, RS encode. |

Method:

1. Compare controlled queue simulation against analytical baselines where closed forms exist.
2. Compare M/G/c approximations against service distributions sampled from real benchmark tables.
3. Stress near-saturation cases to show how error changes as utilization approaches 1.

Metrics:

| Metric | Meaning |
|---|---|
| Mean waiting-time error percent | Queue delay accuracy. |
| Mean response-time error percent | End-to-end queue service accuracy. |
| Error versus utilization | Whether accuracy degrades near saturation. |
| Worst M/G/c scenario | Identifies risky regions. |

Suggested command:

```bash
cd proxy_dd
./run_paper_evaluation.sh \
  --profiles "c3 dianalap toge" \
  --skip-interpolation \
  --skip-sensitivity \
  --queue-samples 50000 \
  --queue-warmup 5000
```

Paper figure/table:

- Table: mean waiting and response error per machine.
- Figure: response-time error versus utilization.
- Figure: M/G/c measured-service profile comparison.

Acceptance target:

- Low average response-time error for stable utilization regions.
- Explicitly discuss near-saturation behavior as a known hard case rather than hiding it.

### E3. End-to-End Real Pipeline Validation

Purpose: show that the simulator predicts complete pipeline behavior, including stage-level input requirements, application work, output requirements, size changes, and worker scaling.

Test design:

| Factor | Values |
|---|---|
| Workers | 1, 2, 4, 8, 16. |
| Objects | 10, 100, 1000, as feasible. |
| Object size | 1 MB, 10 MB, 100 MB. |
| Input mode | Compressible, random. |
| Pipeline complexity | Minimal, baseline, heavy, maximal. |
| NFR families | Compression, hash, cipher, RS reliability. |
| Hardware profile | At least one laptop profile for real runs; simulator profiles for all machines. |

Method:

1. Generate simulator and real-pipeline configurations from the same workload definition.
2. Run the real byte-level pipeline for tractable scenarios.
3. Run the simulator for the same scenarios.
4. Compare total pipeline time, per-stage time, and time breakdowns.
5. Use larger object counts and sizes only in simulation to show extrapolation.

Metrics:

| Metric | Meaning |
|---|---|
| Pipeline total APE | End-to-end prediction error. |
| Per-stage APE | Whether errors localize to specific stages. |
| Component APE | Input, application, output, compression, hash, crypto. |
| Bottleneck agreement | Whether simulator and real run identify the same slowest stage. |
| Ranking agreement | Whether simulator and real run rank worker counts/configurations similarly. |

Suggested commands:

```bash
python3 real_pipeline_reference/sync_from_simulator_config.py \
  --simulator-config proxy_dd/config_distributed_example.json \
  --out real_pipeline_reference/sample_config.json

python3 real_pipeline_reference/pipeline_runner.py \
  --config real_pipeline_reference/sample_config.json \
  --workers 8

python3 real_pipeline_reference/compare_real_pipeline_vs_simulator.py \
  --real-results real_pipeline_reference/results/stage_summary.csv \
  --simulator-results proxy_dd/results
```

For single-operation sweeps:

```bash
python3 benchmark_single_requirement.py \
  --sweep \
  --all-requirements \
  --objects-list 10,100 \
  --size-mb-list 1,10,100 \
  --workers-list 1,4,8,16 \
  --hardware-profile dianalap \
  --service-time-models all
```

Paper figure/table:

- Figure: simulator versus real pipeline total time across worker counts.
- Figure: stage breakdown for real and simulated runs side by side.
- Table: bottleneck agreement and top-1/top-3 configuration agreement.

Acceptance target:

- Simulator should preserve configuration ranking even when exact timing has some error.
- Bottleneck stage should match the real pipeline for most tested scenarios.

### E4. Heterogeneous Continuum Scenario Study

Purpose: show why the simulator is useful for continuum systems where stages run on heterogeneous machines connected by limited networks.

Test design:

Use a four-stage pipeline similar to `proxy_dd/config_distributed_example.json` and vary:

| Factor | Values |
|---|---|
| Stage placement | All laptop, all HPC, all Raspberry Pi, mixed edge-to-HPC, mixed HPC-to-edge. |
| Link bandwidth | 10, 25, 50, 100, 200 MB/s. |
| Link latency | 1, 10, 20, 50, 100 ms. |
| Storage bandwidth | 10, 50, 100, 250, 500 MB/s. |
| Payload size | 1, 10, 50, 100, 1000 MB. |
| Application size factor | 0.6, 0.8, 1.0, 1.2. |

Method:

1. Create placement scenarios that emulate edge, cloud/HPC, and mixed continuum deployments.
2. For each scenario, record total time, per-stage time, data transferred, and bottleneck resource.
3. Identify when moving a stage to faster hardware helps, and when network/storage costs cancel the benefit.

Metrics:

| Metric | Meaning |
|---|---|
| Pipeline makespan | Main end-to-end performance measure. |
| Throughput | Objects processed per second. |
| Stage utilization / bottleneck | Explains limiting component. |
| Network transfer contribution | Shows continuum communication penalty. |
| Storage contribution | Shows local I/O bottleneck. |
| Speedup versus baseline placement | Shows benefit of placement decisions. |

Paper figure/table:

- Heatmap: best stage placement by payload size and network bandwidth.
- Stacked bars: application, NFR, storage, and network contributions.
- Table: best placement, worst placement, and speedup.

Acceptance target:

- Show at least one non-obvious case where the fastest hardware is not the best placement because transfer or storage cost dominates.

### E5. Sensitivity and Scaling Analysis

Purpose: show that the simulator behaves plausibly over large parameter sweeps and reveals scaling limits.

Test design:

Run one-factor-at-a-time and selected two-factor sweeps:

| Factor | Current Values | Add for Paper |
|---|---|---|
| Payload size | 1, 5, 10, 25, 50 MB or existing 1 to 1000 MB | Use 1, 10, 50, 100, 1000 MB to align with measured data. |
| Devices / objects | 20, 50, 100, 200, 400 | Add 1000 and 10000 simulation-only if runtime allows. |
| Workers | 1, 2, 4, 8, 16 | Add 32 and 64 simulation-only. |
| Network bandwidth | 10, 25, 50, 100, 200 MB/s | Keep as is. |
| Storage bandwidth | 10, 50, 100, 250, 500 MB/s | Keep as is. |
| Hardware profile | C3, DianaLap, Toge | Add DanteLap after script support. |
| NFR complexity | Minimal, baseline, heavy, maximal | Keep as is. |

Method:

1. Run the existing sensitivity analysis with at least three repeats.
2. Add two-factor sweeps for workers x payload size, workers x network bandwidth, and NFR complexity x payload size.
3. Detect saturation points where adding workers gives less than 10 percent improvement.
4. Record monotonicity violations and explain them through bottleneck shifts.

Metrics:

| Metric | Meaning |
|---|---|
| Speedup | `T_1_worker / T_n_workers`. |
| Parallel efficiency | `speedup / n_workers`. |
| Saturation point | First worker count with marginal improvement below threshold. |
| Monotonicity pass rate | Plausibility of simulator trends. |
| Bottleneck shift | When dominant cost changes from application to NFR, storage, or network. |

Suggested command:

```bash
cd proxy_dd
./run_paper_evaluation.sh \
  --profiles "c3 dianalap toge" \
  --sensitivity-repeats 3
```

Paper figure/table:

- Figure: worker scaling with speedup and efficiency.
- Figure: sensitivity tornado plot showing which factors affect makespan most.
- Figure: bottleneck shift across payload size and NFR complexity.

Acceptance target:

- Trends should match expected behavior for payload, devices, workers, and NFR complexity.
- Mixed trends for network/storage should be explained as bottleneck shifts, not just reported as failures.

### E6. Simulator Runtime and Exploration Throughput

Purpose: demonstrate that the simulator enables many more experiments than direct real-pipeline execution.

Test design:

For the same set of configurations, measure:

| Mode | What Runs |
|---|---|
| Real pipeline | Actual byte-level processing in `real_pipeline_reference`. |
| Simulator | `proxy_dd/main` for the same configuration. |
| Simulator sweep | Hundreds or thousands of generated configurations. |

Method:

1. Choose a tractable set of scenarios: for example 3 sizes x 4 worker counts x 4 NFR pipelines.
2. Run both real pipeline and simulator for those scenarios.
3. Measure wall-clock evaluation time, CPU time if available, disk usage, and number of completed scenarios.
4. Then run a large simulator-only sweep that would be infeasible with real runs.

Metrics:

| Metric | Meaning |
|---|---|
| Evaluation wall-clock time | Researcher time saved. |
| Scenarios per hour | Exploration throughput. |
| Speedup of evaluation | Real runtime divided by simulator runtime. |
| Disk usage | Practical reproducibility cost. |
| Maximum explored design space | Scale of scenarios considered. |

Paper figure/table:

- Table: real versus simulator evaluation cost.
- Figure: scenarios explored over time.

Acceptance target:

- Show an order-of-magnitude improvement in exploration throughput for at least moderate payload sizes.

### E7. Decision-Support / Optimization Test

Purpose: show that the simulator is useful not merely as a predictor, but as a tool for choosing configurations.

Test design:

Define an optimization task:

> Given a workload, choose the worker count, NFR algorithms, and stage placement that minimize makespan while satisfying integrity, confidentiality, reliability, and storage/network constraints.

Candidate configurations:

| Dimension | Values |
|---|---|
| Workers | 1, 2, 4, 8, 16. |
| Compression | None, LZ4, ZLIB, ZSTD. |
| Hash | SHA256, BLAKE3, SHA3_256. |
| Cipher | None, AES, CHACHA20. |
| Reliability | None, RS `k=4,m=2`, RS `k=8,m=4`. |
| Placement | All local, all HPC, mixed edge/HPC, mixed laptop/HPC. |

Method:

1. Use the simulator to rank all candidate configurations.
2. Select the predicted top 5 configurations.
3. Run the real pipeline for the top 5 and a random sample of non-top configurations.
4. Compare the simulator's selected configuration against the best real measured configuration among the validated set.

Metrics:

| Metric | Meaning |
|---|---|
| Top-k recall | Whether real-best configurations appear in simulator top-k. |
| Regret percent | `(real_time_selected - real_time_best) / real_time_best`. |
| Spearman rank correlation | Whether simulator preserves ordering. |
| Constraint satisfaction rate | Whether selected configs satisfy NFR constraints. |

Paper figure/table:

- Table: simulator top 5 versus real measured rank and regret.
- Figure: predicted versus real rank.

Acceptance target:

- The selected configuration should be within 5 to 15 percent of the best real measured configuration, depending on scenario variability.

### E8. Ablation Study

Purpose: show that the simulator's design choices matter.

Compare these variants:

| Variant | How to Run / Approximate |
|---|---|
| Full simulator | `proxy_dd`. |
| Interpolation-only baseline | `proxy_dd_interpolation_only`. |
| Linear service-time model | `service_time_model=linear`. |
| Log-log service-time model | `service_time_model=log-log`. |
| Hardware-specific profiles | Use each machine's real values. |
| Hardware-agnostic profile | Use one profile for all machines. |
| Queue-disabled or queue-simplified | Use single-requirement benchmark settings that disable queue/filesystem timing where appropriate. |
| Size-transform disabled | Set application size factor to 1.0 and compare against size-transform-enabled runs. |

Method:

1. Run the same validation set under each variant.
2. Compare prediction error and ranking quality.
3. Attribute improvements to model features.

Metrics:

| Metric | Meaning |
|---|---|
| Pipeline MAPE delta | Accuracy gain from full model. |
| Ranking improvement | Decision-support gain. |
| Bottleneck agreement improvement | Explainability gain. |
| Error by component | Which model component helps which part. |

Paper figure/table:

- Table: ablation variants versus MAPE, top-k recall, and bottleneck agreement.
- Figure: error reduction from each simulator feature.

Acceptance target:

- The full simulator should outperform interpolation-only and hardware-agnostic baselines on end-to-end prediction or ranking.

## Statistical Practice

Use this minimum statistical protocol:

1. Run at least 3 repeats for simulator sensitivity experiments.
2. Run at least 5 repeats for small real-pipeline scenarios where runtime permits.
3. Report mean plus 95 percent confidence interval for main timing results.
4. Use median and P90/P95 error for skewed error distributions.
5. Fix random seeds for generated workloads and queueing validation.
6. Keep raw CSVs and generated configs for every figure.

For large real runs where repeats are expensive, report that limitation and compensate with repeated smaller runs plus simulator-only scaling.

## Recommended Paper Structure for Evaluation

1. Experimental setup:
   - Hardware profiles and measured datasets.
   - Pipeline configuration and NFR operations.
   - Real reference pipeline and simulator setup.
   - Metrics and statistical protocol.

2. Model validation:
   - Service-time interpolation accuracy.
   - Queueing validation.
   - End-to-end real pipeline comparison.

3. Benefits of simulation:
   - Scaling and sensitivity analysis.
   - Heterogeneous continuum placement case study.
   - Exploration throughput compared with real execution.

4. Decision support:
   - Simulator-selected configurations.
   - Top-k validation and regret.

5. Ablation:
   - Which model components are responsible for accuracy and insight.

6. Threats to validity:
   - Limited real hardware diversity.
   - Synthetic workload assumptions.
   - Interpolation outside measured data range.
   - Dependency/library version effects for cryptographic and compression operations.
   - Queueing assumptions near saturation.

## Minimal Submission-Ready Evaluation Package

If time is tight, prioritize this subset:

1. E1 Service-time validation with `benchmark_single_requirement.py` on DianaLap first, then other profiles when matching real execution is available.
2. E2 Queueing validation with the current analytical scenarios.
3. E3 End-to-end validation on the real reference pipeline for a small factorial design.
4. E5 Sensitivity analysis with three repeats.
5. E6 Simulator throughput versus real pipeline cost.
6. One ablation: full simulator versus interpolation-only.

This gives the paper a strong evaluation arc: component correctness, end-to-end accuracy, scalability, and practical benefit.

## Expected Tables and Figures

| Artifact | Source | Paper Claim |
|---|---|---|
| Table 1: hardware and dataset summary | `machine_dataset_summary.csv` and `machine_info.json` | Evaluation spans heterogeneous continuum hardware. |
| Table 2: service-time prediction error | `benchmark_single_requirement.py` / `sweep_error_metrics.csv` | Simulator operation models are accurate enough for design exploration. |
| Table 3: queueing validation | `validate_queueing.py` | Queueing behavior is credible under controlled workloads. |
| Figure 1: predicted versus real service time | `benchmark_single_requirement.py` sweep summaries | Timing predictions track measurements. |
| Figure 2: real versus simulated pipeline breakdown | `real_pipeline_reference` comparison | End-to-end behavior matches measured stages. |
| Figure 3: worker scaling and saturation | sensitivity results | Simulator reveals scalability limits. |
| Figure 4: heterogeneous placement heatmap | placement sweep | Simulator supports continuum deployment decisions. |
| Figure 5: scenarios explored per hour | runtime comparison | Simulator saves experimental effort. |
| Table 4: decision-support top-k results | optimization test | Simulator recommendations are near optimal. |
| Table 5: ablation results | `proxy_dd` versus baselines | Model components improve accuracy and insight. |

## Concrete Next Steps

1. Extend profile support for `dantelap` in validation scripts, or exclude it from paper claims.
2. Add a two-factor sensitivity driver for workers x payload size and workers x network bandwidth.
3. Add a placement-sweep script that rewrites `machines` and `links` in `config_distributed_example.json`.
4. Add a runtime logger around real-pipeline and simulator runs to compute scenarios per hour.
5. Add a small optimizer/ranker that enumerates candidate configurations and exports top-k recommendations.
6. Update `summarize_paper_evaluation.py` to include confidence intervals, P90/P95 error, ranking metrics, and ablation tables.
