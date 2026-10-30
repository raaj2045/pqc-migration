# Evaluation notes

Sections VI-B and VI-C of the paper summarise this document. Raw data and scripts are in [`experiments/migration_cost/`](../experiments/migration_cost), [`experiments/migration_throughput/`](../experiments/migration_throughput) and [`benchmarks/`](../benchmarks).

## Storage estimates: method

Storage figures are analytical estimates, not database measurements.

- **Transaction history** counts the encoded wire size of each `MsgRecvPacket` transaction: a 110-byte envelope, a 4,513-byte message (the median of 110 measured deliveries), and the signer's key and signature.
- **Account state** counts a flat 100 bytes per account, covering the store key and account fields, plus the public key once it is stored.

Neither includes IAVL inner nodes, version history, LevelDB framing or compression, indexes, or ICS-20 receipts, commitments and voucher balances. The figures therefore compare the two schemes rather than predict disk usage. Results for 10 million migrations:

| | secp256k1 | ML-DSA-65 |
|---|---|---|
| Account state at migration | 953.7 MiB | 953.7 MiB |
| Account state, all accounts signed once | 1.24 GiB | 19.11 GiB |
| Transaction history | 43.96 GiB | 92.05 GiB |

A migrated recipient is only an address, with no public key stored until its owner first signs, so account state is identical at migration. Once every migrated account has signed, account state grows 15.43×, an upper bound because it assumes every account eventually transacts. Transaction history grows only 2.09×, because each `MsgRecvPacket` is dominated by its Merkle-Patricia proof of roughly 4,513 B, which is the same for both schemes.

## Block capacity

A standard 1-input/1-output `MsgSend` grows from 319 B to 5,483 B with ML-DSA-65 (1,952 B public key and 3,309 B signature, against 33 B and 64 B for secp256k1). A 4 MiB block therefore holds at most 13,111 secp256k1 transactions but only 762 ML-DSA-65 transactions, a 17.2× reduction. Doubling or quadrupling the block size keeps the same ratio, because per-transaction size dominates the constant block-level overhead (header and commit signatures, roughly 11.6 KB).

Matching secp256k1's throughput at the same block interval would need about 69 MiB blocks. This is an arithmetic extrapolation, not evidence that such blocks are viable: gossip bandwidth, mempool dynamics and state-sync snapshots would need separate analysis. See [`benchmarks/block_packing/`](../benchmarks/block_packing).

## Per-step migration latency

[`fig_time_by_step.pdf`](../experiments/migration_cost/fig_time_by_step.pdf) shows end-to-end migration latency step by step, for both key types. The median voucher availability time was 557.6 s, of which 540.4 s (96.9 %) was the wait for Ethereum finality on 6-second slots. Submission, light-client update and delivery took 15.9 s together. Proof generation applies only to acknowledgement completion. The per-migration data is in [`experiments/migration_cost/results/latency_by_step.csv`](../experiments/migration_cost/results/latency_by_step.csv).
