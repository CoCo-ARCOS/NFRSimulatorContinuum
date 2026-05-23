#!/bin/bash

# Configuration
STUDIES=10
WORKERS=4
PARTITION="large"

echo "=========================================================="
echo "Deploying Distributed Pipeline to Slurm"
echo "Workload: $STUDIES studies"
echo "Workers per node: $WORKERS"
echo "=========================================================="

# Clean up before deployment
rm -rf tmp_edge tmp_fog tmp_cloud workflow_timing.log slurm-*.out

# 1. Deploy EDGE Node
EDGE_JOB=$(sbatch --parsable --partition=$PARTITION --nodes=1 -c $WORKERS --wrap="venv/bin/python workflow_multiprocessing.py --stage edge --studies $STUDIES --workers $WORKERS")
echo "-> Submitted EDGE stage (JobID: $EDGE_JOB)"

# 2. Deploy FOG Node
FOG_JOB=$(sbatch --parsable --partition=$PARTITION --nodes=1 -c $WORKERS --dependency=afterok:$EDGE_JOB --wrap="venv/bin/python workflow_multiprocessing.py --stage fog --studies $STUDIES --workers $WORKERS")
echo "-> Submitted FOG stage  (JobID: $FOG_JOB) [Depends on EDGE]"

# 3. Deploy CLOUD Node
CLOUD_JOB=$(sbatch --parsable --partition=$PARTITION --nodes=1 -c $WORKERS --dependency=afterok:$FOG_JOB --wrap="venv/bin/python workflow_multiprocessing.py --stage cloud --studies $STUDIES --workers $WORKERS")
echo "-> Submitted CLOUD stage (JobID: $CLOUD_JOB) [Depends on FOG]"

echo ""
echo "=========================================================="
echo "All stages successfully queued on Slurm!"
echo "Monitor your jobs with: squeue -u \$USER"
echo "Check the slurm-<jobid>.out files for logs."
echo "=========================================================="
