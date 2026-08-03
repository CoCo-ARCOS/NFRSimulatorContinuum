#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <nfr_dag_v2_source_dir> [site_json] [output_dir]" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "$0")" && pwd)
SIM_DIR=$(cd "$1" && pwd)
SITE=${2:-"$ROOT/configs/site.synthetic.json"}
OUT=${3:-"$ROOT/evaluation-output"}

make -C "$SIM_DIR"
mkdir -p "$OUT"

python3 "$ROOT/scripts/generate_experiments.py" \
  --site "$SITE" \
  --workflows "$ROOT/configs/workflows.json" \
  --suite "$ROOT/configs/evaluation_suite.json" \
  --output "$OUT/generated"

python3 "$ROOT/scripts/run_experiments.py" \
  --manifest "$OUT/generated/manifest.json" \
  --profiler "$ROOT/scripts/nfr_contract_profiler.py" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT/results" \
  --jobs "${JOBS:-1}"

python3 "$ROOT/scripts/analyze_results.py" \
  --manifest "$OUT/generated/manifest.json" \
  --results "$OUT/results" \
  --output "$OUT/analysis"

python3 "$ROOT/scripts/holdout_validate.py" \
  --manifest "$OUT/generated/manifest.json" \
  --selected-configs "$OUT/analysis/selected_configs.json" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT/holdout"

echo "Evaluation complete: $OUT"
