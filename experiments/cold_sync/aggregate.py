#!/usr/bin/env python3
"""Produce fig_cold_sync_time.pdf and summary.md from results/<scheme>.json.

The headline plot is deliberately simple: height vs elapsed time, one line
per scheme. That's the picture reviewers actually need to see — a flatter
slope for ML-DSA-44 would demonstrate the faster-verify trade-off.

We also report a compact table: wall-clock time to catch up, peak CPU,
peak memory, total block I/O read/written.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib.pyplot as plt

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "figure.dpi": 150,
})

EXP_DIR = Path(__file__).parent.resolve()
RESULTS_DIR = EXP_DIR / "results"
SCHEMES = ["secp256k1", "mldsa44"]
COLORS = {"secp256k1": "#2E86AB", "mldsa44": "#E94F37"}


# --------------------------------------------------------------------------- #
# docker-stats value parsers (values are human-formatted strings)             #
# --------------------------------------------------------------------------- #
_UNIT = {"B": 1, "kB": 1e3, "KB": 1e3, "KiB": 1024,
         "MB": 1e6, "MiB": 1024**2,
         "GB": 1e9, "GiB": 1024**3,
         "TB": 1e12, "TiB": 1024**4}


def _parse_size(s: str) -> float:
    if s is None:
        return 0.0
    m = re.match(r"^\s*([\d.]+)\s*([A-Za-z]+)\s*$", s.strip())
    if not m:
        return 0.0
    num, unit = m.group(1), m.group(2)
    return float(num) * _UNIT.get(unit, 1)


def _parse_block_io(s: str) -> tuple[float, float]:
    # "1.23MB / 4.56MB"
    if not s or " / " not in s:
        return 0.0, 0.0
    r, w = s.split(" / ", 1)
    return _parse_size(r), _parse_size(w)


def _parse_pct(s: str) -> float:
    if not s:
        return 0.0
    return float(s.rstrip("%"))


def _parse_mem(s: str) -> float:
    # "101.1MiB / 15.2GiB"
    if not s:
        return 0.0
    return _parse_size(s.split(" / ", 1)[0])


# --------------------------------------------------------------------------- #
# Per-run derivations                                                         #
# --------------------------------------------------------------------------- #
def summarise(run: dict) -> dict:
    tel = run.get("telemetry") or {}
    status = tel.get("status_samples") or []
    stats = tel.get("stat_samples") or []

    t0 = tel.get("first_status_ms") or (status[0]["ts_ms"] if status else None)
    caught_ms = tel.get("caught_up_at_ms")
    caught_h = tel.get("caught_up_at_height")

    sync_s = (caught_ms - t0) / 1000.0 if (t0 and caught_ms) else None
    blocks = caught_h or (status[-1]["height"] if status else None)
    bps = blocks / sync_s if (blocks and sync_s and sync_s > 0) else None

    cpu_vals = [_parse_pct(s.get("CPUPerc", "0%")) for s in stats]
    mem_vals = [_parse_mem(s.get("MemUsage", "")) for s in stats]
    io_r = [_parse_block_io(s.get("BlockIO", ""))[0] for s in stats]
    io_w = [_parse_block_io(s.get("BlockIO", ""))[1] for s in stats]

    return {
        "scheme": run.get("scheme"),
        "caught_up": bool(run.get("caught_up")),
        "sync_duration_s": sync_s,
        "tip_height": blocks,
        "blocks_per_s": bps,
        "cpu_peak_pct": max(cpu_vals) if cpu_vals else None,
        "cpu_mean_pct": (sum(cpu_vals) / len(cpu_vals)) if cpu_vals else None,
        "mem_peak_bytes": max(mem_vals) if mem_vals else None,
        "blockio_read_bytes_final": io_r[-1] if io_r else None,
        "blockio_write_bytes_final": io_w[-1] if io_w else None,
    }


# --------------------------------------------------------------------------- #
# Plot                                                                        #
# --------------------------------------------------------------------------- #
def plot_height_vs_time(runs: dict, out_path: Path):
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    for scheme in SCHEMES:
        run = runs.get(scheme)
        if not run:
            continue
        tel = run.get("telemetry") or {}
        status = tel.get("status_samples") or []
        t0 = tel.get("first_status_ms") or (status[0]["ts_ms"] if status else None)
        if t0 is None or not status:
            continue
        xs = [(s["ts_ms"] - t0) / 1000.0 for s in status]
        ys = [s["height"] for s in status]
        ax.plot(xs, ys, color=COLORS.get(scheme, "#444"), linewidth=1.8, label=scheme)
        caught = tel.get("caught_up_at_ms")
        if caught:
            ax.axvline((caught - t0) / 1000.0,
                       color=COLORS.get(scheme, "#444"),
                       linestyle=":", linewidth=0.9, alpha=0.7)
    ax.set_xlabel("elapsed since fresh-node start (s)")
    ax.set_ylabel("latest block height")
    ax.set_title("Cold-sync height trajectory")
    ax.grid(True, linestyle="--", alpha=0.3)
    ax.legend(frameon=True)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Summary markdown                                                            #
# --------------------------------------------------------------------------- #
def write_summary(summaries: dict, outpath: Path):
    def fmt_sec(v): return "—" if v is None else f"{v:.1f} s"
    def fmt_mib(v): return "—" if v is None else f"{v/1024/1024:.1f} MiB"
    def fmt_mb(v):  return "—" if v is None else f"{v/1e6:.1f} MB"
    def fmt_pct(v): return "—" if v is None else f"{v:.0f}%"
    def fmt_num(v): return "—" if v is None else f"{v:.1f}"

    lines = ["# Cold-sync experiment — summary", ""]
    if not summaries:
        lines.append("(no results yet)")
        outpath.write_text("\n".join(lines) + "\n")
        return

    rows = [summaries[s] for s in SCHEMES if s in summaries]
    if len(rows) == 2:
        a, b = rows
        if a["sync_duration_s"] and b["sync_duration_s"]:
            ratio = b["sync_duration_s"] / a["sync_duration_s"]
            lines.append(f"**Headline:** at N={rows[0].get('validators','?')} validators, "
                         f"fresh-node cold sync to {a['tip_height']} blocks took "
                         f"{fmt_sec(a['sync_duration_s'])} for {a['scheme']} vs "
                         f"{fmt_sec(b['sync_duration_s'])} for {b['scheme']} — "
                         f"a ratio of **{ratio:.2f}×** ({b['scheme']}/{a['scheme']}).")
            lines.append("")

    lines.append("| metric | secp256k1 | ML-DSA-44 |")
    lines.append("|---|---:|---:|")

    def row(label, key, fmt):
        a = summaries.get("secp256k1", {}).get(key)
        b = summaries.get("mldsa44", {}).get(key)
        lines.append(f"| {label} | {fmt(a)} | {fmt(b)} |")

    row("wall-clock sync time", "sync_duration_s", fmt_sec)
    row("blocks synced", "tip_height", fmt_num)
    row("blocks/sec", "blocks_per_s", fmt_num)
    row("peak CPU", "cpu_peak_pct", fmt_pct)
    row("mean CPU", "cpu_mean_pct", fmt_pct)
    row("peak memory", "mem_peak_bytes", fmt_mib)
    row("blkio read (final)", "blockio_read_bytes_final", fmt_mb)
    row("blkio write (final)", "blockio_write_bytes_final", fmt_mb)

    lines.append("")
    lines.append("See `fig_cold_sync_time.pdf` for the height-vs-elapsed trajectory.")
    outpath.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", type=Path, default=EXP_DIR)
    args = ap.parse_args()

    runs = {}
    summaries = {}
    for p in sorted(RESULTS_DIR.glob("*.json")):
        try:
            raw = json.loads(p.read_text())
        except Exception as e:
            print(f"warn: {p}: {e}")
            continue
        scheme = raw.get("scheme") or p.stem
        runs[scheme] = raw
        summaries[scheme] = summarise(raw)

    args.outdir.mkdir(parents=True, exist_ok=True)
    plot_height_vs_time(runs, args.outdir / "fig_cold_sync_time.pdf")
    write_summary(summaries, args.outdir / "summary.md")
    (args.outdir / "aggregate.json").write_text(json.dumps(summaries, indent=2, default=str))
    print(f"loaded {len(runs)} result files; wrote figure + summary.md to {args.outdir}")


if __name__ == "__main__":
    main()
