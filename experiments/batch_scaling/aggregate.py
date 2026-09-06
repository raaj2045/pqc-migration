#!/usr/bin/env python3
"""Aggregation for the batch-scaling sweep.

For each group size, across its successful repeats:
  - average total time (submission -> every packet acked), with a 95% CI
  - average throughput (group_size / that repeat's total time), with a 95% CI
  - average gas cost per transfer within the batch
  - the light-client-update-vs-per-transfer gas split, on both legs where it
    can be measured or estimated (see "Gas split methodology" below)

A group size with any failed repeat is still listed — as a failure, with
whatever partial data exists — never silently dropped. Statistics (mean, CI)
are computed only over the repeats that actually completed (status == "ok").

Gas split methodology
----------------------
Return leg (EVM -> Cosmos ack): exact, always. step-ack.js reports the
MsgUpdateClient gas separately from the MsgAcknowledgement gas per Cosmos tx
(loadgen.py records this per packet), so the update-client cost for a window
is just the (usually single) nonzero value among the packets acked in it.

Forward leg (Cosmos -> EVM relay): exact when relay-batch.js's
debug_traceTransaction call trace was available (per-cell "traceAvailable"),
giving the updateClient call frame's gas directly, separate from every
recvPacket call frame. That single-account, single-transaction path
(relay-batch.js) is no longer what loadgen.py uses by default — it now
chunks and pools the relay across an EVM account pool (relay_pool.py), which
records exact per-chunk light-client-update counts (batch_relay's
numUpdateClientCalls / numChunks — see README.md's "Chunked relay: true vs
idealized amortization"), a coarser but still exact signal, rather than a
per-call-frame gas split. When neither is available for a cell, this script
instead fits total forward-leg gas = intercept + slope * group_size by
ordinary least squares across the group sizes that DO have data, and reports
the intercept as an ESTIMATED light-client-update cost and the slope as an
ESTIMATED per-transfer cost. This is clearly labeled as an estimate in the
output and
is only used as a fallback.

Outputs:
  results/raw_packets.csv   one row per packet (plottable)
  results/by_group.csv      one row per (group_size, repeat)
  results/summary.md        summary tables
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

T_CRIT = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
          6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ci95(xs):
    n = len(xs)
    if n < 2:
        return float("nan")
    t = T_CRIT.get(n - 1, 1.96)
    return t * stdev(xs) / math.sqrt(n)


def ols(xs, ys):
    """Simple OLS: returns (intercept, slope). NaNs if underdetermined."""
    n = len(xs)
    if n < 2 or len(set(xs)) < 2:
        return float("nan"), float("nan")
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return float("nan"), float("nan")
    slope = sxy / sxx
    intercept = my - slope * mx
    return intercept, slope


def load_runs():
    runs = []
    for p in sorted(RESULTS.glob("G*_rep*.json")):
        runs.append(json.loads(p.read_text()))
    return runs


def total_batch_gas(run) -> float | None:
    """Forward-leg total (from batch-relay.json) + every packet's return-leg
    ack gas + return-leg update-client gas, summed across every packet that
    recorded one. update_client_gas_return_leg is nonzero only on a packet
    whose ack actually triggered a real MsgUpdateClient — usually just the
    first ack after a window opens, but NOT always: at large group sizes the
    ack phase itself can span multiple real Ethereum finality epochs (each
    ack is submitted sequentially, so total ack wall-clock time grows with
    group size), which forces more than one genuine update within what
    loadgen.py still counts as a single "window" iteration. Summing
    unconditionally (not deduping to "first per window") is what captures
    that TRUE cost instead of an idealized one — see
    experiments/batch_scaling/README.md's "Chunked relay: true vs idealized
    amortization" section.
    """
    br = run.get("batch_relay") or {}
    forward = br.get("totalGas")
    if forward is None:
        return None
    total = int(forward)
    for p in run["packets"]:
        total += int(p.get("ack_gas", 0) or 0)
        total += int(p.get("update_client_gas_return_leg", 0) or 0)
    return total


def write_raw_packets(runs):
    out = RESULTS / "raw_packets.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_size", "ack_pool_size", "repeat", "seq", "status", "window_id",
                    "submit_ts", "commit_ts", "credited_ts", "credit_latency_s",
                    "ack_ts", "roundtrip_latency_s",
                    "ack_gas", "update_client_gas_return_leg"])
        for r in runs:
            for p in r["packets"]:
                cl = (p["credited_ts"] - p["submit_ts"]) if p.get("credited_ts") else ""
                rl = (p["ack_ts"] - p["submit_ts"]) if p.get("ack_ts") else ""
                w.writerow([r["group_size"], r.get("ack_pool_size", 1), r["repeat"], p["seq"],
                            p["status"], p["window_id"], p["submit_ts"], p["commit_ts"],
                            p.get("credited_ts", 0), cl, p.get("ack_ts", 0), rl,
                            p.get("ack_gas", ""), p.get("update_client_gas_return_leg", "")])
    return out


def per_run_rows(runs):
    rows = []
    for r in runs:
        g = r["group_size"]
        ok = r.get("status") == "ok"
        total_time = float("nan")
        if ok:
            acked = [p for p in r["packets"] if p["status"] == "acked"]
            submits = [p["submit_ts"] for p in r["packets"]]
            if len(acked) == g and submits:
                total_time = max(p["ack_ts"] for p in acked) - min(submits)
        tg = total_batch_gas(r) if ok else None
        br = r.get("batch_relay") or {}
        ls = r.get("latency_stats") or {}
        credit_d = ls.get("credit_latency_s") or {}
        rt_d = ls.get("round_trip_latency_s") or {}
        rows.append({
            "group_size": g,
            "ack_pool_size": r.get("ack_pool_size", 1),
            "repeat": r["repeat"],
            "status": r.get("status"),
            "failure_kind": r.get("failure_kind", ""),
            "acked": r.get("acked", 0),
            "windows_used": r.get("windows_used", 0),
            "total_time_s": total_time,
            "throughput_tps": (g / total_time) if ok and total_time and total_time > 0 else float("nan"),
            "total_gas": tg if tg is not None else float("nan"),
            "gas_per_transfer": (tg / g) if (ok and tg is not None) else float("nan"),
            "forward_total_gas": br.get("totalGas", ""),
            "forward_trace_available": br.get("traceAvailable", False),
            "forward_update_client_gas": br.get("updateClientGas", ""),
            "credit_latency_mean_s": credit_d.get("mean", ""),
            "credit_latency_median_s": credit_d.get("median", ""),
            "credit_latency_p95_s": credit_d.get("p95", ""),
            "round_trip_latency_mean_s": rt_d.get("mean", ""),
            "round_trip_latency_median_s": rt_d.get("median", ""),
            "round_trip_latency_p95_s": rt_d.get("p95", ""),
        })
    return rows


def write_by_group(rows):
    out = RESULTS / "by_group.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return out


def cell_key(r):
    """(group_size, ack_pool_size): ack pooling changes total_time_s and
    gas_per_transfer materially (it's the whole point of this axis — see
    README.md's "Pooled return-leg acks" and "Chunked relay: true vs
    idealized amortization" sections), so grouping by group_size alone
    would silently average together two different configurations' results
    if both are ever swept at the same group size.
    """
    return (r["group_size"], r.get("ack_pool_size", 1))


def group(rows, key, only_ok=True):
    g = {}
    for r in rows:
        if only_ok and r["status"] != "ok":
            continue
        v = r[key]
        if isinstance(v, float) and math.isnan(v):
            continue
        g.setdefault(cell_key(r), []).append(v)
    return g


def forward_leg_gas_split(runs):
    """Per group size: exact split if every 'ok' repeat at that size had a
    trace, else the size is excluded from the exact table and covered by the
    regression fallback instead.
    """
    exact = {}
    regression_points = []  # (group_size, forward_total_gas)
    for r in runs:
        if r.get("status") != "ok":
            continue
        br = r.get("batch_relay") or {}
        forward_total = br.get("totalGas")
        if forward_total is None:
            continue
        regression_points.append((r["group_size"], int(forward_total)))
        if br.get("traceAvailable") and br.get("updateClientGas") is not None:
            exact.setdefault(r["group_size"], []).append({
                "update_client_gas": int(br["updateClientGas"]),
                "per_packet_gas": [int(x) for x in (br.get("perPacketGas") or [])],
            })
    return exact, regression_points


def write_summary(runs, rows):
    sizes = sorted({r["group_size"] for r in rows})
    cells = sorted({cell_key(r) for r in rows})
    time_g = group(rows, "total_time_s")
    tps_g = group(rows, "throughput_tps")
    gpt_g = group(rows, "gas_per_transfer")
    status_by_cell = {}
    for r in rows:
        status_by_cell.setdefault(cell_key(r), []).append(r["status"])

    exact_split, reg_points = forward_leg_gas_split(runs)

    L = []
    L.append("# Batch scaling — results\n")
    L.append("Transfer-mechanism scaling on the bridge's forward leg (Cosmos -> EVM), "
             "measured against SP1MockVerifier so proving time (~10 min/proof against the "
             "real Groth16 verifier, measured separately) does not confound the result. "
             "See README.md.\n")
    L.append(f"Group sizes swept: {', '.join(str(s) for s in sizes)}. "
             f"Cells (group size × ack_pool_size): {len(cells)}.\n")

    L.append("\n## Per cell (group size × ack_pool_size)\n")
    L.append("Rows with ack_pool_size > 1 used the pooled return-leg ack path (see README.md's "
             "\"Pooled return-leg acks\") — kept as separate rows from ack_pool_size=1 at the same "
             "group size deliberately, since pooling changes total time and gas/transfer "
             "materially and averaging them together would hide that.\n")
    L.append("| group size | ack pool | ok/attempted | mean time (s) | 95% CI | "
             "mean throughput (transfers/s) | 95% CI | mean gas/transfer |")
    L.append("|---|---|---|---|---|---|---|---|")
    for key in cells:
        g, ack_ps = key
        statuses = status_by_cell[key]
        ok_n = sum(1 for s in statuses if s == "ok")
        t = time_g.get(key, [])
        tp = tps_g.get(key, [])
        gp = gpt_g.get(key, [])
        t_str = f"{mean(t):.1f}" if t else "—"
        t_ci = f"±{ci95(t):.1f}" if len(t) >= 2 else ("n/a" if t else "—")
        tp_str = f"{mean(tp):.3f}" if tp else "—"
        tp_ci = f"±{ci95(tp):.3f}" if len(tp) >= 2 else ("n/a" if tp else "—")
        gp_str = f"{mean(gp):,.0f}" if gp else "—"
        L.append(f"| {g} | {ack_ps} | {ok_n}/{len(statuses)} | {t_str} | {t_ci} | "
                 f"{tp_str} | {tp_ci} | {gp_str} |")

    L.append("\n## Per-transaction latency (per cell, not averaged across repeats — "
             "percentiles don't combine that way)\n")
    L.append("| group size | ack pool | repeat | credit mean/median/p95 (s) | "
             "round-trip mean/median/p95 (s) |")
    L.append("|---|---|---|---|---|")
    for r in runs:
        if r.get("status") != "ok":
            continue
        ls = r.get("latency_stats") or {}
        cd, rt = ls.get("credit_latency_s") or {}, ls.get("round_trip_latency_s") or {}
        if not cd.get("n") and not rt.get("n"):
            continue
        c_str = (f"{cd['mean']:.1f}/{cd['median']:.1f}/{cd['p95']:.1f}" if cd.get("n") else "—")
        rt_str = (f"{rt['mean']:.1f}/{rt['median']:.1f}/{rt['p95']:.1f}" if rt.get("n") else "—")
        L.append(f"| {r['group_size']} | {r.get('ack_pool_size', 1)} | {r['repeat']} | "
                 f"{c_str} | {rt_str} |")

    failing = [(key, [s for s in status_by_cell[key] if s != 'ok']) for key in cells]
    failing = [(key, fails) for key, fails in failing if fails]
    if failing:
        L.append("\n### Failures\n")
        for key, fails in failing:
            g, ack_ps = key
            examples = [r["failure_kind"] for r in rows if cell_key(r) == key and r["status"] != "ok"]
            L.append(f"- group_size={g} ack_pool_size={ack_ps}: {len(fails)} failing repeat(s) — "
                     f"{', '.join(examples[:5])}")
        largest_ok = max([g for g in sizes if all(
            status_by_cell[key].count("ok") == len(status_by_cell[key])
            for key in cells if key[0] == g)], default=None)
        if largest_ok is not None:
            L.append(f"\n**Largest group size with all repeats (all ack_pool_size configs at "
                      f"that size) succeeding: {largest_ok}.** Sizes above the first failure "
                      f"were not attempted (see run_sweep.py's escalation rule).\n")

    L.append("\n## Forward-leg gas split: light-client update vs per-packet recv\n")
    if exact_split:
        L.append("Exact, from relay-batch.js's call trace (debug_traceTransaction), "
                 "averaged over cells where the trace was available. (Newer, chunked/pooled "
                 "cells report numUpdateClientCalls/numChunks instead — see batch_relay in "
                 "the raw per-cell JSON, and README.md's \"Chunked relay\" section.)\n")
        L.append("| group size | mean update-client gas | mean per-packet recv gas | n cells with trace |")
        L.append("|---|---|---|---|")
        for g in sorted(exact_split):
            entries = exact_split[g]
            upd = mean([e["update_client_gas"] for e in entries])
            per_pkt_all = [x for e in entries for x in e["per_packet_gas"]]
            per_pkt = mean(per_pkt_all) if per_pkt_all else float("nan")
            L.append(f"| {g} | {upd:,.0f} | {per_pkt:,.0f} | {len(entries)} |")
    sizes_without_trace = [g for g in sizes if g not in exact_split and any(
        r["group_size"] == g and r["status"] == "ok" for r in rows)]
    if sizes_without_trace:
        intercept, slope = ols([x for x, _ in reg_points], [y for _, y in reg_points])
        if not math.isnan(intercept):
            L.append(f"\n**ESTIMATED** (debug_traceTransaction unavailable for group size(s) "
                     f"{', '.join(str(s) for s in sizes_without_trace)}): fitting "
                     f"forward-leg total gas = a + b*group_size by OLS across all cells with "
                     f"data gives light-client-update ≈ **{intercept:,.0f} gas**, "
                     f"per-packet recv ≈ **{slope:,.0f} gas**. This is a regression estimate, "
                     f"not a per-call measurement — see this file's module docstring.\n")
        else:
            L.append("\n**No gas split available** — neither a call trace nor enough distinct "
                     "group sizes with data to fit a regression.\n")
    if not exact_split and not sizes_without_trace:
        L.append("\nNo successful cells with forward-leg gas data yet.\n")

    (RESULTS / "summary.md").write_text("\n".join(L) + "\n")


def main():
    runs = load_runs()
    if not runs:
        print("no result files in results/ — nothing to aggregate")
        return
    write_raw_packets(runs)
    rows = per_run_rows(runs)
    write_by_group(rows)
    write_summary(runs, rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"aggregated {len(runs)} runs ({ok} ok, {len(runs) - ok} failed)")


if __name__ == "__main__":
    main()
