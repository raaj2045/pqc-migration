#!/usr/bin/env python3
"""Plot simulated on-chain account-state growth for secp256k1 vs ML-DSA-44.

Deterministic figure: state size at every tx count is computed by the
storage_sim Go tool from per-tx wire sizes and a closed-form
power-law model for unique-account growth. No randomness, no
measurement error.
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).parent

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


def load(scheme: str, size: str):
    with open(HERE / f"results_{scheme}_{size}.json") as f:
        return json.load(f)


def bytes_to_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def plot_state_growth() -> None:
    fig, ax = plt.subplots(figsize=(6.0, 4.0))

    last_xy = {}
    for scheme, color, label in (
        ("secp256k1", COLOR_SECP, "secp256k1"),
        ("mldsa44", COLOR_MLDSA, "ML-DSA-44"),
    ):
        data = load(scheme, "10m")
        series = [s for s in data["series"] if s["tx_count"] > 0]
        xs = [s["tx_count"] for s in series]
        ys = [s["total_state_bytes"] / (1024 * 1024) for s in series]
        ax.plot(xs, ys, color=color, linewidth=2.0, label=label)
        last_xy[scheme] = (xs[-1], ys[-1])

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Cumulative transactions (log)")
    ax.set_ylabel("Account-state size (MiB, log)")
    ax.grid(True, which="both", linestyle="--", alpha=0.4)
    ax.legend(loc="upper left", frameon=False)

    # Single ratio annotation at the rightmost point.
    final_secp = last_xy["secp256k1"][1]
    final_ml = last_xy["mldsa44"][1]
    ratio = final_ml / final_secp
    x_anchor = last_xy["secp256k1"][0]
    # Bracket spanning the two lines at the right edge.
    ax.annotate(
        "", xy=(x_anchor, final_ml), xytext=(x_anchor, final_secp),
        arrowprops=dict(arrowstyle="<->", color="#555", lw=0.8),
    )
    ax.text(x_anchor, (final_secp * final_ml) ** 0.5,
            f"  {ratio:.2f}×",
            ha="left", va="center", fontsize=10,
            fontweight="bold", color="#222")

    fig.tight_layout()
    out = HERE / "fig_state_growth.pdf"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def write_summary() -> None:
    rows = []
    for size_label, size_key in (("100 K", "100k"), ("1 M", "1m"), ("10 M", "10m")):
        secp = load("secp256k1", size_key)
        ml = load("mldsa44", size_key)
        rows.append({
            "n": size_label,
            "accounts": secp["final_unique_accounts"],
            "secp_state": secp["final_state_bytes"],
            "ml_state": ml["final_state_bytes"],
            "secp_tx": secp["final_tx_bytes"],
            "ml_tx": ml["final_tx_bytes"],
        })

    lines = []
    lines.append("# Storage simulation — secp256k1 vs ML-DSA-44\n")
    lines.append(
        "Default tx mix `transfer:60,migration:20,stake:15,gov:5`. "
        "Account-state storage uses a power-law growth model "
        "(`unique(n) = 1.256 · n^0.8`), giving a ~5% new-signer ratio at 10 M tx. "
        "Sizes are modeled against Cosmos SDK proto types; see "
        "`tools/storage_sim/main.go` for constants and references.\n"
    )

    lines.append("## Per-tx wire size\n")
    lines.append("| Component | secp256k1 | ML-DSA-44 |")
    lines.append("|---|---:|---:|")
    lines.append("| Envelope overhead | 110 B | 110 B |")
    lines.append("| Average message body | 105 B | 105 B |")
    lines.append("| Public key | 33 B | 1312 B |")
    lines.append("| Signature | 64 B | 2420 B |")
    lines.append("| **Total per tx** | **312 B** | **3947 B** |\n")

    lines.append("## Final chain size by N\n")
    lines.append("| N (tx) | Accounts | secp256k1 state | ML-DSA-44 state | State ratio | secp256k1 tx data | ML-DSA-44 tx data | Tx-data ratio |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        state_ratio = r["ml_state"] / r["secp_state"]
        tx_ratio = r["ml_tx"] / r["secp_tx"]
        lines.append(
            f"| {r['n']} | {r['accounts']:,} | "
            f"{bytes_to_human(r['secp_state'])} | {bytes_to_human(r['ml_state'])} | "
            f"{state_ratio:.2f}x | "
            f"{bytes_to_human(r['secp_tx'])} | {bytes_to_human(r['ml_tx'])} | "
            f"{tx_ratio:.2f}x |"
        )
    lines.append("")

    r10 = rows[-1]
    state_ratio = r10["ml_state"] / r10["secp_state"]
    ml_gb = r10["ml_state"] / (1024 ** 3)
    secp_gb = r10["secp_state"] / (1024 ** 3)
    ml_mb = r10["ml_state"] / (1024 ** 2)
    secp_mb = r10["secp_state"] / (1024 ** 2)
    lines.append("## Headline result\n")
    lines.append(
        f"**At 10M transactions, ML-DSA-44 state is {ml_mb:.1f} MiB "
        f"({ml_gb:.3f} GiB) vs secp256k1 {secp_mb:.1f} MiB ({secp_gb:.3f} GiB), "
        f"a ratio of {state_ratio:.2f}x.**\n"
    )
    total_ml = r10["ml_state"] + r10["ml_tx"]
    total_secp = r10["secp_state"] + r10["secp_tx"]
    total_ratio = total_ml / total_secp
    lines.append(
        f"Including historical tx data, the full ledger footprint at 10 M tx is "
        f"{total_ml / (1024**3):.2f} GiB (ML-DSA-44) vs "
        f"{total_secp / (1024**3):.2f} GiB (secp256k1), a ratio of {total_ratio:.2f}x.\n"
    )

    out = HERE / "summary.md"
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    plot_state_growth()
    write_summary()
