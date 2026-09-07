#!/usr/bin/env python3
"""Three figures for the Ethereum -> Cosmos migration measurements.

    python3 plot_data.py [--csv migration_metrics_detailed.csv]

    fig_latency_by_operation.pdf   how long each step of a migration takes
    fig_time_vs_transactions.pdf   total time against how many transfers move
                                   at once
    fig_gas_by_operation.pdf       gas each step costs, 1 transfer vs 10

Error bars are 95% confidence intervals over the repeats in the CSV. Each
bar's repeat count is printed under it, so a thin interval from few repeats
cannot be mistaken for a well-sampled one.
"""
import argparse

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
    fig.savefig(name, format="pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {name}")


# --- 1: how long each step takes --------------------------------------------

def latency_by_operation(df, out, n_users=1):
    sub = df[df["N_Users"] == n_users]
    if sub.empty:
        print(f"skipping {out}: no rows with N_Users == {n_users}")
        return
    names, means, cis, colors, counts = [], [], [], [], []
    for col, label, color in OPERATIONS:
        m, ci = mean_ci(sub[col])
        names.append(label)
        means.append(m)
        cis.append(ci)
        colors.append(color)
        counts.append(len(sub))

    fig, ax = plt.subplots(figsize=(7, 4.4))
    x = np.arange(len(names))
    ax.bar(x, means, 0.6, yerr=cis, color=colors, capsize=4,
           ecolor="#52514e", error_kw={"elinewidth": 1.5}, zorder=3)
    # The finality wait is two orders above the rest, so a linear axis hides
    # every other bar. Log keeps all four readable; the printed value on each
    # bar is what should actually be read off.
    ax.set_yscale("log")
    for xi, m, ci in zip(x, means, cis):
        ax.annotate(f"{m:,.1f}s", (xi, m + ci), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=9, color="#0b0b0b")
    ax.set_title(f"Time taken by each step of a migration\n"
                 f"{n_users} transfer{'s' if n_users != 1 else ''}, {KEY_NOTE}")
    ax.set_ylabel("Seconds (log scale)")
    # Repeat count goes in the tick label rather than under the bar, where it
    # would sit on top of the two-line operation names.
    ax.set_xticks(x, [f"{nm}\n(n={c})" for nm, c in zip(names, counts)])
    ax.margins(y=0.25)
    save(fig, out)


# --- 2: total time against batch size ---------------------------------------

def time_vs_transactions(df, out):
    sizes = sorted(df["N_Users"].unique())
    means, cis, counts = [], [], []
    for n in sizes:
        m, ci = mean_ci(df[df["N_Users"] == n]["T_Total_s"])
        means.append(m)
        cis.append(ci)
        counts.append(int((df["N_Users"] == n).sum()))

    fig, ax = plt.subplots(figsize=(7, 4.4))
    ax.errorbar(sizes, means, yerr=cis, fmt="o-", color="#2a78d6",
                ecolor="#2a78d6", elinewidth=1.5, capsize=4, linewidth=2,
                markersize=7, zorder=3)
    # Labels sit BELOW the line: it runs near the top of the axes, so anything
    # above it collides with the title.
    for n, m, c in zip(sizes, means, counts):
        ax.annotate(f"{m:,.0f}s\n{n / m:.2f}/s\n(n={c})", (n, m),
                    textcoords="offset points", xytext=(0, -14), ha="center",
                    va="top", fontsize=8, color="#52514e")
    ax.set_title(f"Total time to move a batch of transfers\n{KEY_NOTE}")
    ax.set_xlabel("Transfers moved at once")
    ax.set_ylabel("Total time (seconds)")
    ax.set_xscale("log")
    ax.set_xticks(sizes)
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.set_ylim(0, max(np.array(means) + np.array(cis)) * 1.18)
    ax.margins(x=0.15)
    save(fig, out)


# --- 3: gas each step costs, one transfer against ten -----------------------

def gas_by_operation(df, out, sizes=(1, 10)):
    sizes = [n for n in sizes if (df["N_Users"] == n).any()]
    if not sizes:
        print(f"skipping {out}: no rows at N_Users in {sizes}")
        return
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    x = np.arange(len(GAS_OPERATIONS))
    width = 0.8 / len(sizes)
    for i, n in enumerate(sizes):
        sub = df[df["N_Users"] == n]
        off = (i - (len(sizes) - 1) / 2) * width
        means, cis = zip(*(mean_ci(sub[c]) for c, _, _ in GAS_OPERATIONS))
        ax.bar(x + off, np.array(means) / 1e3, width * 0.9,
               yerr=np.array(cis) / 1e3, color=SIZE_COLORS[i % len(SIZE_COLORS)],
               capsize=3, ecolor="#52514e", error_kw={"elinewidth": 1.2},
               label=f"{n} transfer{'s' if n != 1 else ''} at once (n={len(sub)})",
               zorder=3)
        for xi, m in zip(x + off, means):
            ax.annotate(f"{m / 1e3:,.0f}k", (xi, m / 1e3), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=8, color="#0b0b0b")
    ax.set_title(f"Gas per transfer at each step, one transfer against ten\n{KEY_NOTE}")
    ax.set_ylabel("Gas per transfer (thousands)")
    ax.set_xticks(x, [label for _, label, _ in GAS_OPERATIONS])
    ax.legend(frameon=False)
    ax.margins(y=0.2)
    save(fig, out)

    # The question the figure exists to answer, stated in numbers.
    if len(sizes) >= 2:
        a, b = sizes[0], sizes[1]
        sa, sb = df[df["N_Users"] == a], df[df["N_Users"] == b]
        print(f"\ngas per transfer, {a} at once vs {b} at once")
        for col, name, _ in GAS_OPERATIONS:
            pa, pb = sa[col].mean(), sb[col].mean()
            nm = name.replace("\n", " ")
            print(f"  {nm:22} {pa:>10,.0f} -> {pb:>10,.0f}  ({(pb - pa) / pa * 100:+6.1f}%)")
        ta = sum(sa[c].mean() for c, _, _ in GAS_OPERATIONS)
        tb = sum(sb[c].mean() for c, _, _ in GAS_OPERATIONS)
        print(f"  {'TOTAL':22} {ta:>10,.0f} -> {tb:>10,.0f}  ({(tb - ta) / ta * 100:+6.1f}%)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="migration_metrics_detailed.csv")
    ap.add_argument("--latency-at", type=int, default=1,
                    help="batch size the per-step time figure uses")
    ap.add_argument("--key", default=None,
                    help="signer key type to plot (default: whichever has most rows)")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if df.empty:
        raise SystemExit(f"{args.csv} has no rows")
    # The light-client update is recorded per batch; every gas bar is shown per
    # transfer, so derive its share here.
    if "Update_Client_Gas" in df.columns:
        df["Update_Gas_Per_Tx"] = df["Update_Client_Gas"] / df["N_Users"]
    missing = [c for c, _, _ in OPERATIONS + GAS_OPERATIONS if c not in df.columns]
    if missing:
        raise SystemExit(f"{args.csv} is missing {missing} — regenerate it with measure_data.py")

    # Never average across signer key types: the signing key changes the gas a
    # Cosmos transaction costs, so a mixed mean is a number from no real run.
    keys = df["Signer_Key_Type"].value_counts()
    key = args.key or keys.index[0]
    if key not in set(df["Signer_Key_Type"]):
        raise SystemExit(f"no rows with Signer_Key_Type == {key!r}; have {list(keys.index)}")
    if len(keys) > 1:
        print(f"note: {args.csv} holds {dict(keys)}; plotting {key!r} only "
              f"(--key to choose)")
    df = df[df["Signer_Key_Type"] == key]
    global KEY_NOTE
    KEY_NOTE = f"signed with {key}"

    latency_by_operation(df, "fig_latency_by_operation.pdf", args.latency_at)
    time_vs_transactions(df, "fig_time_vs_transactions.pdf")
    gas_by_operation(df, "fig_gas_by_operation.pdf")


if __name__ == "__main__":
    main()
