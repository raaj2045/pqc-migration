#!/usr/bin/env python3
"""migration_throughput sweep orchestrator.

For each (rate, repeat) cell, run loadgen.py once and write a result JSON.
Resumable: an existing result file is never overwritten, so an interrupted
sweep continues where it stopped.

Hard rules (do not override autonomously):
  - never overwrite an existing result file
  - 3 consecutive *infrastructure* failures abort the sweep
  - a saturated cell (low success rate, high latency) is DATA, not failure
  - total wall-clock budget; stop after the current cell when exceeded

The wall-clock cost is dominated by real Ethereum finality, which is not
shortcut anywhere: each cell waits for the finalized checkpoint to advance
before relaying. At 6 s slots and 32-slot epochs a finality window is roughly
6.4 minutes, so plan on hours, not minutes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
LOGS = HERE / "logs"
STATE = HERE / "sweep_state.json"

DEFAULT_NS = [1, 5, 10, 20, 40]
DEFAULT_REPEATS = 5


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"done": [], "failed": []}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=2))


def cell_path(n, rep):
    return RESULTS / f"N{n}_rep{rep}.json"


def run_cell(n, rep, timeout) -> tuple[bool, str]:
    out = cell_path(n, rep)
    if out.exists():
        return True, "already present (resume)"

    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"N{n}_rep{rep}.log"
    cmd = [sys.executable, str(HERE / "loadgen.py"),
           "--n", str(n), "--repeat", str(rep), "--out", str(out)]

    with log_path.open("w") as lf:
        try:
            r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s (see {log_path.name})"
    if r.returncode != 0:
        return False, f"loadgen exited {r.returncode} (see {log_path.name})"
    if not out.exists():
        return False, "loadgen produced no result file"
    return True, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", type=int, nargs="+", default=DEFAULT_NS,
                    help="packets submitted per finality window")
    ap.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    ap.add_argument("--cell-timeout", type=int, default=3600,
                    help="hard timeout per cell, seconds")
    ap.add_argument("--budget-hours", type=float, default=12.0)
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    st = load_state()
    t0 = time.time()
    consecutive_failures = 0

    total = len(args.ns) * args.repeats
    i = 0
    for nval in args.ns:
        for rep in range(args.repeats):
            i += 1
            key = f"N{nval}_rep{rep}"
            if key in st["done"] or cell_path(nval, rep).exists():
                print(f"[{i}/{total}] {key}: skip (done)", flush=True)
                continue

            elapsed_h = (time.time() - t0) / 3600
            if elapsed_h > args.budget_hours:
                print(f"budget of {args.budget_hours}h exhausted; stopping with "
                      f"{len(st['done'])} cells complete", flush=True)
                save_state(st)
                return

            print(f"[{i}/{total}] {key}: running "
                  f"(elapsed {elapsed_h:.2f}h)", flush=True)
            ok, why = run_cell(nval, rep, args.cell_timeout)
            if ok:
                st["done"].append(key)
                consecutive_failures = 0
                print(f"[{i}/{total}] {key}: {why}", flush=True)
            else:
                st["failed"].append({"cell": key, "why": why})
                consecutive_failures += 1
                print(f"[{i}/{total}] {key}: FAILED — {why}", flush=True)
                if consecutive_failures >= 3:
                    print("3 consecutive infrastructure failures — aborting sweep",
                          flush=True)
                    save_state(st)
                    return
            save_state(st)

        # Aggregate after each N finishes, so intermediate results are
        # readable without waiting for the whole sweep.
        subprocess.run([sys.executable, str(HERE / "aggregate.py")])

    print(f"sweep complete: {len(st['done'])} cells, {len(st['failed'])} failed")


if __name__ == "__main__":
    main()
