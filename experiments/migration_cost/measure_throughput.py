#!/usr/bin/env python3
"""Measure how fast the system moves transfers as more relayers work at once.

    python3 experiments/migration_cost/measure_throughput.py
        [--workers=1,2,5,10] [--batch=50] [--repeats=3]
        [--keys=secp256k1,mldsa65]

What this answers
-----------------
Whether post-quantum signing reduces how much the bridge can move.

One relayer spends almost all its time waiting for Ethereum -- about 540 s of
waiting for a few seconds of work -- so its rate says nothing about the
system's capacity. Capacity comes from running many relayers side by side,
each handling its own group of transfers, until something saturates.

So the number of relayers working at once is the variable, and the rate is
measured for each signing key. If the two keys give the same rate, signing
post-quantum costs no capacity.

What is timed
-------------
Delivery only: the wall time from launching the relayers to the last one
landing its Cosmos transaction. Waiting for Ethereum finality is deliberately
excluded -- it is a fixed delay every transfer pays once, not a limit on the
rate, and once the pipeline is full transfers keep arriving through it. The
return leg is excluded too; generating a real proof takes about ten minutes and
would swamp everything else. Both are measured separately and reported in
fig_time_by_step.pdf.

Cost of running it
------------------
Every relayer needs its own Cosmos account, because two transactions in flight
from one account race for the same sequence number. The packets are submitted
once and one finality wait is shared by every measurement, so the run takes
about an hour and a half rather than a day.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = HERE / "results"
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402

COLUMNS = [
    "Workers",
    "Batch_Size",
    "Repeat",
    "Signer_Key_Type",
    "Transfers",
    "T_Elapsed_s",
    "Transfers_Per_Second",
    "Deliver_Gas_Total",
    "Deliver_Gas_Per_Tx",
    "Generation",
    "Gen_Wait_Finality_s",
]


class WindowClosed(Exception):
    """geth has pruned the state the proofs need: no more rounds are possible."""


def run(cmd, label, quiet=True):
    r = subprocess.run(cmd, cwd=REPO, capture_output=quiet, text=True)
    if r.returncode != 0:
        out = (r.stderr or r.stdout or "") if quiet else ""
        # Running out of the proof window is the expected end of a long run, not
        # a failure: stop cleanly and keep every round already measured.
        if "pruned by geth" in out or "historical state" in out:
            raise WindowClosed(label)
        raise SystemExit(f"[{label}] failed with exit {r.returncode}\n{out[-600:]}")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", default="1,2,5,10",
                    help="how many relayers work at once")
    ap.add_argument("--batch", type=int, default=50,
                    help="transfers each relayer delivers, kept under the "
                         "acknowledgement limit of about 56")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--keys", default="secp256k1,mldsa65")
    ap.add_argument("--amount", default="2000")
    ap.add_argument("--per-user", type=int, default=50)
    ap.add_argument("--pool-file", default="evm-throughput-pool.json")
    ap.add_argument("--out", default="throughput_by_workers.csv")
    ap.add_argument("--generation", default=None,
                    help="reuse an existing generation instead of submitting one")
    args = ap.parse_args()

    workers = [int(w) for w in args.workers.split(",") if w]
    keys = [k for k in args.keys.split(",") if k]
    cfg = config.load()
    work = Path(cfg["DEVNET_DIR"]) / "migration-cost"
    work.mkdir(parents=True, exist_ok=True)

    # Every delivery consumes its packets, so one generation must cover every
    # measurement: each key, each relayer count, each repeat.
    per_key = sum(workers) * args.batch * args.repeats
    total = per_key * len(keys)
    gen = args.generation or f"tp{int(time.time())}"
    send_path = work / f"send-{gen}.json"

    print(f"throughput: relayers {workers} x {args.batch} transfers each x "
          f"{args.repeats} repeat(s) x {len(keys)} key(s) = {total:,} packets")

    if not args.generation:
        users = max(1, -(-total // args.per_user))
        print(f"\n=== generation {gen}: {total:,} packets from {users} account(s) ===")
        run(["node", str(HERE / "setup-user-pool.js"), str(users),
             f"--pool-file={args.pool_file}"], "setup", quiet=False)
        run(["node", str(HERE / "submit-migrations.js"), str(users), args.amount,
             f"--per-user={args.per_user}", f"--label={gen}",
             f"--pool-file={args.pool_file}"], "submit", quiet=False)

    if not send_path.exists():
        raise SystemExit(f"no generation at {send_path}")
    committed = [p for p in json.loads(send_path.read_text())["packets"]
                 if p.get("status") == "committed"]
    if len(committed) < total:
        raise SystemExit(f"generation has {len(committed)} committed packets, need {total}")

    # One Cosmos account per relayer, per key.
    for key_type in keys:
        run(["python3", str(HERE / "setup-signer-pool.py"),
             "--size", str(max(workers)), "--key-type", key_type], f"pool/{key_type}",
            quiet=False)

    # Wait on the LAST packet, not the first. A generation this size spans many
    # blocks, and finality covering the earliest one says nothing about the
    # latest -- those packets would then be unprovable and their rounds would
    # fail on a commitment that reads as absent.
    print("\n=== waiting for finality and updating the client (once) ===")
    run(["node", str(HERE / "relay-recv-batch.js"), str(send_path),
         "--count=1", f"--offset={total - 1}", f"--label={gen}", "--signer-key=validator", "--phase=prepare"],
        "prepare", quiet=False)
    shared = json.loads((work / f"prepare-{gen}.json").read_text())
    slot = shared["useSlot"]
    print(f"  finality {shared['finalityWaitSeconds']:.1f}s, slot {slot}")
    print("  NOTE: proofs can only be fetched for about 5 minutes after this "
          "point, so the measurements run back to back from here.")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if Path(args.out).is_absolute() else RESULTS / args.out
    offset, rows = 0, []
    t_start = time.time()

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        # Repeat is the OUTER loop so that if the proof window closes early the
        # run loses whole repeats across every configuration, rather than
        # losing one key or one relayer count entirely.
        stopped = None
        for rep in range(1, args.repeats + 1):
            if stopped:
                break
            for key_type in keys:
                if stopped:
                    break
                for n_workers in workers:
                    labels = [f"tp-{key_type}-w{n_workers}-r{rep}-{i}"
                              for i in range(n_workers)]
                    offsets = [offset + i * args.batch for i in range(n_workers)]
                    offset += n_workers * args.batch

                    def deliver(job):
                        i, (label, off) = job
                        run(["node", str(HERE / "relay-recv-batch.js"), str(send_path),
                             f"--count={args.batch}", f"--offset={off}",
                             f"--label={label}",
                             f"--signer-key=mv-{key_type}-{i}",
                             "--phase=deliver", f"--use-slot={slot}"], label)

                    # Wall time from launching every relayer to the last one
                    # landing. That span, not the sum of their individual times,
                    # is what the rate is built from.
                    t0 = time.time()
                    try:
                        with ThreadPoolExecutor(max_workers=n_workers) as ex:
                            list(ex.map(deliver, enumerate(zip(labels, offsets))))
                    except WindowClosed:
                        stopped = (rep, key_type, n_workers)
                        break
                    elapsed = time.time() - t0

                    gas = 0
                    for label in labels:
                        recv = json.loads((work / f"recv-{label}.json").read_text())
                        if not recv.get("ok"):
                            raise SystemExit(f"{label}: delivery failed — {recv.get('rawLog')}")
                        gas += recv["gasUsed"]

                    moved = n_workers * args.batch
                    row = {
                        "Workers": n_workers,
                        "Batch_Size": args.batch,
                        "Repeat": rep,
                        "Signer_Key_Type": key_type,
                        "Transfers": moved,
                        "T_Elapsed_s": elapsed,
                        "Transfers_Per_Second": moved / elapsed,
                        "Deliver_Gas_Total": gas,
                        "Deliver_Gas_Per_Tx": gas / moved,
                        "Generation": gen,
                        "Gen_Wait_Finality_s": shared["finalityWaitSeconds"],
                    }
                    w.writerow(row)
                    f.flush()
                    rows.append(row)
                    print(f"  {key_type:10} {n_workers:>2} relayer(s) rep {rep}: "
                          f"{moved:>4} transfers in {elapsed:6.2f}s = "
                          f"{moved / elapsed:7.2f}/s")

    if stopped:
        rep, key_type, n_workers = stopped
        print(f"\nstopped at repeat {rep} ({key_type}, {n_workers} relayers): the "
              f"proof window closed. Every round before it is kept; run again to "
              f"add more repeats, and the results merge.")
    mins = (time.time() - t_start) / 60
    if mins > 5:
        print(f"\nNOTE: the measurements took {mins:.1f} min. The window in which "
              f"proofs can be fetched is about 128 blocks minus the finality lag "
              f"— roughly 5 minutes at 6-second slots. Use fewer repeats per run "
              f"and run it more than once; the results merge.")

    print(f"\nwrote {out_path} ({len(rows)} row(s))")
    print(f"\n{'key':10} {'relayers':>9} {'transfers/s':>12}")
    for key_type in keys:
        for n_workers in workers:
            v = [r["Transfers_Per_Second"] for r in rows
                 if r["Signer_Key_Type"] == key_type and r["Workers"] == n_workers]
            if v:
                print(f"{key_type:10} {n_workers:>9} {sum(v) / len(v):>12.2f}")


if __name__ == "__main__":
    main()
