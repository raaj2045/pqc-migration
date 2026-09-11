# Block packing — secp256k1 vs ML-DSA-65

Packs synthetic 1-in / 1-out `MsgSend` transactions into a Cosmos/CometBFT block until the next tx would overflow, then reports capacity and waste.

## Configuration

- Block max-bytes set with `--max-bytes 4194304` → **4 194 304 B (4 MiB)**, the same limits as the earlier ML-DSA-44 figure so the two can be compared. CometBFT's own genesis default is 22 020 096 B (21 MiB).
- Per-tx size (MsgSend, 1-in 1-out): `envelope(110) + msg(112) + pubkey + signature`. The 112-byte `MsgSend` is measured by encoding one with the SDK v0.55 type.
  - secp256k1: 110 + 112 + 33 + 64 = **319 B**
  - ML-DSA-65: 110 + 112 + 1952 + 3309 = **5483 B**
- Block overhead reserved per CometBFT `MaxDataBytes` formula: `MaxOverheadForBlock + MaxHeaderBytes + MaxCommitBytes(100 validators)` = 11 + 626 + (94 + 100·109) = **11 631 B** header/commit budget.

## Results

| Block config | max_bytes | Scheme     | tx_size | max_tx_per_block | avg_tx_size | block_utilization | wasted_bytes |
|---|---:|---|---:|---:|---:|---:|---:|
| default     |  4 194 304 | secp256k1 |   319 B | 13 111 |   319 B | 99.72 % | 11 895 B |
| default     |  4 194 304 | ML-DSA-65 | 5 483 B |    762 | 5 483 B | 99.61 % | 16 258 B |
| 2× default  |  8 388 608 | secp256k1 |   319 B | 26 260 |   319 B | 99.86 % | 11 668 B |
| 2× default  |  8 388 608 | ML-DSA-65 | 5 483 B |  1 527 | 5 483 B | 99.81 % | 16 067 B |
| 4× default  | 16 777 216 | secp256k1 |   319 B | 52 556 |   319 B | 99.93 % | 11 852 B |
| 4× default  | 16 777 216 | ML-DSA-65 | 5 483 B |  3 057 | 5 483 B | 99.91 % | 15 685 B |

## Takeaways

- At a 4 MiB block, secp256k1 packs **13 111 tx** per block vs ML-DSA-65's **762** — a **17.2× capacity cut** when switching to the post-quantum scheme.
- The ratio is set by the signature and public key: per-tx bytes grow 319 → 5 483 (**17.19×**), and block-level overhead (~11.6 KB) is negligible at these sizes, so doubling or quadrupling the block limit keeps the same 17.2× ratio.
- Wasted bytes per block are always < 0.39 % of the limit — the granularity loss from fixed-size txs is minor, so throughput is essentially determined by per-tx wire size.
- To match secp256k1 throughput under ML-DSA-65, block `max_bytes` would need to grow by ≈ 17.2× (to ~69 MiB from 4 MiB, or ~361 MiB from CometBFT's 21 MiB default). That pressure spills into gossip and state-sync bandwidth.
- This is plain `MsgSend` traffic, where every transaction carries its own signature. In the migration experiment (`experiments/migration_cost/`), one signature covers a whole batch of transfers, so the post-quantum cost there is a fixed ~5.2 KB per transaction rather than per transfer.
