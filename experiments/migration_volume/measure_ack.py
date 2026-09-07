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
proof-api batches natively: one Cosmos delivery transaction becomes one
Ethereum transaction carrying N `ackPacket` calls inside a multicall, with the
client update fused in rather than sent separately. So this yields gas per ack
against batch size — the same amortization question as the forward leg, asked
on the other chain.

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
    "T_Prove_s",
    "T_Submit_s",
    "Recv_Tx",
    "Ack_Tx",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=None,
                    help="comma-separated delivery labels; default is every "
                         "delivery not yet acknowledged")
    ap.add_argument("--out", default="ack_by_batch.csv")
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

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for label in labels:
            recv_path = work / f"recv-{label}.json"
            recv = json.loads(recv_path.read_text())
            if not recv.get("ok") or not recv.get("txhash"):
                skipped.append((label, "delivery did not succeed"))
                continue
            # A delivery split across several transactions has several ack
            # sources; this relays one transaction, so leave those alone rather
            # than silently acknowledging part of a batch.
            if recv.get("chunks", 1) != 1:
                skipped.append((label, f"delivery used {recv['chunks']} transactions"))
                continue

            n = recv["count"]
            cmd = ["node", str(HERE / "relay-ack-batch.js"), recv["txhash"],
                   f"--label={label}", f"--expect={n}"]
            if args.dry_run:
                cmd.append("--dry-run")
            print(f"=== {label}: {n} packet(s) ===")
            r = subprocess.run(cmd, cwd=REPO)
            if r.returncode != 0:
                raise SystemExit(f"[{label}] ack relay failed with exit {r.returncode}")

            ack = json.loads((work / f"ack-{label}.json").read_text())
            if args.dry_run:
                continue
            gas = ack["gasUsed"]
            row = {
                "Batch_Size": n,
                "Run_Label": label,
                "Verifier_Mode": ack["verifierMode"],
                "Ack_Count": ack["ackCount"],
                "Ack_Gas_Total": gas,
                "Ack_Gas_Per_Packet": gas / n,
                "Relay_Bytes": ack["relayBytes"],
                "Relay_Bytes_Per_Packet": ack["relayBytes"] / n,
                "T_Prove_s": ack["proveSeconds"],
                "T_Submit_s": ack.get("submitSeconds"),
                "Recv_Tx": ack["recvTx"],
                "Ack_Tx": ack.get("txHash"),
            }
            w.writerow(row)
            f.flush()
            rows.append(row)

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
    print(f"\n{'batch':>6} {'n':>3} {'ack gas/packet':>15} {'bytes/packet':>13} {'prove s':>8}")
    for s in sizes:
        v = [r for r in rows if r["Batch_Size"] == s]
        print(f"{s:>6} {len(v):>3} "
              f"{sum(r['Ack_Gas_Per_Packet'] for r in v) / len(v):>15,.0f} "
              f"{sum(r['Relay_Bytes_Per_Packet'] for r in v) / len(v):>13,.0f} "
              f"{sum(r['T_Prove_s'] for r in v) / len(v):>8.1f}")


if __name__ == "__main__":
    main()
