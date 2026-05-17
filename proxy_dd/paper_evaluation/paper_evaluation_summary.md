# Overall Simulator Evaluation

Generated from `/home/domizzi/Documents/GitHub/SimulatorContinuum/proxy_dd/paper_evaluation`.

## Interpolation Validation

| Profile | Samples | MAE (s) | RMSE (s) | MAPE (%) | Worst operation by RMSE | Worst RMSE (s) |
|---|---:|---:|---:|---:|---|---:|
| c3 | 100 | 1.998676 | 16.286114 | 191.52 | compress | 32.541945 |
| dianalap | 100 | 2.255640 | 18.180497 | 182.76 | compress | 36.306988 |
| toge | 100 | 7.092706 | 63.945574 | 180.70 | compress | 127.848841 |

## Queueing Validation

| Profile | Mean waiting error (%) | Mean response error (%) | Worst M/G/c scenario | Worst M/G/c response error (%) |
|---|---:|---:|---|---:|
| c3 | 4.14 | 2.62 | mgc_c2_rs84_encode_rho_0.85 | 9.64 |
| dianalap | 4.13 | 2.62 | mgc_c2_rs84_encode_rho_0.85 | 9.63 |
| toge | 4.11 | 2.61 | mgc_c2_rs84_encode_rho_0.85 | 9.50 |

## Sensitivity Analysis

| Factor | Expected trend | Monotonic / plausible | Best setting | Notes |
|---|---|---|---|---|
| devices | increasing | yes | 20-devices | range 20-devices -> 400-devices |
| hardware_profile | qualitative | qualitative | dianalap | fastest=dianalap, slowest=c3 |
| network_bandwidth | decreasing | mixed | 10MBps | range 10MBps -> 200MBps |
| nfr_pipeline | increasing_ordered | yes | minimal | range minimal -> maximal |
| payload_size | increasing | yes | 1MB | range 1MB -> 1000MB |
| storage_bandwidth | decreasing | mixed | 500MBps | range 10MBps -> 500MBps |
| workers | decreasing | yes | 16-workers | best total at 16-workers (227.075s); start=3315.713s, end=227.075s |

## Interpretation

- Interpolation validation reports how closely the service-time model reproduces measured operation times for each hardware profile.
- Queueing validation reports agreement between analytical/approximate baselines and the controlled queueing implementation.
- Sensitivity analysis checks whether system behavior follows expected monotonic trends under workload, bandwidth, worker-count, hardware-profile, and NFR changes.

These tables are intended as the paper-level entry point; the detailed CSVs and plots remain in the corresponding subdirectories.
