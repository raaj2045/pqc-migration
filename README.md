# pqc-migration

Research artifact for **"Framework for Migrating Legacy Blockchain
Accounts to Post-Quantum Systems"** — Mishra, Braeken, Liyanage, Kalla,
2026.

Paper: [link to be added]

The repository contains a Cosmos SDK fork that adds **ML-DSA-44**
(NIST FIPS 204, formerly CRYSTALS-Dilithium) as a first-class signing
algorithm, an Ethereum-side Lock-and-Mint bridge, and the benchmark
and experiment code used to produce every figure in the paper.


## What this repo contains

| Path                       | Purpose                                                                 |
|----------------------------|-------------------------------------------------------------------------|
| `cosmos/`                  | Cosmos SDK v0.50 fork with `crypto/keys/mldsa` package + tx-signing path |
| `ethereum/`                | Hardhat project: `LockAndMint.sol` + Node.js relayer + bridge tooling   |
| `benchmarks/`              | Three local benchmarks (crypto micro, storage growth, block packing) — committed PDFs + caption.txt files |
| `experiments/`             | Multi-validator integration experiments. `validator_scaling_v2/` is the paper's headline experiment with 30 committed result JSONs |
| `tools/`                   | Paper-specific Go helper tools — pre-signer, RPC loadgen, storage simulator, block-packer |
| `REPRODUCE.md`             | Per-figure reproduction recipes — start here if you want to verify the paper |

## Quick start

Build the Cosmos chain, build the Ethereum project, and run one
benchmark — about five minutes end-to-end on a recent laptop.

You'll need Go 1.23.5, Node ≥ 20, GNU Make, and Python 3.9+ with
`matplotlib` and `numpy` (`pip install matplotlib numpy`). Docker is
required only for the full validator-scaling reproduction (see
[`REPRODUCE.md`](REPRODUCE.md)).

```bash
# 1. Cosmos fork
cd cosmos && make build && cd ..

# 2. Ethereum side
cd ethereum && cp .env.example .env && npm ci && npx hardhat compile && cd ..

# 3. One benchmark
cd benchmarks/crypto_micro && go test -bench=. -benchtime=1x -run=^$
```

Each step runs to completion independently; you do not need a network,
Docker, or the other arms running.

## Reproducing paper results

Every figure in the paper is regenerable from raw data committed in
this repo. The map of figure ↔ command is in
[`REPRODUCE.md`](REPRODUCE.md). The fastest path (figures from
existing data, no testnet) finishes in under a minute. The full
end-to-end sweep (rebuild presigned pools, run the 30-cell validator
sweep) is documented for completeness and takes ~5 hours on a
12-core host.

## Repository structure

```
pqc-migration/
├── README.md                              ← you are here
├── REPRODUCE.md                           ← per-figure recipes
├── LICENSE                                ← Apache 2.0
├── CITATION.cff
├── CONTRIBUTING.md
├── SECURITY.md
├── HISTORY.md                             ← upstream attribution
├── .gitignore
├── .github/workflows/ci.yml
│
├── cosmos/                                ← Cosmos SDK fork (~2 MB src + upstream tree)
│   ├── README.md                          ← fork-specific README
│   ├── README.upstream.md                 ← original Cosmos SDK README
│   ├── crypto/keys/mldsa/                 ← the fork's main contribution
│   ├── docker/testnet/                    ← multi-validator harness used by experiments
│   └── (full Cosmos SDK source tree)
│
├── ethereum/                              ← Hardhat project
│   ├── README.md
│   ├── contracts/LockAndMint.sol
│   ├── scripts/relayer.js                 ← Cosmos-bound relayer + worker pool
│   └── tools/loadgen/                     ← lock() loadgen + analyser
│
├── benchmarks/
│   ├── crypto_micro/                      ← keygen / sign / verify / batch / concurrency
│   ├── storage_sim/                       ← state growth at 100k / 1M / 10M txs
│   └── block_packing/                     ← max txs/block at 4/8/16 MiB block size
│
├── experiments/
│   ├── validator_scaling_v2/              ← 30-cell sweep (3 N × 5 rates × 2 schemes)
│   └── cold_sync/                         ← scaffolded, not run (see its README)
│
└── tools/
    ├── presigner/                         ← Go: builds pre-signed tx pool
    ├── loadgen/                           ← Go: pure-stdlib RPC loadgen
    ├── storage_sim/                       ← Go: state-growth simulator
    └── block_packing/                     ← Go: block-packing analyser
```

## Hardware used

Reference platform for every measurement reported in the paper:

- **CPU:** AMD Ryzen 5 7600X (6 physical cores, 12 threads)
- **OS:** Linux 5.15 under WSL2
- **Go:** 1.23.5
- **Node.js:** ≥ 20
- **GNU Make:** any recent version
- **Docker:** any recent version with `docker compose`
- **Python:** 3.9+ with `matplotlib` and `numpy` (`pip install matplotlib numpy`)

The crypto micro-benchmarks and the simulators are CPU-bound and run
on any host with the listed software. The validator-scaling experiment
spins up 4–16 Cosmos validators in Docker containers, each capped at 1
CPU; budget at least 16 cores and 16 GB of memory for it to behave the
way the paper reports.

## Citation

See [`CITATION.cff`](CITATION.cff). Once the paper has a venue and
DOI those will be filled in; the file currently carries TBD-on-
publication placeholders.

## License

Apache 2.0. See [`LICENSE`](LICENSE).

The `cosmos/` directory is a fork of the upstream Cosmos SDK
(Apache 2.0); see [`HISTORY.md`](HISTORY.md) and
[`cosmos/README.upstream.md`](cosmos/README.upstream.md) for
attribution. The `ethereum/` directory uses Hardhat boilerplate (MIT)
on top of which the Lock-and-Mint contract and relayer are original to
this work.
