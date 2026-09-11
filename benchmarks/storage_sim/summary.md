# Storage simulation — secp256k1 vs ML-DSA-65

Default tx mix `transfer:60,migration:20,stake:15,gov:5`. Account-state storage uses a power-law growth model (`unique(n) = 1.256 · n^0.8`), giving a ~5% new-signer ratio at 10 M tx. Sizes are modeled against Cosmos SDK proto types; see `tools/storage_sim/main.go` for constants and references.

## Per-tx wire size

| Component | secp256k1 | ML-DSA-65 |
|---|---:|---:|
| Envelope overhead | 110 B | 110 B |
| Average message body | 105 B | 105 B |
| Public key | 33 B | 1,952 B |
| Signature | 64 B | 3,309 B |
| **Total per tx** | **312 B** | **5,476 B** |

## Final chain size by N

| N (tx) | Accounts | secp256k1 state | ML-DSA-65 state | State ratio | secp256k1 tx data | ML-DSA-65 tx data | Tx-data ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| 100 K | 12,560 | 1.59 MB | 24.58 MB | 15.43x | 29.76 MB | 522.23 MB | 17.55x |
| 1 M | 79,248 | 10.05 MB | 155.08 MB | 15.43x | 297.59 MB | 5.10 GB | 17.55x |
| 10 M | 500,022 | 63.42 MB | 978.51 MB | 15.43x | 2.91 GB | 51.00 GB | 17.55x |

## Headline result

**At 10M transactions, ML-DSA-65 state is 978.5 MiB (0.956 GiB) vs secp256k1 63.4 MiB (0.062 GiB), a ratio of 15.43x.**

Including historical tx data, the full ledger footprint at 10 M tx is 51.96 GiB (ML-DSA-65) vs 2.97 GiB (secp256k1), a ratio of 17.51x.
