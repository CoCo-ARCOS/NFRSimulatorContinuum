#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  echo "Usage: $0 <evaluation_bundle_dir> <nfr_dag_v2_source_dir> <site_json> <workflows_json> <output_dir>" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "$0")" && pwd)
EVAL_ROOT=$(cd "$1" && pwd)
SIM_DIR=$(cd "$2" && pwd)
SITE=$(cd "$(dirname "$3")" && pwd)/$(basename "$3")
WORKFLOWS=$(cd "$(dirname "$4")" && pwd)/$(basename "$4")
OUT=$(mkdir -p "$5" && cd "$5" && pwd)

PROFILER="$EVAL_ROOT/scripts/nfr_contract_profiler.py"
if [ ! -f "$PROFILER" ]; then
  echo "Cannot find $PROFILER" >&2
  exit 1
fi

make -C "$SIM_DIR"

python3 "$ROOT/scripts/generate_robust_inputs.py" \
  --site "$SITE" \
  --workflows "$WORKFLOWS" \
  --output "$OUT/generated" \
  --replications "${REPLICATIONS:-5}" \
  --max-candidates "${MAX_CANDIDATES:-5000}"

python3 "$ROOT/scripts/run_robust_catalogs.py" \
  --manifest "$OUT/generated/manifest.json" \
  --profiler "$PROFILER" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT" \
  --jobs "${JOBS:-1}" \
  --replications "${REPLICATIONS:-5}" \
  --max-candidates "${MAX_CANDIDATES:-5000}" \
  --timeout "${TIMEOUT:-900}"

python3 "$ROOT/scripts/analyze_robust_catalogs.py" \
  --manifest "$OUT/generated/manifest.json" \
  --catalogs "$OUT/catalogs" \
  --output "$OUT/analysis"

python3 "$ROOT/scripts/plot_robust_results.py" \
  --analysis "$OUT/analysis" \
  --output "$OUT/figures"

echo "Robust evaluation complete: $OUT"
