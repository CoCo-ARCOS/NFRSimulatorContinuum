#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <nfr_dag_v2_source_dir>" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "$0")" && pwd)
SIM_DIR=$(cd "$1" && pwd)
OUT="$ROOT/smoke-output"
rm -rf "$OUT"

make -C "$SIM_DIR"
python3 "$ROOT/scripts/generate_experiments.py" \
  --site "$ROOT/configs/site.synthetic.json" \
  --workflows "$ROOT/configs/workflows.json" \
  --suite "$ROOT/configs/evaluation_suite.json" \
  --output "$OUT/generated"

python3 "$ROOT/scripts/run_experiments.py" \
  --manifest "$OUT/generated/manifest.json" \
  --profiler "$ROOT/scripts/nfr_contract_profiler.py" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT/results" \
  --smoke

set +e
python3 "$ROOT/scripts/analyze_results.py" \
  --manifest "$OUT/generated/manifest.json" \
  --results "$OUT/results" \
  --output "$OUT/analysis"
ANALYSIS_RC=$?
set -e
if [ "$ANALYSIS_RC" -ne 0 ] && [ "$ANALYSIS_RC" -ne 1 ]; then
  exit "$ANALYSIS_RC"
fi

python3 "$ROOT/scripts/holdout_validate.py" \
  --manifest "$OUT/generated/manifest.json" \
  --selected-configs "$OUT/analysis/selected_configs.json" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT/holdout" \
  --smoke

echo "Smoke test passed: $OUT"
