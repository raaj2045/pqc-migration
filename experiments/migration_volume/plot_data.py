#!/usr/bin/env python3
"""Three figures for the Ethereum -> Cosmos migration measurements.

    python3 experiments/migration_volume/plot_data.py [--csv results/latency_by_step.csv]

    fig_latency_by_operation.pdf   how long each step of a migration takes
    fig_time_vs_transactions.pdf   total time against how many transfers move
                                   at once
    gas_1_vs_10.md                 table: gas each step costs, 1 transfer vs 10

Error bars are 95% confidence intervals over the repeats in the CSV. Each
bar's repeat count is printed under it, so a thin interval from few repeats
cannot be mistaken for a well-sampled one.
"""
import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# The steps a migration actually goes through, in order. Fetching the proof is
# folded into the client update: it is an off-chain read that costs no gas and
# runs in hundredths of a second, so its own bar would be invisible.
OPERATIONS = [
    ("T_Submit_s", "Submit\non Ethereum", "#2a78d6"),
    ("T_Wait_Finality_s", "Wait for\nEthereum finality", "#eb6834"),
    ("T_Update_Client_s", "Update\nlight client", "#1baf7a"),
    ("T_Deliver_s", "Deliver\non Cosmos", "#4a3aa7"),
]

# All three are gas PER TRANSFER, so the bars are comparable. The light-client
# update is charged once per batch, so its per-transfer share is the batch cost
# divided by the batch size — that division is the whole reason moving
# transfers together is cheaper, and hiding it would flatten the result.
GAS_OPERATIONS = [
    ("Submit_Gas_Per_Tx", "Submit\non Ethereum", "#2a78d6"),
    ("Update_Gas_Per_Tx", "Update\nlight client", "#1baf7a"),
    ("Deliver_Gas_Per_Tx", "Deliver\non Cosmos", "#4a3aa7"),
]

# One colour per batch size, assigned in a fixed order and never recycled.
SIZE_COLORS = ["#2a78d6", "#eb6834"]

# Signing key types. Same two hues the validator-scaling figures give these
# schemes, so a reader comparing figures across the paper does not relearn the
# mapping; marker shape repeats the distinction so nothing rests on colour.
KEY_ORDER = ["secp256k1", "mldsa65"]
KEY_COLORS = {"secp256k1": "#1f77b4", "mldsa65": "#d62728"}
KEY_LABELS = {"secp256k1": "secp256k1 signer", "mldsa65": "ML-DSA-65 signer"}
KEY_MARKERS = {"secp256k1": "o", "mldsa65": "s"}

# Filled in from the data before anything is drawn; every figure states which
# signing key its numbers came from.
KEY_NOTE = ""

plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 11,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "figure.dpi": 150,
})


def mean_ci(values):
    """Mean and 95% confidence half-width. A single repeat has no interval, so
    it gets zero rather than a NaN that would silently vanish from the plot."""
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return (v.mean() if len(v) else np.nan), 0.0
    return v.mean(), 1.96 * v.std(ddof=1) / np.sqrt(len(v))


def save(fig, name):
    name = Path(__file__).resolve().parent / name
    fig.savefig(name, format="pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {name}")


# --- 1: how long each step takes --------------------------------------------

# Steps that happen once for a whole wave of migrations running together, not
# once per migration. Their repeats are waves, not rows: counting the rows
# would treat one measurement copied across a wave as that many independent
# samples and report an interval far tighter than the data supports.
SHARED_STEPS = {"T_Wait_Finality_s", "T_Update_Client_s"}


def step_samples(sub, col):
    """The independent samples of `col`: one per wave for a shared step, one
    per migration for everything else."""
    if col in SHARED_STEPS and "Wave" in sub.columns:
        return sub.groupby(["Wave", "Signer_Key_Type", "N_Users"])[col].first()
    return sub[col]


def latency_by_operation(df, out, n_users=1):
    """One bar per step. When both signing key types are present they sit side
    by side, which is what makes the legend worth having: the interesting
    result is that the two are indistinguishable on time."""
    sub = df[df["N_Users"] == n_users]
    if sub.empty:
        print(f"skipping {out}: no rows with N_Users == {n_users}")
        return
    keys = [k for k in KEY_ORDER if k in set(sub["Signer_Key_Type"])] or \
        sorted(set(sub["Signer_Key_Type"]))

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    x = np.arange(len(OPERATIONS))
    width = 0.8 / len(keys)
    counts = {}
    for i, k in enumerate(keys):
        kd = sub[sub["Signer_Key_Type"] == k]
        off = (i - (len(keys) - 1) / 2) * width
        means, cis, ns = [], [], []
        for col, _, _ in OPERATIONS:
            samples = step_samples(kd, col)
            m, ci = mean_ci(samples)
            means.append(m)
            cis.append(ci)
            ns.append(len(samples))
        counts[k] = ns
        ax.bar(x + off, means, width * 0.9, yerr=cis, capsize=3,
               color=KEY_COLORS.get(k, "#4a3aa7"), ecolor="#52514e",
               error_kw={"elinewidth": 1.3},
               label=KEY_LABELS.get(k, k), zorder=3)
        for xi, m, ci in zip(x + off, means, cis):
            ax.annotate(f"{m:,.1f}", (xi, m + ci), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=7.5, color="#0b0b0b")

    # The finality wait is ~100x every other step, so a linear axis flattens
    # the rest to nothing. Read the printed value, not the bar height.
    ax.set_yscale("log")
    ax.set_title(f"Time taken by each step of a migration "
                 f"({n_users} transfer{'s' if n_users != 1 else ''})")
    ax.set_ylabel("Seconds (log scale)")
    # Repeat counts go under each step. They differ by step on purpose: waiting
    # for finality and updating the client happen once per group of migrations
    # running together, so their repeats are groups, not migrations.
    # One count per key type, in legend order — they differ (the arms have
    # different numbers of repeats), and showing only the first would misstate
    # the other.
    def n_label(j):
        ns = [counts[k][j] for k in keys]
        return "/".join(str(v) for v in dict.fromkeys(ns)) if len(set(ns)) > 1 else str(ns[0])
    ax.set_xticks(x, [f"{lbl}\n(n={n_label(j)})"
                      for j, (_, lbl, _) in enumerate(OPERATIONS)])
    leg = ax.legend(loc="upper right", frameon=True, framealpha=0.95,
                    edgecolor="#d8d8d4", title="error bars: 95% CI")
    leg.get_title().set_fontsize(8)
    ax.margins(y=0.3)
    save(fig, out)


# --- 2: total time against batch size ---------------------------------------

def time_vs_transactions(df, out):
    keys = [k for k in KEY_ORDER if k in set(df["Signer_Key_Type"])] or \
        sorted(set(df["Signer_Key_Type"]))
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    top = 0
    for k in keys:
        kd = df[df["Signer_Key_Type"] == k]
        sizes = sorted(kd["N_Users"].unique())
        means, cis, counts = [], [], []
        for n in sizes:
            m, ci = mean_ci(kd[kd["N_Users"] == n]["T_Total_s"])
            means.append(m)
            cis.append(ci)
            counts.append(int((kd["N_Users"] == n).sum()))
        ax.errorbar(sizes, means, yerr=cis, fmt=KEY_MARKERS.get(k, "o") + "-",
                    color=KEY_COLORS.get(k, "#4a3aa7"), ecolor=KEY_COLORS.get(k, "#4a3aa7"),
                    elinewidth=1.5, capsize=4, linewidth=2, markersize=7,
                    label=KEY_LABELS.get(k, k), zorder=3)
        # Labels sit BELOW the line: it runs near the top of the axes, so
        # anything above it collides with the legend.
        for n, m, c in zip(sizes, means, counts):
            ax.annotate(f"{m:,.0f}s\n{n / m:.2f}/s\n(n={c})", (n, m),
                        textcoords="offset points", xytext=(0, -14), ha="center",
                        va="top", fontsize=7.5, color="#52514e")
        top = max(top, max(np.array(means) + np.array(cis)))

    ax.set_title("Total time to move a batch of transfers")
    ax.set_xlabel("Transfers moved at once")
    ax.set_ylabel("Total time (seconds)")
    ax.set_xscale("log")
    ax.set_xticks(sorted(df["N_Users"].unique()))
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.set_ylim(0, top * 1.30)
    leg = ax.legend(loc="upper right", frameon=True, framealpha=0.95,
                    edgecolor="#d8d8d4", title="error bars: 95% CI")
    leg.get_title().set_fontsize(8)
    ax.margins(x=0.15)
    save(fig, out)


# --- 3: gas each step costs, one transfer against ten -----------------------

def gas_table(df, out, sizes=(1, 10)):
    """A table, not a chart. Three bars per group with one axis carries less
    than the numbers do, and the interesting quantity here is the ratio between
    two columns rather than the shape of a series.

    Note what this does and does not say. Batch size is how many MIGRATIONS
    move together, not how busy the chain is: a single migration still shares
    its blocks with whatever other traffic is running. What changes between the
    columns is only how many transfers split the costs that are charged once
    per transaction.
    """
    sizes = [n for n in sizes if (df["N_Users"] == n).any()]
    if len(sizes) < 2:
        print(f"skipping {out}: need rows at two batch sizes, have {sizes}")
        return
    a, b = sizes[0], sizes[1]
    sa, sb = df[df["N_Users"] == a], df[df["N_Users"] == b]

    rows = []
    for col, name, _ in GAS_OPERATIONS:
        ma, ca = mean_ci(sa[col])
        mb, cb = mean_ci(sb[col])
        rows.append((name.replace("\n", " "), ma, ca, mb, cb,
                     (mb - ma) / ma * 100 if ma else float("nan")))
    ta = sum(r[1] for r in rows)
    tb = sum(r[3] for r in rows)
    rows.append(("Total per transfer", ta, float("nan"), tb, float("nan"),
                 (tb - ta) / ta * 100 if ta else float("nan")))

    def cell(m, ci):
        return f"{m:,.0f}" + ("" if ci != ci else f" ± {ci:,.0f}")

    lines = [
        f"# Gas per transfer: {a} transfer at once against {b}",
        "",
        f"Signed with {KEY_NOTE.replace('signed with ', '')}. "
        f"n={len(sa)} and n={len(sb)} repeats; ± is a 95% confidence interval.",
        "",
        f"| Step | {a} at once | {b} at once | Change |",
        "|---|---:|---:|---:|",
    ]
    for name, ma, ca, mb, cb, pct in rows:
        bold = "**" if name.startswith("Total") else ""
        lines.append(f"| {bold}{name}{bold} | {bold}{cell(ma, ca)}{bold} | "
                     f"{bold}{cell(mb, cb)}{bold} | {bold}{pct:+.1f}%{bold} |")
    lines += [
        "",
        f"Submitting on Ethereum does not change: each user sends their own",
        f"transaction either way. The light-client update is charged once per",
        f"batch, so {b} transfers split one bill. Delivery carries a fixed cost",
        f"per Cosmos transaction on top of a per-packet cost, and that fixed part",
        f"is split the same way.",
        "",
        f"Batch size here is how many migrations move together, not how busy the",
        f"chain is — a single migration still shares its blocks with other traffic.",
        "",
    ]
    text = "\n".join(lines)
    out = Path(__file__).resolve().parent / out
    with open(out, "w") as f:
        f.write(text)
    print(f"wrote {out}\n")
    print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="results/latency_by_step.csv",
                    help="relative paths resolve against this directory")
    ap.add_argument("--latency-at", type=int, default=1,
                    help="batch size the per-step time figure uses")
    ap.add_argument("--key", default=None,
                    help="signer key type to plot (default: whichever has most rows)")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    csv_path = Path(args.csv) if Path(args.csv).is_absolute() else here / args.csv
    if not csv_path.exists():
        raise SystemExit(f"no measurements at {csv_path}")
    df = pd.read_csv(csv_path)
    if df.empty:
        raise SystemExit(f"{csv_path} has no rows")
    # The light-client update is recorded per batch; every gas bar is shown per
    # transfer, so derive its share here.
    if "Update_Client_Gas" in df.columns:
        df["Update_Gas_Per_Tx"] = df["Update_Client_Gas"] / df["N_Users"]
    missing = [c for c, _, _ in OPERATIONS + GAS_OPERATIONS if c not in df.columns]
    if missing:
        raise SystemExit(f"{csv_path} is missing {missing} — regenerate it with measure_data.py")

    # Never average across signer key types: the signing key changes the gas a
    # Cosmos transaction costs, so a mixed mean is a number from no real run.
    keys = df["Signer_Key_Type"].value_counts()
    key = args.key or keys.index[0]
    if key not in set(df["Signer_Key_Type"]):
        raise SystemExit(f"no rows with Signer_Key_Type == {key!r}; have {list(keys.index)}")
    if len(keys) > 1:
        print(f"note: {csv_path.name} holds {dict(keys)}; figures show both, "
              f"the gas table uses {key!r} (--key to choose)")
    global KEY_NOTE
    KEY_NOTE = f"signed with {key}"
    gas_df = df[df["Signer_Key_Type"] == key]

    latency_by_operation(df, "fig_latency_by_operation.pdf", args.latency_at)
    time_vs_transactions(df, "fig_time_vs_transactions.pdf")
    gas_table(gas_df, "gas_1_vs_10.md")


if __name__ == "__main__":
    main()
