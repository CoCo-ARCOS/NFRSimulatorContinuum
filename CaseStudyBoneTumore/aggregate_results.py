import os
import argparse
import pandas as pd
from pathlib import Path

def aggregate(base_dir):
    results = []
    
    base_path = Path(base_dir)
    for exp_dir in base_path.glob("exp_w*"):
        workers_str = exp_dir.name.replace("exp_w", "")
        try:
            workers = int(workers_str)
        except ValueError:
            continue
            
        timing_file = exp_dir / "workflow_timing.log"
        if not timing_file.exists():
            print(f"Warning: {timing_file} not found. Did the Slurm job fail?")
            continue
            
        # Parse the timing file
        try:
            df = pd.read_csv(timing_file)
            if 'duration_seconds' in df.columns:
                total_time = df['duration_seconds'].sum()
                
                # Breakdown by stage
                edge_time = df[df['task'].str.contains('edge|integrity|compress|encrypt|encode', case=False, na=False)]['duration_seconds'].sum()
                fog_time = df[df['task'].str.contains('fog|decode|decrypt|decompress|verify', case=False, na=False)]['duration_seconds'].sum()
                cloud_time = df[df['task'].str.contains('cloud', case=False, na=False)]['duration_seconds'].sum()

                results.append({
                    "Workers": workers,
                    "Total Compute Time (s)": round(total_time, 2),
                    "Edge Node Compute (s)": round(edge_time, 2),
                    "Fog Node Compute (s)": round(fog_time, 2),
                    "Cloud Node Compute (s)": round(cloud_time, 2),
                })
        except Exception as e:
            print(f"Error reading {timing_file}: {e}")
            
    if not results:
        print("No valid timing logs found.")
        return
        
    final_df = pd.DataFrame(results)
    final_df = final_df.sort_values(by="Workers")
    
    output_file = base_path / "final_benchmark.csv"
    final_df.to_csv(output_file, index=False)
    print(f"\nSuccessfully aggregated results into: {output_file}")
    print("\n--- Summary ---")
    print(final_df.to_markdown(index=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base_dir", type=str, required=True)
    args = parser.parse_args()
    
    aggregate(args.base_dir)
