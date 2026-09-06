#!/usr/bin/env python3
"""Figures for the migration_volume sweep (Ethereum -> Cosmos).

    python3 plot_data.py [--csv migration_metrics_detailed.csv]

Reads the CSV measure_data.py writes and produces:

    migration_latency_ci.pdf            end-to-end latency vs N, per key type
    migration_throughput.pdf            migrations/second vs N, per key type
    cosmos_gas_per_transfer.pdf         gas amortization, per key type
    cosmos_operation_gas_breakdown.pdf  MsgUpdateClient vs MsgRecvPacket gas
    migration_latency_phases.pdf        where the wall-clock time actually goes

Style matches experiments/validator_scaling_v2/aggregate.py, and the key-type
colours are the same two the validator-scaling figures use for the same two
signature schemes.
"""
import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Same hues the validator-scaling figures give these two schemes, so a reader
# comparing figures across the paper does not have to relearn the mapping. The
# pair clears the CVD and normal-vision separation floors; marker shape carries
# the same distinction again, so the figures do not depend on colour.
KEY_COLORS = {"secp256k1": "#1f77b4", "mldsa65": "#d62728"}
KEY_LABELS = {"secp256k1": "secp256k1 signer", "mldsa65": "ML-DSA-65 signer"}
KEY_MARKERS = {"secp256k1": "o", "mldsa65": "s"}
KEY_ORDER = ["secp256k1", "mldsa65"]

# The four wall-clock phases, in the order they occur. "Unattributed" is a
# measured residual, not a phase: it is whatever the four measured spans do not
# account for (process startup, poll granularity, gaps between phases). It gets
# a neutral grey precisely so it does not read as a component of the protocol.
PHASES = [
    ("T_Submit_s", "EVM submission", "#2a78d6"),
    ("T_Finality_Wait_s", "Ethereum finality wait", "#eb6834"),
    ("T_Proof_and_Relay_s", "Proof + relay + Cosmos tx", "#1baf7a"),
    ("T_Unattributed_s", "Unattributed", "#9a9a94"),
]

plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 11,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "figure.dpi": 150,
})


def ci95(std, count):
    """95% CI half-width. Undefined for a single trial, so it is drawn as 0
    rather than as a NaN gap that silently vanishes from the figure."""
    return (1.96 * std / np.sqrt(count)).fillna(0.0)


def by_key_and_n(df, col):
    """(mean, ci, count) for `col`, indexed by (Signer_Key_Type, N_Users)."""
    g = df.groupby(["Signer_Key_Type", "N_Users"])[col].agg(["mean", "std", "count"])
    return g["mean"], ci95(g["std"], g["count"]), g["count"]


def present_keys(df):
    return [k for k in KEY_ORDER if k in set(df["Signer_Key_Type"])] or \
        sorted(set(df["Signer_Key_Type"]))


def save(fig, name):
    fig.tight_layout()
    fig.savefig(name, format="pdf")
    plt.close(fig)
    print(f"Generated {name}")


# --- 1 & 2: one measure against N, one line per key type --------------------

def line_vs_n(df, col, title, ylabel, out, logy=False):
    mean, ci, _ = by_key_and_n(df, col)
    fig, ax = plt.subplots(figsize=(7, 4.4))
    keys = present_keys(df)
    for k in keys:
        n = mean.loc[k].index.values
        ax.errorbar(n, mean.loc[k].values, yerr=ci.loc[k].values,
                    fmt=KEY_MARKERS.get(k, "o") + "-", color=KEY_COLORS.get(k, "#4a3aa7"),
                    ecolor=KEY_COLORS.get(k, "#4a3aa7"), elinewidth=1.5, capsize=3,
                    linewidth=2, markersize=6, label=KEY_LABELS.get(k, k), zorder=3)
    # Identity never rests on colour alone: each series also carries its own
    # marker shape (circle vs square), matched to the legend entry.
    ax.set_title(title + "  (95% CI)")
    ax.set_xlabel("Concurrent migrations in the cohort (N)")
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
    ax.set_xscale("log")
    ax.set_xticks(sorted(set(df["N_Users"])))
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    if len(keys) > 1:
        ax.legend(frameon=False)
    ax.margins(x=0.12)
    save(fig, out)


# --- 3: gas per transfer, the amortization result ---------------------------

def gas_per_transfer(df, out):
    mean, ci, _ = by_key_and_n(df, "Cosmos_Gas_Per_Transfer")
    fig, ax = plt.subplots(figsize=(7, 4.4))
    keys = present_keys(df)
    for k in keys:
        n = mean.loc[k].index.values
        ax.errorbar(n, mean.loc[k].values / 1e3, yerr=ci.loc[k].values / 1e3,
                    fmt=KEY_MARKERS.get(k, "o") + "-", color=KEY_COLORS.get(k, "#4a3aa7"),
                    ecolor=KEY_COLORS.get(k, "#4a3aa7"), elinewidth=1.5, capsize=3,
                    linewidth=2, markersize=6, label=KEY_LABELS.get(k, k), zorder=3)
    # The claim the figure exists to support: the PQ premium is a
    # per-transaction constant, so it shrinks as the batch grows.
    if len(keys) == 2 and all(k in mean.index for k in KEY_ORDER):
        a, b = mean.loc[KEY_ORDER[0]], mean.loc[KEY_ORDER[1]]
        for n in a.index.intersection(b.index):
            pct = (b[n] - a[n]) / a[n] * 100
            ax.annotate(f"{pct:+.1f}%", (n, b[n] / 1e3), textcoords="offset points",
                        xytext=(0, 9), ha="center", fontsize=8, color="#52514e")
    ax.set_title("Cosmos gas per migrated transfer")
    ax.set_xlabel("Concurrent migrations in the cohort (N)")
    ax.set_ylabel("Gas per transfer (thousands)")
    ax.set_xscale("log")
    ax.set_xticks(sorted(set(df["N_Users"])))
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    if len(keys) > 1:
        ax.legend(frameon=False)
    ax.margins(x=0.12, y=0.18)
    save(fig, out)


# --- 4: where the Cosmos gas goes -------------------------------------------

def operation_gas_breakdown(df, out):
    keys = present_keys(df)
    ns = sorted(set(df["N_Users"]))
    upd, _, _ = by_key_and_n(df, "Update_Client_Gas")
    rcv, _, _ = by_key_and_n(df, "Recv_Packet_Gas")

    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    width = 0.8 / len(keys)
    x = np.arange(len(ns))
    for i, k in enumerate(keys):
        off = (i - (len(keys) - 1) / 2) * width
        u = np.array([upd.get((k, n), np.nan) for n in ns]) / 1e6
        r = np.array([rcv.get((k, n), np.nan) for n in ns]) / 1e6
        # A 2px surface gap between stacked segments, per the mark spec:
        # linewidth on a surface-coloured edge.
        ax.bar(x + off, u, width * 0.92, color="#4a3aa7",
               edgecolor="white", linewidth=1.5,
               label="MsgUpdateClient" if i == 0 else None)
        ax.bar(x + off, r, width * 0.92, bottom=u, color="#1baf7a",
               edgecolor="white", linewidth=1.5,
               label="MsgRecvPacket (all N)" if i == 0 else None)
    ax.set_title("Cosmos gas by operation, per receive transaction")
    ax.set_xlabel("Concurrent migrations in the cohort (N)")
    ax.set_ylabel("Gas (millions)")
    _key_ticks(ax, x, [(i - (len(keys) - 1) / 2) * width for i in range(len(keys))],
               keys, ns)
    ax.legend(frameon=False, loc="upper left")
    ax.margins(y=0.18)
    save(fig, out)


# --- 5: where the wall-clock time goes --------------------------------------

SHORT = {"secp256k1": "secp", "mldsa65": "ML-DSA"}


def _key_ticks(ax, x, off_list, keys, ns):
    """Two rows of x labels: the cohort size per group, the key type per bar."""
    ax.set_xticks(x, [str(n) for n in ns])
    ax.set_xticks([xi + off for off in off_list for xi in x], minor=True)
    ax.set_xticklabels([SHORT.get(k, k) for k in keys for _ in ns],
                       minor=True, fontsize=7, color="#52514e")
    ax.tick_params(axis="x", which="minor", length=0, pad=1)
    ax.tick_params(axis="x", which="major", length=0, pad=14)


def latency_phases(df, out):
    """Two panels on ONE time axis each: the full stack, and the same stack with
    the finality wait dropped.

    The finality wait is ~50x every other phase on this direction, so in a
    single panel the other three collapse to a hairline. The right panel is the
    same data with that one segment removed — not a second y-scale on the same
    axes, which would misrepresent the comparison.
    """
    keys = present_keys(df)
    ns = sorted(set(df["N_Users"]))
    means = {c: by_key_and_n(df, c)[0] for c, _, _ in PHASES}
    rest = [p for p in PHASES if p[0] != "T_Finality_Wait_s"]

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.6))
    width = 0.8 / len(keys)
    x = np.arange(len(ns))
    offs = [(i - (len(keys) - 1) / 2) * width for i in range(len(keys))]

    for ax, phases, title in (
        (axes[0], PHASES, "All measured phases"),
        (axes[1], rest, "Excluding the finality wait"),
    ):
        for i, k in enumerate(keys):
            off = offs[i]
            totals = np.array([sum(np.nan_to_num(means[c].get((k, n), np.nan))
                                   for c, _, _ in phases) for n in ns])
            bottom = np.zeros(len(ns))
            for col, label, color in phases:
                v = np.array([means[col].get((k, n), np.nan) for n in ns])
                ax.bar(x + off, v, width * 0.92, bottom=bottom, color=color,
                       edgecolor="white", linewidth=1.5,
                       label=label if (i == 0 and ax is axes[0]) else None)
                # Direct value labels: the aqua and grey segments sit under 3:1
                # against the surface, so the numbers carry the reading.
                for xi, vi, bi, tot in zip(x + off, v, bottom, totals):
                    if np.isfinite(vi) and tot > 0 and vi / tot > 0.10:
                        ax.annotate(f"{vi:.0f}", (xi, bi + vi / 2), ha="center",
                                    va="center", fontsize=7, color="#0b0b0b")
                bottom = bottom + np.nan_to_num(v)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Concurrent migrations in the cohort (N)")
        _key_ticks(ax, x, offs, keys, ns)
        ax.margins(y=0.12)
    axes[0].set_ylabel("Wall-clock time (seconds)")
    fig.suptitle("End-to-end migration latency, by measured phase", fontsize=12)
    fig.legend(frameon=False, loc="lower center", ncol=4, fontsize=9,
               bbox_to_anchor=(0.5, -0.01))
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    fig.savefig(out, format="pdf")
    plt.close(fig)
    print(f"Generated {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="migration_metrics_detailed.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if df.empty:
        raise SystemExit(f"{args.csv} has no rows")
    missing = [c for c in ["Signer_Key_Type", "T_Unattributed_s", "T_Finality_Wait_s"]
               if c not in df.columns]
    if missing:
        raise SystemExit(f"{args.csv} is missing {missing} — regenerate it with measure_data.py")

    trials = df.groupby(["Signer_Key_Type", "N_Users"]).size()
    if (trials < 2).any():
        print("note: some cells have a single trial; their CI bars are drawn as zero")

    line_vs_n(df, "T_Total_Latency_s", "End-to-end migration latency",
              "First submission to voucher credited (s)", "migration_latency_ci.pdf")
    line_vs_n(df, "Throughput_TPS", "Effective cohort throughput",
              "Migrations credited per second", "migration_throughput.pdf")
    gas_per_transfer(df, "cosmos_gas_per_transfer.pdf")
    operation_gas_breakdown(df, "cosmos_operation_gas_breakdown.pdf")
    latency_phases(df, "migration_latency_phases.pdf")


if __name__ == "__main__":
    main()
