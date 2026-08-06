#!/usr/bin/env python3
"""Wall-clock and energy measurement for real-engine runs.

Three energy sources are tried, in decreasing order of directness:

``rapl``   the kernel powercap tree read directly. Counters are per-package and
           wrap, so every sample records the wrap point and differences are
           taken modulo it. Usually root-only, so rarely available on a shared
           cluster.
``slurm``  node energy from SLURM's accounting, via ``sstat``/``sacct``. The
           slurmd daemon samples the same counters as root, so this needs no
           privilege of ours -- only that the site runs an ``acct_gather_energy``
           plugin. It is node-level and sampled every ``AcctGatherNodeFreq``
           seconds, so it is only meaningful for an exclusive allocation and a
           region that spans several sampling intervals.
``model``  measured time multiplied by the calibrated active power of the node.

The chosen source travels with every measurement, so a modelled number can
never be reported as a measured one.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

POWERCAP_ROOT = Path("/sys/class/powercap")

# SLURM samples node energy on a timer; a region shorter than a couple of
# intervals cannot be attributed meaningfully.
SLURM_SAMPLE_HINT_S = 30.0

# Domains whose energy is already counted inside the package total.
_NESTED_HINTS = ("core", "uncore")


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _rapl_domains() -> list[dict[str, Any]]:
    """Top-level powercap domains: one package total plus DRAM where present."""
    if not POWERCAP_ROOT.is_dir():
        return []
    domains = []
    for entry in sorted(POWERCAP_ROOT.iterdir()):
        counter = entry / "energy_uj"
        if not counter.exists():
            continue
        name = (entry / "name").read_text().strip() if (entry / "name").exists() else entry.name
        # Skip subdomains already included in their parent package total.
        if any(hint in name.lower() for hint in _NESTED_HINTS):
            continue
        if _read_int(counter) is None:
            continue  # Present but unreadable (needs root on most distributions).
        domains.append({
            "path": counter,
            "name": name,
            "wrap_uj": _read_int(entry / "max_energy_range_uj") or 0,
        })
    return domains


def _sample(domains: list[dict[str, Any]]) -> dict[str, int]:
    return {d["name"]: _read_int(d["path"]) or 0 for d in domains}


def _delta(before: dict[str, int], after: dict[str, int],
           domains: list[dict[str, Any]]) -> float:
    """Joules consumed between two samples, correcting for counter wraparound."""
    wraps = {d["name"]: d["wrap_uj"] for d in domains}
    total_uj = 0
    for name, start in before.items():
        end = after.get(name, start)
        raw = end - start
        if raw < 0:
            raw += wraps.get(name, 0)
        total_uj += max(raw, 0)
    return total_uj / 1e6


def _run(command: list[str], timeout: float = 20.0) -> str | None:
    try:
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _first_number(text: str | None) -> float | None:
    if not text:
        return None
    for line in text.splitlines():
        token = line.strip().split("|")[0].strip()
        if not token or token in {"ConsumedEnergyRaw", "ConsumedEnergy"}:
            continue
        # Values may carry an SI suffix (e.g. "1.20K"); accounting reports raw
        # joules for ConsumedEnergyRaw, so a bare float is the normal case.
        try:
            return float(token)
        except ValueError:
            continue
    return None


def slurm_energy_j() -> float | None:
    """Cumulative node energy of the current SLURM job, in joules.

    Returns ``None`` outside an allocation, or when the site runs no energy
    accounting plugin (in which case the counter is absent or pinned at zero).
    """
    job_id = os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID")
    if not job_id:
        return None
    step = os.environ.get("SLURM_STEP_ID")
    targets = [f"{job_id}.{step}"] if step else []
    targets += [f"{job_id}.batch", job_id]
    for target in targets:
        value = _first_number(
            _run(["sstat", "-n", "-P", "--format=ConsumedEnergyRaw", "-j", target])
        )
        if value:
            return value
    # A finished step is no longer visible to sstat but is to sacct.
    return _first_number(
        _run(["sacct", "-n", "-P", "--format=ConsumedEnergyRaw", "-j", job_id])
    )


def probe() -> dict[str, Any]:
    """Describe the available energy source, for run provenance."""
    domains = _rapl_domains()
    if domains:
        return {
            "source": "rapl",
            "domains": [d["name"] for d in domains],
            "readable": True,
        }

    in_allocation = bool(os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID"))
    if in_allocation and slurm_energy_j() is not None:
        return {
            "source": "slurm",
            "domains": ["node"],
            "readable": True,
            "note": "node-level energy from SLURM accounting; requires an "
                    "exclusive allocation and a region spanning several "
                    f"sampling intervals (~{SLURM_SAMPLE_HINT_S:g}s each)",
        }

    hint = ("powercap is not readable and SLURM energy accounting is "
            "unavailable; runs fall back to the calibrated power model "
            "(--model-power-w or --site/--machine)")
    if in_allocation:
        hint += (". Check whether the site enables it with: "
                 "scontrol show config | grep -i acctgatherenergy")
    return {
        "source": "model",
        "domains": [],
        "readable": False,
        "hint": hint,
    }


class Measurement(dict):
    """Result of one measured region: seconds, joules, and their provenance."""


@contextmanager
def measure(model_power_w: float | None = None,
            allow_slurm: bool = False) -> Iterator[Measurement]:
    """Measure wall time and energy over a region.

    ``model_power_w`` supplies the calibrated active power of the node, used
    when no counter is readable. ``allow_slurm`` enables the SLURM accounting
    source, which is too coarse for individual stages and so is requested only
    for whole-run regions.
    """
    result = Measurement(seconds=0.0, energy_j=None, energy_source="unavailable")
    domains = _rapl_domains()
    before = _sample(domains) if domains else {}
    slurm_before = slurm_energy_j() if (allow_slurm and not domains) else None
    started = time.perf_counter()
    try:
        yield result
    finally:
        elapsed = time.perf_counter() - started
        result["seconds"] = elapsed
        if domains:
            result["energy_j"] = _delta(before, _sample(domains), domains)
            result["energy_source"] = "rapl"
        elif slurm_before is not None:
            slurm_after = slurm_energy_j()
            delta = (slurm_after - slurm_before) if slurm_after is not None else None
            if delta is not None and delta > 0:
                result["energy_j"] = delta
                result["energy_source"] = "slurm"
                # Below a couple of sampling intervals the delta is dominated by
                # where the samples happened to land.
                result["coarse"] = elapsed < 2 * SLURM_SAMPLE_HINT_S
            elif model_power_w is not None:
                result["energy_j"] = elapsed * float(model_power_w)
                result["energy_source"] = "model"
        elif model_power_w is not None:
            result["energy_j"] = elapsed * float(model_power_w)
            result["energy_source"] = "model"


def site_power_w(site_path: Path | str, machine: str) -> float | None:
    """Active power of one machine from a calibrated site file.

    Used as the modelled fallback; returns the max power draw, which is the
    quantity the simulator integrates over busy time.
    """
    try:
        with Path(site_path).open("r", encoding="utf-8") as handle:
            site = json.load(handle)
    except (OSError, ValueError):
        return None
    machines = (site.get("infrastructure") or {}).get("machines") or []
    for entry in machines:
        if str(entry.get("name")) == machine:
            value = entry.get("max_power_w", entry.get("max_power"))
            return float(value) if value is not None else None
    return None


def main() -> int:
    """Report what this node can measure, so a job can be checked before it runs."""
    info = probe()
    info["host"] = os.uname().nodename
    info["in_slurm_allocation"] = bool(
        os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID")
    )
    print(json.dumps(info, indent=2))

    if info["source"] in ("rapl", "slurm"):
        with measure(allow_slurm=True) as m:
            time.sleep(0.5)
        energy = m["energy_j"]
        rendered = f"{energy:.3f} J" if energy is not None else "no delta"
        print(f"\nself-test: {m['seconds']:.3f} s, {rendered} ({m['energy_source']})")
        if m.get("coarse"):
            print("  (region shorter than the sampling interval; "
                  "expect meaningful values only for longer runs)")
    else:
        print("\nenergy will be modelled from time x calibrated power")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
