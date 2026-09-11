#!/usr/bin/env python3
"""Plot simulated account-state growth from Ethereum -> Cosmos migrations,
secp256k1 vs ML-DSA-65.

Deterministic figure: state size at every tx count is computed by the
storage_sim Go tool from per-tx wire sizes and one new keyless account
per migration. No randomness, no measurement error.
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

    last = {}
    for scheme, color, label in (
        ("secp256k1", COLOR_SECP, "secp256k1"),
        ("mldsa65", COLOR_MLDSA, "ML-DSA-65"),
    ):
        data = load(scheme, "10m")
        series = [s for s in data["series"] if s["tx_count"] > 0]
        xs = [s["tx_count"] for s in series]
        at_mig = [s["total_state_bytes"] / (1024 * 1024) for s in series]
        signed = [s["state_bytes_after_first_signature"] / (1024 * 1024) for s in series]
        ax.plot(xs, signed, color=color, linewidth=2.0, linestyle="--",
                label=f"{label}, after each account signs once")
        ax.plot(xs, at_mig, color=color, linewidth=2.0 if scheme == "secp256k1" else 1.2,
                label=f"{label}, at migration")
        last[scheme] = (xs[-1], at_mig[-1], signed[-1])

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Migrations (log)")
    ax.set_ylabel("Account-state size (MiB, log)")
    ax.grid(True, which="both", linestyle="--", alpha=0.4)
    ax.legend(loc="upper left", frameon=False, fontsize=8)

    x_anchor, secp_mig, secp_signed = last["secp256k1"]
    _, ml_mig, ml_signed = last["mldsa65"]
    ax.annotate("", xy=(x_anchor, ml_signed), xytext=(x_anchor, secp_signed),
                arrowprops=dict(arrowstyle="<->", color="#555", lw=0.8))
    ax.text(x_anchor, (secp_signed * ml_signed) ** 0.5, f"  {ml_signed / secp_signed:.2f}×",
            ha="left", va="center", fontsize=10, fontweight="bold", color="#222")
    ax.text(x_anchor, ml_mig, f"  {ml_mig / secp_mig:.2f}×",
            ha="left", va="center", fontsize=10, fontweight="bold", color="#222")

    fig.tight_layout()
    out = HERE / "fig_state_growth.pdf"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def write_summary() -> None:
    rows = []
    for size_label, size_key in (("100 K", "100k"), ("1 M", "1m"), ("10 M", "10m")):
        secp = load("secp256k1", size_key)
        ml = load("mldsa65", size_key)
        rows.append({"n": size_label, "secp": secp, "ml": ml})

    secp, ml = rows[-1]["secp"], rows[-1]["ml"]
    per_tx = {k: d["final_tx_bytes"] / d["num_tx"] for k, d in (("secp", secp), ("ml", ml))}
    body = per_tx["secp"] - secp["envelope_overhead_bytes"] - secp["pubkey_bytes"] - secp["signature_bytes"]

    lines = []
    lines.append("# Storage simulation — secp256k1 vs ML-DSA-65\n")
    lines.append(
        f"Every transaction is an Ethereum → Cosmos migration: one `MsgRecvPacket` "
        f"(ICS-20 receive, {body:,.0f} B, measured in `experiments/migration_cost/`), "
        "signed by the relayer. Each migration credits a new receiver account — "
        "one per migration — and that account holds **no public key** until its "
        "owner first signs a Cosmos transaction. The scheme is the key type of "
        "every signer: the relayer now, and the migrated users once they sign. "
        "Constants and references are in `tools/storage_sim/main.go`.\n"
    )

    lines.append("## Per-tx wire size\n")
    lines.append("| Component | secp256k1 | ML-DSA-65 |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Envelope overhead | {secp['envelope_overhead_bytes']} B | {ml['envelope_overhead_bytes']} B |")
    lines.append(f"| `MsgRecvPacket` | {body:,.0f} B | {body:,.0f} B |")
    lines.append(f"| Relayer public key | {secp['pubkey_bytes']:,} B | {ml['pubkey_bytes']:,} B |")
    lines.append(f"| Relayer signature | {secp['signature_bytes']:,} B | {ml['signature_bytes']:,} B |")
    lines.append(f"| **Total per tx** | **{per_tx['secp']:,.0f} B** | **{per_tx['ml']:,.0f} B** |\n")

    lines.append("## Account state\n")
    lines.append("| Migrations | Accounts | secp256k1, at migration | ML-DSA-65, at migration | Ratio | secp256k1, after each signs once | ML-DSA-65, after each signs once | Ratio |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        s_, m_ = r["secp"], r["ml"]
        lines.append(
            f"| {r['n']} | {s_['final_unique_accounts']:,} | "
            f"{bytes_to_human(s_['final_state_bytes'])} | {bytes_to_human(m_['final_state_bytes'])} | "
            f"{m_['final_state_bytes'] / s_['final_state_bytes']:.2f}x | "
            f"{bytes_to_human(s_['final_state_bytes_after_first_signature'])} | "
            f"{bytes_to_human(m_['final_state_bytes_after_first_signature'])} | "
            f"{m_['final_state_bytes_after_first_signature'] / s_['final_state_bytes_after_first_signature']:.2f}x |"
        )
    lines.append("")

    lines.append("## Transaction history\n")
    lines.append("| Migrations | secp256k1 | ML-DSA-65 | Ratio |")
    lines.append("|---|---:|---:|---:|")
    for r in rows:
        s_, m_ = r["secp"], r["ml"]
        lines.append(f"| {r['n']} | {bytes_to_human(s_['final_tx_bytes'])} | "
                     f"{bytes_to_human(m_['final_tx_bytes'])} | "
                     f"{m_['final_tx_bytes'] / s_['final_tx_bytes']:.2f}x |")
    lines.append("")

    mib = 1024 ** 2
    gib = 1024 ** 3
    lines.append("## Headline result\n")
    lines.append(
        f"**At 10 M migrations, account state is {secp['final_state_bytes'] / mib:,.1f} MiB "
        f"under either scheme** — {ml['final_state_bytes'] / secp['final_state_bytes']:.2f}x. "
        "A migration stores a keyless account, so the receiver's key type costs "
        "nothing; the only difference is the relayer's one stored key.\n"
    )
    lines.append(
        f"The post-quantum key is paid when each migrated user first signs: if all "
        f"10 M do, state reaches {ml['final_state_bytes_after_first_signature'] / gib:.2f} GiB "
        f"(ML-DSA-65) vs {secp['final_state_bytes_after_first_signature'] / gib:.2f} GiB "
        f"(secp256k1), "
        f"{ml['final_state_bytes_after_first_signature'] / secp['final_state_bytes_after_first_signature']:.2f}x.\n"
    )
    lines.append(
        f"Transaction history at 10 M migrations is {ml['final_tx_bytes'] / gib:.2f} GiB (ML-DSA-65) "
        f"vs {secp['final_tx_bytes'] / gib:.2f} GiB (secp256k1), "
        f"{ml['final_tx_bytes'] / secp['final_tx_bytes']:.2f}x. The Merkle-Patricia proof "
        "inside each `MsgRecvPacket` is the same under both schemes, so history "
        "roughly doubles rather than growing with the key-size ratio.\n"
    )
    lines.append(
        "**Limitation — not modelled:** the per-migration state ICS-20 writes "
        "whatever the key type: the packet receipt, the acknowledgement "
        "commitment and the voucher balance. Including it would pull both "
        "totals up by the same amount and push every ratio above closer to 1.\n"
    )

    out = HERE / "summary.md"
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    plot_state_growth()
    write_summary()
