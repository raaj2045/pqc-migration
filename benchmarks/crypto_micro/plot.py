#!/usr/bin/env python3
"""
Crypto microbenchmarks — secp256k1 vs ML-DSA-44 — publication figures (v2).

Six PDFs are produced from results.json:

  fig_keygen_comparison.pdf       bar chart of key generation latency
  fig_signing_by_msg_size.pdf     log-log line plot, sign latency vs msg size
  fig_verification_by_msg_size    log-log line plot, verify latency vs msg size
  fig_batch_verification.pdf      single-panel: amortized per-sig time
  fig_concurrent_signing.pdf      throughput vs goroutines (sub-linear visible)
  fig_memory_allocs.pdf           bytes/op + allocs/op side by side

results.json has ONE benchmark run per (operation, scheme, params)
combination — Go testing.B's auto-tuned internal iteration averaging
gives a stable ns_per_op but no run-to-run variance, so we do NOT
draw confidence intervals. Captions state this.

v2 declutter pass: titles dropped (caption carries them), data labels
limited to one per element, scheme labels minimal ("secp256k1" /
"ML-DSA-44"), reference lines and axis labels trimmed.
"""

import json
import os
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt

plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 11,
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "lines.linewidth": 2.0,
})

COLOR_SECP = "#1f77b4"
COLOR_MLDSA = "#d62728"
COLORS = {"secp256k1": COLOR_SECP, "mldsa44": COLOR_MLDSA}
LABELS = {"secp256k1": "secp256k1", "mldsa44": "ML-DSA-44"}

MSG_SIZES = [100, 1024, 10240, 102400]
MSG_LABELS = ["100 B", "1 KiB", "10 KiB", "100 KiB"]


def load_results(filepath="results.json"):
    with open(filepath, "r") as f:
        return json.load(f)


def group_results(results):
    grouped = defaultdict(list)
    for r in results:
        grouped[r["operation"]].append(r)
    return grouped


def ns_to_us(ns):
    return ns / 1_000


def ns_to_ms(ns):
    return ns / 1_000_000


def plot_keygen_comparison(grouped, out="fig_keygen_comparison.pdf"):
    fig, ax = plt.subplots(figsize=(5.5, 3.5))

    schemes = ["secp256k1", "mldsa44"]
    latencies_us = []
    for s in schemes:
        rows = [r for r in grouped.get("KeyGen", []) if r["scheme"] == s]
        latencies_us.append(ns_to_us(rows[0]["ns_per_op"]) if rows else 0.0)

    x = np.arange(len(schemes))
    bars = ax.bar(x, latencies_us, width=0.55,
                  color=[COLORS[s] for s in schemes],
                  edgecolor="black", linewidth=0.5)
    for b, v in zip(bars, latencies_us):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() * 1.04,
                f"{v:,.1f}", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[s] for s in schemes])
    ax.set_ylabel("Key generation latency (μs, log)")
    ax.set_yscale("log")
    ax.set_ylim(1, max(latencies_us) * 4)
    ax.grid(True, axis="y", which="both", linestyle="--", alpha=0.4)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_op_by_msg_size(grouped, op, ylabel, out):
    fig, ax = plt.subplots(figsize=(5.5, 3.5))

    rows_all = [r for r in grouped.get(op, []) if r["msg_size_bytes"] > 0]
    for scheme in ("secp256k1", "mldsa44"):
        rows = sorted(
            (r for r in rows_all if r["scheme"] == scheme),
            key=lambda r: r["msg_size_bytes"],
        )
        if not rows:
            continue
        xs = [r["msg_size_bytes"] for r in rows]
        ys = [ns_to_us(r["ns_per_op"]) for r in rows]
        ax.plot(xs, ys, "o-",
                color=COLORS[scheme], label=LABELS[scheme],
                linewidth=2.0, markersize=5)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(MSG_SIZES)
    ax.set_xticklabels(MSG_LABELS)
    ax.set_xlabel("Message size (log)")
    ax.set_ylabel(ylabel)
    ax.grid(True, which="major", linestyle="--", alpha=0.4)
    ax.legend(loc="best", frameon=False)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_concurrent_signing(grouped, out="fig_concurrent_signing.pdf"):
    fig, ax = plt.subplots(figsize=(5.5, 3.5))

    rows_all = grouped.get("Throughput") or grouped.get("ConcurrentSign") or []

    for scheme in ("secp256k1", "mldsa44"):
        rows = sorted(
            (r for r in rows_all
             if r["scheme"] == scheme and r.get("goroutines", 0) > 0),
            key=lambda r: r["goroutines"],
        )
        if not rows:
            continue
        gs = [r["goroutines"] for r in rows]
        tps = [1_000_000_000 / r["ns_per_op"] if r["ns_per_op"] > 0 else 0
               for r in rows]
        ax.plot(gs, tps, "o-",
                color=COLORS[scheme], label=LABELS[scheme],
                linewidth=2.0, markersize=5)

    ax.set_xticks([1, 4, 8, 16])
    ax.set_xlabel("Concurrent goroutines")
    ax.set_ylabel("Throughput (signatures/s, log)")
    ax.set_yscale("log")
    ax.grid(True, which="major", linestyle="--", alpha=0.4)
    ax.legend(loc="best", frameon=False)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_batch_verification(grouped, out="fig_batch_verification.pdf"):
    """Single-panel: amortized per-signature verify time vs batch size.
    Total batch time is in results.json; we show only the amortized
    cost since that is the headline."""
    fig, ax = plt.subplots(figsize=(5.5, 3.5))

    rows_all = grouped.get("BatchVerify", [])
    for scheme in ("secp256k1", "mldsa44"):
        rows = sorted(
            (r for r in rows_all
             if r["scheme"] == scheme and r.get("batch_size", 0) > 0),
            key=lambda r: r["batch_size"],
        )
        if not rows:
            continue
        bs = [r["batch_size"] for r in rows]
        per_sig_us = [ns_to_us(r["ns_per_op"]) / r["batch_size"] for r in rows]
        ax.plot(bs, per_sig_us, "o-",
                color=COLORS[scheme], label=LABELS[scheme],
                linewidth=2.0, markersize=5)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks([10, 100, 1000])
    ax.set_xticklabels(["10", "100", "1000"])
    ax.set_xlabel("Batch size (log)")
    ax.set_ylabel("Per-signature verify time (μs, log)")
    ax.grid(True, which="major", linestyle="--", alpha=0.4)
    ax.legend(loc="best", frameon=False)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_memory_allocs(grouped, out="fig_memory_allocs.pdf"):
    fig, (ax_bytes, ax_allocs) = plt.subplots(1, 2, figsize=(9.0, 3.5))

    operations = ["KeyGen", "Sign", "Verify"]
    schemes = ["secp256k1", "mldsa44"]

    bytes_data = {s: [] for s in schemes}
    allocs_data = {s: [] for s in schemes}

    for op in operations:
        for scheme in schemes:
            candidates = [r for r in grouped.get(op, []) if r["scheme"] == scheme]
            if op in ("Sign", "Verify"):
                candidates = [r for r in candidates if r["msg_size_bytes"] == 1024]
            if candidates:
                bytes_data[scheme].append(candidates[0]["bytes_per_op"])
                allocs_data[scheme].append(candidates[0]["allocs_per_op"])
            else:
                bytes_data[scheme].append(0)
                allocs_data[scheme].append(0)

    x = np.arange(len(operations))
    width = 0.36
    for i, scheme in enumerate(schemes):
        offset = (i - 0.5) * width
        b1 = ax_bytes.bar(x + offset, bytes_data[scheme], width,
                          color=COLORS[scheme], label=LABELS[scheme],
                          edgecolor="black", linewidth=0.4)
        b2 = ax_allocs.bar(x + offset, allocs_data[scheme], width,
                           color=COLORS[scheme], label=LABELS[scheme],
                           edgecolor="black", linewidth=0.4)
        for rect, val in zip(b1, bytes_data[scheme]):
            if val > 0:
                ax_bytes.text(rect.get_x() + rect.get_width() / 2,
                              rect.get_height() * 1.05,
                              f"{int(val):,}",
                              ha="center", va="bottom", fontsize=8)
        for rect, val in zip(b2, allocs_data[scheme]):
            if val > 0:
                ax_allocs.text(rect.get_x() + rect.get_width() / 2,
                               rect.get_height() * 1.04,
                               f"{int(val)}",
                               ha="center", va="bottom", fontsize=8)

    op_labels = ["KeyGen", "Sign\n(1 KiB)", "Verify\n(1 KiB)"]
    for ax in (ax_bytes, ax_allocs):
        ax.set_xticks(x)
        ax.set_xticklabels(op_labels)
        ax.set_yscale("log")
        ax.grid(True, axis="y", which="major", linestyle="--", alpha=0.4)
        ax.legend(loc="best", frameon=False)

    ax_bytes.set_ylabel("Bytes per operation (log)")
    ax_allocs.set_ylabel("Allocations per operation (log)")

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def create_summary_table(results, out="summary_table.txt"):
    grouped = group_results(results)
    with open(out, "w") as f:
        f.write("=" * 80 + "\n")
        f.write("CRYPTO BENCHMARK SUMMARY\n")
        f.write("secp256k1 (ECDSA) vs ML-DSA-44 (FIPS 204)\n")
        f.write("=" * 80 + "\n\n")

        f.write("KEY GENERATION\n" + "-" * 40 + "\n")
        for r in grouped.get("KeyGen", []):
            f.write(f"  {r['scheme']:12s}: {ns_to_us(r['ns_per_op']):10.2f} μs  "
                    f"({r['bytes_per_op']:6d} B/op, {r['allocs_per_op']:3d} allocs/op)\n")
        f.write("\n")

        for op_name, op_key in (("SIGNING (by message size)", "Sign"),
                                ("VERIFICATION (by message size)", "Verify")):
            f.write(op_name + "\n" + "-" * 40 + "\n")
            for size, lbl in zip(MSG_SIZES, MSG_LABELS):
                f.write(f"  {lbl}:\n")
                for r in grouped.get(op_key, []):
                    if r["msg_size_bytes"] == size:
                        f.write(f"    {r['scheme']:12s}: "
                                f"{ns_to_us(r['ns_per_op']):10.2f} μs\n")
            f.write("\n")

        f.write("BATCH VERIFICATION\n" + "-" * 40 + "\n")
        for batch in (10, 100, 1000):
            f.write(f"  Batch size {batch}:\n")
            for r in grouped.get("BatchVerify", []):
                if r.get("batch_size") == batch:
                    f.write(f"    {r['scheme']:12s}: "
                            f"{ns_to_ms(r['ns_per_op']):10.2f} ms total "
                            f"({ns_to_us(r['ns_per_op'])/batch:.2f} μs/sig)\n")
        f.write("\n")

        f.write("CONCURRENT SIGNING THROUGHPUT (sigs/sec)\n" + "-" * 40 + "\n")
        for gor in (1, 4, 8, 16):
            f.write(f"  {gor} goroutine(s):\n")
            for r in grouped.get("Throughput", []):
                if r.get("goroutines") == gor:
                    tps = 1_000_000_000 / r["ns_per_op"] if r["ns_per_op"] > 0 else 0
                    f.write(f"    {r['scheme']:12s}: {tps:12.0f} sigs/sec\n")
        f.write("\n" + "=" * 80 + "\n")
    print(f"wrote {out}")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    if not os.path.exists("results.json"):
        print("Error: results.json missing — run cmd/ to produce it first.")
        return 1

    results = load_results("results.json")
    grouped = group_results(results)

    plot_keygen_comparison(grouped)
    plot_op_by_msg_size(
        grouped, "Sign",
        "Signing latency (μs, log)",
        "fig_signing_by_msg_size.pdf",
    )
    plot_op_by_msg_size(
        grouped, "Verify",
        "Verification latency (μs, log)",
        "fig_verification_by_msg_size.pdf",
    )
    plot_concurrent_signing(grouped)
    plot_batch_verification(grouped)
    plot_memory_allocs(grouped)
    create_summary_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
