#!/bin/bash

STUDIES=100
WORKERS_LIST=(1 2 4 8)
BASE_DIR="benchmark_results"

echo "=========================================================="
echo "Starting Automated Multiprocessing Benchmark on Slurm"
echo "Workload: $STUDIES studies"
echo "Workers to test: ${WORKERS_LIST[@]}"
echo "=========================================================="

mkdir -p $BASE_DIR

CLOUD_JOBS=()

for w in "${WORKERS_LIST[@]}"; do
    EXP_DIR="$BASE_DIR/exp_w$w"
    echo "[*] Deploying distributed pipeline for workers=$w (Output: $EXP_DIR)"
    
    # Run the deployment and capture the final Cloud Job ID
    CLOUD_JOB=$(./deploy_distributed.sh $STUDIES $w $EXP_DIR)
    
    if [ -n "$CLOUD_JOB" ]; then
        echo "    -> Deployed. Cloud node will finish under Job ID: $CLOUD_JOB"
        CLOUD_JOBS+=($CLOUD_JOB)
    else
        echo "    -> Error: Failed to deploy job."
    fi
done

echo ""
echo "All experiments queued!"

# Join all cloud job IDs with a colon to create the final dependency
DEPENDENCY_LIST=$(IFS=: ; echo "${CLOUD_JOBS[*]}")

echo "Submitting final Aggregator Node. It will wait for: $DEPENDENCY_LIST"

# Submit the aggregator node
AGG_JOB=$(sbatch --parsable --partition=large --nodes=1 --dependency=afterok:$DEPENDENCY_LIST --wrap="venv/bin/python aggregate_results.py --base_dir $BASE_DIR")

echo "Aggregator Node Job ID: $AGG_JOB"
echo "Once Job $AGG_JOB completes, your final metrics will be compiled in $BASE_DIR/final_benchmark.csv"
echo "=========================================================="
