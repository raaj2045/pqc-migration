# Gas per transfer, by how many move at once

Delivery is Ethereum -> Cosmos, acknowledgement is Cosmos -> Ethereum.
Each figure is gas for ONE transfer; ± is a 95% confidence interval and
n is the number of runs behind it.

## secp256k1 signer

| Transfers at once | Deliver on Cosmos | Acknowledge on Ethereum |
|---:|---:|---:|
| 10 | 153,551 ± 231 (n=3) | 108,919 ± 1,667 (n=3) |
| 25 | 150,652 ± 161 (n=3) | 101,543 ± 10 (n=3) |
| 50 | 151,623 ± 3,573 (n=6) | 99,838 ± 9 (n=3) |
| 100 | 146,361 ± 40 (n=3) | over the 56-ack limit |
| 500 | 145,982 ± 92 (n=3) | over the 56-ack limit |
| 1000 | 145,949 ± 62 (n=3) | over the 56-ack limit |

## ML-DSA-65 signer

| Transfers at once | Deliver on Cosmos | Acknowledge on Ethereum |
|---:|---:|---:|
| 50 | 152,791 ± 253 (n=3) | 102,564 ± 37 (n=3) |
| 100 | 150,752 ± 212 (n=3) | over the 56-ack limit |
| 500 | 149,294 ± 53 (n=3) | over the 56-ack limit |
| 1000 | 149,326 ± 39 (n=3) | over the 56-ack limit |

## Reading it

Cost per transfer falls as more move together, because the costs charged
once per transaction — the light-client update, the per-transaction
overhead and the signature — are divided among more transfers. Both legs
flatten well before the 56-acknowledgement limit, so that limit
costs nothing in gas; it only caps how many transfers one batch may hold.

An acknowledgement batch is whatever one delivery produced, and the
multicall carrying it cannot be split, so delivering more than ~56
transfers at once leaves them impossible to acknowledge.
