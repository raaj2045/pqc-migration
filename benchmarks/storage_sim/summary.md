# Storage simulation — secp256k1 vs ML-DSA-44

Default tx mix `transfer:60,migration:20,stake:15,gov:5`. Account-state storage uses a power-law growth model (`unique(n) = 1.256 · n^0.8`), giving a ~5% new-signer ratio at 10 M tx. Sizes are modeled against Cosmos SDK proto types; see `tools/storage_sim/main.go` for constants and references.

## Per-tx wire size

| Component | secp256k1 | ML-DSA-44 |
|---|---:|---:|
| Envelope overhead | 110 B | 110 B |
| Average message body | 105 B | 105 B |
| Public key | 33 B | 1312 B |
| Signature | 64 B | 2420 B |
| **Total per tx** | **312 B** | **3947 B** |

## Final chain size by N

| N (tx) | Accounts | secp256k1 state | ML-DSA-44 state | State ratio | secp256k1 tx data | ML-DSA-44 tx data | Tx-data ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| 100 K | 12,560 | 1.59 MB | 16.91 MB | 10.62x | 29.76 MB | 376.42 MB | 12.65x |
| 1 M | 79,248 | 10.05 MB | 106.71 MB | 10.62x | 297.59 MB | 3.68 GB | 12.65x |
| 10 M | 500,022 | 63.42 MB | 673.32 MB | 10.62x | 2.91 GB | 36.76 GB | 12.65x |

## Headline result

**At 10M transactions, ML-DSA-44 state is 673.3 MiB (0.658 GiB) vs secp256k1 63.4 MiB (0.062 GiB), a ratio of 10.62x.**

Including historical tx data, the full ledger footprint at 10 M tx is 37.42 GiB (ML-DSA-44) vs 2.97 GiB (secp256k1), a ratio of 12.61x.
