# ethereum

Ethereum-side of a Lock-and-Mint bridge to an
[ML-DSA-44-enabled Cosmos chain](../cosmos/README.md). Users lock
ERC-like balances in a Solidity contract on Ethereum; a Node.js relayer
observes the `TokensLocked` event and mints the equivalent on Cosmos. This
repository contains the contract, the relayer, and the measurement tools
(loadgen + analyser) used for the paper's bridge experiments.

## What's in the box

| Path                                  | Purpose                                                                 |
|---------------------------------------|-------------------------------------------------------------------------|
| `contracts/LockAndMint.sol`           | Solidity contract (mintable balances + `lock()` emitting `TokensLocked`) |
| `ignition/modules/LockAndMint.js`     | Hardhat Ignition deployment module                                      |
| `scripts/relayer.js`                  | Long-running relayer: watches Ethereum, mints on Cosmos                 |
| `scripts/relayer/queue.js`            | SQLite-backed durable queue (WAL journal, audit-log-shaped table)       |
| `scripts/relayer/worker_pool.js`      | Cooperative worker pool; at-most-once claim via `UPDATE…RETURNING`      |
| `scripts/relayer/metrics.js`          | prom-client registry + HTTP server (6 relayer metrics + `/healthz`)     |
| `test/relayer_queue.test.js`          | Durability, no-double-mint, metrics exposure tests                      |
| `tools/loadgen/loadgen.js`            | Paces `lock()` submissions at a target tx/s across pre-funded wallets   |
| `tools/loadgen/analyze.py`            | Plots submission rate + mining-latency CDF from the loadgen JSONL       |

## Prerequisites

- **Node.js** ≥ 20 (matches CI; 18 may work)
- **Python** 3.9+ with `matplotlib` and `numpy` (for `analyze.py`)
- A running Cosmos chain built from the companion repo
  `../cosmos/` (see its `README.md` for build steps)

## Build + test

Before running, copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
# edit .env as needed
npm ci
npx hardhat compile
npm test                  # runs the contract + relayer-queue tests
```

CI (`.github/workflows/ci.yml`) does the same on every push.

## Minimal end-to-end demo (lock on Ethereum → mint on Cosmos)

The full, verified bring-up sequence — Cosmos testnet, anvil/Hardhat
Ethereum node, contract deploy, and relayer — is documented in
[`../REPRODUCE.md`](../REPRODUCE.md) under "Path B". The Cosmos testnet
is **not** started with a bare `make testnet-up`: `init_testnet.sh`
requires pre-signed sender address files, so it is driven through the
presigner → `emit-addresses` → `init_testnet.sh` flow described there.
Once the testnet, deployed contract, and relayer are up, firing a
`lock()` (e.g. via `tools/loadgen/loadgen.js`) produces a relayer
`event_observed` → `mint_ok` within a few seconds; relayer metrics are
at `http://127.0.0.1:9464/metrics`.

### Relayer ↔ bridge-authority relationship

The relayer signs its Cosmos `MsgMint` with the key named by
`COSMOS_FROM_KEY` (node0 in the demo). On the Cosmos side, the
`x/lockandmint` module only accepts a mint whose authority equals the
module's gov-settable `bridge_authority` parameter. `init_testnet.sh`
sets `bridge_authority` to node0's address precisely so the relayer's
signing key matches; on any other chain the relayer key and
`bridge_authority` must be kept in sync (rotate the latter via
`simd tx lockandmint update-params` through a gov proposal).

Each mint also carries an `event_id` — the relayer uses the lock
event's `txHash:logIndex` (its queue correlation id). The chain records
processed `event_id`s and rejects duplicates, so a replayed or
re-observed lock event cannot mint twice.

## Reproducing the paper figures

See `REPRODUCE.md` for the figure-by-figure map. The bridge-specific
figures use `tools/loadgen/analyze.py` on JSONL files captured while the
validator-scaling experiment was running.

## Security notes

- The default owner key used by Hardhat and `tools/loadgen/loadgen.js`
  (`0xac09…ff80`) is the **public, documented Hardhat account[0] private
  key**. It is safe to use on a local dev node; it must never be used with
  a real RPC. `loadgen.js` says so in its banner comment.
- `.env` is `.gitignore`d; `.env.example` is the committed template.
- The SQLite queue file (`data/relayer.sqlite` by default) keeps a full
  audit log of every lock event — rows are marked `completed`/`failed`
  instead of deleted.

## License

Apache-2.0. See `LICENSE`.

## Citing

See `CITATION.cff`.
