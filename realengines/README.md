# Real workflow-engine experiments

Executing contract-driven NFR realizations on real workflow engines — DagOnStar,
Parsl, and Nextflow — to show that the approach is engine-agnostic and to observe
its behaviour outside the simulator.

The offline evaluation (`scripts/analyze_robust_catalogs.py`) answers *which*
realization should run. These experiments answer *does it actually run, the same
way, everywhere*.

---

## 1. What the experiments demonstrate

| Claim | Question | Output |
|---|---|---|
| **E1 Portability** | Do different engines enforce the same plan identically? | `real_engine_portability.csv` |
| **E2 Fidelity** | How does predicted cost compare to measured cost? | `real_engine_fidelity.csv` |
| **E3 Adaptivity** | Does the budget change which realization actually runs? | `real_engine_runs.csv` |

The selection code is shared, not reimplemented: `nfr_tuner.py` imports
`select_contract_aware` and `mandatory_ok` from `scripts/analyze_robust_catalogs.py`,
so an engine executes the realization chosen by exactly the same code as the
offline evaluation.

## 2. How the pieces fit

```
demo_request.json ──▶ nfr_tuner.py ──▶ plan.json          (schema: nfr-realization-plan/1)
                       │  profiles the catalog (cached),
                       │  admits under budgets, selects
                       ▼
        ┌──────────────┴───────────────┐
        │                              │
  Python-native engines          process-based engines
  parsl_workflow.py              nextflow_workflow.py
  dagonstar_workflow.py            └─▶ nextflow/main.nf ──▶ nfr_apply.py
        │                              │
        └──▶ workload.py ◀─────────────┘   (same stages, same mechanisms)
                  │
                  ▼
            run_result.json ──▶ scripts/analyze_real_engines.py ──▶ 3 CSVs
```

`workload.py` holds the stage graph so the engines differ **only** in scheduling.
Any difference in the emitted mechanism trace is therefore a real integration
difference, not an artefact of three separate implementations.

| File | Role |
|---|---|
| `nfr_plan.py` | Engine-agnostic plan format; `apply_output` / `apply_input` |
| `nfr_apply.py` | File-interface enforcement for non-Python engines; appends `enforcement_log.jsonl` |
| `nfr_tuner.py` | Profiler → plan bridge (catalog caching, admission, selection) |
| `measure.py` | Wall time + energy, with source provenance |
| `workload.py` | The stage graph every engine executes |
| `engine_cli.py` | Shared CLI and `run_result.json` schema |
| `run_real_experiments.py` | Sweep driver over engines × profiles × budgets |

---

## 3. Prerequisites

### 3.1 Virtualenv (required)

Distributions following PEP 668 refuse `pip install` into the system
interpreter, so everything runs from a project virtualenv:

```bash
./setup_venv.sh                  # creates .venv, installs requirements.txt
ENGINE_EXTRAS=1 ./setup_venv.sh  # also installs parsl and dagon
```

Expected tail of the output:

```
virtualenv ready: /path/to/NFRSimulatorContinuum/.venv
  ok       pycryptodome
  ok       zfec
  ok       lz4
  ok       zstandard
  ok       pandas
  ok       matplotlib
  ok       parsl (optional)
  missing  dagon (optional)
```

`missing dagon` is fine — DagOnStar is not on PyPI under that name, and the
adapter falls back to inline stages, reporting that it did. `pycryptodome` and
`zfec` are **not** optional: AES and Reed–Solomon fail without them.

The runner scripts source `venv_activate.sh`, which creates the venv on first
use, so `./setup_venv.sh` is only needed if you want the engine extras.

### 3.2 Simulator

The tuner profiles a candidate catalog before selecting, so the simulator must
build:

```bash
make -C proxy_dd            # produces proxy_dd/nfr_dag_sim
```

### 3.3 Engines (all optional)

| Engine | Install | If absent |
|---|---|---|
| Parsl | `pip install parsl` (via `ENGINE_EXTRAS=1`) | run recorded as `skipped` |
| DagOnStar | site-specific | falls back to inline stages |
| Nextflow | `module load nextflow`, or from nextflow.io | run recorded as `skipped` |

A missing engine never fails the sweep — it is recorded and the rest proceeds.

---

## 4. Running

### 4.1 Smoke test (~2 minutes)

Confirm the whole chain works before committing to a real run:

```bash
source venv_activate.sh
PROFILES=balanced BUDGETS=1.30 PAYLOAD_BYTES=1048576 REPEATS=2 REPLICATIONS=2 \
  ./run_realengines_all.sh proxy_dd /tmp/re-smoke
```

### 4.2 Resolve a single plan (no engines involved)

Useful to inspect what the tuner selects:

```bash
python3 realengines/nfr_tuner.py \
  --request realengines/demo_request.json \
  --simulator proxy_dd/nfr_dag_sim --simulator-dir proxy_dd \
  --output /tmp/tuner --replications 2 --max-candidates 40
```

Expected:

```
selection: contract-aware-edge -> candidate 35 (plan edge-cloud-cloud)
  label:     raw[LZ4+AES+RS(k=4,m=2)+SHA256]; derived[LZ4+SHA256]
  predicted: 476.77 J, 1.7526 s, coverage 3.5
  budgets:   616.56 J, 2.2755 s
  raw: compress=LZ4, encrypt=AES, erasure=RS, hash=SHA256
  derived: compress=LZ4, hash=SHA256
```

### 4.3 Run one engine against one plan

```bash
python3 realengines/parsl_workflow.py \
  --plan /tmp/tuner/plan.json --output /tmp/run-parsl \
  --payload-bytes 16777216 --repeats 5 \
  --site configs/site.calibrated.measured.json --machine Edge_p
```

Expected:

```
[parsl] 0.1987 s, 12.915 J (model)
[parsl] mechanisms: raw:compress=LZ4|raw:encrypt=AES|raw:erasure=RS|raw:hash=SHA256|derived:compress=LZ4|derived:hash=SHA256
[parsl] wrote /tmp/run-parsl/run_result.json
```

The mechanism trace must match the plan's label. That equality is the E1 check.

### 4.4 Full local sweep

```bash
./run_realengines_all.sh proxy_dd realengines-output
```

Environment overrides:

| Variable | Default | Meaning |
|---|---|---|
| `ENGINES` | `parsl,dagonstar,nextflow` | engines to run |
| `PROFILES` | all four | contract profiles |
| `BUDGETS` | `1.05,1.30,2.00` | budget multipliers (energy and deadline) |
| `PAYLOAD_BYTES` | `16777216` (16 MiB) | payload per pass |
| `REPEATS` | `3` | passes per run; the first is discarded as warm-up |
| `MIN_SECONDS` | `0` | keep repeating until a run lasts this long |
| `REPLICATIONS` | `3` | simulator replications when profiling |
| `SITE`, `MACHINE` | — | modelled power fallback |
| `VENV` | `./.venv` | virtualenv location |

### 4.5 SLURM — single node

```bash
sbatch slurm_realengines.sh
sbatch --export=ALL,ENGINES=parsl,REPEATS=7 slurm_realengines.sh
```

`--exclusive` is set and **must stay**: both RAPL and SLURM accounting measure
the whole node, so a co-scheduled job would be charged to your measurement.

### 4.6 SLURM — array (one profile per node)

```bash
sbatch slurm_realengines_array.sh                      # note the returned <jobid>
sbatch --dependency=afterok:<jobid> slurm_realengines_merge.sh <jobid>
```

The array writes `realengines-output-<jobid>/<profile>/`, and the merge job
aggregates every `real_engine_runs.json` into one analysis directory.

---

## 5. Energy measurement

Three sources are tried in order; every number carries the source that produced
it, so a modelled value is never mistaken for a measured one.

| Source | Requires | Granularity |
|---|---|---|
| `rapl` | readable `/sys/class/powercap` (usually root-only) | per stage |
| `slurm` | site runs an `acct_gather_energy` plugin | whole run, node-level |
| `model` | `--site`/`--machine` or `--model-power-w` | derived from time |

**Check what your cluster offers — do this first:**

```bash
scontrol show config | grep -iE 'AcctGatherEnergyType|AcctGatherNodeFreq'
python3 realengines/measure.py
```

- `AcctGatherEnergyType=acct_gather_energy/rapl` (or `ipmi`, `xcc`) → you get
  **measured** energy with no special privilege, because slurmd samples the
  counters as root. Set `MIN_SECONDS` to roughly **4× `AcctGatherNodeFreq`**
  (e.g. `AcctGatherNodeFreq=30` → `MIN_SECONDS=120`) so each run spans several
  samples.
- `AcctGatherEnergyType=acct_gather_energy/none` → no measured energy is
  available. Set `SITE`/`MACHINE` and report engine-side energy as *derived from
  calibrated power, with time as the measured quantity*.

`measure.py` output when nothing is available:

```json
{
  "source": "model",
  "readable": false,
  "hint": "powercap is not readable and SLURM energy accounting is unavailable; ...",
  "in_slurm_allocation": false
}
```

Runs shorter than two sampling intervals are flagged `energy_coarse=True`, and
the analysis warns before you quote them.

---

## 6. Expected results

### 6.1 Console summary

```
runs: 8 ok / 12 total across engines: dagonstar, parsl
portability: 4/4 configurations where all engines agree
fidelity: 8 comparable runs, 0 with measured energy (sources: model)
  note: no run had a readable counter or SLURM energy accounting; ...
wrote analysis to realengines-output/analysis
```

`8 ok / 12` with three engines requested and Nextflow not installed is correct:
4 configurations × 3 engines = 12, of which the 4 Nextflow runs are `skipped`.

The energy source reads `model` only when `SITE`/`MACHINE` (or
`--model-power-w`) is set. Without them there is nothing to fall back to and the
source reads `unavailable`, with the energy columns left empty — timing results
are unaffected.

### 6.2 Output tree

```
realengines-output/
├── requests/<profile>.json            request per contract profile
├── tuner/catalogs/<fingerprint>/      cached profiler catalogs (reused)
├── plans/<profile>__b<budget>.json    the realization plan that was executed
├── runs/<profile>__b<budget>/<engine>/
│   ├── run_result.json                measurements, trace, predicted vs measured
│   └── engine.log
├── real_engine_runs.json              raw sweep record
└── analysis/
    ├── real_engine_runs.csv           every run, flattened
    ├── real_engine_portability.csv    E1
    └── real_engine_fidelity.csv       E2
```

### 6.3 E1 — portability

Every row should read `traces_agree=True` and `distinct_traces=1`:

| profile | budget | engines | distinct_traces | traces_agree | all_verified |
|---|---|---|---|---|---|
| balanced | 1.05 | dagonstar,parsl | 1 | True | True |
| balanced | 1.30 | dagonstar,parsl | 1 | True | True |
| security-first | 1.05 | dagonstar,parsl | 1 | True | True |
| security-first | 1.30 | dagonstar,parsl | 1 | True | True |

A `distinct_traces > 1` row is a genuine finding: two engines enforced the same
plan differently, and the `mechanism_trace` column shows both.

### 6.4 E2 — fidelity

Actual output from a 1 MiB smoke run:

```
       profile  budget    engine  predicted_makespan_s  measured_seconds  measured_nfr_seconds  time_ratio
      balanced    1.05     parsl                1.7518            0.1008                0.0179      0.0575
      balanced    1.05 dagonstar                1.7518            0.0920                0.0115      0.0525
security-first    1.05     parsl                1.7518            0.0984                0.0165      0.0562
security-first    1.05 dagonstar                1.7518            0.0909                0.0117      0.0519
```

**Read `time_ratio` carefully.** A ratio near 0.05 is *not* a fidelity failure —
it reflects two deliberate differences in scope:

1. `predicted_makespan_s` covers the whole simulated DAG for the request's trace
   (all objects, across placed machines); one engine pass processes a single
   `--payload-bytes` payload on one node.
2. The simulator includes network transfer and placement energy, which a
   single-node run never incurs.

To compare like with like, either align `--payload-bytes` with the per-object
size in the request's `workflow.trace`, or compare `measured_nfr_seconds`
(mechanism cost only) and the *relative ordering* across profiles rather than
absolute ratios. The consistent per-engine offset visible above (Parsl ~0.10 s
vs DagOnStar ~0.09 s on identical work) is the engine's own scheduling overhead
and is itself a reportable result.

### 6.5 E3 — adaptivity

Different profiles select different mechanisms. From the same run:

```
balanced__b1.05:       raw[LZ4+AES+RS(k=4,m=2)+HMAC_SHA256]; derived[LZ4+HMAC_SHA256]
security-first__b1.05: raw[LZ4+CHACHA20+RS(k=4,m=2)+SHA256]; derived[ZSTD+HMAC_SHA256]
```

Raising the budget should hold or increase `coverage_weighted` and never
decrease it. A configuration where no realization fits is recorded as
`status=rejected` with the reason — that is a result, not an error.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `PyCryptodome is required` | venv not active, or `setup_venv.sh` not run |
| `externally-managed-environment` | Never `pip install` system-wide; use `./setup_venv.sh` |
| `nextflow: skipped (engine not installed)` | Expected without Nextflow; `module load nextflow` |
| `[dagonstar] package not installed; running stages inline` | Expected; mechanisms and measurements are unchanged |
| `no realization fits the budget` | Correct behaviour at tight budgets; the row is `rejected` |
| `profiler failed (exit N)` | See `<output>/tuner/catalogs/<fp>/profiler.log`; usually the simulator was not built |
| `energy_source=model` everywhere | No readable counter and no SLURM accounting — see §5 |
| `energy_source=unavailable`, empty energy columns | No fallback configured; set `SITE`/`MACHINE` or `--model-power-w` |
| `energy_coarse=True` warnings | Raise `MIN_SECONDS` to ~4× `AcctGatherNodeFreq` |
| Catalog seems stale after editing a request | Catalogs are cached by request hash; pass `--force` |

---

## 8. Reproducing the paper numbers

```bash
./setup_venv.sh                       # ENGINE_EXTRAS=1 if installing Parsl here
make -C proxy_dd
sbatch slurm_realengines_array.sh     # note <jobid>
sbatch --dependency=afterok:<jobid> slurm_realengines_merge.sh <jobid>
```

Then `realengines-output-<jobid>/analysis/` holds the three CSVs behind E1–E3.
Record alongside them: the `energy_source` in use, the `AcctGatherNodeFreq` of
the site, and whether runs were exclusive — all three determine how the energy
column may be described in the paper.
