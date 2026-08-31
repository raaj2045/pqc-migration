#!/usr/bin/env python3
"""Assemble cw-ics08-wasm-eth's InstantiateMsg from the collected devnet data.

  InstantiateMsg { client_state: Binary, consensus_state: Binary, checksum: Binary }

client_state / consensus_state are JSON-serialized (serde_json) then base64'd.
Never round-trip client_state through jq: FULU_FORK_EPOCH is 2^64-1 and jq 1.6
stores numbers as doubles, which pushes it above u64::MAX.
"""
import base64
import json
import os
import re
import subprocess
import sys

# Reads and writes inside $DEVNET_DIR, next to the files
# collect-instantiate-inputs.sh produced.
os.chdir(os.environ.get("DEVNET_DIR") or sys.exit("set DEVNET_DIR"))

ROUTER = sys.argv[1]
PUBKEYS_HASH = sys.argv[2]
CHECKSUM_HEX = sys.argv[3]

client_state_text = open("instantiate-inputs/client_state.json").read()

# Substitute the real router address textually so the exact fulu epoch literal
# is preserved (json.loads/dumps would keep it, but sed-style is safest here).
client_state_text = client_state_text.replace(
    '"ibc_contract_address": "0x0000000000000000000000000000000000000000"',
    f'"ibc_contract_address": "{ROUTER}"',
)
assert ROUTER in client_state_text, "router address substitution failed"

# Sanity: the exact u64::MAX literal must still be intact.
assert "18446744073709551615" in client_state_text, "fulu epoch precision lost"

consensus = json.load(open("instantiate-inputs/consensus_state.json"))
consensus["current_sync_committee"]["pubkeys_hash"] = PUBKEYS_HASH
consensus_text = json.dumps(consensus, separators=(",", ":"))

# Invariant enforced by the contract: client_state.latest_slot == consensus_state.slot
latest_slot = int(re.search(r'"latest_slot":\s*(\d+)', client_state_text).group(1))
assert latest_slot == consensus["slot"], (
    f"latest_slot {latest_slot} != consensus slot {consensus['slot']}"
)

msg = {
    "client_state": base64.b64encode(client_state_text.encode()).decode(),
    "consensus_state": base64.b64encode(consensus_text.encode()).decode(),
    "checksum": base64.b64encode(bytes.fromhex(CHECKSUM_HEX)).decode(),
}
json.dump(msg, open("instantiate-msg.json", "w"), indent=2)

print(f"ibc_contract_address : {ROUTER}")
print(f"pubkeys_hash         : {PUBKEYS_HASH}")
print(f"checksum             : {CHECKSUM_HEX}")
print(f"latest_slot          : {latest_slot} (== consensus slot, invariant OK)")
print(f"fulu epoch intact    : {'18446744073709551615' in client_state_text}")
print("wrote instantiate-msg.json")
