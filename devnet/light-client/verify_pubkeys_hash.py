#!/usr/bin/env python3
"""Independently validate the computed pubkeys_hash.

The bootstrap carries current_sync_committee_branch, a Merkle proof of the
SyncCommittee container against the finalized header's state_root. If our
pubkeys_hash is right, folding that branch must reproduce state_root exactly.

SyncCommittee HTR = merkleize([pubkeys_root, aggregate_pubkey_root])
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import config  # noqa: E402
from pubkeys_hash import merkleize, pubkey_root, sha256  # noqa: E402

# Bootstrap payload path: argv[1], else the devnet's collected inputs.
_cfg = config.load()
_default = config.path_in_devnet(_cfg, "instantiate-inputs/bootstrap.json")
bs = json.load(open(sys.argv[1] if len(sys.argv) > 1 else _default))["data"]
state_root = bytes.fromhex(bs["header"]["beacon"]["state_root"][2:])
sc = bs["current_sync_committee"]
branch = [bytes.fromhex(b[2:]) for b in bs["current_sync_committee_branch"]]

pubkeys_root = merkleize([pubkey_root(bytes.fromhex(p[2:])) for p in sc["pubkeys"]])
agg_root = pubkey_root(bytes.fromhex(sc["aggregate_pubkey"][2:]))
leaf = merkleize([pubkeys_root, agg_root])

print(f"pubkeys_hash          : 0x{pubkeys_root.hex()}")
print(f"SyncCommittee HTR     : 0x{leaf.hex()}")
print(f"branch depth          : {len(branch)}")
print(f"target state_root     : 0x{state_root.hex()}")

# Fold the branch for every generalized index at this depth; the correct one
# reproduces state_root. Brute force avoids hardcoding a fork-specific gindex.
depth = len(branch)
for gindex in range(2**depth, 2 ** (depth + 1)):
    node = leaf
    idx = gindex
    for sibling in branch:
        node = sha256(sibling + node) if idx % 2 else sha256(node + sibling)
        idx //= 2
    if node == state_root:
        print(f"\nMATCH at gindex {gindex}: branch folds to state_root")
        print("pubkeys_hash is VERIFIED against on-chain consensus data")
        sys.exit(0)

print("\nNO MATCH — pubkeys_hash or the SSZ derivation is wrong")
sys.exit(1)
