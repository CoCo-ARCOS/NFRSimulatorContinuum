#!/bin/bash
#SBATCH --job-name=nfr_re_array
#SBATCH --output=logs/realengines_%A_%a.log
#SBATCH --error=logs/realengines_%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --array=0-3

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

# module load python/3.10
# module load nextflow

cd "$SLURM_SUBMIT_DIR"
mkdir -p logs

PROFILE_LIST=(balanced security-first resilience-first bandwidth-first)
PROFILE=${PROFILE_LIST[$SLURM_ARRAY_TASK_ID]}

if [ -z "$PROFILE" ]; then
  echo "array index $SLURM_ARRAY_TASK_ID has no profile; check --array range" >&2
  exit 2
fi

export PYTHONUNBUFFERED=1
OUT="${OUT_BASE:-realengines-output-${SLURM_ARRAY_JOB_ID}}/${PROFILE}"
mkdir -p "$OUT"

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

export OBJECTS="${OBJECTS:-1}"
export PAYLOAD_KIND="${PAYLOAD_KIND:-synthetic}"
export PAYLOAD_RATIO="${PAYLOAD_RATIO:-3.0}"
export PAYLOAD_SOURCE="${PAYLOAD_SOURCE:-}"

export NEXTFLOW="${NEXTFLOW:-}"
[ -z "$NEXTFLOW" ] && [ -x bin/nextflow-dist ] && export NEXTFLOW="$PWD/bin/nextflow-dist"

EXTRA=()
[ -n "${SITE:-}" ] && EXTRA+=(--site "$SITE")
[ -n "${MACHINE:-}" ] && EXTRA+=(--machine "$MACHINE")
[ -n "${NEXTFLOW:-}" ] && EXTRA+=(--nextflow "$NEXTFLOW")

python3 realengines/run_real_experiments.py \
  --request realengines/demo_request.json \
  --simulator proxy_dd/nfr_dag_sim \
  --simulator-dir proxy_dd \
  --output "$OUT" \
  --engines "${ENGINES:-parsl,dagonstar,nextflow}" \
  --profiles "$PROFILE" \
  --budgets "${BUDGETS:-1.05,1.15,1.30,1.60,2.00}" \
  --payload-bytes "${PAYLOAD_BYTES:-16777216}" \
  --repeats "${REPEATS:-5}" \
  --min-seconds "${MIN_SECONDS:-120}" \
  --replications "${REPLICATIONS:-3}" \
  "${EXTRA[@]}"

echo
sacct -j "$SLURM_JOB_ID" --format=JobID,Elapsed,ConsumedEnergy,ConsumedEnergyRaw 2>/dev/null || true

echo "Task $SLURM_ARRAY_TASK_ID ($PROFILE) completed."
