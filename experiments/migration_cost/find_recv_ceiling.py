#!/usr/bin/env python3
"""Empirically measure the per-transaction MsgRecvPacket ceiling on the
Ethereum -> Cosmos direction, per SIGNER key type.

WHY THIS EXISTS
---------------
An early fit of `bytes = 186 + 5,890 * n` from only n = 1, 3, 6 projected
the CometBFT `max_tx_bytes` wall (4,194,304 B) at ~712 packets. That is an
extrapolation two orders of magnitude past the data. This measures it.

WHAT ACTUALLY VARIES WITH KEY TYPE
----------------------------------
The DESTINATION key type does not affect a receive transaction at all: the
receiver appears in the packet payload as a 20-byte bech32 address regardless
of key type, and a fresh recipient account carries no pubkey on chain until it
first signs something. Nothing in MsgRecvPacket is a function of the
recipient's key algorithm.

What does vary is the key type of the account that SIGNS the receive
transaction — the relayer. That contributes exactly once per transaction:

    secp256k1   pubkey 33 B  + signature   64 B
    ML-DSA-65   pubkey 1952 B + signature 3309 B

so it moves the INTERCEPT by ~5.2 KB and leaves the per-packet SLOPE (pure MPT
proof data) untouched. This script measures both, rather than assuming it.

METHOD
------
Build the message array ONCE (build-recv-msgs.js), then for each key type
bisect over N: rewrite `signer`, assemble the tx, `tx sign`, `tx encode`, and
measure the real proto-encoded byte length. Nothing is broadcast during the
search, so packets are not consumed and the search is repeatable.

Broadcast confirmation at the boundary is a separate, explicit step
(--confirm): N = ceiling+1 is expected to be rejected by CheckTx for size
(which does NOT consume the packets), and N = ceiling is expected to succeed
(which does).

Usage:
    python3 find_recv_ceiling.py <send-file> --max=800 [--offset=0]
        [--keys=validator,loadgen-pool-0] [--confirm] [--gas-points=1,10,50]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "devnet" / "lib"))
import config  # noqa: E402

MAX_TX_BYTES = 4_194_304       # CometBFT mempool max_tx_bytes
RPC_MAX_BODY_BYTES = 1_000_000  # CometBFT RPC max_body_bytes -- the wall that
                                # ACTUALLY binds. `tx broadcast` base64-encodes
                                # the tx into a JSON-RPC body, so the usable raw
                                # size is ~3/4 of this, far below max_tx_bytes.
                                # Exceeding it is an HTTP 400 "request body too
                                # large" at the RPC, before CheckTx -- so it
                                # does not consume packets.


def b64_len(raw: int) -> int:
    """Encoded length of `raw` bytes in base64, as `tx broadcast` sends it."""
    return (raw + 2) // 3 * 4


def sh(args, timeout=600):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


class Signer:
    """One signing identity under test."""

    def __init__(self, cfg, key_name: str):
        self.cfg = cfg
        self.key = key_name
        r = sh([cfg["PQCHAIND_BIN"], "keys", "show", key_name, "-a",
                "--home", cfg["CHAIN_HOME"], "--keyring-backend", "test"])
        if r.returncode != 0:
            raise RuntimeError(f"keys show {key_name} failed: {r.stderr[-300:]}")
        self.address = r.stdout.strip()
        self.algo = None      # filled in on first sign
        self.pubkey_bytes = None
        self.sig_bytes = None

    def encoded_size(self, msgs, gas="10000000"):
        """Real proto-encoded byte length of the signed tx carrying `msgs`.
        Returns (bytes, err)."""
        cfg = self.cfg
        for m in msgs:
            m["signer"] = self.address
        tx = {
            "body": {"messages": msgs, "memo": "", "timeout_height": "0",
                     "extension_options": [], "non_critical_extension_options": []},
            "auth_info": {"signer_infos": [],
                          "fee": {"amount": [{"denom": "stake", "amount": "6000"}],
                                  "gas_limit": gas, "payer": "", "granter": ""}},
            "signatures": [],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(tx, f)
            unsigned = f.name
        try:
            r = sh([cfg["PQCHAIND_BIN"], "tx", "sign", unsigned, "--from", self.key,
                    "--chain-id", cfg["CHAIN_ID"], "--keyring-backend", "test",
                    "--home", cfg["CHAIN_HOME"], "--node", cfg["CHAIN_NODE"],
                    "--output-document", "/dev/stdout"])
            if r.returncode != 0:
                return None, f"sign: {r.stderr.strip().splitlines()[-1][:160]}"
            signed_json = r.stdout
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                f.write(signed_json)
                signed = f.name
            try:
                e = sh([cfg["PQCHAIND_BIN"], "tx", "encode", signed, "--home", cfg["CHAIN_HOME"]])
                if e.returncode != 0:
                    return None, f"encode: {e.stderr.strip().splitlines()[-1][:160]}"
                n = len(base64.b64decode(e.stdout.strip()))
                if self.algo is None:
                    doc = json.loads(signed_json)
                    pk = doc["auth_info"]["signer_infos"][0]["public_key"]
                    self.algo = pk.get("@type", "?").split(".")[-2]
                    self.pubkey_bytes = len(base64.b64decode(pk.get("key", "")))
                    self.sig_bytes = len(base64.b64decode(doc["signatures"][0]))
                return n, None
            finally:
                os.unlink(signed)
        finally:
            os.unlink(unsigned)

    def broadcast(self, msgs, gas):
        """Sign and actually broadcast. Returns (ok, info)."""
        cfg = self.cfg
        for m in msgs:
            m["signer"] = self.address
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(msgs, f)
            path = f.name
        try:
            r = sh(cfg["SENDTX_CMD"].split() + [path, self.key, str(gas)], timeout=1800)
            out = (r.stdout or "") + (r.stderr or "")
            result = None
            for line in out.splitlines():
                t = line.strip()
                if t.startswith("{") and "gas_used" in t:
                    try:
                        result = json.loads(t)
                    except ValueError:
                        pass
            if result and result.get("code") == 0:
                return True, result
            return False, out.strip()[-500:]
        finally:
            os.unlink(path)


def bisect_ceiling(signer: Signer, msgs, lo, hi, log, limit=MAX_TX_BYTES, tag="max_tx_bytes"):
    """Largest N in [lo, hi] whose signed tx encodes to <= `limit`.
    Bisection, not linear scan. Records every probe."""
    probes = {}

    def size_at(n):
        if n not in probes:
            b, err = signer.encoded_size(json.loads(json.dumps(msgs[:n])))
            if b is None:
                raise RuntimeError(f"could not size N={n}: {err}")
            probes[n] = b
            log(f"    N={n:>4}  raw {b:>10,} B  b64 {b64_len(b):>10,} B  "
                f"{'OK' if b <= limit else 'OVER'} ({b / limit * 100:.1f}% of {tag})")
        return probes[n]

    if size_at(hi) <= limit:
        log(f"    N={hi} still fits — ceiling is at or above the packet supply")
        return hi, probes, False
    if size_at(lo) > limit:
        raise RuntimeError(f"even N={lo} exceeds the limit")
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if size_at(mid) <= limit:
            lo = mid
        else:
            hi = mid
    return lo, probes, True


def fit(points):
    """Least-squares slope/intercept over {n: bytes}."""
    xs = sorted(points)
    n = len(xs)
    mx = sum(xs) / n
    my = sum(points[x] for x in xs) / n
    sxy = sum((x - mx) * (points[x] - my) for x in xs)
    sxx = sum((x - mx) ** 2 for x in xs)
    m = sxy / sxx
    return m, my - m * mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("send_file")
    ap.add_argument("--max", type=int, required=True)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--keys", default="validator,loadgen-pool-0")
    ap.add_argument("--gas-points", default="1,5,25,100")
    ap.add_argument("--confirm", action="store_true",
                    help="broadcast-confirm the boundary for the FIRST key "
                         "(ceiling+1 must be rejected, ceiling must succeed). "
                         "The success case consumes those packets.")
    ap.add_argument("--out", default=str(HERE / "results" / "recv_ceiling.json"))
    args = ap.parse_args()

    cfg = config.require(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_ID",
                         "CHAIN_NODE", "SENDTX_CMD", "DEVNET_DIR", "COSMOS_CLIENT_ID")

    def log(m):
        print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

    # --- build the message array once ------------------------------------
    msg_path = Path(cfg["DEVNET_DIR"]) / "migration-cost" / "ceiling-msgs.json"
    msg_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"building {args.max} MsgRecvPacket (offset {args.offset})...")
    r = subprocess.run(
        ["node", str(HERE / "build-recv-msgs.js"), args.send_file,
         f"--count={args.max}", f"--offset={args.offset}", f"--out={msg_path}"],
        capture_output=True, text=True, timeout=3600)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        log("build-recv-msgs.js failed")
        sys.exit(1)
    build_info = json.loads(r.stdout.strip().splitlines()[-1])
    msgs = json.loads(msg_path.read_text())
    log(f"built {len(msgs)} msgs at slot {build_info['useSlot']} "
        f"(account proof {build_info['accountProofBytes']}B)")

    report = {"max_tx_bytes": MAX_TX_BYTES, "built": build_info, "keys": {}}

    for key_name in args.keys.split(","):
        key_name = key_name.strip()
        log(f"=== signer key: {key_name} ===")
        signer = Signer(cfg, key_name)
        log("  -- size ceiling (mempool max_tx_bytes) --")
        ceiling, probes, exact = bisect_ceiling(signer, msgs, 1, len(msgs), log)
        slope, intercept = fit(probes)
        # The broadcast ceiling: largest N whose BASE64 length fits the RPC
        # body limit. Derived from the same measured slope/intercept, then
        # bisected on real signed sizes so it is measured, not projected.
        log("  -- broadcast ceiling (RPC max_body_bytes, base64) --")
        bmax = min(len(msgs), max(1, int((RPC_MAX_BODY_BYTES * 3 // 4 - intercept) / slope) + 8))
        bceiling, bprobes, bexact = bisect_ceiling(
            signer, msgs, 1, bmax, log,
            limit=RPC_MAX_BODY_BYTES * 3 // 4, tag="usable RPC body")
        while bceiling > 1 and b64_len(bprobes[bceiling]) > RPC_MAX_BODY_BYTES:
            bceiling -= 1
        log(f"  broadcast ceiling {bceiling} packets/tx "
            f"(raw {bprobes.get(bceiling, 0):,} B, b64 {b64_len(bprobes.get(bceiling, 0)):,} B)")
        entry = {
            "address": signer.address, "algo": signer.algo,
            "pubkey_bytes": signer.pubkey_bytes, "sig_bytes": signer.sig_bytes,
            "ceiling": ceiling, "ceiling_is_exact": exact,
            "bytes_at_ceiling": probes[ceiling],
            "broadcast_ceiling": bceiling,
            "broadcast_bytes_raw": bprobes.get(bceiling),
            "broadcast_bytes_b64": b64_len(bprobes.get(bceiling, 0)),
            "slope_bytes_per_packet": slope, "intercept_bytes": intercept,
            "probes": {str(k): v for k, v in sorted(probes.items())},
        }
        log(f"  ceiling {ceiling} packets/tx ({probes[ceiling]:,} B), "
            f"slope {slope:,.1f} B/packet, intercept {intercept:,.0f} B "
            f"[{signer.algo}: pubkey {signer.pubkey_bytes}B sig {signer.sig_bytes}B]")
        report["keys"][key_name] = entry

    # --- gas vs N, measured by real broadcast at a few small N -----------
    # Kept small and separate from the ceiling search: these consume packets.
    gas_points = [int(x) for x in args.gas_points.split(",") if x.strip()]
    report["gas_points"] = {}
    if gas_points:
        first_key = args.keys.split(",")[0].strip()
        signer = Signer(cfg, first_key)
        used = args.max
        offset2 = args.offset + args.max
        log(f"=== gas vs N ({first_key}), fresh packets from offset {offset2} ===")
        for n in gas_points:
            p = Path(cfg["DEVNET_DIR"]) / "migration-cost" / f"gas-msgs-{n}.json"
            rb = subprocess.run(
                ["node", str(HERE / "build-recv-msgs.js"), args.send_file,
                 f"--count={n}", f"--offset={offset2}", f"--out={p}", "--no-update"],
                capture_output=True, text=True, timeout=1800)
            if rb.returncode != 0:
                log(f"  N={n}: build failed: {rb.stderr.strip().splitlines()[-1][:160]}")
                continue
            m = json.loads(p.read_text())
            ok, info = signer.broadcast(m, gas=400_000 + 900_000 * n)
            if ok:
                log(f"  N={n:>4}  gas_used={int(info['gas_used']):>12,}  "
                    f"tx_bytes={info['tx_bytes']:>10,}  "
                    f"({int(info['gas_used']) / n:,.0f} gas/packet)")
                report["gas_points"][str(n)] = {
                    "gas_used": int(info["gas_used"]), "tx_bytes": info["tx_bytes"],
                    "gas_per_packet": int(info["gas_used"]) / n, "txhash": info["txhash"]}
            else:
                log(f"  N={n}: broadcast failed: {str(info)[:200]}")
                report["gas_points"][str(n)] = {"error": str(info)[:300]}
            offset2 += n

    # --- boundary confirmation by real broadcast -------------------------
    if args.confirm:
        first_key = args.keys.split(",")[0].strip()
        signer = Signer(cfg, first_key)
        c = report["keys"][first_key]["broadcast_ceiling"]
        log(f"=== boundary confirmation ({first_key}), ceiling {c} ===")
        conf = {}
        if c + 1 <= len(msgs):
            ok, info = signer.broadcast(json.loads(json.dumps(msgs[:c + 1])),
                                        gas=400_000 + 900_000 * (c + 1))
            conf["above"] = {"n": c + 1, "accepted": ok, "info": str(info)[:300]}
            log(f"  N={c + 1} (ceiling+1): {'ACCEPTED — unexpected' if ok else 'REJECTED as expected'}")
        ok, info = signer.broadcast(json.loads(json.dumps(msgs[:c])),
                                    gas=400_000 + 900_000 * c)
        conf["at"] = {"n": c, "accepted": ok,
                      "info": info if not ok else {k: info[k] for k in ("txhash", "gas_used", "tx_bytes")}}
        log(f"  N={c} (ceiling):   {'ACCEPTED as expected' if ok else 'REJECTED — unexpected: ' + str(info)[:200]}")
        report["confirmation"] = conf

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    log(f"wrote {out}")

    ks = list(report["keys"].items())
    if len(ks) == 2:
        (n1, a), (n2, b) = ks
        for field, what in (("ceiling", "size (max_tx_bytes)"),
                            ("broadcast_ceiling", "broadcast (RPC max_body_bytes)")):
            d = a[field] - b[field]
            pct = d / a[field] * 100 if a[field] else 0
            log(f"CAPACITY DIFFERENCE [{what}]: {n1} ({a['algo']}) {a[field]} vs "
                f"{n2} ({b['algo']}) {b[field]} -> {d} packet(s), {pct:.3f}%")


if __name__ == "__main__":
    main()
