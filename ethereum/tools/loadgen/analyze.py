#!/usr/bin/env python3
"""Analyze a loadgen JSONL: target-vs-achieved submission rate, mining-latency
CDF, and summary.json with percentile stats. Companion to tools/loadgen/loadgen.js."""

import argparse
import json
import math
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "figure.dpi": 150,
})


def load_jsonl(path: Path):
    meta = None
    trailer = None
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "end":
                trailer = obj
            else:
                records.append(obj)
    return meta, trailer, records


def percentile(values, q):
    if not values:
        return None
    return float(np.percentile(values, q))


def plot_submission_rate(meta, records, out_path: Path):
    fig, ax = plt.subplots(figsize=(7.5, 4.0))
    if not records:
        ax.text(0.5, 0.5, "no records", ha="center", va="center", transform=ax.transAxes)
        fig.savefig(out_path)
        plt.close(fig)
        return

    t0 = meta["started_at_ms"] if meta else min(r["submit_time_ms"] for r in records)
    submit_secs = [(r["submit_time_ms"] - t0) / 1000.0 for r in records]
    mined_secs = [
        (r["mined_time_ms"] - t0) / 1000.0 for r in records if r.get("mined_time_ms") is not None
    ]

    duration = meta["duration_s"] if meta else math.ceil(max(submit_secs) + 1)
    n_bins = max(1, int(math.ceil(duration)))
    bins = np.arange(0, n_bins + 1, 1)
    submit_counts, _ = np.histogram(submit_secs, bins=bins)
    mined_counts, _ = np.histogram(mined_secs, bins=bins) if mined_secs else (np.zeros_like(submit_counts), None)
    centers = bins[:-1] + 0.5

    target = meta["target_rate_tx_s"] if meta else None
    if target is not None:
        ax.axhline(target, color="#444", linestyle="--", linewidth=1.2, label=f"target ({target} tx/s)")
    ax.plot(centers, submit_counts, color="#2E86AB", linewidth=1.6, label="achieved submit")
    ax.plot(centers, mined_counts, color="#E94F37", linewidth=1.6, label="achieved mined")

    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("tx / second")
    title = "Submission rate: target vs achieved"
    if meta:
        title += f"  (N={meta['accounts']} wallets, chain_id={meta['chain_id']})"
    ax.set_title(title)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    ax.legend(loc="best", frameon=True)
    ax.set_xlim(0, duration)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_latency_cdf(records, out_path: Path):
    latencies = sorted(r["latency_ms"] for r in records if r.get("latency_ms") is not None)
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    if not latencies:
        ax.text(0.5, 0.5, "no mined txs", ha="center", va="center", transform=ax.transAxes)
        fig.savefig(out_path)
        plt.close(fig)
        return

    ys = np.arange(1, len(latencies) + 1) / len(latencies)
    ax.plot(latencies, ys, color="#2E86AB", linewidth=1.6)
    for q, label in ((50, "p50"), (95, "p95"), (99, "p99")):
        v = percentile(latencies, q)
        ax.axvline(v, color="#888", linestyle=":", linewidth=0.8)
        ax.text(v, 0.02, f" {label}={v:.0f}ms", rotation=90, fontsize=8, va="bottom", color="#555")

    ax.set_xlabel("Mining latency (ms)  [submit → receipt]")
    ax.set_ylabel("CDF")
    ax.set_title(f"Mining-latency CDF  (n={len(latencies)})")
    ax.grid(True, which="both", linestyle="--", alpha=0.3)
    ax.set_xscale("log")
    ax.set_ylim(0, 1.02)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def compute_summary(meta, trailer, records):
    mined = [r for r in records if r.get("mined_time_ms") is not None]
    errors = [r for r in records if r.get("error")]
    latencies = [r["latency_ms"] for r in mined]

    summary = {
        "input": {
            "target_rate_tx_s": meta["target_rate_tx_s"] if meta else None,
            "duration_s": meta["duration_s"] if meta else None,
            "accounts": meta["accounts"] if meta else None,
            "chain_id": meta["chain_id"] if meta else None,
            "contract": meta["contract"] if meta else None,
        },
        "counts": {
            "submitted": len(records),
            "mined": len(mined),
            "errors": len(errors),
        },
    }

    if records:
        t_submit = [r["submit_time_ms"] for r in records]
        submit_span_s = max(1e-9, (max(t_submit) - min(t_submit)) / 1000.0)
        summary["throughput"] = {
            "achieved_submit_rate_tx_s": len(records) / submit_span_s,
        }
    else:
        summary["throughput"] = {"achieved_submit_rate_tx_s": 0.0}

    if mined:
        t_mined = [r["mined_time_ms"] for r in mined]
        mined_span_s = max(1e-9, (max(t_mined) - min(t_mined)) / 1000.0)
        summary["throughput"]["achieved_mined_rate_tx_s"] = len(mined) / mined_span_s
    else:
        summary["throughput"]["achieved_mined_rate_tx_s"] = 0.0

    if latencies:
        summary["latency_ms"] = {
            "count": len(latencies),
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies),
            "mean": statistics.fmean(latencies),
            "stdev": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        }
    else:
        summary["latency_ms"] = None

    if trailer:
        summary["wall_seconds"] = trailer.get("wall_seconds")

    return summary


def main():
    ap = argparse.ArgumentParser(description="Analyze a loadgen JSONL output.")
    ap.add_argument("input", type=Path, help="path to loadgen_*.jsonl")
    ap.add_argument("--outdir", type=Path, default=None,
                    help="directory for plots+summary (default: next to input)")
    args = ap.parse_args()

    meta, trailer, records = load_jsonl(args.input)
    outdir = args.outdir or args.input.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem

    rate_pdf = outdir / f"{stem}_fig_submission_rate.pdf"
    cdf_pdf = outdir / f"{stem}_fig_mining_latency_cdf.pdf"
    summary_path = outdir / f"{stem}_summary.json"

    plot_submission_rate(meta, records, rate_pdf)
    plot_latency_cdf(records, cdf_pdf)
    summary = compute_summary(meta, trailer, records)
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"wrote {rate_pdf}")
    print(f"wrote {cdf_pdf}")
    print(f"wrote {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
