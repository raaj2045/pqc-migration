# Experimental setup

Section V of the paper summarises this table.

| | |
|---|---|
| **Host** | |
| Platform | Ubuntu 26.04.1 LTS on WSL2 |
| Kernel | 6.18.33.2-microsoft-standard-WSL2 |
| Architecture | x86_64 |
| Processor | AMD Ryzen 5 7600X (6 physical, 12 logical cores) |
| Memory | 28 GB available to measurements |
| Deployment | Single host; all components co-located |
| **Cosmos chain** | |
| Mempool | Flood, 5,000 txs (default) |
| Database backend | goleveldb (default) |
| Cosmos SDK | v0.55.0 (stock, native ML-DSA-65) |
| CometBFT | v0.40.0 |
| ibc-go | v11 (commit `ee35255c`) |
| Block time | 5.04 s median (default `timeout_commit`) |
| `max_gas` | Unlimited (default) |
| `max_tx_bytes` | 4 MiB (default) |
| `max_body_bytes` | 16 MB (raised from the 1 MB default) |
| Validators | 1 (Ed25519 consensus key) |
| **Ethereum devnet** | |
| Execution client | geth v1.17.6-unstable |
| Consensus client | Lighthouse v8.2.2 |
| Slot / epoch | 6 s per slot, 32 slots per epoch |
| Fork | Electra from genesis |
| Validators | 128, single validator client |
| Orchestration | Kurtosis 1.15.2, ethereum-package 6.0.0 |
| **Toolchain** | |
| Go | 1.26.5 |
| Node.js | v20.20.2 |
| Python | 3.14.4 |
| Foundry (forge) | 1.0.0 |

Reproduction steps are in [`REPRODUCE.md`](../REPRODUCE.md) and [`docs/getting-started.md`](../docs/getting-started.md).
