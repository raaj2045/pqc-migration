#!/usr/bin/env python3
"""Create and fund a pool of ML-DSA-65 relayer accounts for concurrent load
submission (loadgen.py --pool-size).

Genesis accounts (devnet/scripts/init-chain.sh's TEST_KEYS_ML_DSA) are fixed
at chain-init time — "relayer" and "loadgen" only. Submitting a big group
CONCURRENTLY from a single account is not possible: every tx needs the
account's next sequence number, and two in-flight txs racing for the same
sequence collide (one gets rejected). A pool of independent accounts, each
submitting its own share sequentially, sidesteps this entirely — no account
ever has two in-flight txs.

This script creates N new keys post-genesis (loadgen-pool-0 .. loadgen-pool-
<N-1>) and funds every under-funded one from LOADGEN_KEY in a single
MsgMultiSend transaction (one input, one output per account that needs
topping up). Idempotent: an existing key is reused, balances are checked
first, and only accounts below --amount are included as outputs — if
everyone's already funded, the transaction is skipped entirely.

Usage:
    python3 setup_pool.py --pool-size 10 [--amount 100000000stake]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402

POOL_KEY_PREFIX = "loadgen-pool-"


def pool_key_name(i: int) -> str:
    return f"{POOL_KEY_PREFIX}{i}"


def _run(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def key_exists(cfg, name: str) -> bool:
    r = _run([cfg["PQCHAIND_BIN"], "keys", "show", name, "-a",
              "--home", cfg["CHAIN_HOME"], "--keyring-backend", "test"])
    return r.returncode == 0


def create_key(cfg, name: str, log) -> str:
    if not key_exists(cfg, name):
        r = _run([cfg["PQCHAIND_BIN"], "keys", "add", name, "--key-type", "ml_dsa_65",
                   "--home", cfg["CHAIN_HOME"], "--keyring-backend", "test"], timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"keys add {name} failed: {r.stderr[-400:]}")
        log(f"  created key {name}")
    addr = _run([cfg["PQCHAIND_BIN"], "keys", "show", name, "-a",
                 "--home", cfg["CHAIN_HOME"], "--keyring-backend", "test"]).stdout.strip()
    if not addr:
        raise RuntimeError(f"could not resolve address for key {name}")
    return addr


def balance(cfg, addr: str, denom: str) -> int:
    r = _run([cfg["PQCHAIND_BIN"], "query", "bank", "balances", addr,
              "--home", cfg["CHAIN_HOME"], "--node", cfg["CHAIN_NODE"], "-o", "json"], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"balance query for {addr} failed: {r.stderr[-400:]}")
    out = json.loads(r.stdout)
    for b in out.get("balances", []):
        if b["denom"] == denom:
            return int(b["amount"])
    return 0


def fund_multi(cfg, from_key: str, top_ups: list[tuple[str, int]], denom: str, log) -> None:
    """Fund every (addr, amount) in `top_ups` in a single MsgMultiSend."""
    from_addr = _run([cfg["PQCHAIND_BIN"], "keys", "show", from_key, "-a",
                       "--home", cfg["CHAIN_HOME"], "--keyring-backend", "test"]).stdout.strip()
    total = sum(amount for _, amount in top_ups)
    msg = {
        "@type": "/cosmos.bank.v1beta1.MsgMultiSend",
        "inputs": [{"address": from_addr, "coins": [{"denom": denom, "amount": str(total)}]}],
        "outputs": [{"address": addr, "coins": [{"denom": denom, "amount": str(amount)}]}
                    for addr, amount in top_ups],
    }
    msg_path = Path(cfg["DEVNET_DIR"]) / "msg-fund-pool.json"
    msg_path.write_text(json.dumps(msg))
    # generous fixed gas: base cost plus a per-output allowance, since gas_limit
    # here isn't tied to the (fixed) fee sendtx.py pays.
    gas = str(200_000 + 50_000 * len(top_ups))
    cmd = cfg["SENDTX_CMD"].split() + [str(msg_path), from_key, gas]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    msg_path.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"multi-send funding failed: {(r.stderr or r.stdout)[-400:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out.get("code", 0) != 0:
        raise RuntimeError(f"MsgMultiSend rejected code={out.get('code')}: {out.get('raw_log', '')[:300]}")
    for addr, amount in top_ups:
        log(f"  funded {addr} with {amount}{denom}")
    log(f"  multi-send tx {out['txhash']} ({len(top_ups)} accounts, {total}{denom} total)")


def ensure_pool(cfg, pool_size: int, amount: int, denom: str, log=print) -> list[str]:
    """Returns the pool's key names, in order, after ensuring all exist and
    are funded to at least `amount`."""
    from_key = cfg.get("LOADGEN_KEY") or "loadgen"
    names = [pool_key_name(i) for i in range(pool_size)]
    # `keys add`/`keys show` are purely local (keyring on disk, no chain RPC),
    # each touching only its own key's file, so run them concurrently — this
    # is what makes pool setup scale to large pool sizes.
    with ThreadPoolExecutor(max_workers=min(len(names), 16) or 1) as ex:
        addrs = list(ex.map(lambda name: create_key(cfg, name, log), names))

    top_ups = []
    for name, addr in zip(names, addrs):
        bal = balance(cfg, addr, denom)
        if bal < amount:
            top_ups.append((addr, amount - bal))
        else:
            log(f"  {name} ({addr}) already funded: {bal}{denom}")

    if not top_ups:
        log("  all accounts already funded, skipping funding transaction")
    else:
        fund_multi(cfg, from_key, top_ups, denom, log)
    return names


def resolve_pool(cfg, pool_size: int) -> list[str]:
    """pool_size=1 uses the single LOADGEN_KEY (or RELAYER_KEY) account.
    pool_size>1 requires this module's ensure_pool to have already created
    and funded the pool (checked here, not silently created, so a run never
    pays setup cost inside a timed cell) — fails clearly naming the missing
    key otherwise. Shared by loadgen.py (submission) and ack_pool.py (return-
    leg acks) — same account-pool mechanism, reused for both roles per
    README.md's "Concurrent load: account pools" section.
    """
    if pool_size <= 1:
        return [cfg.get("LOADGEN_KEY") or cfg["RELAYER_KEY"]]
    names = [pool_key_name(i) for i in range(pool_size)]
    missing = [n for n in names if not key_exists(cfg, n)]
    if missing:
        raise RuntimeError(
            f"pool key(s) missing: {', '.join(missing)}. Run "
            f"'python3 setup_pool.py --pool-size {pool_size}' first.")
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-size", type=int, required=True)
    ap.add_argument("--amount", type=int, default=100_000_000,
                     help="stake each pool account is topped up to (default: 100000000, "
                          "enough for tens of thousands of LOADGEN_AMOUNT transfers + fees)")
    ap.add_argument("--denom", default=None, help="default: LOADGEN_DENOM from devnet.env")
    args = ap.parse_args()

    cfg = config.require(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_NODE",
                          "SENDTX_CMD", "DEVNET_DIR", "LOADGEN_KEY")
    denom = args.denom or cfg.get("LOADGEN_DENOM", "stake")

    def log(msg):
        print(msg, flush=True)

    log(f"ensuring pool of {args.pool_size} accounts, each funded to {args.amount}{denom}...")
    names = ensure_pool(cfg, args.pool_size, args.amount, denom, log=log)
    log(f"pool ready: {', '.join(names)}")


if __name__ == "__main__":
    main()
