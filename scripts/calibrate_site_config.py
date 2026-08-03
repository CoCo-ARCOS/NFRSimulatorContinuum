#!/usr/bin/env python3
import json
import csv
import re
from pathlib import Path

# Standard defaults for missing power and network data
# Based on machine_dataset_summary.csv and typical values.
MACHINE_POWER_PROFILES = {
    "toge": {"max_power_w": 12.0, "static_power_fraction": 0.25}, # Raspberry Pi 5
    "dantelap": {"max_power_w": 150.0, "static_power_fraction": 0.15}, # Laptop with GPU
    "dianalap": {"max_power_w": 65.0, "static_power_fraction": 0.20}, # Standard Laptop
    "c3": {"max_power_w": 450.0, "static_power_fraction": 0.40} # HPC node
}

# 1 Gbps default for edge/fog, 10 Gbps default for HPC links
DEFAULT_LINK_BANDWIDTH_BPS = 125 * 1024 * 1024 # 1 Gigabit -> ~125MB/s
DEFAULT_LINK_LATENCY_S = 0.010
DEFAULT_LINK_ENERGY_PER_BYTE_J = 4e-9

def extract_machine_data(real_values_dir: Path):
    metrics = {
        "read_bandwidth_Bps": 0.0,
        "write_bandwidth_Bps": 0.0,
        "speed_metric": 0.0,  # lower is faster
        "ratios": {}
    }
    
    if not real_values_dir.exists():
        return metrics

    # Extract compression ratios from cost-efficiency.csv
    cost_eff = real_values_dir / "cost-efficiency.csv"
    if cost_eff.exists():
        ratios = {}
        with cost_eff.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                algo = row['algoritmo']
                if algo in ['LZ4', 'ZSTD', 'ZLIB']:
                    ratio_str = row['avg_ratio'].replace('x', '')
                    ratios.setdefault(algo, []).append(float(ratio_str))
        for algo, vals in ratios.items():
            metrics['ratios'][algo] = sum(vals) / len(vals)

    # Compute IO bandwidths across all CSVs for 1000MB payloads
    read_bw_vals = []
    write_bw_vals = []
    # For speed metric, we'll use avg_comp_s for AES, 1000MB
    speed_metric = None

    for csv_file in ["cost-efficiency.csv", "confidentiality.csv", "integrity.csv", "reliability.csv"]:
        fpath = real_values_dir / csv_file
        if not fpath.exists(): continue
        with fpath.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                size_mb = float(row['size_mb'])
                if size_mb == 1000.0:
                    bytes_size = 1000 * 1024 * 1024
                    avg_io_read = float(row.get('avg_io_read_s', 0.0))
                    avg_io_write = float(row.get('avg_io_write_s', 0.0))
                    if avg_io_read > 0:
                        read_bw_vals.append(bytes_size / avg_io_read)
                    if avg_io_write > 0:
                        write_bw_vals.append(bytes_size / avg_io_write)
                
                # Speed metric
                if row.get('algoritmo') == 'AES' and float(row['size_mb']) == 1000.0:
                    speed_metric = float(row['avg_comp_s'])

    if read_bw_vals:
        metrics['read_bandwidth_Bps'] = sum(read_bw_vals) / len(read_bw_vals)
    if write_bw_vals:
        metrics['write_bandwidth_Bps'] = sum(write_bw_vals) / len(write_bw_vals)
    if speed_metric is not None:
        metrics['speed_metric'] = speed_metric

    return metrics


def main():
    config_path = Path("/home/domizzi/Documents/GitHub/energynfrscontinuum/NFRSimulatorContinuum/configs/site.calibrated.measured.json")
    with config_path.open() as f:
        config = json.load(f)

    # Step 1: Collect data for each distinct real_values_dir
    machine_metrics = {}
    for m in config['infrastructure']['machines']:
        rdir = Path(m['real_values_dir'])
        machine_id = rdir.parent.name # e.g. 'toge', 'c3'
        if machine_id not in machine_metrics:
            machine_metrics[machine_id] = extract_machine_data(rdir)

    # Step 2: Determine speed factors based on 'toge' (slowest)
    base_speed = machine_metrics.get('toge', {}).get('speed_metric', 1.0)
    if base_speed == 0.0:
        base_speed = 1.0

    # Step 3: Update machines in config
    for m in config['infrastructure']['machines']:
        rdir = Path(m['real_values_dir'])
        machine_id = rdir.parent.name
        data = machine_metrics.get(machine_id, {})

        m['read_bandwidth_Bps'] = round(data.get('read_bandwidth_Bps', 0.0), 2)
        m['write_bandwidth_Bps'] = round(data.get('write_bandwidth_Bps', 0.0), 2)
        
        sm = data.get('speed_metric', base_speed)
        # speed_factor: baseline_time / machine_time
        # toge (baseline) = base_speed / base_speed = 1.0
        # fast machine = base_speed / fast_time = > 1.0
        if sm > 0:
            m['speed_factor'] = round(base_speed / sm, 3)
        else:
            m['speed_factor'] = 1.0

        pdata = MACHINE_POWER_PROFILES.get(machine_id, {"max_power_w": 100.0, "static_power_fraction": 0.2})
        m['max_power_w'] = pdata['max_power_w']
        m['static_power_fraction'] = pdata['static_power_fraction']
        # power model stays linear

    # Step 4: Update mechanism ratios
    ratios_agg = {'LZ4': [], 'ZSTD': [], 'ZLIB': []}
    for m_id, data in machine_metrics.items():
        for algo, ratio in data.get('ratios', {}).items():
            if algo in ratios_agg:
                ratios_agg[algo].append(ratio)
    
    for algo in ['LZ4', 'ZSTD', 'ZLIB']:
        if algo in config['mechanism_models'] and ratios_agg[algo]:
            avg_r = sum(ratios_agg[algo]) / len(ratios_agg[algo])
            config['mechanism_models'][algo]['ratio'] = round(avg_r, 3)

    # Step 5: Fill missing link data with defaults
    for l in config['infrastructure']['links']:
        l['bandwidth_Bps'] = DEFAULT_LINK_BANDWIDTH_BPS
        l['latency_s'] = DEFAULT_LINK_LATENCY_S
        l['energy_per_byte_j'] = DEFAULT_LINK_ENERGY_PER_BYTE_J

    # Write out calibrated config
    with config_path.open('w') as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"Updated {config_path}")

if __name__ == "__main__":
    main()
