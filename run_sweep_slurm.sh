#!/bin/bash
#SBATCH --job-name=simulator_sweep
#SBATCH --output=slurm_logs/sweep_%j.out
#SBATCH --error=slurm_logs/sweep_%j.err
#SBATCH --time=12:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --partition=compute

# ==============================================================================
# Slurm Job Script for SimulatorContinuum - Single Requirement Benchmarks
# ==============================================================================
#
# USAGE:
#   sbatch run_sweep_slurm.sh [OPTIONS]
#
# EXAMPLE:
#   sbatch run_sweep_slurm.sh --requirement-type compress --sweep --objects-list 10,100
#
# DESC:
#   This script activates the local python virtual environment, loads required
#   modules, ensures output directories exist, and runs the benchmark script.
# ==============================================================================

# 1. Environment Setup
# Load Python module if necessary on your cluster (uncomment if needed)
# module load python/3.12

# Determine script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# Activate Virtual Environment
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "Warning: No venv found. Proceeding with system python."
fi

# 2. Directory Preparation
mkdir -p slurm_logs
mkdir -p single_requirement_benchmarks

# 3. Execution
echo "Starting job: $SLURM_JOB_ID"
echo "Node: $SLURMD_NODENAME"
echo "Date: $(date)"
echo "Arguments: $@"
echo "-----------------------------------------------------------------"

# Run the benchmark script, passing all slurm script arguments directly to it
python3 benchmark_single_requirement.py "$@"

EXIT_CODE=$?

echo "-----------------------------------------------------------------"
echo "Job finished with exit code: $EXIT_CODE"
echo "End Date: $(date)"
exit $EXIT_CODE
