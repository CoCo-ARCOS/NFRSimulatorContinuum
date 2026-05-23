#!/bin/bash

# Configuration
STUDIES=10
WORKERS=(1 2 4 8)
PYTHON_EXEC="venv/bin/python"
SCRIPT="workflow_multiprocessing.py"

echo "=========================================================="
echo "Starting Multiprocessing Benchmark"
echo "Workload: $STUDIES studies (~$((STUDIES * 161)) DICOM files)"
echo "Testing Worker Counts: ${WORKERS[@]}"
echo "=========================================================="
echo ""

# Store results for summary
declare -A results

for w in "${WORKERS[@]}"; do
    echo "----------------------------------------------------------"
    echo ">>> Running with $w worker(s) <<<"
    
    # 1. Clean up previous runs to ensure accurate I/O
    rm -rf tmp_edge tmp_fog tmp_cloud workflow_timing.log
    
    # 2. Run workflow and capture output
    output=$($PYTHON_EXEC $SCRIPT --studies $STUDIES --workers $w 2>&1)
    
    # 3. Extract the timing from the output
    exec_time=$(echo "$output" | grep -oP '\[TIMING\] Overall execution time: \K[0-9.]+')
    
    if [ -z "$exec_time" ]; then
        echo "Error: Could not extract execution time. Check for errors."
        echo "$output"
        results[$w]="Failed"
    else
        echo "Completed in $exec_time seconds"
        results[$w]=$exec_time
    fi
done

echo ""
echo "=========================================================="
echo "Benchmark Summary (Studies=$STUDIES)"
echo "=========================================================="
printf "%-10s | %-20s\n" "Workers" "Execution Time (s)"
printf "%-10s | %-20s\n" "----------" "--------------------"
for w in "${WORKERS[@]}"; do
    printf "%-10s | %-20s\n" "$w" "${results[$w]}"
done
echo "=========================================================="
