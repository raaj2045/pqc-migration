# Measured limits

Every limit on this path is set by infrastructure — node configuration and
client software — not by the signature algorithm. Measured against the live
devnet with the mock SP1 verifier.

## Summary

| Limit | Set by | Ceiling | Can a batch be split to get past it? |
|---|---|---|---|
| Delivery transaction size | CometBFT `max_tx_bytes`, 4 MB | ~675 transfers | yes |
| Delivery over stock RPC | CometBFT `max_body_bytes`, 1 MB | 124 transfers | yes |
| Acknowledgement size | geth `txMaxSize`, 128 KB | **~56 transfers** | **no** |
| Proof availability | geth `TriesInMemory`, 128 blocks | ~5-minute window | n/a |

**The binding limit is ~56.** An acknowledgement batch is whatever one delivery
produced, and it cannot be split, so a delivery larger than ~56 transfers can
never be acknowledged. Quoting the delivery ceiling alone overstates the usable
batch size by an order of magnitude.

## Delivery: Ethereum → Cosmos

Two walls, whichever is smaller:

- **`max_body_bytes` (1 MB default).** `pqchaind tx broadcast` base64-encodes
  the transaction into a JSON-RPC body, and base64 inflates by 4/3, so the
  usable raw transaction is ~750 KB — under a fifth of `max_tx_bytes`.
  Over-limit transactions are refused with HTTP 400 at the RPC, before CheckTx,
  so they consume nothing. This is node configuration; raising it exposes the
  wall below.
- **`max_tx_bytes` (4 MB).** The protocol limit. Over-limit transactions fail
  CheckTx with code 21.

| | secp256k1 | ML-DSA-65 |
|---|---|---|
| public key / signature | 33 B / 64 B | 1,952 B / 3,309 B |
| bytes per transfer | 6,000.2 | 6,000.2 |
| fixed overhead | −14 B | 5,155 B |
| ceiling at 1 MB RPC | 124 | 124 |
| ceiling at 4 MB mempool | 699 | 698 |

The per-transfer cost is identical between key types because it is entirely
Merkle-Patricia proof data, which contains nothing key-dependent. A post-quantum
signature costs a fixed ~5.2 KB per transaction — 0.7 % of a 750 KB transaction
— so it does not cost a single transfer slot. **Capacity difference: 0 %.**

The ceiling drifts as the router's storage trie deepens: 699 early in a devnet's
life, 675 after ~10,000 packets. Chunk sizes are therefore derived at run time
from the node's own configuration and a real signed transaction, never
hard-coded.

## Acknowledgement: Cosmos → Ethereum

Walled by geth's `txMaxSize`, 128 KB — a budget 32× smaller than the delivery
side's.

| acknowledgements | signed transaction |
|---:|---:|
| 50 | 105,397 B |
| 100 | 209,397 B |

≈2,094 B each, so **~56 per Ethereum transaction** at a 0.9 safety margin.

**This one cannot be worked around by splitting.** proof-api fuses a single
`update_client_and_membership` proof over every packet in the request, and
`SP1ICS07Tendermint` verifies those key/value pairs once and caches them *for
the duration of the transaction*. Re-encoding a subset as its own multicall
fails on the second transaction:

```
SP1ICS07Tendermint.KeyValuePairNotInCache(...)
```

Proving and acknowledging must share one transaction. This is the structural
difference from delivery, where every `MsgRecvPacket` carries its own proof and
1,000 transfers split into 590 + 410 at no cost — 145,949 gas per transfer
against 145,982 for 500 in a single transaction.

## Proof availability

geth runs `--gcmode=full` with `TriesInMemory = 128`, so `eth_getProof` works
only within ~128 blocks of head. Finality lags ~70 blocks, leaving a usable
window of roughly 58 blocks — about 5 minutes at this devnet's 6-second slots
(12 at mainnet's 12-second slots). Packets left unrelayed past it
become permanently unprovable and must be re-sent.

Proofs do not expire once built, only the ability to fetch them, so a large run
fetches every proof up front and broadcasts afterwards.

## Gas

Delivery, per transaction:

```
secp256k1   gas = 50,075 + 141,712 · n
ML-DSA-65   gas = 196,233 + 141,728 · n
```

Per-transfer slopes agree to within 16 gas (0.01 %). A post-quantum signer costs
a ~146,000 gas constant per transaction, so its share per transfer falls as more
transfers move together:

| transfers at once | 1 | 10 | 50 | 124 |
|---|---:|---:|---:|---:|
| ML-DSA-65 premium | +81.8 % | +8.9 % | +2.2 % | **+0.8 %** |

Post-quantum overhead on this path is a per-transaction constant, not a
per-transfer tax, on both size and gas.

## What this means

Capacity here is infrastructure-bound. Three separate limits bind before
post-quantum signature size matters at all, and at the one that binds first the
two key types carry an identical number of transfers.
