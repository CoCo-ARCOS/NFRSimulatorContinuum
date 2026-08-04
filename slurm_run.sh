#!/bin/bash
#SBATCH --job-name=robust_eval
#SBATCH --output=robust_eval_%j.log
#SBATCH --error=robust_eval_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32      # Adjust this based on your cluster's nodes
#SBATCH --mem=32G               # Memory allocation
#SBATCH --time=24:00:00         # Maximum time allowed for the job

# Load any necessary modules (uncomment and adjust if your cluster requires specific Python versions)
# module load python/3.10

# Go to the directory where the job was submitted from
cd $SLURM_SUBMIT_DIR

# Set the JOBS variable dynamically based on the CPUs allocated by Slurm
export JOBS=$SLURM_CPUS_PER_TASK
export REPLICATIONS=5

echo "Starting robust evaluation with $JOBS parallel jobs..."

# Run the evaluation script
./run_robust_all.sh . proxy_dd/ configs/site.literature.json configs/workflows.json robust-output

echo "Job completed."
