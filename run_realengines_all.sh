#!/usr/bin/env bash
set -euo pipefail

# Real-engine experiments: resolve a realization plan per (contract, budget)
# and execute it under every available workflow engine.
#
# Usage: run_realengines_all.sh <nfr_dag_v2_source_dir> [output_dir]
#
# Environment:
#   ENGINES        engines to run          (default: parsl,dagonstar,nextflow)
#   PROFILES       contract profiles       (default: all four)
#   BUDGETS        budget multipliers      (default: 1.05,1.30,2.00)
#   PAYLOAD_BYTES  payload per pass        (default: 16 MiB)
#   PAYLOAD_KIND   synthetic|random|file   (default: synthetic, compressible)
#   PAYLOAD_RATIO  target compression ratio (default: 3.0)
#   PAYLOAD_SOURCE file or directory of real files, for PAYLOAD_KIND=file
#   PAYLOAD_SEED   payload reproducibility seed (default: 0)
#   OBJECTS        payloads processed per pass (default: 1)
#   REPEATS        passes per run          (default: 3, first discarded)
#   MIN_SECONDS    keep repeating until a run lasts this long (default: 0)
#   REPLICATIONS   simulator replications  (default: 3)
#   SITE, MACHINE  modelled power fallback when no energy counter is readable
#   REQUEST        profiler request        (default: realistic_request.json)
#   VENV           virtualenv location     (default: ./.venv)
#   NEXTFLOW       nextflow executable     (e.g. bin/nextflow-dist, see fetch_nextflow.sh)

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <nfr_dag_v2_source_dir> [output_dir]" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "$0")" && pwd)
SIM_DIR=$(cd "$1" && pwd)
OUT=${2:-"$ROOT/realengines-output"}
mkdir -p "$OUT"

# shellcheck disable=SC1091
source "$ROOT/venv_activate.sh"
echo "python: $(command -v python3)"

make -C "$SIM_DIR"

echo "== energy measurement capability =="
python3 "$ROOT/realengines/measure.py" || true
echo

EXTRA=()
[ -n "${SITE:-}" ] && EXTRA+=(--site "$SITE")
[ -n "${MACHINE:-}" ] && EXTRA+=(--machine "$MACHINE")
# Self-contained Nextflow bundle, for clusters without outbound network access.
[ -n "${NEXTFLOW:-}" ] && EXTRA+=(--nextflow "$NEXTFLOW")
# Corpus of real files (a directory avoids the tiling that inflates ratios).
[ -n "${PAYLOAD_SOURCE:-}" ] && EXTRA+=(--payload-source "$PAYLOAD_SOURCE")
[ -n "${PAYLOAD_SEED:-}" ] && EXTRA+=(--payload-seed "$PAYLOAD_SEED")
[ -n "${OBJECTS:-}" ] && EXTRA+=(--objects "$OBJECTS")

python3 "$ROOT/realengines/run_real_experiments.py" \
  --request "${REQUEST:-$ROOT/realengines/realistic_request.json}" \
  --simulator "$SIM_DIR/nfr_dag_sim" \
  --simulator-dir "$SIM_DIR" \
  --output "$OUT" \
  --engines "${ENGINES:-parsl,dagonstar,nextflow}" \
  --profiles "${PROFILES:-balanced,security-first,resilience-first,bandwidth-first}" \
  --budgets "${BUDGETS:-1.05,1.30,2.00}" \
  --payload-bytes "${PAYLOAD_BYTES:-16777216}" \
  --payload-kind "${PAYLOAD_KIND:-synthetic}" \
  --payload-ratio "${PAYLOAD_RATIO:-3.0}" \
  --repeats "${REPEATS:-3}" \
  --min-seconds "${MIN_SECONDS:-0}" \
  --replications "${REPLICATIONS:-3}" \
  "${EXTRA[@]}"

python3 "$ROOT/scripts/analyze_real_engines.py" \
  --runs "$OUT/real_engine_runs.json" \
  --output "$OUT/analysis"

echo "Real-engine evaluation complete: $OUT"
