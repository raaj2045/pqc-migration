# Validator-scaling experiment

This is the sweep that answers Review 1: how does validator-set size interact
with ML-DSA-44's larger signatures? For each scheme ∈ {secp256k1, mldsa44} and
each N ∈ {4, 7, 16, 32}, we run the full bridge stack (Cosmos testnet +
Hardhat + relayer + loadgen) at five target lock() rates and collect RPC,
Prometheus, and relayer telemetry.

Total wall time: ~6 h for the full sweep (40 runs × 5 min + setup overhead).

## Layout

```
experiments/validator_scaling/
  run_sweep.py     orchestrator: brings up stack, runs loadgen, tears down
  collect.py       background sampler (Tendermint RPC + Prometheus)
  aggregate.py     reads results/, emits 4 figures + summary.md
  results/         per-run JSON: N<n>_rate<r>_<scheme>.json  (resume key)
  logs/            hardhat + relayer stdout per (scheme, N)
  loadgen_runs/    raw JSONL + per-run loadgen plots
  state/           relayer SQLite DBs (one per scheme/N)
```

## Prerequisites

On the host (not inside a container):

```bash
# Host binary the relayer drives
cd ../../cosmos-latest && make build     # produces build/simd

# Node deps for Hardhat (used for unit tests + Ignition deploy) and for
# the relayer + loadgen
cd ../ethereum-lockandmint && npm install

# Anvil (Foundry) — used by run_sweep.py as the loadgen-side Ethereum node
# instead of `npx hardhat node`. See "Ethereum dev node: Anvil, not Hardhat".
curl -L https://foundry.paradigm.xyz | bash && foundryup

# Python deps for collector + aggregator
python3 -m pip install matplotlib numpy

# Docker / docker compose / jq / curl on PATH (testnet harness uses them)
```

## Ethereum dev node: Anvil, not Hardhat

`run_sweep.py` launches **Anvil** (from Foundry) as the loadgen-facing
Ethereum node, not `npx hardhat node`. Hardhat's JSON-RPC server is
single-threaded and saturates around 150 tx/s — at our rate=500 target
the sender queue outruns the node's ability to mine, the loadgen drain
exceeds its timeout, and the experiment records a stub instead of a
clean result. Anvil serves the same JSON-RPC surface with a
multi-threaded server and comfortably sustains 500+ tx/s on this host.

Anvil is invoked with:

```
anvil --host 0.0.0.0 --port 8545 \
      --chain-id 31337 --accounts 20 --balance 10000
```

`--block-time` is deliberately omitted so Anvil mines each transaction
instantly (its default). The loadgen's pre-fund step does `await tx.wait()`
once per wallet for gas and again once per wallet for token balance;
under a 1-second block time this would serialize to 200+ seconds of pure
pre-fund wall time per run, pushing 5-minute experiments past the
subprocess timeout before any real load lands. Instant mining is what
loadgen was designed against when Hardhat was the node, and it keeps the
Ethereum side from becoming the throughput ceiling.

Both Anvil and Hardhat derive their dev accounts from the same mnemonic
(`test test test test test test test test test test test junk`), so the
well-known account[0] private key `0xac09…ff80` that `loadgen.js`
hardcodes is valid on either. Hardhat remains in the loop for unit tests
(`npx hardhat test`) and for the Ignition deployment step
(`npx hardhat ignition deploy … --network localhost`) since both rely on
Hardhat's JS-side config/plugins rather than on the RPC performance.

If Anvil is not on PATH, `run_sweep.py` falls back to `npx hardhat node`
automatically and logs a warning — runs at rate >= 300 will likely
regress to the old stub pattern in that mode.

Layout assumption: the two repos live side-by-side under one parent:

```
<parent>/
  cosmos-latest/
  ethereum-lockandmint/
  experiments/validator_scaling/   <-- this directory
```

## Running

```bash
# Full sweep (both schemes, all N, all rates) — ~6 hours
python3 run_sweep.py

# Just the ML-DSA-44 half
python3 run_sweep.py --schemes mldsa44

# Smoke test: one scheme, one N, one short run
python3 run_sweep.py --schemes secp256k1 --validators 4 --rates 10 --duration 60

# Aggregate whatever's in results/ so far
python3 aggregate.py
```

## Resuming after a crash

Each per-run result lands at `results/N<n>_rate<r>_<scheme>.json` **before**
the next configuration begins. `run_sweep.py` checks for existing files and
skips them. To re-run a specific point, delete its file:

```bash
rm results/N16_rate100_mldsa44.json
python3 run_sweep.py --schemes mldsa44 --validators 16 --rates 100
```

If a run raised mid-flight, the script still writes an error stub to
`results/` so the slot isn't silently lost — delete that stub to retry.

## What gets collected per run

Every `results/N<n>_rate<r>_<scheme>.json` contains:

- `loadgen_summary` — from `tools/loadgen/analyze.py`: achieved submit/mined
  rate, latency p50/p95/p99.
- `telemetry.blocks` — one record per block seen during the run:
  `{height, time_iso, num_txs, total_tx_bytes, proposer_address}`.
- `telemetry.gossip_samples` — periodic per-validator snapshots of
  `cometbft_p2p_peer_send_bytes_total` and `_receive_bytes_total` (summed
  across peers), peer count, consensus height.
- `telemetry.relayer_samples` — periodic scrapes of the six relayer
  Prometheus metrics, histograms included.

`aggregate.py` derives per-run scalars from these and emits the four figures
the paper cites:

| File                                    | X-axis   | Y-axis                        |
|-----------------------------------------|----------|-------------------------------|
| `fig_tps_vs_validators.pdf`             | N        | achieved tx/s                 |
| `fig_block_interval_vs_validators.pdf`  | N        | mean block interval (ms)      |
| `fig_gossip_bw_vs_validators.pdf`       | N        | bytes/s per validator (log)   |
| `fig_e2e_latency_p99_vs_validators.pdf` | N        | bridge e2e latency p99 (s)    |

Each figure has two panels (secp256k1 | mldsa44) with one line per target rate.

## Gotchas

- **Port 8545 already in use.** If your host already runs Hardhat or Anvil,
  stop it — the orchestrator binds `:8545` exclusively (Anvil on 0.0.0.0,
  Hardhat on 127.0.0.1).
- **Testnet down doesn't tear down, complains about volumes.** The Makefile
  uses `docker compose down -v`. If you see leftover containers, run
  `make -C ../../cosmos-latest/docker/testnet down` manually.
- **Prometheus metrics missing.** The CometBFT fork may label its p2p metrics
  with the `tendermint_` prefix instead of `cometbft_`; `collect.py` accepts
  both. If neither shows up, gossip plots will be blank — the block and
  loadgen data are independent and still usable.
- **Relayer key 'node0' not in keyring.** The testnet's `--keyring-backend
  test` stores `node<i>` keys under `testnet-data/node<i>/simd/keyring-test`;
  `run_sweep.py` wires `COSMOS_HOME` to `testnet-data/node0/simd` so the
  relayer picks up the right keyring. If you change where the home mounts,
  update `relayer_env()`.
