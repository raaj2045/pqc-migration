# Gas per transfer, by how many move at once

Delivery is Ethereum -> Cosmos, acknowledgement is Cosmos -> Ethereum.
Each figure is gas for ONE transfer; ± is a 95% confidence interval and
n is the number of runs behind it.

## secp256k1 signer

| Transfers at once | Deliver on Cosmos | Acknowledge on Ethereum |
|---:|---:|---:|
| 10 | 149,348 ± 8,239 (n=4) | 108,919 ± 1,667 (n=3) · real verifier 132,695 (n=1) |
| 20 | 129,851 ± 0 (n=1) | real verifier 114,420 (n=1) |
| 25 | 150,652 ± 161 (n=3) | 101,543 ± 10 (n=3) |
| 30 | 133,957 ± 0 (n=1) | real verifier 109,293 (n=1) |
| 40 | 133,323 ± 0 (n=1) | real verifier 106,553 (n=1) |
| 50 | 148,972 ± 6,008 (n=7) | 99,840 ± 15 (n=2) |
| 60 | 133,014 ± 0 (n=1) | over the 56-ack limit |
| 100 | 146,361 ± 40 (n=3) | over the 56-ack limit |
| 500 | 145,982 ± 92 (n=3) | over the 56-ack limit |
| 1000 | 145,949 ± 62 (n=3) | over the 56-ack limit |

## ML-DSA-65 signer

| Transfers at once | Deliver on Cosmos | Acknowledge on Ethereum |
|---:|---:|---:|
| 10 | 168,018 ± 600 (n=3) | 108,267 ± 1,647 (n=3) |
| 25 | 156,511 ± 456 (n=3) | 102,021 ± 664 (n=3) |
| 50 | 152,791 ± 253 (n=3) | 102,564 ± 37 (n=3) |
| 100 | 150,752 ± 212 (n=3) | over the 56-ack limit |
| 500 | 149,294 ± 53 (n=3) | over the 56-ack limit |
| 1000 | 149,326 ± 39 (n=3) | over the 56-ack limit |

## Reading it

Cost per transfer falls as more move together, because the costs charged
once per transaction — the CosmWasm verification, the per-transaction
overhead and the signature — are divided among more transfers. Both legs
flatten well before the 56-acknowledgement limit, so that limit
costs nothing in gas; it only caps how many transfers one batch may hold.

An acknowledgement batch is whatever one delivery produced, and the
multicall carrying it cannot be split, so delivering more than ~56
transfers at once leaves them impossible to acknowledge.

The acknowledgement figures marked *real verifier* (10, 20, 30, 40 transfers
at once) include the on-chain Groth16 proof check, about 207,000 gas
charged once per transaction. Every other acknowledgement figure was
measured against the mock verifier, whose proof check does nothing, so
it carries no such charge. The two are not comparable down a column:
a marked row is higher because it paid for verification, not because
cost per transfer rose with batch size. See the proving-cost section
of README.md.
