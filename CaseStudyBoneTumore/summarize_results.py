import os
import glob
import pandas as pd
import re
import sys

def summarize(base_dir):
    data = []
    
    # Search for all configuration directories
    config_dirs = glob.glob(os.path.join(base_dir, "workers_*_studies_*"))
    
    for exp_dir in config_dirs:
        basename = os.path.basename(exp_dir)
        m = re.match(r"workers_(\d+)_studies_(\d+)", basename)
        if not m:
            continue
        
        w = int(m.group(1))
        s = int(m.group(2))
        
        # Search for all repetition runs in this config
        for run_dir in glob.glob(os.path.join(exp_dir, "run_*")):
            run_idx = int(run_dir.split('_')[-1])
            
            # Read and parse workflow_timing.log to get individual task durations
            timing_file = os.path.join(run_dir, "workflow_timing.log")
            if os.path.exists(timing_file):
                try:
                    df = pd.read_csv(timing_file)
                    for _, row in df.iterrows():
                        data.append({
                            'workers': w,
                            'studies': s,
                            'run': run_idx,
                            'task': row['task'],
                            'duration_seconds': row['duration_seconds']
                        })
                except Exception as e:
                    print(f"Failed to read {timing_file}: {e}")
                    
            # Read and parse stdout.log to get overall execution time
            stdout_file = os.path.join(run_dir, "stdout.log")
            if os.path.exists(stdout_file):
                with open(stdout_file, 'r') as f:
                    content = f.read()
                    match = re.search(r"\[TIMING\] Overall execution time: ([\d\.]+) seconds", content)
                    if match:
                        overall_time = float(match.group(1))
                        data.append({
                            'workers': w,
                            'studies': s,
                            'run': run_idx,
                            'task': 'overall_workflow',
                            'duration_seconds': overall_time
                        })
                        
    if not data:
        print("No data found to summarize. Have you executed the experiments yet?")
        return
        
    df_all = pd.DataFrame(data)
    
    # Calculate Mean and Std grouped by workers, studies, and task
    # Note: If a single run outputs multiple rows for the same task (e.g. multiple studies),
    # this will aggregate across ALL occurrences of the task across all runs for this config.
    summary = df_all.groupby(['workers', 'studies', 'task'])['duration_seconds'].agg(['mean', 'std', 'count']).reset_index()
    
    # Sort for readability
    summary = summary.sort_values(by=['workers', 'studies', 'task'])
    
    # Save to CSV
    out_csv = os.path.join(base_dir, "summary_statistics.csv")
    summary.to_csv(out_csv, index=False)
    
    print(f"\n--- SUMMARY STATISTICS ---")
    print(summary.to_string(index=False))
    print(f"\nDetailed CSV successfully saved to {out_csv}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        summarize(sys.argv[1])
    else:
        summarize("experiments_results")
