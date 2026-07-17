import json
import subprocess
import os
import csv
import argparse
import itertools

# Constants
ENERGY_CSV = "results/energy_by_machine.csv"
STAGE_CSV = "results/stage_totals_by_workers.csv"

def plot_pareto_frontier(candidates_data):
    """
    Generates a 3D scatter plot of Makespan vs Total Energy vs Edge Energy.
    Highlights the Pareto optimal configurations.
    """
    try:
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D
    except ImportError:
        print("matplotlib is required for plotting. Install it using: pip install matplotlib")
        return

    # Filter for valid data
    data = [c for c in candidates_data if c["makespan"] > 0 and c["total_energy"] > 0]
    if not data:
        print("No valid data to plot.")
        return
    
    # Calculate Pareto front (minimizing all 3 objectives)
    pareto_front = []
    for i, c1 in enumerate(data):
        is_pareto = True
        for j, c2 in enumerate(data):
            if i == j:
                continue
            if (c2["makespan"] <= c1["makespan"] and c2["total_energy"] <= c1["total_energy"] and c2["edge_energy"] <= c1["edge_energy"]) and \
               (c2["makespan"] < c1["makespan"] or c2["total_energy"] < c1["total_energy"] or c2["edge_energy"] < c1["edge_energy"]):
                is_pareto = False
                break
        if is_pareto:
            pareto_front.append(c1)
            
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')
    
    # Separate viable and non-viable for different marker styles
    v_data = [c for c in data if c["viable"]]
    nv_data = [c for c in data if not c["viable"]]

    if nv_data:
        ax.scatter([c["makespan"] for c in nv_data], [c["total_energy"] for c in nv_data], [c["edge_energy"] for c in nv_data], 
                   c='red', marker='x', alpha=0.3, label='Discarded (Budget Exceeded)')
    
    if v_data:
        sc = ax.scatter([c["makespan"] for c in v_data], [c["total_energy"] for c in v_data], [c["edge_energy"] for c in v_data], 
                        c=[c["edge_energy"] for c in v_data], cmap='viridis', marker='o', s=50, label='Viable Candidates')
        plt.colorbar(sc, ax=ax, label='Edge Energy (J)', pad=0.1)
        
    # Plot Pareto Front Highlight
    if pareto_front:
        ax.scatter([c["makespan"] for c in pareto_front], [c["total_energy"] for c in pareto_front], [c["edge_energy"] for c in pareto_front], 
                   c='none', edgecolors='black', s=120, linewidth=2, label='Pareto Optimal')

    ax.set_xlabel('Makespan ($C_{max}$) (s)')
    ax.set_ylabel('Total Energy (J)')
    ax.set_zlabel('Edge Energy (J)')
    ax.set_title('Continuum Deployment Trade-offs')
    ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1))
    
    plt.tight_layout()
    output_file = 'pareto_frontier.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"\nPlot saved to {output_file}")

def plot_additional_analytics(candidates_data):
    """
    Generates additional analytics plots: 2D Tradeoffs, Placement Distribution, and NFR Distribution.
    """
    try:
        import matplotlib.pyplot as plt
        import collections
    except ImportError:
        return

    v_data = [c for c in candidates_data if c["viable"]]
    if not v_data:
        print("No viable candidates for additional analytics.")
        return

    # 1. 2D Trade-off (Makespan vs Total Energy)
    fig, ax = plt.subplots(figsize=(8, 6))
    makespans = [c["makespan"] for c in v_data]
    energies = [c["total_energy"] for c in v_data]
    edge_energies = [c["edge_energy"] for c in v_data]
    
    sc = ax.scatter(makespans, energies, c=edge_energies, cmap='coolwarm', s=60, edgecolors='k')
    plt.colorbar(sc, ax=ax, label='Edge Energy (J)')
    
    ax.set_xlabel('Makespan ($C_{max}$) (s)')
    ax.set_ylabel('Total Energy (J)')
    ax.set_title('Viable Deployments: Makespan vs. Total Energy')
    ax.grid(True, linestyle='--', alpha=0.6)
    
    plt.tight_layout()
    plt.savefig('tradeoff_2d.png', dpi=300)
    print("Saved plot to tradeoff_2d.png")
    
    # 2. Placement Distribution Bar Chart
    tasks = list(v_data[0]["placement"].keys())
    machines = ["Edge", "Fog", "Cloud"]
    
    counts = {t: {m: 0 for m in machines} for t in tasks}
    for c in v_data:
        for t, m in c["placement"].items():
            if m in counts[t]:
                counts[t][m] += 1
            
    fig, ax = plt.subplots(figsize=(10, 6))
    x = range(len(tasks))
    width = 0.25
    
    for i, m in enumerate(machines):
        y = [counts[t][m] for t in tasks]
        ax.bar([pos + i*width for pos in x], y, width, label=m)
        
    ax.set_xticks([pos + width for pos in x])
    ax.set_xticklabels(tasks)
    ax.set_ylabel('Number of Viable Configurations')
    ax.set_title('Task Placement Distribution Across Viable Candidates')
    ax.legend()
    
    plt.tight_layout()
    plt.savefig('placement_distribution.png', dpi=300)
    print("Saved plot to placement_distribution.png")
    
    # 3. NFR combination distribution
    nfr_counts = collections.Counter()
    for c in v_data:
        nfr_str = f"{c['nfr_config'].get('compression', 'None')} + {c['nfr_config'].get('encryption', 'None')}"
        nfr_counts[nfr_str] += 1
        
    fig, ax = plt.subplots(figsize=(8, 6))
    labels = list(nfr_counts.keys())
    vals = list(nfr_counts.values())
    ax.bar(labels, vals, color='teal', edgecolor='black')
    ax.set_ylabel('Frequency in Viable Candidates')
    ax.set_title('NFR Pipeline Distribution')
    plt.xticks(rotation=45, ha='right')
    
    plt.tight_layout()
    plt.savefig('nfr_distribution.png', dpi=300)
    print("Saved plot to nfr_distribution.png")

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
    parser.add_argument("--plot", action="store_true", help="Plot a 3D Pareto frontier at the end of the simulation")
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
    
    plot_data = []

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
        
        plot_data.append({
            "makespan": c_max,
            "total_energy": total_energy,
            "edge_energy": edge_energy,
            "viable": is_viable,
            "label": f"Candidate {i}",
            "placement": semantic_placement,
            "nfr_config": candidate['nfr_config']
        })

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
        
    if args.plot:
        print("\nGenerating Pareto frontier plot and additional analytics...")
        plot_pareto_frontier(plot_data)
        plot_additional_analytics(plot_data)

if __name__ == "__main__":
    main()