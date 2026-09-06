#!/usr/bin/env python3
"""Relayer step: poll the beacon light_client/finality_update and submit it to
08-wasm-1 as a MsgUpdateClient, keeping the Ethereum light client current.

Usage: update-eth-client.py [<client-id>] [--signer-key=<keyring key name>]
--signer-key defaults to RELAYER_KEY (devnet.env) — pass a pool account's key
name to sign (and correctly self-attribute) the update from that account
instead, e.g. from ack_pool.py's concurrent workers.

Header {
  active_sync_committee: {"Current": SyncCommittee},   # full 512 pubkeys
  consensus_update: LightClientUpdate,                 # = finality_update.data
  trusted_slot: u64 (as string),                       # slot we already trust
}
wrapped as /ibc.lightclients.wasm.v1.ClientMessage { data: base64(json) }.
"""
import base64
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import config  # noqa: E402

CFG = config.require(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_NODE",
                     "BEACON_URL", "SENDTX_CMD", "RELAYER_KEY")
BIN = CFG["PQCHAIND_BIN"]
HOME = CFG["CHAIN_HOME"]
NODE = CFG["CHAIN_NODE"]
BEACON = CFG["BEACON_URL"]

# --signer-key=<keyring key name> lets a pool worker (ack_pool.py) update the
# client from its own account instead of always RELAYER_KEY — a concurrent
# pool needs each worker's tx signed AND self-consistent (the msg's "signer"
# address must match whichever key actually signs it, or the ante handler
# rejects it), so the signer ADDRESS below is always resolved from SIGNER_KEY
# live against the keyring.
_argv = [a for a in sys.argv[1:] if not a.startswith("--signer-key=")]
_signer_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--signer-key=")), None)
SIGNER_KEY = _signer_arg or CFG["RELAYER_KEY"]
CLIENT_ID = _argv[0] if _argv else CFG.get("COSMOS_CLIENT_ID", "08-wasm-1")
SIGNER_ADDR = subprocess.check_output(
    [BIN, "keys", "show", SIGNER_KEY, "-a", "--home", HOME, "--keyring-backend", "test"],
    text=True).strip()


def get(url):
    return json.loads(subprocess.check_output(["curl", "-s", url]))


def cli(args):
    return subprocess.check_output(
        [BIN, *args, "--home", HOME, "--node", NODE, "-o", "json"], text=True
    )


def current_trusted_slot():
    """Highest slot we hold a consensus state for."""
    states = json.loads(cli(["query", "ibc", "client", "consensus-states", CLIENT_ID]))
    return max(int(e["height"]["revision_height"]) for e in states["consensus_states"])


def main():
    trusted_slot = current_trusted_slot()

    # The active sync committee is not stored on-chain in full (only its hash),
    # so the relayer must supply it. Take it from the bootstrap at the
    # finalized checkpoint of the period we are updating within.
    fin = get(f"{BEACON}/eth/v1/beacon/states/head/finality_checkpoints")
    fin_root = fin["data"]["finalized"]["root"]
    bootstrap = get(f"{BEACON}/eth/v1/beacon/light_client/bootstrap/{fin_root}")
    sync_committee = bootstrap["data"]["current_sync_committee"]

    update = get(f"{BEACON}/eth/v1/beacon/light_client/finality_update")["data"]
    new_slot = int(update["finalized_header"]["beacon"]["slot"])
    if new_slot <= trusted_slot:
        print(f"no-op: finalized slot {new_slot} <= trusted {trusted_slot}")
        return

    consensus_update = dict(update)
    consensus_update["next_sync_committee"] = None
    consensus_update["next_sync_committee_branch"] = None

    header = {
        "active_sync_committee": {"Current": sync_committee},
        "consensus_update": consensus_update,
        # Plain u64 on the Rust side (Header has no #[serde_as]), so this must
        # be a JSON number — unlike the slots inside LightClientUpdate, which
        # are DisplayFromStr and therefore strings.
        "trusted_slot": trusted_slot,
    }
    header_bz = json.dumps(header, separators=(",", ":")).encode()

    msg = {
        "@type": "/ibc.core.client.v1.MsgUpdateClient",
        "client_id": CLIENT_ID,
        "client_message": {
            "@type": "/ibc.lightclients.wasm.v1.ClientMessage",
            "data": base64.b64encode(header_bz).decode(),
        },
        "signer": SIGNER_ADDR,
    }
    path = config.path_in_devnet(CFG, "msg-update-eth-client.json")
    json.dump(msg, open(path, "w"))
    print(f"updating {CLIENT_ID}: trusted_slot {trusted_slot} -> finalized {new_slot}")

    # The contract derives "current slot" from the Cosmos block time, which can
    # lag the beacon by a slot or two. When that happens the update is not
    # wrong, just early — wait for the chain's clock to catch up and resend.
    import time
    for attempt in range(6):
        out = subprocess.run(
            [*CFG["SENDTX_CMD"].split(), path, SIGNER_KEY, "4000000"],
            capture_output=True, text=True,
        )
        result = out.stdout.strip() or out.stderr.strip()[-800:]
        if "more recent than the calculated current slot" in result:
            print(f"  attempt {attempt + 1}: chain clock behind signature slot, waiting")
            time.sleep(8)
            continue
        print(result)
        return
    print("gave up waiting for the chain clock to catch up")


if __name__ == "__main__":
    main()
