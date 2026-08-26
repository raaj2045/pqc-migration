#!/usr/bin/env python3
"""Adversarial light-client update harness for cw-ics08-wasm-eth.

Builds a genuine MsgUpdateClient from the live beacon chain, tampers with
exactly ONE field, submits it, and reports the chain's rejection verbatim.

The point of tampering one field at a time is discrimination: a harness that
corrupts everything at once cannot distinguish "the client checked the thing I
broke" from "the client rejected malformed input generically". Each mode below
leaves the BLS sync-committee signature genuinely valid, so a pass means the
client is verifying the binding between the signed beacon header and the
payload — not merely checking a signature.

Modes:
  baseline      untampered. CONTROL: must SUCCEED. Run before and after the
                adversarial modes so a rejection cannot be confused with a
                client that was simply broken or stalled.
  state_root    finalized_header.execution.state_root corrupted.
  block_number  finalized_header.execution.block_number set to a value that
                does not match the synced chain.
  nonfinal      finalized_header replaced with a recent NON-FINALIZED header,
                exercising the finality-depth check.

Usage:
  python3 adversarial_update.py <mode> [client-id]

Requires a live devnet. Configuration resolves through devnet/lib/config.py --
the same precedence chain the devnet scripts use -- so nothing is hardcoded.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402

MODES = ("baseline", "state_root", "block_number", "nonfinal")


def get(url):
    return json.loads(subprocess.check_output(["curl", "-s", "-m", "30", url]))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.exit(f"usage: adversarial_update.py <{'|'.join(MODES)}> [client-id]")
    mode = sys.argv[1]

    cfg = config.require(
        config.load(),
        "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_NODE",
        "BEACON_URL", "COSMOS_CLIENT_ID", "SENDTX_CMD", "VALIDATOR",
    )
    client_id = sys.argv[2] if len(sys.argv) > 2 else cfg["COSMOS_CLIENT_ID"]
    beacon = cfg["BEACON_URL"].rstrip("/")

    def cli(args):
        return subprocess.check_output(
            [cfg["PQCHAIND_BIN"], *args, "--home", cfg["CHAIN_HOME"],
             "--node", cfg["CHAIN_NODE"], "-o", "json"], text=True)

    states = json.loads(cli(["query", "ibc", "client", "consensus-states", client_id]))
    trusted = max(int(e["height"]["revision_height"]) for e in states["consensus_states"])

    fin = get(f"{beacon}/eth/v1/beacon/states/head/finality_checkpoints")
    fin_root = fin["data"]["finalized"]["root"]
    bootstrap = get(f"{beacon}/eth/v1/beacon/light_client/bootstrap/{fin_root}")
    if "data" not in bootstrap:
        # Most commonly the devnet has run past a sync-committee period
        # boundary: the beacon node only serves a bootstrap for a period it
        # still holds. Advancing across a period needs next_sync_committee in
        # the update, which the relayer tooling here does not supply.
        sys.exit(
            f"cannot build an update: beacon returned no bootstrap for finalized "
            f"root {fin_root}\n  response: {json.dumps(bootstrap)[:300]}\n"
            f"  If this says 'Sync committee for period N not found', the devnet "
            f"has crossed a sync-committee period boundary and this harness "
            f"cannot advance the client across it. Rebuild the devnet, or extend "
            f"the relayer to carry next_sync_committee.")
    sync_committee = bootstrap["data"]["current_sync_committee"]

    update = get(f"{beacon}/eth/v1/beacon/light_client/finality_update")["data"]
    cu = dict(update)
    cu["next_sync_committee"] = None
    cu["next_sync_committee_branch"] = None

    fin_slot = int(cu["finalized_header"]["beacon"]["slot"])
    orig_root = cu["finalized_header"]["execution"]["state_root"]
    orig_blk = cu["finalized_header"]["execution"]["block_number"]
    head_slot = int(get(f"{beacon}/eth/v1/beacon/headers/head")
                    ["data"]["header"]["message"]["slot"])

    print(f"MODE={mode}  client={client_id}")
    print(f"  trusted slot (client) : {trusted}")
    print(f"  finalized slot        : {fin_slot}")
    print(f"  head slot (non-final) : {head_slot}")
    print(f"  exec state_root       : {orig_root}")
    print(f"  exec block_number     : {orig_blk}")

    if mode == "state_root":
        bad = "0x" + ("de" * 32)
        cu["finalized_header"]["execution"]["state_root"] = bad
        print(f"  TAMPERED state_root   : {bad}")
    elif mode == "block_number":
        bad = str(int(orig_blk) + 100000)
        cu["finalized_header"]["execution"]["block_number"] = bad
        print(f"  TAMPERED block_number : {bad} (chain has {orig_blk})")
    elif mode == "nonfinal":
        hdr = get(f"{beacon}/eth/v1/beacon/headers/head")["data"]["header"]["message"]
        body = get(f"{beacon}/eth/v2/beacon/blocks/{head_slot}")["data"]["message"]["body"]
        ep = body["execution_payload"]
        cu["finalized_header"]["beacon"] = {
            "slot": str(head_slot),
            "proposer_index": hdr["proposer_index"],
            "parent_root": hdr["parent_root"],
            "state_root": hdr["state_root"],
            "body_root": hdr["body_root"],
        }
        cu["finalized_header"]["execution"]["state_root"] = ep["state_root"]
        cu["finalized_header"]["execution"]["block_number"] = ep["block_number"]
        print(f"  TAMPERED finalized_header -> NON-FINALIZED slot {head_slot} "
              f"(exec block {ep['block_number']}); real finality at slot {fin_slot}")

    header = {"active_sync_committee": {"Current": sync_committee},
              "consensus_update": cu,
              # Plain u64 on the Rust side, so a JSON number -- unlike the slots
              # inside LightClientUpdate, which are DisplayFromStr strings.
              "trusted_slot": trusted}
    msg = {
        "@type": "/ibc.core.client.v1.MsgUpdateClient",
        "client_id": client_id,
        "client_message": {
            "@type": "/ibc.lightclients.wasm.v1.ClientMessage",
            "data": base64.b64encode(
                json.dumps(header, separators=(",", ":")).encode()).decode(),
        },
        "signer": cfg["VALIDATOR"],
    }
    path = Path(cfg["DEVNET_DIR"]) / f"msg-adv-{mode}.json"
    path.write_text(json.dumps(msg))

    # The contract derives "current slot" from Cosmos block time, which can lag
    # the beacon. That is not a rejection, just an early submission -- retry.
    for attempt in range(6):
        out = subprocess.run(cfg["SENDTX_CMD"].split() + [str(path), "validator", "4000000"],
                             capture_output=True, text=True)
        res = (out.stdout or "").strip() + (out.stderr or "").strip()[-1500:]
        if "more recent than the calculated current slot" in res:
            print(f"  (chain clock behind beacon, retry {attempt + 1})")
            time.sleep(8)
            continue
        print("  RESULT:")
        print("   ", res.replace("\n", "\n    ")[:2000])
        break
    path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
