#!/usr/bin/env python3
"""Empirically determine the real packets-per-relay-transaction ceiling for
THIS harness's payload shape, under go-ethereum's tx-size cap.

Why this exists: go-ethereum defaults to a 128KB (131072-byte) limit on a
transaction's RLP-serialized size (txpool's txMaxSize = 4 * txSlotSize).
This is a client default, not a protocol rule, but it is what essentially
every real node runs, so it is the real-world ceiling relayers hit — not an
artifact of this devnet. Real zkRollup teams have hit exactly this: go-
ethereum issue #23920 reports Aztec fitting ~112 of their transactions into
one call under this same cap. Our own ceiling is NOT assumed to match
Aztec's ~112 — our relay-batch calldata (an ICS26Router multicall carrying
one SP1 update-client proof plus N recvPacket messages, each with its own
membership proof) has a different shape and per-packet byte cost than
whatever Aztec was packing, so this script measures OUR real number instead
of reusing theirs. See README.md's "Relay chunk size" section for the result
and how it compares.

Method: submit a batch of real, committed transfers (using the same pool
submission path as loadgen.py, for speed), then bisect over prefixes of
that batch, asking proof-api to build (but never broadcast) the relay
transaction for each prefix and measuring its SIGNED, RLP-serialized size —
the exact quantity geth's txMaxSize check applies to (probe-relay-size.js).
Bisection is safe here because probing never touches chain state: proof-api
returns proof material without submitting anything, so the same committed
packets can be probed at any prefix length repeatedly.

The probe transfers submitted here are deliberately never relayed — they are
consumed for their calldata size only and are harmless leftover state on the
Cosmos chain (they simply sit un-relayed).

Usage:
    python3 find_relay_ceiling.py [--probe-count 130] [--pool-size 10]

Writes results/relay_ceiling.json: {"ceiling": N, "tx_max_size": 131072,
"signed_tx_bytes_at_ceiling": ..., "signed_tx_bytes_at_ceiling_plus_one": ...}
relay_pool.py reads this as the default chunk size (override with
--chunk-size on loadgen.py).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402
import check_setup  # noqa: E402
import loadgen  # noqa: E402

TX_MAX_SIZE = 131072  # go-ethereum's default txpool.txMaxSize (4 * txSlotSize)


def probe(sizes_cache, committed, n):
    if n in sizes_cache:
        return sizes_cache[n]
    cmd = ["node", str(HERE / "probe-relay-size.js")] + committed[:n]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"probe at n={n} failed: {(r.stderr or r.stdout)[-500:]}")
    res = json.loads(r.stdout.strip().splitlines()[-1])
    sizes_cache[n] = res
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-count", type=int, default=130,
                     help="committed transfers to bisect over; must comfortably bracket "
                          "the real ceiling (known from earlier runs: below 100)")
    ap.add_argument("--pool-size", type=int, default=10)
    ap.add_argument("--skip-setup-check", action="store_true")
    args = ap.parse_args()

    cfg = config.require(
        config.load(),
        "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_ID", "CHAIN_NODE", "COSMOS_CLIENT_ID",
        "RECEIVER_ADDR", "SENDTX_CMD", "DEVNET_DIR", "GETH_RPC", "SP1_ICS07", "SP1_VERIFIER_MOCK",
    )

    def log(msg):
        print(msg, flush=True)

    if not args.skip_setup_check:
        check_setup.run_all(cfg, log=log)

    chain = loadgen.Chain(cfg)
    pool_keys = loadgen.resolve_pool(cfg, args.pool_size)
    log(f"submitting {args.probe_count} probe transfers across pool of {len(pool_keys)}...")
    packets = loadgen.submit_group(chain, cfg, args.probe_count, pool_keys, log)
    loadgen.await_commits(chain, packets, log)
    loadgen.await_provable_height(chain, packets, log)
    committed = [p.tx_hash for p in packets if p.status == "committed"]
    log(f"{len(committed)}/{args.probe_count} committed and provable; probing relay-tx sizes...")

    sizes_cache = {}

    # Calibration points, echoing the Aztec (#23920) ~112-per-call reference point.
    for c in (50, 100, 112, 130):
        if c > len(committed):
            continue
        res = probe(sizes_cache, committed, c)
        over = " OVER 131072B CAP" if res["signedTxBytes"] >= TX_MAX_SIZE else ""
        log(f"  n={c:4d}: calldata {res['calldataBytes']:7d}B  signed-tx {res['signedTxBytes']:7d}B{over}")

    lo, hi = 1, len(committed)
    best = None
    first_over = None
    while lo <= hi:
        mid = (lo + hi) // 2
        res = probe(sizes_cache, committed, mid)
        if res["signedTxBytes"] < TX_MAX_SIZE:
            best = res
            lo = mid + 1
        else:
            first_over = res
            hi = mid - 1

    if best is None:
        raise SystemExit(f"even n=1 exceeds {TX_MAX_SIZE}B — something is wrong; check calldata shape")

    log(f"\nCEILING: {best['count']} packets fit under {TX_MAX_SIZE}B "
        f"(signed tx {best['signedTxBytes']}B)")
    if first_over:
        log(f"  next size up (n={first_over['count']}) is {first_over['signedTxBytes']}B — over the cap")
    per_packet = None
    if first_over and best["count"] != first_over["count"]:
        per_packet = first_over["signedTxBytes"] - best["signedTxBytes"]
        log(f"  marginal cost per additional packet: ~{per_packet}B")

    out = {
        "ceiling": best["count"],
        "tx_max_size": TX_MAX_SIZE,
        "signed_tx_bytes_at_ceiling": best["signedTxBytes"],
        "signed_tx_bytes_at_ceiling_plus_one": first_over["signedTxBytes"] if first_over else None,
        "marginal_bytes_per_packet": per_packet,
    }
    out_path = HERE / "results" / "relay_ceiling.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    log(f"wrote {out_path}")


if __name__ == "__main__":
    main()
