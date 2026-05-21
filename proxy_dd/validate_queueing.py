#!/usr/bin/env python3
import argparse
import csv
import heapq
import math
import random
import subprocess
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


DEFAULT_MGCC_PROFILE_SPECS = [
    {
        "name": "zlib_compress",
        "source_file": "cost-efficiency.csv",
        "algorithm": "ZLIB",
        "time_column": "avg_comp_s",
    },
    {
        "name": "sha256_hash",
        "source_file": "integrity.csv",
        "algorithm": "SHA256",
        "time_column": "avg_time_seconds",
    },
    {
        "name": "rs84_encode",
        "source_file": "reliability.csv",
        "algorithm": "RS",
        "k": 8,
        "m": 4,
        "time_column": "avg_encoding_s",
    },
]


DEFAULT_SCENARIOS = [
    {"name": "mm1_rho_0.2", "model": "mm1", "lambda": 1.0, "mu": 5.0, "servers": 1},
    {"name": "mm1_rho_0.4", "model": "mm1", "lambda": 2.0, "mu": 5.0, "servers": 1},
    {"name": "mm1_rho_0.6", "model": "mm1", "lambda": 3.0, "mu": 5.0, "servers": 1},
    {"name": "mm1_rho_0.8", "model": "mm1", "lambda": 4.0, "mu": 5.0, "servers": 1},
    {"name": "mm1_rho_0.9", "model": "mm1", "lambda": 4.5, "mu": 5.0, "servers": 1},

    {"name": "md1_rho_0.2", "model": "md1", "lambda": 1.0, "mu": 5.0, "servers": 1},
    {"name": "md1_rho_0.4", "model": "md1", "lambda": 2.0, "mu": 5.0, "servers": 1},
    {"name": "md1_rho_0.6", "model": "md1", "lambda": 3.0, "mu": 5.0, "servers": 1},
    {"name": "md1_rho_0.8", "model": "md1", "lambda": 4.0, "mu": 5.0, "servers": 1},
    {"name": "md1_rho_0.9", "model": "md1", "lambda": 4.5, "mu": 5.0, "servers": 1},

    {"name": "mmc_c2_rho_0.4", "model": "mmc", "lambda": 4.0, "mu": 5.0, "servers": 2},
    {"name": "mmc_c2_rho_0.6", "model": "mmc", "lambda": 6.0, "mu": 5.0, "servers": 2},
    {"name": "mmc_c2_rho_0.8", "model": "mmc", "lambda": 8.0, "mu": 5.0, "servers": 2},
    {"name": "mmc_c2_rho_0.9", "model": "mmc", "lambda": 9.0, "mu": 5.0, "servers": 2},
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate queueing behavior against analytical baselines and a controlled reference simulator."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=50000,
        help="Number of jobs to simulate in the controlled reference implementation.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=5000,
        help="Number of initial jobs to discard for warm-up.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=12345,
        help="Base random seed for the controlled reference simulation.",
    )
    parser.add_argument(
        "--use-simulator",
        action="store_true",
        help="Also compare against the queue estimator used by the simulator.",
    )
    parser.add_argument(
        "--simulator-container",
        default=None,
        help="Docker container name that exposes ./single, for example application_agent0.",
    )
    parser.add_argument(
        "--container-platform",
        choices=("docker", "apptainer", "singularity"),
        default="docker",
        help="Container runtime used for --use-simulator. Docker uses --simulator-container; Apptainer/Singularity use --simulator-image.",
    )
    parser.add_argument(
        "--simulator-image",
        default="../stages/single_queue.sif",
        help="Apptainer/Singularity SIF used by the queue estimator.",
    )
    parser.add_argument(
        "--simulator-samples",
        type=int,
        default=50000,
        help="Number of samples to send to the simulator queue estimator.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("results/queueing_validation_summary.csv"),
        help="CSV path for per-scenario results.",
    )
    parser.add_argument(
        "--overall-out",
        type=Path,
        default=Path("results/queueing_validation_overall.csv"),
        help="CSV path for aggregated error metrics.",
    )
    parser.add_argument(
        "--tables-out",
        type=Path,
        default=Path("results/queueing_validation_tables.csv"),
        help="CSV path for paper-friendly validation tables.",
    )
    parser.add_argument(
        "--plots-dir",
        type=Path,
        default=Path("results/queueing_validation_plots"),
        help="Directory for generated plots.",
    )
    parser.add_argument(
        "--real-values-dir",
        type=Path,
        default=Path("real_values"),
        help="Directory containing real measurement CSVs used to build M/G/c service profiles.",
    )
    parser.add_argument(
        "--hardware-profile",
        choices=("c3", "toge", "dianalap"),
        default=None,
        help="Shortcut for results_different_machines/organized/<profile>/real_values when building M/G/c profiles.",
    )
    parser.add_argument(
        "--machine-datasets-root",
        type=Path,
        default=Path("results_different_machines/organized"),
        help="Root directory containing organized machine datasets.",
    )
    return parser.parse_args()


def resolve_real_values_dir(repo_dir, real_values_dir, hardware_profile=None, machine_datasets_root=None):
    if hardware_profile:
        root = machine_datasets_root or Path("results_different_machines/organized")
        root = (repo_dir / root).resolve() if not root.is_absolute() else root.resolve()
        return (root / hardware_profile / "real_values").resolve()
    return (repo_dir / real_values_dir).resolve() if not real_values_dir.is_absolute() else real_values_dir.resolve()


def load_mgcc_profiles(real_values_dir):
    profiles = {}
    for spec in DEFAULT_MGCC_PROFILE_SPECS:
        path = real_values_dir / spec["source_file"]
        service_times = []
        with path.open(newline="", encoding="utf-8") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                if row["algoritmo"].strip() != spec["algorithm"]:
                    continue
                if "k" in spec and int(row["k_datos"]) != spec["k"]:
                    continue
                if "m" in spec and int(row["m_paridad"]) != spec["m"]:
                    continue
                service_times.append(float(row[spec["time_column"]]))
        if not service_times:
            continue
        profiles[spec["name"]] = {
            "name": spec["name"],
            "service_times": service_times,
            "mean_service_time": sum(service_times) / len(service_times),
        }
    return profiles


def build_mgcc_scenarios(real_values_dir):
    profiles = load_mgcc_profiles(real_values_dir)
    scenarios = []
    for profile_name, profile in profiles.items():
        for rho in (0.4, 0.7, 0.85):
            servers = 2
            lambda_rate = rho * servers / profile["mean_service_time"]
            scenarios.append(
                {
                    "name": f"mgc_c{servers}_{profile_name}_rho_{rho:.2f}",
                    "model": "mgc",
                    "lambda": lambda_rate,
                    "servers": servers,
                    "service_profile": profile,
                }
            )
    return scenarios


def theoretical_mm1(lambda_rate, mu_rate):
    rho = lambda_rate / mu_rate
    w = 1.0 / (mu_rate - lambda_rate)
    wq = rho / (mu_rate - lambda_rate)
    return {"rho": rho, "waiting_time": wq, "response_time": w}


def theoretical_md1(lambda_rate, mu_rate):
    service_time = 1.0 / mu_rate
    rho = lambda_rate * service_time
    wq = (rho * service_time) / (2.0 * (1.0 - rho))
    w = wq + service_time
    return {"rho": rho, "waiting_time": wq, "response_time": w}


def theoretical_mmc(lambda_rate, mu_rate, servers):
    if servers <= 0:
        raise ValueError("servers must be positive")
    a = lambda_rate / mu_rate
    rho = lambda_rate / (servers * mu_rate)
    if rho >= 1.0:
        raise ValueError("M/M/c requires rho < 1 for a stable system")

    series_sum = sum((a ** n) / math.factorial(n) for n in range(servers))
    tail = ((a ** servers) / math.factorial(servers)) * (1.0 / (1.0 - rho))
    p0 = 1.0 / (series_sum + tail)
    erlang_c = tail * p0
    wq = erlang_c / (servers * mu_rate - lambda_rate)
    w = wq + (1.0 / mu_rate)
    return {"rho": rho, "waiting_time": wq, "response_time": w}


def baseline_mgc_approx(lambda_rate, service_times, servers):
    if not service_times:
        raise ValueError("service_times cannot be empty")
    mean_service = sum(service_times) / len(service_times)
    mean_sq = sum(value * value for value in service_times) / len(service_times)
    variance = max(0.0, mean_sq - mean_service * mean_service)
    cs2 = variance / (mean_service * mean_service) if mean_service > 0 else 0.0
    mmc = theoretical_mmc(lambda_rate, 1.0 / mean_service, servers)
    factor = (1.0 + cs2) / 2.0
    wq = factor * mmc["waiting_time"]
    w = wq + mean_service
    return {
        "rho": lambda_rate * mean_service / servers,
        "waiting_time": wq,
        "response_time": w,
        "cs2": cs2,
    }


def theoretical_metrics(model, lambda_rate, mu_rate, servers=1):
    if model == "mm1":
        return theoretical_mm1(lambda_rate, mu_rate)
    if model == "md1":
        return theoretical_md1(lambda_rate, mu_rate)
    if model == "mmc":
        return theoretical_mmc(lambda_rate, mu_rate, servers)
    raise ValueError(f"Unsupported model: {model}")


def simulate_queue(model, lambda_rate, mu_rate, samples, warmup, seed, servers=1, service_profile=None):
    rng = random.Random(seed)
    service_time_mean = 1.0 / mu_rate
    current_time = 0.0
    waiting_times = []
    response_times = []
    service_times = []
    server_heap = [0.0] * max(1, servers)

    for job_id in range(samples):
        current_time += rng.expovariate(lambda_rate)
        if model in ("mm1", "mmc"):
            service_time = rng.expovariate(mu_rate)
        elif model == "md1":
            service_time = service_time_mean
        elif model == "mgc":
            if not service_profile or not service_profile["service_times"]:
                raise ValueError("mgc requires a non-empty service profile")
            service_time = rng.choice(service_profile["service_times"])
        else:
            raise ValueError(f"Unsupported model: {model}")

        next_available = heapq.heappop(server_heap)
        service_start = max(current_time, next_available)
        completion_time = service_start + service_time
        waiting_time = service_start - current_time
        response_time = completion_time - current_time
        heapq.heappush(server_heap, completion_time)

        if job_id >= warmup:
            waiting_times.append(waiting_time)
            response_times.append(response_time)
            service_times.append(service_time)

    observed_jobs = len(waiting_times)
    makespan = max(server_heap) - min(current_time, 0.0)
    utilization = sum(service_times) / (servers * makespan) if observed_jobs > 0 and makespan > 0 else 0.0
    return {
        "jobs": observed_jobs,
        "waiting_time": sum(waiting_times) / observed_jobs if observed_jobs else 0.0,
        "response_time": sum(response_times) / observed_jobs if observed_jobs else 0.0,
        "utilization_estimate": utilization,
    }


def run_simulator_queue_estimator(container_name, lambda_rate, mu_rate, samples, container_platform="docker", simulator_image=None):
    mean_interarrival = 1.0 / lambda_rate
    mean_service = 1.0 / mu_rate
    if container_platform == "docker":
        if not container_name:
            raise ValueError("simulator container name is required for docker")
        command = [
            "docker",
            "exec",
            container_name,
            "./single",
            f"{mean_interarrival}",
            f"{mean_service}",
            f"{samples}",
        ]
    else:
        if not simulator_image:
            raise ValueError("simulator image/SIF is required for apptainer")
        command = [
            container_platform,
            "run",
            simulator_image,
            f"{mean_interarrival}",
            f"{mean_service}",
            f"{samples}",
        ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)

    print(f"Simulator output for lambda={lambda_rate}, mu={mu_rate}:\n{result.stdout}")

    avg_delay = None
    simulation_time = None
    avg_queue = None
    utilization = None
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 4:
            avg_delay = float(parts[0])
            avg_queue = float(parts[1])
            utilization = float(parts[2])
            simulation_time = float(parts[3])

    if avg_delay is None:
        raise RuntimeError(f"Could not parse simulator output:\n{result.stdout}")

    return {
        "waiting_time": avg_delay,
        "response_time": avg_delay + mean_service,
        "simulation_time": simulation_time,
        "avg_queue": avg_queue,
        "utilization": utilization,
    }


def abs_pct_error(actual, estimate):
    if actual == 0:
        return 0.0
    return abs(estimate - actual) / actual * 100.0


def collect_results(args):
    rows = []
    simulator_enabled = args.use_simulator and (
        bool(args.simulator_container) if args.container_platform == "docker" else bool(args.simulator_image)
    )
    scenarios = list(DEFAULT_SCENARIOS)
    scenarios.extend(build_mgcc_scenarios(args.real_values_dir))

    for index, scenario in enumerate(scenarios):
        model = scenario["model"]
        lambda_rate = scenario["lambda"]
        servers = scenario.get("servers", 1)
        service_profile = scenario.get("service_profile")

        if model == "mgc":
            mu_rate = 1.0 / service_profile["mean_service_time"]
            theory = baseline_mgc_approx(lambda_rate, service_profile["service_times"], servers)
            baseline_type = "allen-cunneen-approx"
        else:
            mu_rate = scenario["mu"]
            theory = theoretical_metrics(model, lambda_rate, mu_rate, servers)
            baseline_type = "theoretical"

        reference = simulate_queue(model, lambda_rate, mu_rate, args.samples, args.warmup, args.seed + index, servers, service_profile)

        row = {
            "scenario": scenario["name"],
            "model": model,
            "servers": servers,
            "profile_name": service_profile["name"] if service_profile else "",
            "lambda_rate": lambda_rate,
            "mu_rate": mu_rate,
            "rho": theory["rho"],
            "baseline_type": baseline_type,
            "theoretical_waiting_time": theory["waiting_time"],
            "theoretical_response_time": theory["response_time"],
            "reference_waiting_time": reference["waiting_time"],
            "reference_response_time": reference["response_time"],
            "reference_waiting_error_percent": abs_pct_error(theory["waiting_time"], reference["waiting_time"]),
            "reference_response_error_percent": abs_pct_error(theory["response_time"], reference["response_time"]),
        }

        if simulator_enabled and model == "mm1":
            try:
                simulator = run_simulator_queue_estimator(
                    args.simulator_container,
                    lambda_rate,
                    mu_rate,
                    args.simulator_samples,
                    args.container_platform,
                    args.simulator_image,
                )
                row["simulator_waiting_time"] = simulator["waiting_time"]
                row["simulator_response_time"] = simulator["response_time"]
                row["simulator_waiting_error_percent"] = abs_pct_error(theory["waiting_time"], simulator["waiting_time"])
                row["simulator_response_error_percent"] = abs_pct_error(theory["response_time"], simulator["response_time"])
            except Exception as exc:
                row["simulator_waiting_time"] = ""
                row["simulator_response_time"] = ""
                row["simulator_waiting_error_percent"] = ""
                row["simulator_response_error_percent"] = ""
                row["simulator_error"] = str(exc)
        else:
            row["simulator_waiting_time"] = ""
            row["simulator_response_time"] = ""
            row["simulator_waiting_error_percent"] = ""
            row["simulator_response_error_percent"] = ""

        rows.append(row)

    return rows


def mean(values):
    filtered = [value for value in values if value != ""]
    return sum(filtered) / len(filtered) if filtered else 0.0


def write_summary_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario",
        "model",
        "servers",
        "profile_name",
        "lambda_rate",
        "mu_rate",
        "rho",
        "baseline_type",
        "theoretical_waiting_time",
        "theoretical_response_time",
        "reference_waiting_time",
        "reference_response_time",
        "reference_waiting_error_percent",
        "reference_response_error_percent",
        "simulator_waiting_time",
        "simulator_response_time",
        "simulator_waiting_error_percent",
        "simulator_response_error_percent",
        "simulator_error",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_validation_tables(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "validation_scope",
        "scenario",
        "model",
        "servers",
        "rho",
        "baseline_type",
        "theoretical_waiting_time",
        "theoretical_response_time",
        "observed_waiting_time",
        "observed_response_time",
        "waiting_error_percent",
        "response_error_percent",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "validation_scope": "reference",
                    "scenario": row["scenario"],
                    "model": row["model"],
                    "servers": row["servers"],
                    "rho": row["rho"],
                    "baseline_type": row["baseline_type"],
                    "theoretical_waiting_time": row["theoretical_waiting_time"],
                    "theoretical_response_time": row["theoretical_response_time"],
                    "observed_waiting_time": row["reference_waiting_time"],
                    "observed_response_time": row["reference_response_time"],
                    "waiting_error_percent": row["reference_waiting_error_percent"],
                    "response_error_percent": row["reference_response_error_percent"],
                }
            )

        for row in rows:
            if row["simulator_waiting_time"] == "":
                continue
            writer.writerow(
                {
                    "validation_scope": "simulator",
                    "scenario": row["scenario"],
                    "model": row["model"],
                    "servers": row["servers"],
                    "rho": row["rho"],
                    "baseline_type": row["baseline_type"],
                    "theoretical_waiting_time": row["theoretical_waiting_time"],
                    "theoretical_response_time": row["theoretical_response_time"],
                    "observed_waiting_time": row["simulator_waiting_time"],
                    "observed_response_time": row["simulator_response_time"],
                    "waiting_error_percent": row["simulator_waiting_error_percent"],
                    "response_error_percent": row["simulator_response_error_percent"],
                }
            )


def write_overall_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    summaries = []
    by_model = defaultdict(list)
    for row in rows:
        by_model[row["model"]].append(row)

    for model, model_rows in sorted(by_model.items()):
        summaries.append(
            {
                "scope": f"reference:{model}",
                "mean_waiting_error_percent": mean([row["reference_waiting_error_percent"] for row in model_rows]),
                "mean_response_error_percent": mean([row["reference_response_error_percent"] for row in model_rows]),
            }
        )
        simulator_wait = [row["simulator_waiting_error_percent"] for row in model_rows if row["simulator_waiting_error_percent"] != ""]
        simulator_resp = [row["simulator_response_error_percent"] for row in model_rows if row["simulator_response_error_percent"] != ""]
        if simulator_wait and simulator_resp:
            summaries.append(
                {
                    "scope": f"simulator:{model}",
                    "mean_waiting_error_percent": mean(simulator_wait),
                    "mean_response_error_percent": mean(simulator_resp),
                }
            )

    summaries.append(
        {
            "scope": "reference:overall",
            "mean_waiting_error_percent": mean([row["reference_waiting_error_percent"] for row in rows]),
            "mean_response_error_percent": mean([row["reference_response_error_percent"] for row in rows]),
        }
    )
    simulator_wait = [row["simulator_waiting_error_percent"] for row in rows if row["simulator_waiting_error_percent"] != ""]
    simulator_resp = [row["simulator_response_error_percent"] for row in rows if row["simulator_response_error_percent"] != ""]
    if simulator_wait and simulator_resp:
        summaries.append(
            {
                "scope": "simulator:overall",
                "mean_waiting_error_percent": mean(simulator_wait),
                "mean_response_error_percent": mean(simulator_resp),
            }
        )

    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["scope", "mean_waiting_error_percent", "mean_response_error_percent"])
        writer.writeheader()
        writer.writerows(summaries)


def save_figure(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_waiting_time_vs_load(rows, plots_dir):
    plots_dir.mkdir(parents=True, exist_ok=True)
    models = ["mm1", "md1", "mmc", "mgc"]
    fig, axes = plt.subplots(1, len(models), figsize=(5.5 * len(models), 4.5), sharey=False)
    if len(models) == 1:
        axes = [axes]
    for ax, model in zip(axes, models):
        model_rows = sorted([row for row in rows if row["model"] == model], key=lambda row: row["rho"])
        if model == "mgc":
            by_profile = defaultdict(list)
            for row in model_rows:
                by_profile[row["profile_name"]].append(row)
            for profile_name, profile_rows in sorted(by_profile.items()):
                profile_rows = sorted(profile_rows, key=lambda row: row["rho"])
                ax.plot(
                    [row["rho"] for row in profile_rows],
                    [row["theoretical_waiting_time"] for row in profile_rows],
                    marker="o",
                    label=f"{profile_name} approximation",
                )
                ax.plot(
                    [row["rho"] for row in profile_rows],
                    [row["reference_waiting_time"] for row in profile_rows],
                    marker="s",
                    label=f"{profile_name} reference",
                )
        else:
            rho = [row["rho"] for row in model_rows]
            theory = [row["theoretical_waiting_time"] for row in model_rows]
            reference = [row["reference_waiting_time"] for row in model_rows]
            ax.plot(rho, theory, marker="o", label="theoretical")
            ax.plot(rho, reference, marker="s", label="reference simulation")
            simulator_rows = [row for row in model_rows if row["simulator_waiting_time"] != ""]
            if simulator_rows:
                ax.plot(
                    [row["rho"] for row in simulator_rows],
                    [row["simulator_waiting_time"] for row in simulator_rows],
                    marker="^",
                    label="simulator",
                )
        title = model.upper()
        if model == "mmc":
            server_set = sorted({row["servers"] for row in model_rows})
            if len(server_set) == 1:
                title = f"MMC (c={server_set[0]})"
        if model == "mgc":
            server_set = sorted({row["servers"] for row in model_rows})
            if len(server_set) == 1:
                title = f"MGC (c={server_set[0]})"
        ax.set_title(title)
        ax.set_xlabel("Utilization rho")
        ax.set_ylabel("Average waiting time Wq (s)")
        ax.grid(True, alpha=0.25)
        ax.legend()

    fig.suptitle("Queueing Validation: Waiting Time vs Load")
    save_figure(fig, plots_dir / "waiting_time_vs_load.png")


def plot_simulator_waiting_time_vs_load(rows, plots_dir):
    simulator_rows = sorted(
        [row for row in rows if row["model"] == "mm1" and row["simulator_waiting_time"] != ""],
        key=lambda row: row["rho"],
    )
    if not simulator_rows:
        return

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.plot(
        [row["rho"] for row in simulator_rows],
        [row["theoretical_waiting_time"] for row in simulator_rows],
        marker="o",
        label="theoretical",
    )
    ax.plot(
        [row["rho"] for row in simulator_rows],
        [row["simulator_waiting_time"] for row in simulator_rows],
        marker="^",
        label="simulator",
    )
    ax.set_title("Simulator Queue Validation (MM1): Waiting Time")
    ax.set_xlabel("Utilization rho")
    ax.set_ylabel("Average waiting time Wq (s)")
    ax.grid(True, alpha=0.25)
    ax.legend()
    save_figure(fig, plots_dir / "simulator_waiting_time_vs_load_mm1.png")


def plot_response_time_vs_load(rows, plots_dir):
    models = ["mm1", "md1", "mmc", "mgc"]
    fig, axes = plt.subplots(1, len(models), figsize=(5.5 * len(models), 4.5), sharey=False)
    if len(models) == 1:
        axes = [axes]
    for ax, model in zip(axes, models):
        model_rows = sorted([row for row in rows if row["model"] == model], key=lambda row: row["rho"])
        if model == "mgc":
            by_profile = defaultdict(list)
            for row in model_rows:
                by_profile[row["profile_name"]].append(row)
            for profile_name, profile_rows in sorted(by_profile.items()):
                profile_rows = sorted(profile_rows, key=lambda row: row["rho"])
                ax.plot(
                    [row["rho"] for row in profile_rows],
                    [row["theoretical_response_time"] for row in profile_rows],
                    marker="o",
                    label=f"{profile_name} approximation",
                )
                ax.plot(
                    [row["rho"] for row in profile_rows],
                    [row["reference_response_time"] for row in profile_rows],
                    marker="s",
                    label=f"{profile_name} reference",
                )
        else:
            rho = [row["rho"] for row in model_rows]
            theory = [row["theoretical_response_time"] for row in model_rows]
            reference = [row["reference_response_time"] for row in model_rows]
            ax.plot(rho, theory, marker="o", label="theoretical")
            ax.plot(rho, reference, marker="s", label="reference simulation")
            simulator_rows = [row for row in model_rows if row["simulator_response_time"] != ""]
            if simulator_rows:
                ax.plot(
                    [row["rho"] for row in simulator_rows],
                    [row["simulator_response_time"] for row in simulator_rows],
                    marker="^",
                    label="simulator",
                )
        title = model.upper()
        if model == "mmc":
            server_set = sorted({row["servers"] for row in model_rows})
            if len(server_set) == 1:
                title = f"MMC (c={server_set[0]})"
        if model == "mgc":
            server_set = sorted({row["servers"] for row in model_rows})
            if len(server_set) == 1:
                title = f"MGC (c={server_set[0]})"
        ax.set_title(title)
        ax.set_xlabel("Utilization rho")
        ax.set_ylabel("Average response time W (s)")
        ax.grid(True, alpha=0.25)
        ax.legend()

    fig.suptitle("Queueing Validation: Response Time vs Load")
    save_figure(fig, plots_dir / "response_time_vs_load.png")


def plot_simulator_response_time_vs_load(rows, plots_dir):
    simulator_rows = sorted(
        [row for row in rows if row["model"] == "mm1" and row["simulator_response_time"] != ""],
        key=lambda row: row["rho"],
    )
    if not simulator_rows:
        return

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.plot(
        [row["rho"] for row in simulator_rows],
        [row["theoretical_response_time"] for row in simulator_rows],
        marker="o",
        label="theoretical",
    )
    ax.plot(
        [row["rho"] for row in simulator_rows],
        [row["simulator_response_time"] for row in simulator_rows],
        marker="^",
        label="simulator",
    )
    ax.set_title("Simulator Queue Validation (MM1): Response Time")
    ax.set_xlabel("Utilization rho")
    ax.set_ylabel("Average response time W (s)")
    ax.grid(True, alpha=0.25)
    ax.legend()
    save_figure(fig, plots_dir / "simulator_response_time_vs_load_mm1.png")


def plot_reference_error(rows, plots_dir):
    labels = [row["scenario"] for row in rows]
    x = list(range(len(labels)))
    width = 0.35
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar([i - width / 2 for i in x], [row["reference_waiting_error_percent"] for row in rows], width=width, label="Wq error")
    ax.bar([i + width / 2 for i in x], [row["reference_response_error_percent"] for row in rows], width=width, label="W error")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_ylabel("Absolute percentage error (%)")
    ax.set_title("Reference Simulation Error vs Analytical Baseline")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    save_figure(fig, plots_dir / "reference_error_by_scenario.png")


def plot_simulator_error(rows, plots_dir):
    simulator_rows = [row for row in rows if row["model"] == "mm1" and row["simulator_waiting_error_percent"] != ""]
    if not simulator_rows:
        return

    labels = [row["scenario"] for row in simulator_rows]
    x = list(range(len(labels)))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.bar([i - width / 2 for i in x], [row["simulator_waiting_error_percent"] for row in simulator_rows], width=width, label="Wq error")
    ax.bar([i + width / 2 for i in x], [row["simulator_response_error_percent"] for row in simulator_rows], width=width, label="W error")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_ylabel("Absolute percentage error (%)")
    ax.set_title("Simulator Queue Validation Error (MM1)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    save_figure(fig, plots_dir / "simulator_error_by_scenario_mm1.png")


def generate_plots(rows, plots_dir):
    plot_waiting_time_vs_load(rows, plots_dir)
    plot_response_time_vs_load(rows, plots_dir)
    plot_reference_error(rows, plots_dir)
    plot_simulator_waiting_time_vs_load(rows, plots_dir)
    plot_simulator_response_time_vs_load(rows, plots_dir)
    plot_simulator_error(rows, plots_dir)


def print_report(rows):
    print("Reference validation")
    print("Scenario\tModel\tServers\tRho\tBaseline\tBase Wq\tRef Wq\tBase W\tRef W\tRef Wq Err(%)\tRef W Err(%)")
    for row in rows:
        print(
            f"{row['scenario']}\t{row['model']}\t{row['servers']}\t{row['rho']:.2f}\t{row['baseline_type']}\t"
            f"{row['theoretical_waiting_time']:.6f}\t{row['reference_waiting_time']:.6f}\t"
            f"{row['theoretical_response_time']:.6f}\t{row['reference_response_time']:.6f}\t"
            f"{row['reference_waiting_error_percent']:.2f}\t{row['reference_response_error_percent']:.2f}"
        )
    simulator_rows = [row for row in rows if row["model"] == "mm1" and row["simulator_waiting_time"] != ""]
    if simulator_rows:
        print("\nSimulator validation (MM1 only)")
        print("Scenario\tSim Wq\tSim W\tSim Wq Err(%)\tSim W Err(%)")
        for row in simulator_rows:
            print(
                f"{row['scenario']}\t{float(row['simulator_waiting_time']):.6f}\t{float(row['simulator_response_time']):.6f}\t"
                f"{float(row['simulator_waiting_error_percent']):.2f}\t{float(row['simulator_response_error_percent']):.2f}"
            )


def main():
    args = parse_args()
    repo_dir = Path(__file__).resolve().parent
    args.real_values_dir = resolve_real_values_dir(
        repo_dir,
        args.real_values_dir,
        hardware_profile=args.hardware_profile,
        machine_datasets_root=args.machine_datasets_root,
    )
    summary_out = (repo_dir / args.summary_out).resolve() if not args.summary_out.is_absolute() else args.summary_out.resolve()
    overall_out = (repo_dir / args.overall_out).resolve() if not args.overall_out.is_absolute() else args.overall_out.resolve()
    plots_dir = (repo_dir / args.plots_dir).resolve() if not args.plots_dir.is_absolute() else args.plots_dir.resolve()
    paper_table_out = (repo_dir / args.tables_out).resolve() if not args.tables_out.is_absolute() else args.tables_out.resolve()

    rows = collect_results(args)
    write_summary_csv(summary_out, rows)
    write_validation_tables(paper_table_out, rows)
    write_overall_csv(overall_out, rows)
    generate_plots(rows, plots_dir)
    print_report(rows)
    print(f"\nSummary written to {summary_out}")
    print(f"Validation tables written to {paper_table_out}")
    print(f"Overall metrics written to {overall_out}")
    print(f"Plots written to {plots_dir}")


if __name__ == "__main__":
    main()
