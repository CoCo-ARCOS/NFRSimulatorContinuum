#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$ROOT_DIR/paper_evaluation"
CONFIG_PATH="$ROOT_DIR/config_distributed_example.json"
INTERPOLATION_MODE="leave-one-out"
QUEUE_SAMPLES=50000
QUEUE_WARMUP=5000
SENSITIVITY_REPEATS=1
RUN_SENSITIVITY=1
RUN_QUEUEING=1
RUN_INTERPOLATION=1
PROFILES=("c3" "dianalap" "toge")
QUEUE_USE_SIMULATOR=0
SIMULATOR_CONTAINER=""
SENSITIVITY_FACTORS=(
  payload_size
  devices
  workers
  network_bandwidth
  storage_bandwidth
  hardware_profile
  nfr_pipeline
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --output-dir DIR              Output root directory. Default: proxy_dd/paper_evaluation
  --config FILE                 Simulator config for sensitivity analysis.
  --interpolation-mode MODE     leave-one-out | fit. Default: leave-one-out
  --queue-samples N             Queueing validation sample count. Default: 50000
  --queue-warmup N              Queueing warmup count. Default: 5000
  --sensitivity-repeats N       Repeats per sensitivity point. Default: 1
  --profiles "p1 p2 ..."        Hardware profiles to evaluate. Default: "c3 dianalap toge"
  --sensitivity-factors "..."   Factors to pass to sensitivity_analysis.py.
  --skip-interpolation          Skip interpolation validation.
  --skip-queueing               Skip queueing validation.
  --skip-sensitivity            Skip sensitivity analysis.
  --use-queue-simulator NAME    Also compare MM1 against the queue-estimator container.
  -h, --help                    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --interpolation-mode)
      INTERPOLATION_MODE="$2"
      shift 2
      ;;
    --queue-samples)
      QUEUE_SAMPLES="$2"
      shift 2
      ;;
    --queue-warmup)
      QUEUE_WARMUP="$2"
      shift 2
      ;;
    --sensitivity-repeats)
      SENSITIVITY_REPEATS="$2"
      shift 2
      ;;
    --profiles)
      read -r -a PROFILES <<< "$2"
      shift 2
      ;;
    --sensitivity-factors)
      read -r -a SENSITIVITY_FACTORS <<< "$2"
      shift 2
      ;;
    --skip-interpolation)
      RUN_INTERPOLATION=0
      shift
      ;;
    --skip-queueing)
      RUN_QUEUEING=0
      shift
      ;;
    --skip-sensitivity)
      RUN_SENSITIVITY=0
      shift
      ;;
    --use-queue-simulator)
      QUEUE_USE_SIMULATOR=1
      SIMULATOR_CONTAINER="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/logs"

export MPLCONFIGDIR="${MPLCONFIGDIR:-$OUTPUT_DIR/.mplconfig}"
mkdir -p "$MPLCONFIGDIR"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run_cmd() {
  local log_name="$1"
  shift
  log "$*"
  "$@" 2>&1 | tee "$OUTPUT_DIR/logs/${log_name}.log"
}

cd "$ROOT_DIR"

run_cmd build make
run_cmd organize_machine_results python3 organize_machine_results.py

if [[ "$RUN_INTERPOLATION" -eq 1 ]]; then
  for profile in "${PROFILES[@]}"; do
    profile_out="$OUTPUT_DIR/interpolation/$profile"
    mkdir -p "$profile_out"
    run_cmd "interpolation_${profile}" \
      python3 validate_interpolation.py \
      --hardware-profile "$profile" \
      --mode "$INTERPOLATION_MODE" \
      --out "$profile_out/detailed.csv" \
      --summary-out "$profile_out/summary.csv" \
      --plots-dir "$profile_out/plots"
  done
fi

if [[ "$RUN_QUEUEING" -eq 1 ]]; then
  for profile in "${PROFILES[@]}"; do
    profile_out="$OUTPUT_DIR/queueing/$profile"
    mkdir -p "$profile_out"
    queue_cmd=(
      python3 validate_queueing.py
      --hardware-profile "$profile"
      --samples "$QUEUE_SAMPLES"
      --warmup "$QUEUE_WARMUP"
      --summary-out "$profile_out/summary.csv"
      --overall-out "$profile_out/overall.csv"
      --tables-out "$profile_out/tables.csv"
      --plots-dir "$profile_out/plots"
    )
    if [[ "$QUEUE_USE_SIMULATOR" -eq 1 ]]; then
      queue_cmd+=(--use-simulator --simulator-container "$SIMULATOR_CONTAINER")
    fi
    run_cmd "queueing_${profile}" "${queue_cmd[@]}"
  done
fi

if [[ "$RUN_SENSITIVITY" -eq 1 ]]; then
  run_cmd sensitivity \
    python3 sensitivity_analysis.py \
    --config "$CONFIG_PATH" \
    --output "$OUTPUT_DIR/sensitivity" \
    --repeats "$SENSITIVITY_REPEATS" \
    --factors "${SENSITIVITY_FACTORS[@]}"
fi

run_cmd summarize \
  python3 summarize_paper_evaluation.py \
  --input-root "$OUTPUT_DIR" \
  --output-md "$OUTPUT_DIR/paper_evaluation_summary.md"

log "Paper evaluation complete."
log "Summary: $OUTPUT_DIR/paper_evaluation_summary.md"
