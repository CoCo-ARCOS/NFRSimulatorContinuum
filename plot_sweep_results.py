#!/usr/bin/env python3
import os
import sys
import pandas as pd
from pathlib import Path

# Ensure matplotlib is installed, otherwise try to import it or print clear help
try:
    import matplotlib.pyplot as plt
    import seaborn as sns
except ImportError:
    print("Error: matplotlib and seaborn are required to run this script.")
    print("Please install them using: pip install matplotlib seaborn")
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 plot_sweep_results.py <path_to_sweep_summary.csv>")
        sys.exit(1)
        
    csv_path = Path(sys.argv[1]).resolve()
    if not csv_path.exists():
        print(f"Error: File not found at '{csv_path}'")
        sys.exit(1)
        
    print(f"Reading sweep summary from {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # Filter only successful runs
    df = df[df["status"] == "ok"]
    if df.empty:
        print("Error: No successful runs found in the CSV.")
        sys.exit(1)
        
    # Group by application / algorithm
    # An application is defined by (requirement_type, algorithm)
    df["app_name"] = df["requirement_type"] + " (" + df["algorithm"] + ")"
    apps = df["app_name"].unique()
    
    # Create a plots directory in the same directory as the CSV
    plots_dir = csv_path.parent / "plots"
    plots_dir.mkdir(exist_ok=True)
    
    print(f"Generating line plots in {plots_dir}...")
    
    # Use seaborn premium styles
    sns.set_theme(style="whitegrid")
    plt.rcParams.update({
        "font.size": 11,
        "axes.labelsize": 12,
        "axes.titlesize": 14,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "figure.titlesize": 16
    })
    
    for app in apps:
        app_df = df[df["app_name"] == app].copy()
        
        # Sort by size_mb for correct line plotting
        app_df = app_df.sort_values(by="size_mb")
        
        # Define configuration labels dynamically
        # Standard: service_time_model
        # RS: service_time_model + K + M
        if "RS" in app:
            app_df["config_label"] = (
                app_df["service_time_model"] + 
                " (K=" + app_df["ida_k"].astype(str) + 
                ", M=" + app_df["ida_m"].astype(str) + ")"
            )
        else:
            app_df["config_label"] = app_df["service_time_model"]
            
        configs = app_df["config_label"].unique()
        
        # Setup the plot
        plt.figure(figsize=(10, 6), dpi=150)
        
        # Generate distinct colors for each configuration
        colors = sns.color_palette("husl", len(configs))
        
        for i, config in enumerate(configs):
            config_df = app_df[app_df["config_label"] == config]
            color = colors[i]
            
            # Plot Real Time (Solid line with markers)
            plt.plot(
                config_df["size_mb"], 
                config_df["stage_compute_seconds_real_seconds"], 
                label=f"Real ({config})",
                color=color,
                linestyle="-",
                marker="o",
                linewidth=2,
                markersize=6
            )
            
            # Plot Simulator Time (Dashed line with matching color and different marker)
            plt.plot(
                config_df["size_mb"], 
                config_df["stage_compute_seconds_simulator_seconds"], 
                label=f"Simulated ({config})",
                color=color,
                linestyle="--",
                marker="x",
                linewidth=2,
                markersize=6
            )
            
        plt.title(f"Performance Comparison: {app}", pad=15)
        plt.xlabel("Workload Size (MB)", labelpad=10)
        plt.ylabel("Time (seconds)", labelpad=10)
        plt.xscale("log") # Log scale is highly recommended for workload sizes (e.g. 1, 10, 100)
        
        # If sizes are 1, 10, 100, let's explicitly format x-axis ticks
        sizes = sorted(app_df["size_mb"].unique())
        plt.xticks(sizes, [f"{s} MB" for s in sizes])
        
        # Add legend
        plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left", borderaxespad=0.)
        plt.tight_layout()
        
        # Save figure
        safe_app_name = app.replace(" ", "_").replace("(", "").replace(")", "").lower()
        save_path = plots_dir / f"{safe_app_name}_performance.png"
        plt.savefig(save_path, bbox_inches="tight")
        plt.close()
        
        print(f" - Saved: {save_path.name}")
        
    print("\nAll plots generated successfully!")
    print(f"You can view the resulting PNG files under: {plots_dir}")

if __name__ == "__main__":
    main()
