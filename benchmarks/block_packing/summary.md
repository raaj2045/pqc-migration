# Block packing — secp256k1 vs ML-DSA-65

Packs synthetic 1-in / 1-out `MsgSend` transactions into a Cosmos/CometBFT block until the next tx would overflow, then reports capacity and waste.

## Configuration

- Block max-bytes set with `--max-bytes 4194304` → **4 194 304 B (4 MiB)**, the same limits as the earlier ML-DSA-44 figure so the two can be compared. CometBFT's own genesis default is 22 020 096 B (21 MiB).
- Per-tx size (MsgSend, 1-in 1-out): `envelope(110) + msg(80) + pubkey + signature`.
  - secp256k1: 110 + 80 + 33 + 64 = **287 B**
  - ML-DSA-65: 110 + 80 + 1952 + 3309 = **5451 B**
- Block overhead reserved per CometBFT `MaxDataBytes` formula: `MaxOverheadForBlock + MaxHeaderBytes + MaxCommitBytes(100 validators)` = 11 + 626 + (94 + 100·109) = **11 631 B** header/commit budget.

## Results

| Block config | max_bytes | Scheme     | tx_size | max_tx_per_block | avg_tx_size | block_utilization | wasted_bytes |
|---|---:|---|---:|---:|---:|---:|---:|
| default     |  4 194 304 | secp256k1 |   287 B | 14 573 |   287 B | 99.72 % | 11 853 B |
| default     |  4 194 304 | ML-DSA-65 | 5 451 B |    767 | 5 451 B | 99.68 % | 13 387 B |
| 2× default  |  8 388 608 | secp256k1 |   287 B | 29 188 |   287 B | 99.86 % | 11 652 B |
| 2× default  |  8 388 608 | ML-DSA-65 | 5 451 B |  1 536 | 5 451 B | 99.81 % | 15 872 B |
| 4× default  | 16 777 216 | secp256k1 |   287 B | 58 416 |   287 B | 99.93 % | 11 824 B |
| 4× default  | 16 777 216 | ML-DSA-65 | 5 451 B |  3 075 | 5 451 B | 99.91 % | 15 391 B |

## Takeaways

- At a 4 MiB block, secp256k1 packs **14 573 tx** per block vs ML-DSA-65's **767** — a **19.0× capacity cut** when switching to the post-quantum scheme.
- The ratio is set by the signature and public key: per-tx bytes grow 287 → 5 451 (**18.99×**), and block-level overhead (~11.6 KB) is negligible at these sizes, so doubling or quadrupling the block limit keeps the same 19× ratio.
- Wasted bytes per block are always < 0.32 % of the limit — the granularity loss from fixed-size txs is minor, so throughput is essentially determined by per-tx wire size.
- To match secp256k1 throughput under ML-DSA-65, block `max_bytes` would need to grow by ≈ 19× (to ~76 MiB from 4 MiB, or ~399 MiB from CometBFT's 21 MiB default). That pressure spills into gossip and state-sync bandwidth.
- This is plain `MsgSend` traffic, where every transaction carries its own signature. In the migration experiment (`experiments/migration_cost/`), one signature covers a whole batch of transfers, so the post-quantum cost there is a fixed ~5.2 KB per transaction rather than per transfer.
