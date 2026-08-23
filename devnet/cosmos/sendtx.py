#!/usr/bin/env python3
# Assemble, sign (ML-DSA-65 keyring), broadcast, and await one Cosmos tx.
# Usage: sendtx.py <msg-json-file> <from-key> [gas]
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import config  # noqa: E402

_CFG = config.require(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_ID", "CHAIN_NODE")
BIN = _CFG["PQCHAIND_BIN"]
HOME = _CFG["CHAIN_HOME"]
CHAIN = _CFG["CHAIN_ID"]
NODE = _CFG["CHAIN_NODE"]


def run(args, parse=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAIL: {' '.join(args[:4])}...\nstdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}")
    return json.loads(r.stdout) if parse else r.stdout


def main():
    msg_file, from_key = sys.argv[1], sys.argv[2]
    gas = sys.argv[3] if len(sys.argv) > 3 else "600000"
    msgs = json.load(open(msg_file))
    if not isinstance(msgs, list):
        msgs = [msgs]

    tx = {
        "body": {
            "messages": msgs,
            "memo": "",
            "timeout_height": "0",
            "extension_options": [],
            "non_critical_extension_options": [],
        },
        "auth_info": {
            "signer_infos": [],
            "fee": {"amount": [{"denom": "stake", "amount": "6000"}], "gas_limit": gas, "payer": "", "granter": ""},
        },
        "signatures": [],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(tx, f)
        unsigned = f.name

    signed_out = run([
        BIN, "tx", "sign", unsigned, "--from", from_key, "--chain-id", CHAIN,
        "--keyring-backend", "test", "--home", HOME, "--node", NODE, "--output-document", "/dev/stdout",
    ], parse=False)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(signed_out)
        signed = f.name

    res = run([BIN, "tx", "broadcast", signed, "--node", NODE, "--home", HOME, "-o", "json"])
    txhash = res["txhash"]
    if res.get("code", 0) != 0:
        sys.exit(f"BROADCAST FAIL code={res['code']} log={res.get('raw_log')}")

    for _ in range(30):
        time.sleep(2)
        q = subprocess.run([BIN, "query", "tx", txhash, "--node", NODE, "--home", HOME, "-o", "json"],
                           capture_output=True, text=True)
        if q.returncode == 0:
            resp = json.loads(q.stdout)
            out = {"txhash": txhash, "code": resp["code"], "height": resp["height"],
                   "gas_wanted": resp["gas_wanted"], "gas_used": resp["gas_used"]}
            if resp["code"] != 0:
                out["raw_log"] = resp["raw_log"]
            # on-wire size: proto-encode the signed tx and measure the bytes
            import base64
            enc = run([BIN, "tx", "encode", signed, "--home", HOME], parse=False).strip()
            out["tx_bytes"] = len(base64.b64decode(enc))
            print(json.dumps(out))
            if resp["code"] != 0:
                sys.exit(1)
            return
    sys.exit(f"tx {txhash} not included after 60s")


if __name__ == "__main__":
    main()
