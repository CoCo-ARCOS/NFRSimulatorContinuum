#!/bin/bash
#SBATCH --job-name=nfr_realengines
#SBATCH --output=realengines_%j.log
#SBATCH --error=realengines_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=08:00:00
#SBATCH --exclusive          # Energy and timing measurements need an idle node.

# Real-engine experiments on one node.
#
# --exclusive matters for energy: both RAPL and SLURM's accounting measure the
# whole node, so a co-scheduled job would be charged to this measurement.
#
# Energy source, in order of preference:
#   1. powercap, if this node lets us read it (usually root-only);
#   2. SLURM accounting, if the site runs an acct_gather_energy plugin -- this
#      needs no privilege of ours, since slurmd samples the counters. Check with
#          scontrol show config | grep -i AcctGatherEnergyType
#   3. otherwise time x calibrated power, reported as "model".
#
# MIN_SECONDS matters for source 2: SLURM samples node energy every
# AcctGatherNodeFreq seconds, so each engine run is extended to span several
# intervals. Check the site's interval with
#     scontrol show config | grep -i AcctGatherNodeFreq
# and set MIN_SECONDS to a few times that value.
#
# Submit with:  sbatch slurm_realengines.sh
# Override per submission, e.g.:
#   sbatch --export=ALL,ENGINES=parsl,REPEATS=5 slurm_realengines.sh

# module load python/3.10
# module load nextflow

cd "$SLURM_SUBMIT_DIR"

export PYTHONUNBUFFERED=1
export ENGINES="${ENGINES:-parsl,dagonstar,nextflow}"
export PROFILES="${PROFILES:-balanced,security-first,resilience-first,bandwidth-first}"
export BUDGETS="${BUDGETS:-1.05,1.30,2.00}"
export PAYLOAD_BYTES="${PAYLOAD_BYTES:-16777216}"
export REPEATS="${REPEATS:-5}"
export MIN_SECONDS="${MIN_SECONDS:-120}"
export REPLICATIONS="${REPLICATIONS:-3}"

# Modelled power fallback, used only when no counter and no accounting exist.
export SITE="${SITE:-configs/site.calibrated.measured.json}"

# Self-contained Nextflow bundle for offline nodes (see fetch_nextflow.sh).
# Falls back to whatever "nextflow" is on PATH, e.g. from "module load nextflow".
export OBJECTS="${OBJECTS:-1}"
export PAYLOAD_KIND="${PAYLOAD_KIND:-synthetic}"
export PAYLOAD_RATIO="${PAYLOAD_RATIO:-3.0}"
export PAYLOAD_SOURCE="${PAYLOAD_SOURCE:-}"

export NEXTFLOW="${NEXTFLOW:-}"
[ -z "$NEXTFLOW" ] && [ -x bin/nextflow-dist ] && export NEXTFLOW="$PWD/bin/nextflow-dist"
export MACHINE="${MACHINE:-}"

OUT="${OUT:-realengines-output-${SLURM_JOB_ID}}"

echo "host:     $(hostname)"
echo "engines:  $ENGINES"
echo "profiles: $PROFILES"
echo "budgets:  $BUDGETS"
echo "output:   $OUT"
echo

echo "== SLURM energy accounting configuration =="
scontrol show config 2>/dev/null | grep -iE 'acctgatherenergytype|acctgathernodefreq' || \
  echo "  (scontrol unavailable)"
echo

./run_realengines_all.sh proxy_dd "$OUT"

# Job-level energy as recorded by the accounting database, for cross-checking
# the per-run numbers against a source we did not compute ourselves.
echo
echo "== job energy from accounting =="
sacct -j "$SLURM_JOB_ID" --format=JobID,JobName%20,Elapsed,ConsumedEnergy,ConsumedEnergyRaw 2>/dev/null || \
  echo "  (sacct unavailable)"

echo "Job completed."
