#!/bin/bash

# Exit on error
set -e

# Activate the virtual environment
source venv/bin/activate

# Define the dimensions to test
WORKERS=(1 2 4 8 16 32)
STUDIES=(50 100 150)
REPETITIONS=10

# Define execution mode (--local for local threading, empty string for Slurm)
# Change this to EXEC_MODE="" when submitting to your Slurm cluster!
#EXEC_MODE="--local"

# Base output directory for all experiment runs
BASE_OUTPUT_DIR="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/"
mkdir -p "$BASE_OUTPUT_DIR"

echo "Starting workflow experiments..."
echo "Testing workers: ${WORKERS[*]}"
echo "Testing studies: ${STUDIES[*]}"
echo "Repetitions per config: $REPETITIONS"
echo "Execution Mode: Slurm"
echo ""

for w in "${WORKERS[@]}"; do
    for s in "${STUDIES[@]}"; do
        for r in $(seq 1 $REPETITIONS); do
            echo "======================================================"
            echo "Running experiment: Workers = $w | Studies = $s | Run = $r"
            echo "======================================================"
            
            # Create a unique output directory for this parameter combination and run
            EXP_DIR="$BASE_OUTPUT_DIR/workers_${w}_studies_${s}/run_${r}"
            mkdir -p "$EXP_DIR"
            
            # Execute the workflow and capture stdout to extract overall timing later
            python workflow.py --workers "$w" --studies "$s" --output_dir "$EXP_DIR" > "$EXP_DIR/stdout.log" 2>&1
            
            # Preserve the timing log by moving it into the specific experiment directory
            if [ -f workflow_timing.log ]; then
                mv workflow_timing.log "$EXP_DIR/workflow_timing.log"
            fi
            
            echo "Finished run $r for Workers = $w | Studies = $s"
            echo ""
        done
    done
done

echo "All experiments finished successfully!"
echo "Aggregating results..."
python summarize_results.py "$BASE_OUTPUT_DIR"
echo "Summarization complete! Check $BASE_OUTPUT_DIR/summary_statistics.csv"
