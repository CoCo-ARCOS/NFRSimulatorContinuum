#!/usr/bin/env python3
"""
Run simulated CT scan pipeline experiments matching the real workflow study.
Calibrate using measured timings, execute simulations, and plot results.
"""
import subprocess
import sys
import json
import csv
import re
from pathlib import Path
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np


WORKERS_LIST = [1, 2, 4, 8, 16]
STUDIES_LIST = [1, 10, 100]
CALIBRATION_FILE = Path("CaseStudyBoneTumore/resultadostiempos/workers_1_studies_1_workflow_timing.log")
DATASET = Path.home() / "Downloads" / "medicalimages" / "dicoms" / "Px9"
OUTPUT_BASE = Path("simulated_pipeline_results")
SIMULATOR = Path("proxy_dd/main").resolve()
HARDWARE_PROFILE = "c3"


def run_experiment(workers, studies):
    """Run a single ct_scan_pipeline experiment and extract timing."""
    output_dir = OUTPUT_BASE / f"workers_{workers}_studies_{studies}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(output_dir)
    
    cmd = [
        "python3",
        "proxy_dd/ct_scan_pipeline.py",
        "--dataset", str(DATASET),
        "--workers", str(workers),
        "--studies", str(studies),
        "--calibration-file", str(CALIBRATION_FILE),
        "--output-dir", str(output_dir),
        "--simulator", str(SIMULATOR),
        "--hardware-profile", HARDWARE_PROFILE,
    ]
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Running: workers={workers}, studies={studies}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr[:500]}")
            print(f"  STDOUT: {result.stdout[:500]}")
            return None
        
        # Extract timing from stdout
        match = re.search(r"Simulation complete", result.stdout)
        if not match:
            print(f"  ERROR: Simulation did not complete")
            return None
        
        # Read the stage totals CSV to get pipeline timing
        stage_totals_file = output_dir / "results" / "stage_totals_by_workers.csv"
        if not stage_totals_file.exists():
            print(f"  ERROR: stage_totals_by_workers.csv not found at {stage_totals_file}")
            return None
        
        # Read the stage totals and extract the pipeline total (sum of all stages' total_seconds)
        with stage_totals_file.open() as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            if not rows:
                print(f"  ERROR: stage_totals_by_workers.csv is empty")
                return None
            # Sum up the total_seconds from all stages to get pipeline completion time
            total_time = sum(float(row.get('total_seconds', 0)) for row in rows)
        
        print(f"  ✓ Complete: {total_time:.2f} seconds")
        return total_time
    
    except subprocess.TimeoutExpired:
        print(f"  ERROR: Timeout after 300 seconds")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def main():
    print(f"Starting calibrated pipeline simulations at {datetime.now()}")
    print(f"Calibration file: {CALIBRATION_FILE}")
    print(f"Dataset: {DATASET}")
    print(f"Simulator: {SIMULATOR}")
    print(f"Hardware profile: {HARDWARE_PROFILE}")
    print()
    
    # Verify calibration file exists
    if not CALIBRATION_FILE.exists():
        print(f"ERROR: Calibration file not found: {CALIBRATION_FILE}")
        return 1
    
    # Verify dataset exists
    if not DATASET.exists():
        print(f"ERROR: Dataset not found: {DATASET}")
        return 1
    
    # Verify simulator exists
    if not SIMULATOR.exists():
        print(f"ERROR: Simulator not found: {SIMULATOR}")
        return 1
    
    # Run all experiments
    results = {}
    for studies in STUDIES_LIST:
        results[studies] = {}
        for workers in WORKERS_LIST:
            timing = run_experiment(workers, studies)
            results[studies][workers] = timing
    
    print("\n" + "="*60)
    print("SIMULATION RESULTS SUMMARY")
    print("="*60)
    
    # Save results to CSV
    results_csv = OUTPUT_BASE / "simulation_results.csv"
    with results_csv.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["workers"] + [f"studies_{s}" for s in STUDIES_LIST])
        for workers in WORKERS_LIST:
            row = [workers]
            for studies in STUDIES_LIST:
                timing = results[studies].get(workers)
                row.append(f"{timing:.2f}" if timing is not None else "FAIL")
            writer.writerow(row)
    
    print(f"Results saved to: {results_csv}")
    
    # Print simulated table
    print("\nSimulated Execution times (seconds):")
    print(f"{'Workers':>10}", end="")
    for studies in STUDIES_LIST:
        print(f"{studies:>15}", end="")
    print()
    print("-" * (10 + 15 * len(STUDIES_LIST)))
    for workers in WORKERS_LIST:
        print(f"{workers:>10}", end="")
        for studies in STUDIES_LIST:
            timing = results[studies].get(workers)
            if timing is not None:
                print(f"{timing:>15.2f}", end="")
            else:
                print(f"{'FAIL':>15}", end="")
        print()
    
    # Create plots
    plot_simulations(results)
    
    # Load and print real results for comparison
    real_results = load_real_results()
    if real_results:
        print("\nReal Workflow Execution times (seconds):")
        print(f"{'Workers':>10}", end="")
        for studies in STUDIES_LIST:
            print(f"{studies:>15}", end="")
        print()
        print("-" * (10 + 15 * len(STUDIES_LIST)))
        for workers in WORKERS_LIST:
            print(f"{workers:>10}", end="")
            for studies in STUDIES_LIST:
                timing = real_results.get(studies, {}).get(workers)
                if timing is not None:
                    print(f"{timing:>15.2f}", end="")
                else:
                    print(f"{'N/A':>15}", end="")
            print()
        
        # Print error percentages
        print("\nSimulation Error (% difference from real):")
        print(f"{'Workers':>10}", end="")
        for studies in STUDIES_LIST:
            print(f"{studies:>15}", end="")
        print()
        print("-" * (10 + 15 * len(STUDIES_LIST)))
        for workers in WORKERS_LIST:
            print(f"{workers:>10}", end="")
            for studies in STUDIES_LIST:
                sim = results[studies].get(workers)
                real = real_results.get(studies, {}).get(workers)
                if sim is not None and real is not None:
                    error = ((sim - real) / real) * 100
                    print(f"{error:>14.1f}%", end="")
                else:
                    print(f"{'N/A':>15}", end="")
            print()
    
    print("\n" + "="*60)
    
    print("\n" + "="*60)
    print(f"Completed at {datetime.now()}")
    return 0


def load_real_results():
    """Load real execution results from output.txt."""
    output_file = Path("CaseStudyBoneTumore/resultadostiempos/output.txt")
    if not output_file.exists():
        return {}
    
    real_results = {}
    with output_file.open() as fh:
        content = fh.read()
    
    exp_re = re.compile(r'Running experiment: Workers = (\d+) \| Studies = (\d+)')
    time_re = re.compile(r'\[TIMING\] Overall execution time: ([0-9.]+) seconds')
    
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = exp_re.search(line)
        if m:
            workers = int(m.group(1))
            studies = int(m.group(2))
            t = None
            # Search for timing in the next 500 lines
            for j in range(i+1, min(len(lines), i+500)):
                if lines[j].startswith('[TIMING] Overall execution time:'):
                    tm = time_re.search(lines[j])
                    if tm:
                        t = float(tm.group(1))
                    break
                if lines[j].startswith('Finished experiment:'):
                    break
            if t is not None:
                if studies not in real_results:
                    real_results[studies] = {}
                real_results[studies][workers] = t
    
    return real_results


def plot_simulations(results):
    """Create comparison plots of simulated results."""
    # Load real results for comparison
    real_results = load_real_results()
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # Plot 1: Simulated execution time vs workers for each study count
    ax = axes[0, 0]
    for studies in STUDIES_LIST:
        workers_list = []
        times_list = []
        for workers in WORKERS_LIST:
            timing = results[studies].get(workers)
            if timing is not None:
                workers_list.append(workers)
                times_list.append(timing)
        if workers_list:
            ax.plot(workers_list, times_list, marker='o', label=f'{studies} studies', linewidth=2)
    
    ax.set_xlabel('Workers', fontsize=11)
    ax.set_ylabel('Execution time (s)', fontsize=11)
    ax.set_title('Simulated: Execution Time vs Workers', fontsize=11, fontweight='bold')
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.set_xticks(WORKERS_LIST)
    
    # Plot 2: Real execution time vs workers (if available)
    ax = axes[0, 1]
    if real_results:
        for studies in STUDIES_LIST:
            if studies in real_results:
                workers_list = []
                times_list = []
                for workers in WORKERS_LIST:
                    timing = real_results[studies].get(workers)
                    if timing is not None:
                        workers_list.append(workers)
                        times_list.append(timing)
                if workers_list:
                    ax.plot(workers_list, times_list, marker='s', label=f'{studies} studies', linewidth=2)
        ax.set_xlabel('Workers', fontsize=11)
        ax.set_ylabel('Execution time (s)', fontsize=11)
        ax.set_title('Real Workflow: Execution Time vs Workers', fontsize=11, fontweight='bold')
        ax.legend()
        ax.grid(True, linestyle='--', alpha=0.5)
        ax.set_xticks(WORKERS_LIST)
    else:
        ax.text(0.5, 0.5, 'Real results not available', ha='center', va='center', transform=ax.transAxes)
        ax.set_title('Real Workflow: Not Available', fontsize=11, fontweight='bold')
    
    # Plot 3: Speedup - simulated (normalized to 1 worker)
    ax = axes[1, 0]
    for studies in STUDIES_LIST:
        workers_list = []
        speedup_list = []
        baseline = results[studies].get(1)
        if baseline is None:
            continue
        for workers in WORKERS_LIST:
            timing = results[studies].get(workers)
            if timing is not None:
                workers_list.append(workers)
                speedup_list.append(baseline / timing)
        if workers_list:
            ax.plot(workers_list, speedup_list, marker='o', label=f'{studies} studies', linewidth=2)
    
    # Ideal speedup line
    ax.plot(WORKERS_LIST, WORKERS_LIST, 'k--', alpha=0.5, label='Ideal speedup')
    
    ax.set_xlabel('Workers', fontsize=11)
    ax.set_ylabel('Speedup (vs 1 worker)', fontsize=11)
    ax.set_title('Simulated: Speedup vs Workers', fontsize=11, fontweight='bold')
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.set_xticks(WORKERS_LIST)
    
    # Plot 4: Speedup - real (if available)
    ax = axes[1, 1]
    if real_results:
        for studies in STUDIES_LIST:
            if studies in real_results:
                workers_list = []
                speedup_list = []
                baseline = real_results[studies].get(1)
                if baseline is None:
                    continue
                for workers in WORKERS_LIST:
                    timing = real_results[studies].get(workers)
                    if timing is not None:
                        workers_list.append(workers)
                        speedup_list.append(baseline / timing)
                if workers_list:
                    ax.plot(workers_list, speedup_list, marker='s', label=f'{studies} studies', linewidth=2)
        
        # Ideal speedup line
        ax.plot(WORKERS_LIST, WORKERS_LIST, 'k--', alpha=0.5, label='Ideal speedup')
        ax.set_xlabel('Workers', fontsize=11)
        ax.set_ylabel('Speedup (vs 1 worker)', fontsize=11)
        ax.set_title('Real Workflow: Speedup vs Workers', fontsize=11, fontweight='bold')
        ax.legend()
        ax.grid(True, linestyle='--', alpha=0.5)
        ax.set_xticks(WORKERS_LIST)
    else:
        ax.text(0.5, 0.5, 'Real results not available', ha='center', va='center', transform=ax.transAxes)
        ax.set_title('Real Workflow: Not Available', fontsize=11, fontweight='bold')
    
    plt.tight_layout()
    plot_file = OUTPUT_BASE / "simulation_results.png"
    fig.savefig(plot_file, dpi=150)
    print(f"\nPlot saved to: {plot_file}")
    plt.close(fig)
    
    # If real results exist, create a separate comparison plot
    if real_results:
        create_comparison_plot(results, real_results)


def create_comparison_plot(sim_results, real_results):
    """Create side-by-side comparison of simulated vs real results."""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    
    # Plot for each study count
    for idx, studies in enumerate(STUDIES_LIST):
        ax = axes[idx]
        x = np.arange(len(WORKERS_LIST))
        width = 0.35
        
        sim_times = []
        real_times = []
        for workers in WORKERS_LIST:
            st = sim_results[studies].get(workers)
            rt = real_results.get(studies, {}).get(workers)
            sim_times.append(st if st is not None else 0)
            real_times.append(rt if rt is not None else 0)
        
        bars1 = ax.bar(x - width/2, sim_times, width, label='Simulated', alpha=0.8)
        if any(real_times):
            bars2 = ax.bar(x + width/2, real_times, width, label='Real', alpha=0.8)
        
        ax.set_xlabel('Workers', fontsize=11)
        ax.set_ylabel('Execution time (s)', fontsize=11)
        ax.set_title(f'{studies} Studies', fontsize=12, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels(WORKERS_LIST)
        ax.legend()
        ax.grid(True, axis='y', linestyle='--', alpha=0.5)
    
    plt.suptitle('Simulated vs Real Workflow Execution Times', fontsize=13, fontweight='bold', y=1.02)
    plt.tight_layout()
    comp_file = OUTPUT_BASE / "simulation_vs_real_comparison.png"
    fig.savefig(comp_file, dpi=150, bbox_inches='tight')
    print(f"Comparison plot saved to: {comp_file}")
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())
