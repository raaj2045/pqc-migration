#!/usr/bin/env python3
"""Measure delivery cost and time across batch sizes, without re-paying the
Ethereum finality wait for every repeat.

    python3 experiments/migration_volume/measure_delivery.py \
        --sizes=50,100,500,1000 --repeats=3 --signer=validator

Why this exists
---------------
A full migration is ~555 s and ~540 s of that is waiting for Ethereum
finality. That wait is a property of Ethereum's epoch clock, not of the batch:
it is the same whether one transfer moves or a thousand. Paying it once per
repeat makes large batch sizes unaffordable to measure.

So it is paid ONCE per generation and shared:

    submit every packet the run will need      one shot
    wait for finality                          once, measured, recorded
    update the light client                    once, measured, recorded
    deliver batch after batch from that pool   the actual measurement

Nothing is faked. The delivery transactions are real, carry real
Merkle-Patricia proofs, and are verified by the real light-client contract.
What changes is only that the shared prelude is not repeated.

What this does and does not measure
-----------------------------------
Delivery gas, delivery time, transaction size and chunking behaviour are all
measured directly. END-TO-END LATENCY IS NOT: a row's `T_Deliver_s` is the
delivery step alone. Total time for a migration of that size is the finality
wait recorded in the generation, plus submission, plus delivery — the parts
are in the CSV and stay separate rather than being silently added up.

Running alongside another measurement
-------------------------------------
`--pool-file`, `--signer` and `--out` all default to values distinct from
measure_data.py's, so the two can run at once without handing out the same EVM
account or the same Cosmos signing account. They will still contend for blocks
on both chains, which shows up in the timings.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402

COLUMNS = [
    "Batch_Size",
    "Repeat",
    "Signer_Key_Type",
    "Signer_Key",
    "Run_Label",
    "Generation",
    "Offset",
    "Deliver_Gas_Total",
    "Deliver_Gas_Per_Tx",
    "Deliver_Tx_Bytes",
    "Chunks",
    "T_Deliver_s",
    "Deliver_Per_Tx_s",
    # shared by every row of a generation, measured once
    "Gen_Wait_Finality_s",
    "Gen_Update_Client_Gas",
    "Gen_Update_Client_s",
    "Gen_Use_Slot",
]


def run(cmd, label, quiet=False):
    if not quiet:
        print(f"    $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        raise SystemExit(f"[{label}] failed with exit {r.returncode} — stopping")


def require(d, key, where, positive=True):
    if key not in d or d[key] is None:
        raise SystemExit(f"{where}: missing required field {key!r}")
    try:
        num = float(d[key])
    except (TypeError, ValueError):
        raise SystemExit(f"{where}: field {key!r} is {d[key]!r}, which is not a number")
    if positive and num <= 0:
        raise SystemExit(f"{where}: field {key!r} is {d[key]!r}, expected a positive number")
    return d[key]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="50,100,500,1000",
                    help="batch sizes to measure, comma separated")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--signer", default="validator",
                    help="keyring key signing the delivery transactions")
    ap.add_argument("--amount", default="2000")
    ap.add_argument("--per-user", type=int, default=50,
                    help="transfers each EVM account submits when building the pool")
    ap.add_argument("--pool-file", default="evm-delivery-pool.json",
                    help="EVM account pool file, kept apart from measure_data.py's")
    ap.add_argument("--out", default="delivery_metrics.csv")
    ap.add_argument("--generation", default=None,
                    help="reuse an existing generation label instead of submitting "
                         "a new one (must still be inside the packet timeout)")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",") if x]
    cfg = config.load()
    out_dir = Path(cfg["DEVNET_DIR"]) / "migration-volume"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Every delivery consumes its packets, so the generation must hold the sum
    # of every batch this run will deliver.
    total_packets = sum(sizes) * args.repeats
    gen = args.generation or f"gen{int(time.time())}"
    send_path = out_dir / f"send-{gen}.json"

    print(f"delivery measurement: sizes {sizes} x {args.repeats} repeat(s) "
          f"= {total_packets:,} packets, signer {args.signer}")

    if not args.generation:
        users = max(1, -(-total_packets // args.per_user))   # ceil
        print(f"\n=== generation {gen}: {total_packets:,} packets "
              f"from {users} account(s), {args.per_user} each ===")
        run(["node", str(HERE / "setup-user-pool.js"), str(users),
             f"--pool-file={args.pool_file}"], "setup")
        run(["node", str(HERE / "submit-migrations.js"), str(users), args.amount,
             f"--per-user={args.per_user}", f"--label={gen}",
             f"--pool-file={args.pool_file}"], "submit")

    if not send_path.exists():
        raise SystemExit(f"no generation at {send_path}")
    send = json.loads(send_path.read_text())
    committed = [p for p in send["packets"] if p.get("status") == "committed"]
    if len(committed) < total_packets:
        raise SystemExit(f"generation has {len(committed)} committed packets, "
                         f"need {total_packets}")

    # --- the shared prelude, paid once ------------------------------------
    print(f"\n=== waiting for finality and updating the client (once) ===")
    run(["node", str(HERE / "relay-recv-batch.js"), str(send_path),
         "--count=1", f"--label={gen}", f"--signer-key={args.signer}",
         "--phase=prepare"], "prepare")
    shared = json.loads((out_dir / f"prepare-{gen}.json").read_text())
    print(f"  finality {shared['finalityWaitSeconds']:.1f}s, "
          f"update {shared['updateGas']} gas, slot {shared['useSlot']}")

    out_path = REPO / args.out
    rows = []
    offset = 0
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for size in sizes:
            for rep in range(1, args.repeats + 1):
                label = f"d{size}-r{rep}-{args.signer}"
                print(f"\n=== deliver {size} packet(s), repeat {rep} "
                      f"(offset {offset}) ===")
                run(["node", str(HERE / "relay-recv-batch.js"), str(send_path),
                     f"--count={size}", f"--offset={offset}", f"--label={label}",
                     f"--signer-key={args.signer}", "--phase=deliver",
                     f"--use-slot={shared['useSlot']}"], label)
                recv = json.loads((out_dir / f"recv-{label}.json").read_text())
                if not recv.get("ok"):
                    raise SystemExit(f"{label}: delivery failed — {recv.get('rawLog')}")

                gas = int(require(recv, "gasUsed", label))
                secs = float(require(recv, "txSeconds", label, positive=False))
                row = {
                    "Batch_Size": size,
                    "Repeat": rep,
                    "Signer_Key_Type": recv["signerAlgo"],
                    "Signer_Key": recv["signerKey"],
                    "Run_Label": label,
                    "Generation": gen,
                    "Offset": offset,
                    "Deliver_Gas_Total": gas,
                    "Deliver_Gas_Per_Tx": gas / size,
                    "Deliver_Tx_Bytes": int(require(recv, "txBytes", label)),
                    "Chunks": recv.get("chunks", 1),
                    "T_Deliver_s": secs,
                    "Deliver_Per_Tx_s": secs / size,
                    "Gen_Wait_Finality_s": shared["finalityWaitSeconds"],
                    "Gen_Update_Client_Gas": shared["updateGas"],
                    "Gen_Update_Client_s": shared["updateSeconds"],
                    "Gen_Use_Slot": shared["useSlot"],
                }
                w.writerow(row)
                f.flush()
                rows.append(row)
                offset += size

    print(f"\nwrote {out_path} ({len(rows)} row(s))")
    print(f"{'size':>6} {'chunks':>7} {'gas/transfer':>13} {'tx bytes':>10} {'deliver s':>10}")
    for size in sizes:
        v = [r for r in rows if r["Batch_Size"] == size]
        if not v:
            continue
        print(f"{size:>6} {v[0]['Chunks']:>7} "
              f"{sum(r['Deliver_Gas_Per_Tx'] for r in v) / len(v):>13,.0f} "
              f"{sum(r['Deliver_Tx_Bytes'] for r in v) // len(v):>10,} "
              f"{sum(r['T_Deliver_s'] for r in v) / len(v):>10.2f}")


if __name__ == "__main__":
    main()
