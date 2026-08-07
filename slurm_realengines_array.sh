#!/bin/bash
#SBATCH --job-name=nfr_re_array
#SBATCH --output=logs/realengines_%A_%a.log
#SBATCH --error=logs/realengines_%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --array=0-3
#SBATCH --partition=large

# Real-engine experiments as a SLURM array: one contract profile per task, each
# on its own exclusive node.
#
# Exclusivity is not a performance preference here. RAPL counts energy for the
# whole package, so a co-scheduled job would be charged to this measurement;
# one profile per node also keeps the timings independent.
#
#   sbatch slurm_realengines_array.sh
#   sbatch --array=0-3 --export=ALL,REPEATS=7 slurm_realengines_array.sh
#
# Aggregate once every task has finished:
#   sbatch --dependency=afterok:<jobid> slurm_realengines_merge.sh <jobid>

module load python/3.10

ENGINE_EXTRAS=1 ./setup_venv.sh
# module load nextflow

cd "$SLURM_SUBMIT_DIR"
mkdir -p logs

export PATH=/home/dantsanc/jdk-21.0.6+7/bin:$PATH

# Must cover the --array range above: index N runs PROFILE_LIST[N].
PROFILE_LIST=(balanced security-first resilience-first bandwidth-first)
PROFILE=${PROFILE_LIST[$SLURM_ARRAY_TASK_ID]}

if [ -z "$PROFILE" ]; then
  echo "array index $SLURM_ARRAY_TASK_ID has no profile; check --array range" >&2
  exit 2
fi

export PYTHONUNBUFFERED=1
OUT="${OUT_BASE:-realengines-output-${SLURM_ARRAY_JOB_ID}}/${PROFILE}"
mkdir -p "$OUT"

# One catalog cache for the whole array: without it every task re-profiles the
# same scenarios, which for four tasks is four times the simulation cost.
export TUNER_CACHE="${TUNER_CACHE:-${OUT_BASE:-realengines-output-${SLURM_ARRAY_JOB_ID}}/tuner-cache}"
mkdir -p "$TUNER_CACHE"

# shellcheck disable=SC1091
source ./venv_activate.sh

echo "host:    $(hostname)"
echo "task:    $SLURM_ARRAY_TASK_ID"
echo "profile: $PROFILE"
echo "python:  $(command -v python3)"
echo "output:  $OUT"
echo

scontrol show config 2>/dev/null | grep -iE 'acctgatherenergytype|acctgathernodefreq' || true
python3 realengines/measure.py || true
echo

# Everything below is passed through run_realengines_all.sh, which is the one
# place the driver is invoked. Setting these here and calling the driver
# directly is how this script previously drifted: OBJECTS and TUNER_CACHE were
# exported but never forwarded, and the request differed from the single-node
# path.
export PROFILES="$PROFILE"
export OBJECTS="${OBJECTS:-4}"
export PAYLOAD_BYTES="${PAYLOAD_BYTES:-67108864}"
export PAYLOAD_KIND="${PAYLOAD_KIND:-synthetic}"
export PAYLOAD_RATIO="${PAYLOAD_RATIO:-3.0}"
export PAYLOAD_SOURCE="${PAYLOAD_SOURCE:-}"
export BUDGETS="${BUDGETS:-1.05,1.30,2.00}"
export REPEATS="${REPEATS:-3}"
# RAPL is read per region, so runs need not be stretched for coarse accounting.
export MIN_SECONDS="${MIN_SECONDS:-0}"
export REPLICATIONS="${REPLICATIONS:-3}"
export ENGINES="${ENGINES:-parsl,dagonstar,nextflow}"

export NEXTFLOW="${NEXTFLOW:-}"
[ -z "$NEXTFLOW" ] && [ -x bin/nextflow-dist ] && export NEXTFLOW="$PWD/bin/nextflow-dist"

./run_realengines_all.sh proxy_dd "$OUT"

echo
sacct -j "$SLURM_JOB_ID" --format=JobID,Elapsed,ConsumedEnergy,ConsumedEnergyRaw 2>/dev/null || true

echo "Task $SLURM_ARRAY_TASK_ID ($PROFILE) completed."
