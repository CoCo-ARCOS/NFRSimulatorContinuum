#!/usr/bin/env python3
"""Analyze the real-engine runs into paper-ready tables.

Emits three CSVs, one per claim:

  real_engine_runs.csv       every run, flattened;
  real_engine_portability.csv  per (profile, budget): do the engines agree on
                             the mechanisms they executed;
  real_engine_fidelity.csv   predicted against measured cost, with the ratio
                             that the fidelity discussion quotes.

Measured energy is only comparable to the prediction when it was actually
measured; runs that fell back to the modelled power are kept but flagged, so a
table can exclude them without recomputing anything.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def flatten(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        measured = record.get("measured") or {}
        predicted = record.get("predicted") or {}
        selection = record.get("selection") or {}
        rows.append({
            "profile": record.get("profile", ""),
            "budget_multiplier": record.get("budget_multiplier", ""),
            "engine": record.get("engine", ""),
            "status": record.get("status", ""),
            "host": record.get("host", ""),
            "candidate_id": selection.get("candidate_id", ""),
            "label": selection.get("label", ""),
            "coverage_weighted": selection.get("coverage_weighted", ""),
            "predicted_energy_j": predicted.get("energy_j", ""),
            "predicted_makespan_s": predicted.get("makespan_s", ""),
            "measured_seconds": measured.get("seconds_mean", ""),
            "measured_energy_j": measured.get("energy_j_mean", ""),
            "measured_nfr_seconds": measured.get("nfr_seconds_mean", ""),
            "energy_source": measured.get("energy_source", ""),
            "energy_coarse": measured.get("energy_coarse", ""),
            "run_energy_j": measured.get("run_energy_j", ""),
            "run_seconds": measured.get("run_seconds", ""),
            "passes_measured": measured.get("passes_measured", ""),
            "mechanism_trace": record.get("mechanism_trace", ""),
            "trace_stable": record.get("mechanism_trace_stable", ""),
            "integrity_verified": record.get("integrity_verified", ""),
            "payload_bytes": record.get("payload_bytes", ""),
        })
    return rows


def portability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per configuration: did every engine execute the same mechanisms."""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["status"] == "ok":
            groups[(row["profile"], row["budget_multiplier"])].append(row)

    out = []
    for (profile, budget), members in sorted(groups.items()):
        traces = {m["mechanism_trace"] for m in members}
        out.append({
            "profile": profile,
            "budget_multiplier": budget,
            "engines": ",".join(sorted(m["engine"] for m in members)),
            "engine_count": len(members),
            "distinct_traces": len(traces),
            "traces_agree": len(traces) == 1,
            "mechanism_trace": sorted(traces)[0] if len(traces) == 1 else "|".join(sorted(traces)),
            "all_verified": all(str(m["integrity_verified"]).lower() == "true" for m in members),
        })
    return out


def fidelity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Predicted against measured cost for each successful run."""
    out = []
    for row in rows:
        if row["status"] != "ok":
            continue
        entry = {
            "profile": row["profile"],
            "budget_multiplier": row["budget_multiplier"],
            "engine": row["engine"],
            "label": row["label"],
            "predicted_makespan_s": row["predicted_makespan_s"],
            "measured_seconds": row["measured_seconds"],
            "measured_nfr_seconds": row["measured_nfr_seconds"],
            "predicted_energy_j": row["predicted_energy_j"],
            "measured_energy_j": row["measured_energy_j"],
            "energy_source": row["energy_source"],
            # Both rapl and SLURM accounting are real measurements; only the
            # modelled fallback is derived from time.
            "energy_measured": row["energy_source"] in ("rapl", "slurm"),
            "energy_coarse": row["energy_coarse"],
        }
        try:
            predicted = float(row["predicted_makespan_s"])
            measured = float(row["measured_seconds"])
            entry["time_ratio"] = measured / predicted if predicted else ""
        except (TypeError, ValueError):
            entry["time_ratio"] = ""
        try:
            predicted = float(row["predicted_energy_j"])
            measured = float(row["measured_energy_j"])
            entry["energy_ratio"] = measured / predicted if predicted else ""
        except (TypeError, ValueError):
            entry["energy_ratio"] = ""
        out.append(entry)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", required=True, type=Path, nargs="+",
                        help="One or more real_engine_runs.json files; pass several "
                             "to aggregate the tasks of a SLURM array job")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    for path in args.runs:
        records.extend(load_json(path).get("records", []))
    rows = flatten(records)
    port = portability(rows)
    fid = fidelity(rows)

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "real_engine_runs.csv", rows)
    write_csv(args.output / "real_engine_portability.csv", port)
    write_csv(args.output / "real_engine_fidelity.csv", fid)

    ok = [r for r in rows if r["status"] == "ok"]
    engines = sorted({r["engine"] for r in ok})
    agree = [p for p in port if p["traces_agree"]]
    measured_energy = [f for f in fid if f["energy_measured"]]
    print(f"runs: {len(ok)} ok / {len(rows)} total across engines: {', '.join(engines) or 'none'}")
    print(f"portability: {len(agree)}/{len(port)} configurations where all engines agree")
    sources = sorted({f["energy_source"] for f in fid if f["energy_source"]})
    print(f"fidelity: {len(fid)} comparable runs, {len(measured_energy)} with measured energy "
          f"(sources: {', '.join(sources) or 'none'})")
    if not measured_energy and fid:
        print("  note: no run had a readable counter or SLURM energy accounting; "
              "energy columns are modelled from time x power, not measured")
    coarse = [f for f in measured_energy if str(f["energy_coarse"]).lower() == "true"]
    if coarse:
        print(f"  warning: {len(coarse)} run(s) were shorter than the accounting "
              "sampling interval; raise --min-seconds before quoting their energy")
    print(f"wrote analysis to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
