#!/usr/bin/env python3
"""Measure the return leg: acknowledgements travelling Cosmos -> Ethereum.

    python3 experiments/migration_volume/measure_ack.py [--labels=a,b,c]
                                                        [--out=ack_by_batch.csv]

Every delivery this experiment has already made left a `recv-<label>.json`
holding the Cosmos transaction hash that carried N acknowledgements. This
relays each of those back to Ethereum and records what it cost, so the return
leg gets the same batch-size treatment as the forward one. No new packets are
sent; it consumes work already done.

By default it picks up every delivery that has not been acknowledged yet.

What it measures
----------------
proof-api batches natively: one Cosmos delivery transaction becomes a multicall
of N `ackPacket` calls, with the client update fused in rather than sent
separately. So this yields gas per ack against batch size — the same
amortization question as the forward leg, asked on the other chain.

The two directions do not have the same headroom. Delivery is walled by
CometBFT's 4 MB `max_tx_bytes`, around 590 packets; acknowledgement is walled
by geth's 128 KB `txMaxSize`, around 56 acks at the measured ~2,080 bytes each
— over thirty times smaller. A large delivery therefore needs several
Ethereum transactions to answer it, and relay-ack-batch.js splits the multicall
to fit.

What it does not measure
------------------------
Whether proof verification is real depends on which SP1 verifier the EVM light
client is bound to, and that is fixed when the client is created. Against the
MOCK verifier this measures the mechanism only — batching, calldata size, and
the gas of everything except the proof check — and `proveSeconds` is mock
proving, not Groth16. The mode is read from the contract and recorded on every
row, so a mock run cannot later be mistaken for a real one.
"""
import argparse
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = HERE / "results"
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402

COLUMNS = [
    "Batch_Size",
    "Run_Label",
    "Verifier_Mode",
    "Ack_Count",
    "Ack_Gas_Total",
    "Ack_Gas_Per_Packet",
    "Relay_Bytes",
    "Relay_Bytes_Per_Packet",
    "Ack_Chunks",
    "T_Prove_s",
    "T_Submit_s",
    "Recv_Tx",
    "Ack_Tx",
]


def run(cmd, label):
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        raise SystemExit(f"[{label}] failed with exit {r.returncode}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=None,
                    help="comma-separated delivery labels; default is every "
                         "delivery not yet acknowledged")
    ap.add_argument("--out", default="ack_by_batch.csv")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="acks to relay at once. Each needs its own Ethereum "
                         "sender account (two in-flight transactions from one "
                         "account race for the same nonce), taken from "
                         "--sender-pool")
    ap.add_argument("--sender-pool", default="evm-ack-pool.json",
                    help="EVM account pool used when --concurrency > 1")
    ap.add_argument("--dry-run", action="store_true",
                    help="ask proof-api what it would build, but broadcast nothing")
    args = ap.parse_args()

    cfg = config.require(config.load(), "DEVNET_DIR", "PROOF_API_ADDR")
    work = Path(cfg["DEVNET_DIR"]) / "migration-volume"

    if args.labels:
        labels = [x for x in args.labels.split(",") if x]
    else:
        labels = sorted(p.stem[len("recv-"):] for p in work.glob("recv-*.json")
                        if not (work / f"ack-{p.stem[len('recv-'):]}.json").exists())
    if not labels:
        raise SystemExit("nothing to acknowledge: every delivery already has an ack-*.json")

    print(f"return leg: {len(labels)} delivery/deliveries to acknowledge\n")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if Path(args.out).is_absolute() else RESULTS / args.out
    rows, skipped = [], []

    # Decide what to relay before doing any of it, so the work can be handed to
    # several senders at once.
    todo = []
    for label in labels:
        recv = json.loads((work / f"recv-{label}.json").read_text())
        if not recv.get("ok") or not recv.get("txhash"):
            skipped.append((label, "delivery did not succeed"))
        elif recv.get("chunks", 1) != 1:
            # Several ack sources; relaying one would half-close the batch.
            skipped.append((label, f"delivery used {recv['chunks']} transactions"))
        else:
            todo.append((label, recv))

    if args.concurrency > 1:
        run(["node", str(HERE / "setup-user-pool.js"), str(args.concurrency),
             f"--pool-file={args.sender_pool}"], "sender-pool")

    def relay(job):
        i, (label, recv) = job
        n = recv["count"]
        cmd = ["node", str(HERE / "relay-ack-batch.js"), recv["txhash"],
               f"--label={label}", f"--expect={n}"]
        if args.concurrency > 1:
            cmd += [f"--sender-pool={args.sender_pool}",
                    f"--sender-index={i % args.concurrency}"]
        if args.dry_run:
            cmd.append("--dry-run")
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        return label, n, r

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        results = list(ex.map(relay, enumerate(todo)))

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for label, n, r in results:
            print(f"=== {label}: {n} packet(s) ===")
            print(r.stdout, end="")
            if r.returncode != 0:
                msg = (r.stderr or r.stdout).strip().splitlines()
                why = msg[-1] if msg else "unknown"
                # A batch too large to acknowledge is a measurement, not a
                # crash: it is where this leg's ceiling is.
                if "does not fit" in why:
                    skipped.append((label, why))
                    continue
                raise SystemExit(f"[{label}] ack relay failed: {why}")

            ack = json.loads((work / f"ack-{label}.json").read_text())
            if args.dry_run:
                continue
            gas = ack["gasUsed"]
            w.writerow({
                "Batch_Size": n,
                "Run_Label": label,
                "Verifier_Mode": ack["verifierMode"],
                "Ack_Count": ack["ackCount"],
                "Ack_Gas_Total": gas,
                "Ack_Gas_Per_Packet": gas / n,
                "Relay_Bytes": ack["relayBytes"],
                "Relay_Bytes_Per_Packet": ack["relayBytes"] / n,
                "Ack_Chunks": ack.get("chunks", 1),
                "T_Prove_s": ack["proveSeconds"],
                "T_Submit_s": ack.get("submitSeconds"),
                "Recv_Tx": ack["recvTx"],
                "Ack_Tx": ack.get("txHash"),
            })
            f.flush()
            rows.append({"Batch_Size": n, "Ack_Gas_Per_Packet": gas / n,
                         "Relay_Bytes_Per_Packet": ack["relayBytes"] / n,
                         "Ack_Chunks": ack.get("chunks", 1),
                         "T_Prove_s": ack["proveSeconds"],
                         "Verifier_Mode": ack["verifierMode"]})

    for label, why in skipped:
        print(f"  skipped {label}: {why}")
    if not rows:
        print("\nno rows written")
        return

    print(f"\nwrote {out_path} ({len(rows)} row(s))")
    modes = {r["Verifier_Mode"] for r in rows}
    if modes != {"real"}:
        print(f"NOTE: verifier mode {modes} — with the mock verifier the proof "
              f"check is a no-op, so these are mechanism costs, not proving costs.")
    sizes = sorted({r["Batch_Size"] for r in rows})
    print(f"\n{'batch':>6} {'n':>3} {'txs':>4} {'ack gas/packet':>15} {'bytes/packet':>13} {'prove s':>8}")
    for s in sizes:
        v = [r for r in rows if r["Batch_Size"] == s]
        print(f"{s:>6} {len(v):>3} {v[0]['Ack_Chunks']:>4} "
              f"{sum(r['Ack_Gas_Per_Packet'] for r in v) / len(v):>15,.0f} "
              f"{sum(r['Relay_Bytes_Per_Packet'] for r in v) / len(v):>13,.0f} "
              f"{sum(r['T_Prove_s'] for r in v) / len(v):>8.1f}")


if __name__ == "__main__":
    main()
