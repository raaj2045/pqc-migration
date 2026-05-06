# Block packing — secp256k1 vs ML-DSA-44

Packs synthetic 1-in / 1-out `MsgSend` transactions into a Cosmos/CometBFT block until the next tx would overflow, then reports capacity and waste.

## Configuration

- Block max-bytes read from `~/.simapp/config/genesis.json` → **4 194 304 B (4 MiB)**. CometBFT's own genesis default is 22 020 096 B (21 MiB); this chain is configured tighter.
- Per-tx size (MsgSend, 1-in 1-out): `envelope(110) + msg(80) + pubkey + signature`.
  - secp256k1: 110 + 80 + 33 + 64 = **287 B**
  - ML-DSA-44: 110 + 80 + 1312 + 2420 = **3922 B**
- Block overhead reserved per CometBFT `MaxDataBytes` formula: `MaxOverheadForBlock + MaxHeaderBytes + MaxCommitBytes(100 validators)` = 11 + 626 + (94 + 100·109) = **11 631 B** header/commit budget.

## Results

| Block config | max_bytes | Scheme     | tx_size | max_tx_per_block | avg_tx_size | block_utilization | wasted_bytes |
|---|---:|---|---:|---:|---:|---:|---:|
| default     |  4 194 304 | secp256k1 |   287 B | 14 573 |   287 B | 99.72 % | 11 853 B |
| default     |  4 194 304 | ML-DSA-44 | 3 922 B |  1 066 | 3 922 B | 99.68 % | 13 452 B |
| 2× default  |  8 388 608 | secp256k1 |   287 B | 29 188 |   287 B | 99.86 % | 11 652 B |
| 2× default  |  8 388 608 | ML-DSA-44 | 3 922 B |  2 135 | 3 922 B | 99.82 % | 15 138 B |
| 4× default  | 16 777 216 | secp256k1 |   287 B | 58 416 |   287 B | 99.93 % | 11 824 B |
| 4× default  | 16 777 216 | ML-DSA-44 | 3 922 B |  4 274 | 3 922 B | 99.91 % | 14 588 B |

## Takeaways

- At the chain's configured 4 MiB block, secp256k1 packs **14 573 tx** per block vs ML-DSA-44's **1 066** — a **13.67× capacity cut** when switching to the PQ scheme.
- The ratio is dominated by the signature/pubkey payload: per-tx bytes grow 287 → 3 922 (**13.67×**), and block-level overhead (~11.6 KB) is negligible at these sizes, so doubling or quadrupling the block limit preserves the same 13.67× ratio.
- Wasted bytes per block are always < 0.35 % of the limit — the granularity loss from fixed-size txs is minor, so throughput is essentially determined by per-tx wire size.
- To match secp256k1 throughput under ML-DSA-44, block `max_bytes` would need to grow by ≈ 13.67× (to ~57 MiB at the current setting, or ~286 MiB relative to CometBFT's 21 MiB default). That pressure spills into gossip and state-sync bandwidth.
