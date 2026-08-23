#!/usr/bin/env python3
"""Compute the SSZ hash-tree-root of a sync committee's pubkey vector.

cw-ics08-wasm-eth stores SummarizedSyncCommittee { pubkeys_hash, aggregate_pubkey }
where pubkeys_hash = pubkeys.tree_hash_root() over Vector[BLSPubkey, 512]
(packages/ethereum/types/src/consensus/sync_committee.rs). The Beacon API serves
the pubkeys but not their root, so the caller must compute it.

Usage: pubkeys_hash.py <json-file-containing-array-of-0x-pubkeys>
"""
import hashlib
import json
import sys


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def merkleize(chunks: list[bytes]) -> bytes:
    """Merkleize a list of 32-byte chunks, padding to the next power of two."""
    if not chunks:
        return b"\x00" * 32
    width = 1
    while width < len(chunks):
        width *= 2
    layer = chunks + [b"\x00" * 32] * (width - len(chunks))
    while len(layer) > 1:
        layer = [sha256(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
    return layer[0]


def pubkey_root(pk: bytes) -> bytes:
    """HTR of a 48-byte BLSPubkey: pack into 32-byte chunks, then merkleize."""
    assert len(pk) == 48, f"expected 48-byte pubkey, got {len(pk)}"
    padded = pk + b"\x00" * (64 - 48)  # 48 bytes -> two chunks
    return merkleize([padded[0:32], padded[32:64]])


def main() -> None:
    pubkeys = json.load(open(sys.argv[1]))
    roots = [pubkey_root(bytes.fromhex(p.removeprefix("0x"))) for p in pubkeys]
    print(f"pubkeys: {len(roots)}", file=sys.stderr)
    print("0x" + merkleize(roots).hex())


if __name__ == "__main__":
    main()
