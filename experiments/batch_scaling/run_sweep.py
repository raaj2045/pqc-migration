#!/usr/bin/env python3
"""batch_scaling run orchestrator.

For each group size (ascending) x repeat, run loadgen.py once and write a
result JSON. Resumable: an existing result file is never overwritten, so an
interrupted run continues where it stopped.

Escalation rule (the one thing this orchestrator enforces beyond
migration_throughput/run_sweep.py's pattern): group sizes are attempted in
ascending order, and if ANY repeat at a given size fails with a genuine
breaking error — gas limit exceeded, timeout, revert, or anything else
relay_pool.py/loadgen.py classifies as a failure rather than success — the
run stops and does NOT attempt larger sizes. That failure is itself the
result for that point in the run: see README.md's "Known limitations" and
the EXECUTION note this was built to satisfy.

The wall-clock cost is dominated by real Ethereum finality on the return
(ack) leg, not shortcut here: each group waits for the finalized checkpoint to
advance before the acks it carries can be submitted. At 6 s slots and
32-slot epochs a finality window is roughly 6.4 minutes; budget accordingly.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = HERE / "results"
LOGS = HERE / "logs"
STATE = HERE / "sweep_state.json"

sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402
import check_setup  # noqa: E402

DEFAULT_GROUP_SIZES = [1, 10, 50, 100, 250, 500]
DEFAULT_REPEATS = 5


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {
        "done": [], "failed": [], "stopped_after": None}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=2))


def cell_path(g, rep, ack_pool_size=1):
    # ack_pool_size in the filename when >1 so running both the sequential-
    # ack and pooled-ack configurations at the same group size (comparing
    # the latency/gas tradeoff across scale) never collide on one result
    # file — the default (1) keeps the original filename unchanged, so
    # existing small-run results and tooling that reads them are unaffected.
    suffix = f"_ack{ack_pool_size}" if ack_pool_size != 1 else ""
    return RESULTS / f"G{g}_rep{rep}{suffix}.json"


def run_cell(g, rep, timeout, pool_size, relay_pool_size, chunk_size, ack_pool_size) -> tuple[bool, str]:
    """Returns (ok, why). ok=False covers both infra breakage (no result file)
    and a genuine recorded run failure (loadgen.py exits 1 but still writes
    a result file describing the failure) — the caller distinguishes them.

    A timeout here does NOT lose loadgen.py's own progress: it checkpoints
    incrementally during the (usually hours-long) ack phase, and this
    function's own out.exists() check means a subsequent call — the next
    run_sweep.py invocation, or a manual retry — resumes from that
    checkpoint rather than restarting the cell. See loadgen.py's run_one
    docstring and README.md's "Resumability" section.
    """
    out = cell_path(g, rep, ack_pool_size)
    if out.exists():
        data = json.loads(out.read_text())
        return data.get("status") == "ok", "already present (resume)"

    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{out.stem}.log"
    cmd = [sys.executable, str(HERE / "loadgen.py"),
           "--group-size", str(g), "--repeat", str(rep), "--out", str(out),
           "--pool-size", str(pool_size), "--relay-pool-size", str(relay_pool_size),
           "--ack-pool-size", str(ack_pool_size),
           "--skip-setup-check"]  # checked once up front by main()
    if chunk_size is not None:
        cmd += ["--chunk-size", str(chunk_size)]

    with log_path.open("w") as lf:
        try:
            r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, (f"loadgen.py itself timed out after {timeout}s (see {log_path.name}) — "
                            f"its own progress checkpoint is intact; re-running this run (or "
                            f"loadgen.py directly with the same --out) resumes rather than restarting")

    if not out.exists():
        return False, f"loadgen.py exited {r.returncode} with no result file (see {log_path.name})"

    data = json.loads(out.read_text())
    if data.get("status") != "ok":
        return False, f"{data.get('failure_kind', 'unknown')}: {data.get('failure_message', '')[:200]}"
    return True, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group-sizes", type=int, nargs="+", default=DEFAULT_GROUP_SIZES,
                    help="transfers grouped into one shared light-client update, "
                         "attempted in ascending order regardless of the order given here")
    ap.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    ap.add_argument("--cell-timeout", type=int, default=3600,
                    help="hard timeout per cell, seconds. Must exceed the slowest cell's real "
                         "wall-clock time (large group sizes with ack_pool_size=1 can run many "
                         "hours) — a timeout here does not lose progress (loadgen.py checkpoints "
                         "internally) but does stop the run's escalation until re-invoked.")
    ap.add_argument("--budget-hours", type=float, default=24.0)
    ap.add_argument("--pool-size", type=int, default=1,
                    help="passed through to loadgen.py --pool-size (Cosmos submission pool)")
    ap.add_argument("--relay-pool-size", type=int, default=1,
                    help="passed through to loadgen.py --relay-pool-size (EVM relay pool)")
    ap.add_argument("--chunk-size", type=int, default=None,
                    help="passed through to loadgen.py --chunk-size")
    ap.add_argument("--ack-pool-size", type=int, default=1,
                    help="passed through to loadgen.py --ack-pool-size (Cosmos ack pool). Also "
                         "included in each cell's result filename when != 1, so sweeping the "
                         "same group sizes under both ack_pool_size=1 and >1 never collides.")
    ap.add_argument("--skip-setup-check", action="store_true",
                    help="skip the one-time SP1MockVerifier/devnet-up precondition check")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    if not args.skip_setup_check:
        cfg = config.load()
        try:
            check_setup.run_all(cfg, log=log)
        except check_setup.SetupError as e:
            log(f"SETUP CHECK FAILED: {e}")
            sys.exit(1)

    sizes = sorted(set(args.group_sizes))
    st = load_state()
    t0 = time.time()

    total = len(sizes) * args.repeats
    i = 0
    for g in sizes:
        cell_failed_at_this_size = False

        for rep in range(args.repeats):
            i += 1
            key = cell_path(g, rep, args.ack_pool_size).stem
            if key in st["done"]:
                log(f"[{i}/{total}] {key}: skip (done)")
                continue

            elapsed_h = (time.time() - t0) / 3600
            if elapsed_h > args.budget_hours:
                log(f"budget of {args.budget_hours}h exhausted; stopping with "
                    f"{len(st['done'])} cells complete")
                save_state(st)
                return

            log(f"[{i}/{total}] {key}: running (elapsed {elapsed_h:.2f}h)")
            ok, why = run_cell(g, rep, args.cell_timeout, args.pool_size,
                                args.relay_pool_size, args.chunk_size, args.ack_pool_size)
            if ok:
                st["done"].append(key)
                log(f"[{i}/{total}] {key}: {why}")
            else:
                st["failed"].append({"cell": key, "why": why})
                cell_failed_at_this_size = True
                log(f"[{i}/{total}] {key}: FAILED — {why}")
            save_state(st)

        subprocess.run([sys.executable, str(HERE / "aggregate.py")])

        if cell_failed_at_this_size:
            log(f"group_size={g} had a failing repeat — stopping before any larger "
                f"group size. This is the run's recorded ceiling, not an error to "
                f"retry; see README.md.")
            st["stopped_after"] = g
            save_state(st)
            return

    log(f"run complete: {len(st['done'])} cells, {len(st['failed'])} failed")


if __name__ == "__main__":
    main()
