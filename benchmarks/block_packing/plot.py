#!/usr/bin/env python3
"""Plot per-block transaction capacity for secp256k1 vs ML-DSA-44.

Deterministic figure: counts come from arithmetic on per-tx wire sizes
and the configured `consensus_params.block.max_bytes`. No measurement
error; running the script always produces the same figure.
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

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

data = json.loads((HERE / "results.json").read_text())

configs = []
for r in data:
    if r["block_config"] not in configs:
        configs.append(r["block_config"])
by_scheme = {s: {} for s in ("secp256k1", "mldsa44")}
for r in data:
    by_scheme[r["scheme"]][r["block_config"]] = r

fig, ax = plt.subplots(figsize=(6.0, 4.0))
x = np.arange(len(configs))
w = 0.36

secp_counts = [by_scheme["secp256k1"][c]["max_tx_per_block"] for c in configs]
ml_counts = [by_scheme["mldsa44"][c]["max_tx_per_block"] for c in configs]

bars_secp = ax.bar(x - w / 2, secp_counts, w,
                   color=COLOR_SECP, label="secp256k1",
                   edgecolor="black", linewidth=0.4)
bars_ml = ax.bar(x + w / 2, ml_counts, w,
                 color=COLOR_MLDSA, label="ML-DSA-44",
                 edgecolor="black", linewidth=0.4)

# Absolute count on each bar (no ratio annotations — caption carries that).
for bars, vals in ((bars_secp, secp_counts), (bars_ml, ml_counts)):
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.1,
                f"{v:,}", ha="center", va="bottom", fontsize=8)

# X-tick labels: just the size in MiB.
xtick_labels = [
    f"{by_scheme['secp256k1'][c]['block_max_bytes'] / (1024 * 1024):.0f} MiB"
    for c in configs
]
ax.set_xticks(x)
ax.set_xticklabels(xtick_labels)

ax.set_xlabel("Block size limit")
ax.set_ylabel("Transactions per block (log)")
ax.set_yscale("log")
ax.set_ylim(1, max(secp_counts) * 5)
ax.grid(True, axis="y", which="both", linestyle="--", alpha=0.4)
ax.legend(loc="lower right", frameon=False)

fig.tight_layout()
out = HERE / "fig_block_capacity.pdf"
fig.savefig(out)
plt.close(fig)
print(f"wrote {out}")
