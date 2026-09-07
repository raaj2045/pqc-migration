#!/usr/bin/env python3
"""Figures and tables for the Ethereum -> Cosmos migration measurements.

    python3 experiments/migration_volume/plot_data.py

Reads everything under results/ and writes:

    fig_time_by_step.pdf     how long each step of a migration takes
    fig_time_by_batch.pdf    total time against how many transfers move at once
    cost_by_batch.md         gas per transfer on each leg, by batch size

Error bars and the ± in the table are 95% confidence intervals. Repeat counts
are printed on every figure and in every table row, because they differ between
steps: waiting for Ethereum finality and updating the light client happen once
for a whole group of migrations, so their repeats are groups, not migrations.
"""
import argparse
import csv
import glob
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

# The steps a migration goes through, in order. Fetching the proof is left out:
# it is an off-chain read costing no gas and hundredths of a second, so its bar
# would be invisible.
STEPS = [
    ("T_Submit_s", "Submit\non Ethereum", "#2a78d6"),
    ("T_Wait_Finality_s", "Wait for\nEthereum finality", "#eb6834"),
    ("T_Update_Client_s", "Update\nlight client", "#1baf7a"),
    ("T_Deliver_s", "Deliver\non Cosmos", "#4a3aa7"),
]

# Steps that happen once for a whole group of migrations rather than once per
# migration. Their repeats are groups: counting rows would treat one
# measurement copied across a group as that many independent samples.
SHARED_STEPS = {"T_Wait_Finality_s", "T_Update_Client_s"}

# Signing key types, in a fixed order, with the same two colours the
# validator-scaling figures give these schemes. Marker shape repeats the
# distinction so nothing depends on colour alone.
KEY_ORDER = ["secp256k1", "mldsa65"]
KEY_COLORS = {"secp256k1": "#1f77b4", "mldsa65": "#d62728"}
KEY_LABELS = {"secp256k1": "secp256k1 signer", "mldsa65": "ML-DSA-65 signer"}
KEY_MARKERS = {"secp256k1": "o", "mldsa65": "s"}

plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update({
    "font.family": "serif", "font.size": 11, "axes.titlesize": 12,
    "axes.labelsize": 11, "legend.fontsize": 9, "figure.dpi": 150,
})


def mean_ci(values):
    """Mean and 95% confidence half-width. One sample has no interval, so it
    gets zero rather than a gap that would vanish silently from a figure."""
    v = np.asarray(list(values), dtype=float)
    if len(v) == 0:
        return float("nan"), 0.0
    if len(v) < 2:
        return v.mean(), 0.0
    return v.mean(), 1.96 * v.std(ddof=1) / np.sqrt(len(v))


def keys_present(df):
    return [k for k in KEY_ORDER if k in set(df["Signer_Key_Type"])] or \
        sorted(set(df["Signer_Key_Type"]))


def read_many(pattern):
    """Concatenate every results CSV matching a glob, or None if there are none."""
    files = sorted(glob.glob(str(RESULTS / pattern)))
    frames = [pd.read_csv(f) for f in files if Path(f).stat().st_size]
    return pd.concat(frames, ignore_index=True) if frames else None


def save(fig, name):
    out = HERE / name
    fig.savefig(out, format="pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {name}")


def legend(ax, title="error bars: 95% CI"):
    leg = ax.legend(loc="upper right", frameon=True, framealpha=0.95,
                    edgecolor="#d8d8d4", title=title)
    leg.get_title().set_fontsize(8)


# --- 1: how long each step takes --------------------------------------------

def fig_time_by_step(df, out, batch=1):
    sub = df[df["N_Users"] == batch]
    if sub.empty:
        print(f"skipping {out}: no rows at batch size {batch}")
        return
    keys = keys_present(sub)
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    x = np.arange(len(STEPS))
    width = 0.8 / len(keys)
    counts = {}

    for i, k in enumerate(keys):
        kd = sub[sub["Signer_Key_Type"] == k]
        off = (i - (len(keys) - 1) / 2) * width
        means, cis, ns = [], [], []
        for col, _, _ in STEPS:
            # A shared step is sampled once per group, not once per migration.
            samples = (kd.groupby("Wave")[col].first() if col in SHARED_STEPS
                       and "Wave" in kd.columns else kd[col])
            m, ci = mean_ci(samples)
            means.append(m); cis.append(ci); ns.append(len(samples))
        counts[k] = ns
        ax.bar(x + off, means, width * 0.9, yerr=cis, capsize=3,
               color=KEY_COLORS.get(k, "#4a3aa7"), ecolor="#52514e",
               error_kw={"elinewidth": 1.3}, label=KEY_LABELS.get(k, k), zorder=3)
        for xi, m, ci in zip(x + off, means, cis):
            ax.annotate(f"{m:,.1f}", (xi, m + ci), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=7.5, color="#0b0b0b")

    # The finality wait is around a hundred times every other step, so a linear
    # axis flattens the rest to nothing. Read the printed value, not the height.
    ax.set_yscale("log")
    ax.set_title(f"Time taken by each step of a migration "
                 f"({batch} transfer{'s' if batch != 1 else ''})")
    ax.set_ylabel("Seconds (log scale)")

    def n_for(j):
        ns = [counts[k][j] for k in keys]
        return "/".join(str(v) for v in dict.fromkeys(ns))
    ax.set_xticks(x, [f"{lbl}\n(n={n_for(j)})" for j, (_, lbl, _) in enumerate(STEPS)])
    legend(ax)
    ax.margins(y=0.3)
    save(fig, out)


# --- 2: total time against batch size ---------------------------------------

def fig_time_by_batch(delivery, out):
    """Total time for one batch: the Ethereum finality wait plus delivery.

    Both come from the same run. The wait is charged in full because a batch on
    its own would pay all of it. Submitting on Ethereum is not included -- users
    do that themselves, before the bridge is involved.
    """
    if delivery is None:
        print(f"skipping {out}: no delivery results")
        return
    d = delivery.copy()
    d["Total_s"] = d["Gen_Wait_Finality_s"] + d["T_Deliver_s"]
    keys = keys_present(d)

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    top = 0
    for k in keys:
        kd = d[d["Signer_Key_Type"] == k]
        sizes = sorted(kd["Batch_Size"].unique())
        means, cis, counts = [], [], []
        for n in sizes:
            m, ci = mean_ci(kd[kd["Batch_Size"] == n]["Total_s"])
            means.append(m); cis.append(ci)
            counts.append(int((kd["Batch_Size"] == n).sum()))
        ax.errorbar(sizes, means, yerr=cis, fmt=KEY_MARKERS.get(k, "o") + "-",
                    color=KEY_COLORS.get(k, "#4a3aa7"), ecolor=KEY_COLORS.get(k, "#4a3aa7"),
                    elinewidth=1.5, capsize=4, linewidth=2, markersize=7,
                    label=KEY_LABELS.get(k, k), zorder=3)
        # Labels below the line: it sits near the top of the axes.
        for n, m, c in zip(sizes, means, counts):
            ax.annotate(f"{m:,.0f}s\n{n / m:.2f}/s\n(n={c})", (n, m),
                        textcoords="offset points", xytext=(0, -14), ha="center",
                        va="top", fontsize=7.5, color="#52514e")
        top = max(top, max(np.array(means) + np.array(cis)))

    ax.set_title("Total time to move a batch of transfers\n"
                 "Ethereum finality wait plus delivery; rate shown per point")
    ax.set_xlabel("Transfers moved at once")
    ax.set_ylabel("Total time (seconds)")
    ax.set_xscale("log")
    ax.set_xticks(sorted(d["Batch_Size"].unique()))
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.set_ylim(0, top * 1.30)
    legend(ax)
    ax.margins(x=0.15)
    save(fig, out)


# --- 3: gas per transfer on each leg ----------------------------------------

def cost_by_batch(delivery, ack, out, ceiling=56):
    """A table, not a chart: the interesting quantity is the ratio between
    columns, which numbers carry better than bar heights."""
    if delivery is None:
        print(f"skipping {out}: no delivery results")
        return
    lines = [
        "# Gas per transfer, by how many move at once",
        "",
        "Delivery is Ethereum -> Cosmos, acknowledgement is Cosmos -> Ethereum.",
        "Each figure is gas for ONE transfer; ± is a 95% confidence interval and",
        "n is the number of runs behind it.",
        "",
    ]
    for k in keys_present(delivery):
        kd = delivery[delivery["Signer_Key_Type"] == k]
        ka = ack[ack["Signer_Key_Type"] == k] if ack is not None else None
        lines += [f"## {KEY_LABELS.get(k, k)}", "",
                  "| Transfers at once | Deliver on Cosmos | Acknowledge on Ethereum |",
                  "|---:|---:|---:|"]
        for n in sorted(kd["Batch_Size"].unique()):
            dm, dc = mean_ci(kd[kd["Batch_Size"] == n]["Deliver_Gas_Per_Tx"])
            dn = int((kd["Batch_Size"] == n).sum())
            cell_d = f"{dm:,.0f} ± {dc:,.0f} (n={dn})"
            cell_a = "—"
            if ka is not None and (ka["Batch_Size"] == n).any():
                am, ac = mean_ci(ka[ka["Batch_Size"] == n]["Ack_Gas_Per_Packet"])
                an = int((ka["Batch_Size"] == n).sum())
                cell_a = f"{am:,.0f} ± {ac:,.0f} (n={an})"
            elif n > ceiling:
                cell_a = f"over the {ceiling}-ack limit"
            lines.append(f"| {n} | {cell_d} | {cell_a} |")
        lines.append("")

    lines += [
        "## Reading it",
        "",
        f"Cost per transfer falls as more move together, because the costs charged",
        f"once per transaction — the light-client update, the per-transaction",
        f"overhead and the signature — are divided among more transfers. Both legs",
        f"flatten well before the {ceiling}-acknowledgement limit, so that limit",
        f"costs nothing in gas; it only caps how many transfers one batch may hold.",
        "",
        "An acknowledgement batch is whatever one delivery produced, and the",
        f"multicall carrying it cannot be split, so delivering more than ~{ceiling}",
        "transfers at once leaves them impossible to acknowledge.",
        "",
    ]
    text = "\n".join(lines)
    (HERE / out).write_text(text)
    print(f"wrote {out}\n")
    print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", type=int, default=1,
                    help="batch size the per-step figure uses")
    args = ap.parse_args()

    latency_path = RESULTS / "latency_by_step.csv"
    if latency_path.exists() and latency_path.stat().st_size:
        fig_time_by_step(pd.read_csv(latency_path), "fig_time_by_step.pdf", args.batch)
    else:
        print(f"skipping fig_time_by_step.pdf: no {latency_path.name}")

    delivery = read_many("delivery_*.csv")
    ack = read_many("ack_*.csv")
    if ack is not None:
        # The ack results record the signing key by name, not by algorithm.
        ack["Signer_Key_Type"] = np.where(
            ack["Run_Label"].str.contains("relayer"), "mldsa65", "secp256k1")

    fig_time_by_batch(delivery, "fig_time_by_batch.pdf")
    cost_by_batch(delivery, ack, "cost_by_batch.md")


if __name__ == "__main__":
    main()
