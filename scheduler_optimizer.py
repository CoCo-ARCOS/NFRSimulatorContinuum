import json
import subprocess
import os
import csv
import argparse
import itertools

# Constants
ENERGY_CSV = "results/energy_by_machine.csv"
STAGE_CSV = "results/stage_totals_by_workers.csv"

def generate_dynamic_candidates(tasks, machines, nfr_options):
    """
    Generates the complete search space.
    Maps every task to every possible machine, and tests all NFR options.
    """
    # 1. Generate all possible placement mappings: Task -> Machine
    # e.g., if tasks=[T1, T2] and machines=[M1, M2], placements will be:
    # {T1: M1, T2: M1}, {T1: M1, T2: M2}, etc.
    task_names = [t["name"] for t in tasks]
    machine_names = [m["name"] for m in machines]
    
    placement_combos = list(itertools.product(machine_names, repeat=len(tasks)))
    placements = [dict(zip(task_names, combo)) for combo in placement_combos]
    
    # 2. Generate candidates combining Placements and NFR Options
    candidates = []
    for placement in placements:
        for nfr in nfr_options:
            candidates.append({
                "placement": placement,
                "nfr_config": nfr  # e.g., {"compression": "LZ4", "encryption": "AES"}
            })
    return candidates

def create_dynamic_config_json(candidate, tasks, machines, links, filename):
    """
    Generates a simulator-compatible config.json dynamically based on input.
    """
    # Deep copy tasks to modify them safely
    stages_config = []
    for task in tasks:
        stage = {
            "name": task["name"],
            "b_fs": task.get("b_fs", 100),
            "b_fs_read": task.get("b_fs_read", 100),
            "b_fs_write": task.get("b_fs_write", 100),
            "application_mean_service_time": task.get("service_time", 1.0),
            "output_requirements": []
        }
        
        # Inject selected NFR algorithms if they apply to this stage
        if task.get("requires_nfr", False):
            if candidate["nfr_config"].get("compression"):
                stage["output_requirements"].append({
                    "type": "compress", 
                    "algorithm": candidate["nfr_config"]["compression"]
                })
            if candidate["nfr_config"].get("encryption"):
                stage["output_requirements"].append({
                    "type": "encrypt", 
                    "algorithm": candidate["nfr_config"]["encryption"]
                })
        stages_config.append(stage)

    # Prepare physical machines (with clean, empty stage lists initially)
    machines_config = []
    for m in machines:
        machines_config.append({
            "name": m["name"],
            "stages": [],
            "hardware_profile": m.get("hardware_profile", "default"),
            "power_model": m.get("power_model", "linear"),
            "max_power": m.get("max_power", 150.0),
            "static_power_percent": m.get("static_power_percent", 0.5)
        })

    # Map the tasks to their scheduled machines
    for stage_name, machine_name in candidate["placement"].items():
        for m in machines_config:
            if m["name"] == machine_name:
                m["stages"].append(stage_name)

    # Final config JSON structure
    config = {
        "workers": 1,
        "traces_number": 1,
        "traces": [
            {
                "MUESTRAS": 100,
                "inter_arrival": 0.18,
                "DISTRIBUTION": 3,
                "mean": 15.0,
                "stddev": 0.6,
                "SIZE": 1048576,
                "stddevS": 0.5,
                "Concurrency": 1
            }
        ],
        "agent_type": "output",
        "stages": stages_config,
        "machines": machines_config,
        "links": links
    }

    with open(filename, 'w') as f:
        json.dump(config, f, indent=2)

def run_simulator(simulator_cmd, config_file, simulator_dir):
    cmd = [simulator_cmd, os.path.basename(config_file), "log-log", "docker"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=simulator_dir)
    return result.returncode == 0

def extract_metrics(simulator_dir, machines_to_track):
    """
    Extracts the makespan and the energy consumption of specified target nodes.
    """
    energy_csv_path = os.path.join(simulator_dir, ENERGY_CSV)
    stage_csv_path = os.path.join(simulator_dir, STAGE_CSV)
    
    # Extract energy for tracked nodes (e.g., Edge)
    energy_metrics = {m: 0.0 for m in machines_to_track}
    total_workflow_energy = 0.0
    
    if os.path.exists(energy_csv_path):
        with open(energy_csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                m_name = row["machine_name"]
                m_energy = float(row["energy_joules"])
                total_workflow_energy += m_energy
                if m_name in energy_metrics:
                    energy_metrics[m_name] = m_energy
                    
    c_max = 0.0
    if os.path.exists(stage_csv_path):
        with open(stage_csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_time = float(row["total_seconds"])
                if total_time > c_max:
                    c_max = total_time

    return energy_metrics, total_workflow_energy, c_max

def main():
    parser = argparse.ArgumentParser(description="Continuum General Workflow Scheduler")
    parser.add_argument("--global-budget", type=float, default=500000.0, help="Global energy budget (J)")
    parser.add_argument("--edge-budget", type=float, default=8000.0, help="Edge energy budget specifically (J)")
    parser.add_argument("--simulator-cmd", type=str, required=True, help="Path to simulator executable")
    parser.add_argument("--simulator-dir", type=str, default=".", help="Directory of simulator")
    args = parser.parse_args()

    # Absolute path to prevent directory change issues
    simulator_cmd_abs = os.path.abspath(args.simulator_cmd)

    # Define dynamic inputs
    tasks = [
        {"name": "stage1", "desc": "preprocessing", "service_time": 1.2, "requires_nfr": True, "b_fs": 100},
        {"name": "stage2", "desc": "inference", "service_time": 3.5, "requires_nfr": False, "b_fs": 100},
        {"name": "stage3", "desc": "archiving", "service_time": 0.8, "requires_nfr": True, "b_fs": 100}
    ]

    machines = [
        {"name": "Edge", "max_power": 8.0, "static_power_percent": 0.35, "power_model": "linear", "hardware_profile": "toge"},
        {"name": "Fog", "max_power": 45.0, "static_power_percent": 0.40, "power_model": "linear", "hardware_profile": "dantelap"},
        {"name": "Cloud", "max_power": 350.0, "static_power_percent": 0.60, "power_model": "linear", "hardware_profile": "c3"}
    ]

    links = [
        {"from": "Edge", "to": "Fog", "b_net": 10 * 1048576, "latency_ms": 15},
        {"from": "Fog", "to": "Cloud", "b_net": 100 * 1048576, "latency_ms": 5},
        {"from": "Edge", "to": "Cloud", "b_net": 5 * 1048576, "latency_ms": 45}
    ]

    nfr_options = [
        {"compression": "LZ4", "encryption": "AES"},
        {"compression": "ZSTD", "encryption": "CHACHA20"},
        {"compression": "BZ2", "encryption": "AES"}
    ]

    candidates = generate_dynamic_candidates(tasks, machines, nfr_options)
    
    best_candidate = None
    best_c_max = float('inf')
    best_config_file = None
    best_energy_metrics = {}
    best_total_energy = 0.0

    print(f"Orchestrating {len(candidates)} candidate deployments across the continuum...")
    print("-" * 60)

    for i, candidate in enumerate(candidates):
        config_file = os.path.join(args.simulator_dir, f"dynamic_candidate_{i}.json")
        create_dynamic_config_json(candidate, tasks, machines, links, config_file)
        
        success = run_simulator(simulator_cmd_abs, config_file, args.simulator_dir)
        if not success:
            continue
            
        energy_metrics, total_energy, c_max = extract_metrics(args.simulator_dir, ["Edge"])
        edge_energy = energy_metrics.get("Edge", 0.0)

        semantic_placement = {next(t["desc"] for t in tasks if t["name"] == k): v for k, v in candidate['placement'].items()}
        print(f"Candidate {i} | Placement: {semantic_placement} | NFR: {candidate['nfr_config']}")
        print(f"  Edge Energy: {edge_energy:.2f} J | Total Energy: {total_energy:.2f} J | Makespan: {c_max:.2f} s")

        # Dynamic multi-constraint validation
        is_viable = (edge_energy <= args.edge_budget) and (total_energy <= args.global_budget)

        if is_viable:
            print("  Status: ✅ Accepted")
            if c_max < best_c_max:
                best_c_max = c_max
                best_candidate = candidate
                best_config_file = config_file
                best_energy_metrics = energy_metrics
                best_total_energy = total_energy
        else:
            reason = []
            if edge_energy > args.edge_budget:
                reason.append(f"Edge Battery Blown (+{edge_energy - args.edge_budget:.1f}J)")
            if total_energy > args.global_budget:
                reason.append(f"Global Budget Blown (+{total_energy - args.global_budget:.1f}J)")
            print(f"  Status: ❌ Discarded ({', '.join(reason)})")
        print("-" * 60)

    print("\n=== Optimal Continuum Deployment Found ===")
    if best_candidate:
        semantic_best = {next(t["desc"] for t in tasks if t["name"] == k): v for k, v in best_candidate['placement'].items()}
        print(f"  Configuration File: {best_config_file}")
        print(f"  Scheduled Placement: {semantic_best}")
        print(f"  Selected NFR Pipeline: {best_candidate['nfr_config']}")
        print(f"  Edge Energy Footprint: {best_energy_metrics.get('Edge', 0.0):.2f} J (Limit: {args.edge_budget} J)")
        print(f"  Total Continuum Energy: {best_total_energy:.2f} J (Limit: {args.global_budget} J)")
        print(f"  Optimal Makespan (C_max): {best_c_max:.2f} s")
    else:
        print("  Status: Failure. No viable configurations met the combined constraints.")

if __name__ == "__main__":
    main()