#!/usr/bin/env python3
"""Create and fund Cosmos accounts to sign delivery transactions with.

Flows running at the same time cannot share a signing account: every Cosmos
transaction needs that account's next sequence number, and two in-flight
transactions racing for the same one collide. Each concurrent flow therefore
needs an account to itself.

Keys are created post-genesis and funded from an existing funded account.
Idempotent: an existing key is reused and only under-funded accounts are
topped up, so re-running when everything is funded does nothing.

    python3 setup-signer-pool.py --size 10 --key-type secp256k1
    python3 setup-signer-pool.py --size 10 --key-type mldsa65

Names are `mv-<key-type>-<i>`, so the two pools never collide.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "devnet" / "lib"))
import config  # noqa: E402

# What `pqchaind keys add --key-type` calls each algorithm, keyed by the short
# name used everywhere else in this experiment.
KEY_TYPES = {"secp256k1": "secp256k1", "mldsa65": "ml_dsa_65"}


def cli(cfg, args, parse=True):
    r = subprocess.run(
        [cfg["PQCHAIND_BIN"], *args, "--home", cfg["CHAIN_HOME"], "--keyring-backend", "test"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{' '.join(args[:3])} failed: {(r.stderr or r.stdout)[-400:]}")
    return json.loads(r.stdout) if parse else r.stdout


def pool_names(key_type, size):
    return [f"mv-{key_type}-{i}" for i in range(size)]


def ensure_keys(cfg, names, key_type):
    existing = {k["name"] for k in cli(cfg, ["keys", "list", "--output", "json"])}
    addresses = {}
    for name in names:
        if name not in existing:
            cli(cfg, ["keys", "add", name, "--key-type", KEY_TYPES[key_type],
                      "--output", "json"], parse=False)
            print(f"  created {name} ({key_type})")
        addresses[name] = cli(cfg, ["keys", "show", name, "-a"], parse=False).strip()
    return addresses


def balances(cfg, addresses, denom):
    out = {}
    for name, addr in addresses.items():
        r = subprocess.run(
            [cfg["PQCHAIND_BIN"], "query", "bank", "balances", addr,
             "--node", cfg["CHAIN_NODE"], "--home", cfg["CHAIN_HOME"], "--output", "json"],
            capture_output=True, text=True)
        amount = 0
        if r.returncode == 0:
            for c in json.loads(r.stdout).get("balances", []):
                if c["denom"] == denom:
                    amount = int(c["amount"])
        out[name] = amount
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--key-type", choices=sorted(KEY_TYPES), required=True)
    ap.add_argument("--amount", type=int, default=200_000_000,
                    help="top every account up to at least this many stake")
    ap.add_argument("--from-key", default=None,
                    help="funding account (default: RELAYER_KEY)")
    args = ap.parse_args()

    cfg = config.require(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_NODE",
                         "CHAIN_ID", "RELAYER_KEY", "SENDTX_CMD")
    funder = args.from_key or cfg["RELAYER_KEY"]
    names = pool_names(args.key_type, args.size)

    print(f"signer pool: {args.size} x {args.key_type}")
    addresses = ensure_keys(cfg, names, args.key_type)

    have = balances(cfg, addresses, "stake")
    short = {n: args.amount - have[n] for n in names if have[n] < args.amount}
    if not short:
        print(f"  all {len(names)} account(s) already hold >= {args.amount:,} stake")
        return

    print(f"  funding {len(short)} account(s) from {funder}")
    funder_addr = cli(cfg, ["keys", "show", funder, "-a"], parse=False).strip()
    total = sum(short.values())
    msg = {
        "@type": "/cosmos.bank.v1beta1.MsgMultiSend",
        "inputs": [{"address": funder_addr,
                    "coins": [{"denom": "stake", "amount": str(total)}]}],
        "outputs": [{"address": addresses[n],
                     "coins": [{"denom": "stake", "amount": str(v)}]}
                    for n, v in sorted(short.items())],
    }
    path = Path(cfg["DEVNET_DIR"]) / "msg-signer-pool-fund.json"
    path.write_text(json.dumps([msg]))
    r = subprocess.run(cfg["SENDTX_CMD"].split() + [str(path), funder, "400000"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"funding failed: {(r.stderr or r.stdout)[-600:]}")
    print(f"  funded {len(short)} account(s) with {total:,} stake total")

    still = {n: v for n, v in balances(cfg, addresses, "stake").items() if v < args.amount}
    if still:
        raise SystemExit(f"still under-funded after the transfer: {sorted(still)}")
    print(f"  pool ready: {', '.join(names[:4])}{' ...' if len(names) > 4 else ''}")


if __name__ == "__main__":
    main()
