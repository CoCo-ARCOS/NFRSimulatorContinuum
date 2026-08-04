#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "Usage: $0 <evaluation_bundle_dir> <nfr_dag_v2_source_dir> <site_json> <workflows_json>" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "$0")" && pwd)
EVAL_ROOT=$(cd "$1" && pwd)
SIM_DIR=$(cd "$2" && pwd)
SITE=$(cd "$(dirname "$3")" && pwd)/$(basename "$3")
WORKFLOWS=$(cd "$(dirname "$4")" && pwd)/$(basename "$4")
OUT="$ROOT/smoke-output"
rm -rf "$OUT"

PROFILER="$EVAL_ROOT/scripts/nfr_contract_profiler.py"
make -C "$SIM_DIR"

python3 "$ROOT/scripts/generate_robust_inputs.py" \
  --site "$SITE" \
  --workflows "$WORKFLOWS" \
  --output "$OUT/generated" \
  --replications 1 \
  --max-candidates 5000

python3 "$ROOT/scripts/run_robust_catalogs.py" \
  --manifest "$OUT/generated/manifest.json" \
  --profiler "$PROFILER" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT" \
  --jobs 1 \
  --replications 1 \
  --max-candidates 5000 \
  --smoke

python3 "$ROOT/scripts/analyze_robust_catalogs.py" \
  --manifest "$OUT/generated/manifest.json" \
  --catalogs "$OUT/catalogs" \
  --output "$OUT/analysis"

python3 "$ROOT/scripts/plot_robust_results.py" \
  --analysis "$OUT/analysis" \
  --output "$OUT/figures"

echo "Robust smoke test complete: $OUT"
