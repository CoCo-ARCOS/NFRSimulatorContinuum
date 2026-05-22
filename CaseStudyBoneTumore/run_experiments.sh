#!/bin/bash

# Exit on error
set -e

# Activate the virtual environment
#source venv/bin/activate

# Define the dimensions to test
# Adjust these arrays to test more dimensions or larger scales
WORKERS=(1 2 4 8 16)
STUDIES=(1 10 100)

# Define execution mode (--local for local threading, empty string for Slurm)
# Change this to EXEC_MODE="" when submitting to your Slurm cluster!

# Base output directory for all experiment runs
BASE_OUTPUT_DIR="/lustre/uc3m_a0/dynamic/dantedomizzi/parsl/"
mkdir -p "$BASE_OUTPUT_DIR"

echo "Starting workflow experiments..."
echo "Testing workers: ${WORKERS[*]}"
echo "Testing studies: ${STUDIES[*]}"
echo "Execution Mode: ${EXEC_MODE:-Slurm}"
echo ""

for w in "${WORKERS[@]}"; do
    for s in "${STUDIES[@]}"; do
        echo "======================================================"
        echo "Running experiment: Workers = $w | Studies = $s"
        echo "======================================================"
        
        # Create a unique output directory for this parameter combination
        EXP_DIR="$BASE_OUTPUT_DIR/workers_${w}_studies_${s}"
        
        # Execute the workflow
        python3 workflow.py --dataset /lustre/uc3m_a0/dynamic/dantedomizzi/dicoms/ --workers "$w" --studies "$s" --output_dir "$EXP_DIR" 
        
        # Preserve the timing log by moving it into the specific experiment directory
        if [ -f workflow_timing.log ]; then
            mv workflow_timing.log "$EXP_DIR/workflow_timing.log"
        fi
        
        echo "Finished experiment: Workers = $w | Studies = $s"
        echo ""
    done
done

echo "All experiments finished successfully! Results are stored in $BASE_OUTPUT_DIR/"
