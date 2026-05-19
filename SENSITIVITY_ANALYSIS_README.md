# Sensitivity Analysis Benchmarking Guide

## Overview

The `sensitivity_analysis.py` script performs one-factor-at-a-time (OFAT) sensitivity analysis on the SimulatorContinuum proxy_dd simulator. It systematically varies key system parameters while holding others constant to understand simulator behavior and monotonicity.

## Factors Analyzed

### 1. **Payload Size** (5 values)
- 1 MB, 5 MB, 10 MB, 25 MB, 50 MB
- Tests: data processing time increases linearly with payload size
- Expected behavior: Larger payloads → longer transfer and processing times

### 2. **Device Count** (5 values)  
- 20, 50, 100, 200, 400 devices
- Tests: more objects increase total workload and queueing
- Expected behavior: More devices → longer makespan

### 3. **Worker Count** (5 values)
- 1, 2, 4, 8, 16 workers
- Tests: parallelization reduces wall-clock time until overhead dominates
- Expected behavior: Linear speedup up to resource saturation

### 4. **Network Bandwidth** (5 values)
- 10, 25, 50, 100, 200 MB/s
- Tests: network bottleneck impact (between machines)
- Expected behavior: Higher bandwidth → lower transfer latency

### 5. **Storage Bandwidth** (5 values)
- 10, 50, 100, 250, 500 MB/s
- Tests: filesystem I/O bottleneck (input/output requirements)
- Expected behavior: Higher bandwidth → lower I/O latency

### 6. **Hardware Profile** (4 values)
- Fast (0.5×), Baseline (1.0×), Slow (2.0×), Very Slow (4.0×)
- Tests: application time multipliers (slower hardware)
- Expected behavior: Slower profiles → linearly longer execution

### 7. **NFR Pipeline Complexity** (4 values)
- Minimal (hash only), Baseline (stage defaults), Heavy (compression+cipher+hash), Maximal (all algorithms)
- Tests: cryptographic and compression overhead
- Expected behavior: More operations → higher total overhead, but monotonic scaling

## Running the Benchmarks

### Full Analysis (all factors, single run each)
```bash
cd proxy_dd
python3 sensitivity_analysis.py \
  --config config_distributed_example.json \
  --output sensitivity_results_full \
  --repeats 1
```

### Specific Factors with Repeats
```bash
python3 sensitivity_analysis.py \
  --factors workers network_bandwidth storage_bandwidth \
  --output results_network_study \
  --repeats 3
```

### Quick Test (single factor)
```bash
python3 sensitivity_analysis.py \
  --factors workers \
  --repeats 2 \
  --output quick_test
```

## Output Files

For each factor analysis:
- `sensitivity_results_full/sensitivity_summary.csv` — aggregated metrics across all scenarios
- `sensitivity_results_full/<factor>/<value>/run_<n>/` — individual run data
  - `config.json` — configuration for this run
  - `results/stage_totals_by_workers.csv` — simulator output metrics
  - `simulator_stdout.txt`, `simulator_stderr.txt` — raw simulator output

## Generated Plots

For each factor, three plots are generated:
1. **Total vs Factor** — pipeline total time and max stage time
2. **Breakdown** — input, application, output time components
3. **NFR Overhead** — compression, hash, crypto time trends

Example: `sensitivity_results_full/sensitivity_workers_*.png`

## Expected Monotonic Behavior

✓ **Payload Size** — increases latency (linear with size)  
✓ **Devices** — increases makespan (more objects to process)  
✓ **Workers** — decreases wall-clock time (until saturation)  
✓ **Network Bandwidth** — decreases latency (higher BW = faster transfer)  
✓ **Storage Bandwidth** — decreases I/O time (higher BW = faster I/O)  
✓ **Hardware Profile** — increases time (slower hardware takes longer)  
✓ **NFR Pipeline** — increases overhead but scales consistently  

## CSV Summary Format

```
factor,value_label,value,workers,trace_objects,repeat_count,
pipeline_total_seconds,pipeline_max_stage_seconds,
pipeline_input_seconds,pipeline_application_seconds,pipeline_output_seconds,
pipeline_compression_seconds,pipeline_hash_seconds,pipeline_crypto_seconds,
stage_count,objects
```

Example row:
```
workers,4-workers,4,4,90100.0,1,895.27,259.69,275.47,301.37,318.43,158.02,276.39,159.49,4.0,100.0
```

## Performance Notes

- **Single factor run**: ~30-60 seconds per scenario (depends on workload)
- **Full 33-scenario analysis**: ~30-60 minutes for single repeats
- **With repeats=3**: ~90-180 minutes for robust averages
- Results are written incrementally; safe to interrupt and resume

## Validating Plausibility

After running benchmarks, check:

1. **Worker scaling**: Total time should decrease roughly as `O(1/sqrt(workers))` after initial linear phase
2. **Bandwidth effects**: Doubling bandwidth should roughly halve transfer time
3. **Payload effects**: Time should scale linearly or super-linearly with payload size
4. **NFR overhead**: Should dominate for large payloads and dense pipelines
5. **Hardware**: Multipliers should apply directly to application service times

## Integration with Existing Benchmarks

The sensitivity script reuses `benchmark_workers.py` helpers:
- `run_simulation()` — execute simulator with config
- `read_stage_totals()` — parse stage metrics from results
- `write_config()` — serialize JSON configs
- Plotting utilities for consistent chart generation

## Future Extensions

- [ ] Cross-factor interaction analysis (e.g., workers × network_bandwidth)
- [ ] Sensitivity surface plots (2D factor sweeps)
- [ ] Statistical confidence intervals for repeated runs
- [ ] Automated detection of saturation points
- [ ] Performance per unit cost analysis
