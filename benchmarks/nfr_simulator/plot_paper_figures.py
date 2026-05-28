#!/usr/bin/env python3
"""
Script to recreate and plot four figures in a 2x2 grid for paper presentation.
Plots are generated from data to allow modifications.
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from collections import defaultdict

plt.style.use("paper.mplstyle")

pt = 1./72.27
jour_sizes = {"PRD": {"onecol": 246.*pt, "twocol": 510.*pt},
              "CQG": {"onecol": 374.*pt}, }
my_width = jour_sizes["PRD"]["twocol"]
golden = (1 + 5 ** 0.5) / 1.5
# golden = (1 + 5 ** 0.5) / 2.4 # you can modify here for a higher height if needed
plt.rcParams.update({
    'axes.labelsize': 14,       # Axis label font size
    'legend.fontsize': 14,      # Legend font size
    'xtick.labelsize': 12,      # X-axis tick label font size
})

# Define paths
base_path = Path(__file__).parent
sensitivity_results_path = base_path / "proxy_dd" / "sensitivity_results"

def aggregate_sensitivity_results(factor_name, subdirs):
    """Aggregate results from sensitivity analysis subdirectories."""
    factor_results = defaultdict(list)
    
    for subdir in subdirs:
        subdir_path = sensitivity_results_path / factor_name / subdir
        if not subdir_path.exists():
            continue
        
        # Collect results from all runs in this subdirectory
        for run_dir in sorted(subdir_path.glob("run_*")):
            results_file = run_dir / "results" / "stage_totals_by_workers.csv"
            if results_file.exists():
                df = pd.read_csv(results_file)
                # Sum total_seconds across all stages for this run
                total_time = df["total_seconds"].sum()
                factor_results[subdir].append(total_time)
    
    # Calculate averages and maintain order
    ordered_labels = subdirs
    averages = {}
    for label in ordered_labels:
        if label in factor_results and factor_results[label]:
            averages[label] = np.mean(factor_results[label])
    
    return averages

# Load worker scaling data
workers_df = pd.read_csv(sensitivity_results_path / "workers_detailed_summary.csv")

# Aggregate sensitivity data from subdirectories
devices_dirs = ["20-devices", "50-devices", "100-devices", "200-devices", "400-devices"]
payload_dirs = ["1MB", "10MB", "100MB", "1000MB"]
storage_dirs = ["10MBps", "50MBps", "100MBps", "250MBps", "500MBps"]

devices_dirs = ["20-devices", "50-devices", "100-devices", "200-devices", "400-devices"]
payload_dirs = ["1MB", "10MB", "100MB", "1000MB"]
storage_dirs = ["10MBps", "50MBps", "100MBps", "250MBps", "500MBps"]

devices_data = aggregate_sensitivity_results("devices", devices_dirs)
payload_data = aggregate_sensitivity_results("payload_size", payload_dirs)
storage_bw_data = aggregate_sensitivity_results("storage_bandwidth", storage_dirs)

# Extract worker scaling data - calculate pipeline total time
worker_scaling = workers_df.groupby("workers").agg({
    "pipeline_avg_seconds": "first",
    "pipeline_avg_input_seconds": "first",
    "pipeline_avg_application_seconds": "first",
    "pipeline_avg_output_seconds": "first"
}).reset_index().sort_values("workers")


# Extract worker scaling data - calculate pipeline total time
worker_scaling = workers_df.groupby("workers").agg({
    "pipeline_avg_seconds": "first",
    "pipeline_avg_input_seconds": "first",
    "pipeline_avg_application_seconds": "first",
    "pipeline_avg_output_seconds": "first"
}).reset_index().sort_values("workers")

# Create 2x2 subplot grid with shared y-axis
fig, axes = plt.subplots(2, 2, figsize=(my_width, my_width / golden), sharey=False)

# --- Plot 1: Devices Sensitivity ---
ax1 = axes[0, 0]
if devices_data:
    device_labels = list(devices_data.keys())
    # Remove the text from labels for cleaner x-axis
    device_labels = [label.split("-")[0] for label in device_labels]
    device_times = list(devices_data.values())
    ax1.plot(range(len(device_labels)), device_times, marker="o", linewidth=2, markersize=8)
    ax1.set_xticks(range(len(device_labels)))
    ax1.set_xticklabels(device_labels)
    #ax1.set_title("Devices Sensitivity Analysis", fontsize=12, fontweight="bold")
    ax1.set_xlabel("Number of Devices", fontsize=11)
    ax1.grid(True, linestyle="--", alpha=0.3)
else:
    ax1.text(0.5, 0.5, "No devices data available", ha="center", va="center", transform=ax1.transAxes)
    ax1.set_title("Devices Sensitivity Analysis", fontsize=12, fontweight="bold")

# --- Plot 2: Payload Size Sensitivity ---
ax2 = axes[0, 1]
if payload_data:
    payload_labels = list(payload_data.keys())
    payload_labels = [label.replace("MB", "") for label in payload_labels]  # Clean up labels if needed
    payload_times = list(payload_data.values())
    ax2.plot(range(len(payload_labels)), payload_times, marker="o", linewidth=2, markersize=8, label="Pipeline total")
    ax2.set_xticks(range(len(payload_labels)))
    ax2.set_xticklabels(payload_labels)
    #ax2.set_title("Payload Size Sensitivity Analysis", fontsize=12, fontweight="bold")
    ax2.set_xlabel("Payload Size (MB)", fontsize=11)
    ax2.grid(True, linestyle="--", alpha=0.3)
else:
    ax2.text(0.5, 0.5, "No payload size data available", ha="center", va="center", transform=ax2.transAxes)
    ax2.set_title("Payload Size Sensitivity Analysis", fontsize=12, fontweight="bold")

# --- Plot 3: Worker Scaling Pipeline Breakdown ---
ax3 = axes[1, 0]
workers = worker_scaling["workers"].values
input_times = worker_scaling["pipeline_avg_input_seconds"].values
app_times = worker_scaling["pipeline_avg_application_seconds"].values
output_times = worker_scaling["pipeline_avg_output_seconds"].values

total_times = input_times + app_times + output_times

ax3.plot(range(len(workers)), total_times, marker="o", label="Input", linewidth=2, markersize=8)
#ax3.plot(range(len(workers)), app_times, marker="s", label="Application", linewidth=2, markersize=8)
#ax3.plot(range(len(workers)), output_times, marker="^", label="Output", linewidth=2, markersize=8)

ax3.set_xticks(range(len(workers)))
ax3.set_xticklabels([str(w) for w in workers])
#ax3.set_title("Worker Scaling Pipeline Breakdown", fontsize=12, fontweight="bold")
ax3.set_xlabel("Number of Workers", fontsize=11)
ax3.grid(True, linestyle="--", alpha=0.3)
#ax3.legend(fontsize=10, loc="upper right")


# --- Plot 4: Storage Bandwidth Sensitivity ---
ax4 = axes[1, 1]
if storage_bw_data:
    storage_labels = list(storage_bw_data.keys())
    storage_labels = [label.replace("MBps", "") for label in storage_labels]  # Clean up labels if needed
    storage_times = list(storage_bw_data.values())
    ax4.plot(range(len(storage_labels)), storage_times, marker="o", linewidth=2, markersize=8, label="Pipeline total")
    ax4.set_xticks(range(len(storage_labels)))
    ax4.set_xticklabels(storage_labels)
    #ax4.set_title("Storage Bandwidth Sensitivity Analysis", fontsize=12, fontweight="bold")
    ax4.set_xlabel("Storage Bandwidth (MBps)", fontsize=11)
    ax4.grid(True, linestyle="--", alpha=0.3)
else:
    ax4.text(0.5, 0.5, "No storage bandwidth data available", ha="center", va="center", transform=ax4.transAxes)
    ax4.set_title("Storage Bandwidth Sensitivity Analysis", fontsize=12, fontweight="bold")

# Overall layout
fig.supylabel("Response Time (seconds)", fontsize=14)
plt.tight_layout()
plt.savefig(base_path / "paper_figures_grid.pdf", dpi=150, bbox_inches="tight")
print("✓ Paper figures grid saved to: paper_figures_grid.png")

# Also display the plot
#plt.show()
