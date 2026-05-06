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

Requires **three terminals** and a built `simd` binary at
`../cosmos/build/simd`.

```bash
# Terminal 1 — Cosmos testnet (ML-DSA-44, 4 validators)
cd ../cosmos
make testnet-up N=4 KEY_TYPE=mldsa44
make testnet-health N=4

# Terminal 2 — Ethereum (Hardhat local node) + contract deployment
cd ../ethereum
npx hardhat node        # leave running

# Terminal 2b (another window) — deploy
npx hardhat ignition deploy ignition/modules/LockAndMint.js \
  --network localhost --no-prompt

# Terminal 3 — relayer, pointed at node0 of the testnet
cd ../ethereum
cp .env.example .env     # edit if you need custom paths
COSMOS_HOME=../cosmos/docker/testnet/testnet-data/node0/simd \
COSMOS_NODE=tcp://127.0.0.1:26657 \
COSMOS_CHAIN_ID=testnet \
COSMOS_FROM_KEY=node0 \
COSMOS_CLI_PATH=../cosmos/build/simd \
node scripts/relayer.js

# Terminal 2b — drive a lock() transaction
# The loadgen is the easiest way to fire a single lock:
node tools/loadgen/loadgen.js --rate 1 --duration 2 --accounts 1
```

You should see the relayer log `event_observed` → `mint_ok` within a few
seconds. Relayer metrics are exposed at `http://127.0.0.1:9464/metrics`.

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
