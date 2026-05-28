#!/usr/bin/env python3
"""
Plot pipeline architectures makespan vs payload size from
`proxy_dd/sensitivity_results/pipeline_architectures_summary.csv`.

Saves `pipeline_architectures_plot.png` in the same folder.
"""
import argparse
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import csv
import numpy as np

plt.style.use("paper.mplstyle")

pt = 1./72.27
jour_sizes = {"PRD": {"onecol": 246.*pt, "twocol": 510.*pt},
              "CQG": {"onecol": 374.*pt}, }
my_width = jour_sizes["PRD"]["twocol"]
golden = (1 + 5 ** 0.5) / 1.1


def plot_pipeline_architectures(csv_path: Path, out_path: Path, dpi: int = 150):
    df = pd.read_csv(csv_path)

    # If this is a full sensitivity_summary.csv, extract pipeline_architectures rows
    if "factor" in df.columns:
        df = df[df["factor"] == "pipeline_architectures"].copy()
        # derive payload_mb from numeric value (bytes -> MB) if available
        if "value" in df.columns:
            try:
                df["payload_mb"] = pd.to_numeric(df["value"], errors="coerce") / 1e6
            except Exception:
                df["payload_mb"] = pd.NA
        # pipeline total seconds may be under pipeline_total_seconds
        if "pipeline_total_seconds" not in df.columns and "pipeline_total_seconds" in df.columns:
            df["pipeline_total_seconds"] = pd.to_numeric(df.get("pipeline_total_seconds", 0), errors="coerce")
        # group: prefer existing 'group' column, else use value_label
        if "group" not in df.columns and "value_label" in df.columns:
            # transform value_label to group by stripping payload size and keeping algorithm
            df["group"] = df["value_label"].str.split("_").str[1]

    # Ensure numeric
    if "payload_mb" in df.columns:
        df["payload_mb"] = pd.to_numeric(df["payload_mb"], errors="coerce")
    else:
        df["payload_mb"] = pd.NA
    if "pipeline_total_seconds" in df.columns:
        df["pipeline_total_seconds"] = pd.to_numeric(df["pipeline_total_seconds"], errors="coerce")

    groups = df["group"].unique() if "group" in df.columns else []

    #plt.style.use("seaborn-darkgrid")
    fig, ax = plt.subplots(figsize=(my_width, my_width / golden))

    # set different marker styles for each group
    markers = ['o', 's', '^', 'D', 'v', 'P', '*', 'X']

    for grp in sorted(groups):
        sub = df[df["group"] == grp].copy()
        if sub.empty:
            continue
        # aggregate mean and std across runs for each payload
        agg = (
            sub.groupby("payload_mb")["pipeline_total_seconds"]
            .agg(["mean", "std"]) 
            .reset_index()
            .sort_values("payload_mb")
        )
        if agg.empty:
            continue
        x = agg["payload_mb"].values
        y = agg["mean"].values
        ystd = agg["std"].fillna(0).values
        idx = sorted(groups).index(grp)
        ax.plot(x, y, marker=markers[idx % len(markers)], linewidth=1, label=grp)
        # plot std shading if non-zero
        if any(ystd > 0):
            ax.fill_between(x, y - ystd, y + ystd, alpha=0.2, color=plt.get_cmap("tab10").colors[idx % 10])

    ax.set_xscale("log")
    ax.set_xticks(sorted(df["payload_mb"].unique()))
    ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax.set_xlabel("Payload size (MB)")
    ax.set_ylabel("Response Time (seconds)")
    

    plt.grid(True, which="both", ls="--", lw=0.5)
    #ax.set_title("Pipeline architectures: Makespan vs Payload Size")
    ax.legend(frameon=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    print(f"Saved plot to: {out_path}")


def measure_compression_bytes(root_dir: Path, summary_csv: Path, out_csv: Path):
    """Scan pipeline_architectures run folders, compute average transferred bytes per algorithm/payload.

    Writes `out_csv` with columns: algorithm, payload_mb, avg_bytes
    """
    root = root_dir / "proxy_dd" / "sensitivity_results" / "pipeline_architectures"
    if not root.exists():
        raise FileNotFoundError(f"Pipeline architectures folder not found: {root}")

    rows = []
    # iterate subdirectories like '1_Uncomp_10MB'
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.split("_")
        if len(parts) < 3:
            continue
        alg = parts[1]
        payload_label = parts[-1]
        try:
            payload_mb = int(payload_label.replace('MB',''))
        except Exception:
            continue

        # collect bytes across runs
        bytes_list = []
        for run in sorted(d.glob('run_*')):
            sizes_csv = run / 'results' / 'stage_sizes.csv'
            total_bytes = None

            # Prefer per-run stage_sizes.csv produced by sensitivity_analysis
            if sizes_csv.exists():
                try:
                    with sizes_csv.open('r', encoding='utf-8') as fp:
                        reader = csv.DictReader(fp)
                        out_sum = 0.0
                        count = 0
                        for r in reader:
                            try:
                                ob = float(r.get('output_bytes', 0.0))
                            except Exception:
                                ob = 0.0
                            out_sum += ob
                            count += 1
                        if count > 0:
                            total_bytes = out_sum
                except Exception:
                    total_bytes = None

            # fallback to legacy link_metrics.csv scanning if stage_sizes not found
            if total_bytes is None:
                metrics = run / 'results' / 'link_metrics.csv'
                if not metrics.exists():
                    continue
                try:
                    # read bytes column and sum across links
                    with metrics.open('r', encoding='utf-8') as fp:
                        reader = csv.DictReader(fp)
                        total = 0
                        count = 0
                        for r in reader:
                            b = r.get('bytes') or r.get('Bytes')
                            if b is None:
                                continue
                            try:
                                total += float(b)
                            except Exception:
                                continue
                            count += 1
                        if count > 0:
                            total_bytes = total
                except Exception:
                    continue

            if total_bytes is not None:
                bytes_list.append(total_bytes)

        if bytes_list:
            rows.append({
                'algorithm': alg,
                'payload_mb': payload_mb,
                'avg_bytes': float(np.mean(bytes_list)),
                'runs': len(bytes_list),
            })

    out_df = pd.DataFrame(rows)
    if out_df.empty:
        print('No link metrics found to measure compression bytes.')
        return out_df

    out_df = out_df.sort_values(['algorithm','payload_mb'])
    out_df.to_csv(out_csv, index=False)
    print(f'Wrote compression bytes summary to: {out_csv}')
    return out_df


def plot_compression_reduction(bytes_csv: Path, out_png: Path, dpi: int =150):
    df = pd.read_csv(bytes_csv)
    # normalize algorithm names (use full token)
    df['algorithm'] = df['algorithm'].astype(str)

    # find uncompressed baseline rows (algorithm contains 'Uncomp' or startswith '1')
    # pick exact match 'Uncomp' token if present
    baseline = df[df['algorithm'].str.contains('Uncomp', case=False, na=False)]
    if baseline.empty:
        # try algorithm 'No' or numeric prefix '1'
        baseline = df[df['algorithm'].str.match('1|No', na=False)]

    if baseline.empty:
        print('Baseline (uncompressed) not found; cannot compute reduction')
        return

    # compute baseline bytes per payload
    base_map = {row['payload_mb']: row['avg_bytes'] for _, row in baseline.iterrows()}

    rows = []
    for _, row in df.iterrows():
        pb = row['payload_mb']
        if pb not in base_map or base_map[pb] == 0:
            continue
        reduction = 1.0 - (row['avg_bytes'] / base_map[pb])
        rows.append({'algorithm': row['algorithm'], 'payload_mb': pb, 'avg_bytes': row['avg_bytes'], 'reduction': reduction})

    rdf = pd.DataFrame(rows)
    if rdf.empty:
        print('No comparable data to compute reduction.')
        return rdf

    #plt.style.use('seaborn-darkgrid')
    fig, ax = plt.subplots(figsize=(8,5))
    for alg in sorted(rdf['algorithm'].unique()):
        sub = rdf[rdf['algorithm']==alg].sort_values('payload_mb')
        ax.plot(sub['payload_mb'], sub['reduction']*100.0, marker='o', label=alg)

    ax.set_xscale('log')
    ax.set_xlabel('Payload size (MB)')
    ax.set_ylabel('Size reduction (%)')
    ax.set_title('Compression size reduction vs payload')
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=dpi)
    print(f'Saved compression reduction plot to: {out_png}')
    return rdf


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Plot pipeline architectures summary")
    p.add_argument("--csv", type=Path, default=Path("proxy_dd/sensitivity_results/pipeline_architectures_summary.csv"))
    p.add_argument("--out", type=Path, default=Path("pipeline_architectures_plot.pdf"))
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--measure-compression", action="store_true", help="Scan run folders and measure transferred bytes to compute compression reduction")
    p.add_argument("--bytes-out", type=Path, default=Path("proxy_dd/sensitivity_results/pipeline_architectures_bytes.csv"), help="CSV output for measured bytes per algorithm/payload")
    p.add_argument("--reduction-out", type=Path, default=Path("proxy_dd/sensitivity_results/pipeline_architectures_compression_reduction.png"), help="PNG output for compression reduction plot")
    args = p.parse_args()
    plot_pipeline_architectures(args.csv, args.out, args.dpi)
    if args.measure_compression:
        bytes_df = measure_compression_bytes(Path('.'), args.csv, args.bytes_out)
        if not bytes_df.empty:
            plot_compression_reduction(args.bytes_out, args.reduction_out, dpi=args.dpi)
