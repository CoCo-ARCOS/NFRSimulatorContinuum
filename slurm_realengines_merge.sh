#!/bin/bash
#SBATCH --job-name=nfr_re_merge
#SBATCH --output=logs/realengines_merge_%j.log
#SBATCH --error=logs/realengines_merge_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:30:00

# Aggregate the per-profile results of an array run into the paper tables.
#
#   sbatch --dependency=afterok:<array_jobid> slurm_realengines_merge.sh <array_jobid>
#
# Analysis only, so no exclusive node is needed.

# module load python/3.10

cd "$SLURM_SUBMIT_DIR"
mkdir -p logs

# shellcheck disable=SC1091
source ./venv_activate.sh

ARRAY_JOB_ID=${1:?usage: slurm_realengines_merge.sh <array_jobid>}
OUT="${OUT_BASE:-realengines-output-${ARRAY_JOB_ID}}"

if [ ! -d "$OUT" ]; then
  echo "no results directory at $OUT" >&2
  exit 2
fi

mapfile -t RUNS < <(find "$OUT" -name real_engine_runs.json | sort)
if [ "${#RUNS[@]}" -eq 0 ]; then
  echo "no real_engine_runs.json under $OUT" >&2
  exit 2
fi

echo "aggregating ${#RUNS[@]} task result files from $OUT"

python3 scripts/analyze_real_engines.py \
  --runs "${RUNS[@]}" \
  --output "$OUT/analysis"

echo "Merge completed: $OUT/analysis"
