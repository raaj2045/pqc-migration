# cosmos — ML-DSA-44 fork

This repository is a fork of [cosmos-sdk](https://github.com/cosmos/cosmos-sdk)
that adds support for the NIST-standardised post-quantum signature
**ML-DSA-44** (FIPS 204, formerly CRYSTALS-Dilithium) as a first-class
signing algorithm for **user-account transaction signing**. Validator
consensus keys remain ed25519 in this fork; extending ML-DSA-44 to the
consensus layer is future work. See `README.upstream.md` for the upstream
SDK README.

This repo pairs with
[`ethereum/`](../ethereum/) — a Lock-and-Mint bridge contract and relayer
used in the paper's end-to-end experiments.

## What this fork adds

| Area                        | Location                                       | Summary                                                   |
|-----------------------------|------------------------------------------------|-----------------------------------------------------------|
| ML-DSA-44 key type          | `crypto/keys/mldsa/`                           | Implements `cryptotypes.PrivKey`/`PubKey` for ML-DSA-44.  |
| `--key-type mldsa44`        | `x/genutil` + `simd testnet init-files`        | Generate validator keys under the new scheme.             |
| Lock-and-Mint module        | `x/lockandmint/`                               | Bridge mint target: `MsgMint` carries an `event_id` (relayer's `txHash:logIndex`) and the keeper rejects missing or already-processed ids (replay/double-mint protection). Mint is gated by a gov-settable `bridge_authority` param (rotated via `MsgUpdateParams`), distinct from the module's gov authority. |
| Crypto micro-benchmarks     | `benchmarks/crypto_micro/`                     | Keygen / sign / verify / batch / memory profiles.         |
| Storage-growth simulator    | `tools/storage_sim/`, `benchmarks/storage_sim/`| On-chain state growth at N ∈ {100k, 1M, 10M} txs.         |
| Block-packing analysis      | `tools/block_packing/`, `benchmarks/block_packing/` | Max txs/block under default / 2× / 4× `max_bytes`.   |
| Multi-validator docker testnet | `docker/testnet/`                           | `make testnet-up N={4,7,16,32} KEY_TYPE={secp256k1,mldsa44}` |
| Paper experiments           | `../experiments/`                              | Validator-scaling sweep + cold-sync replay.               |

## Prerequisites

- **Go** 1.23.5 (matches what `make build` expects; older minor versions may work)
- **Docker** + **docker compose** (for the multi-validator testnet)
- **jq** and **curl** (used by the testnet health check and experiment drivers)
- **Python** 3.9+ with `matplotlib` and `numpy` (plotting / aggregation)

## Build

```bash
make build              # produces ./build/simd (glibc cgo build, matches CI)
./build/simd version
```

## Minimal ML-DSA-44 demo

Start a single-node ML-DSA-44 chain locally and send a transaction signed
with a post-quantum validator key.

```bash
# 1. Set up a one-validator testnet
SIMD=./build/simd
$SIMD testnet init-files \
  --validator-count 1 \
  --output-dir ./.testnets \
  --chain-id demo \
  --key-type mldsa44 \
  --keyring-backend test

# 2. Start it (logs to stdout)
$SIMD start --home ./.testnets/node0/simd

# 3. In a second terminal: send a transaction and observe it committed
$SIMD tx bank send \
  $($SIMD keys show node0 -a --keyring-backend test \
      --home ./.testnets/node0/simd) \
  cosmos1abc... 100stake \
  --home ./.testnets/node0/simd --keyring-backend test \
  --chain-id demo --yes
```

The validator's private key is a 2.5 KB ML-DSA-44 key; the tx signature is
~2.4 KB; verification happens inside `VerifySignature` in
`crypto/keys/mldsa/`.

## End-to-end bridge demo

See [`../ethereum/README.md`](../ethereum/README.md) for the full
"lock on Ethereum → mint on Cosmos" walkthrough.

Bringing up the multi-validator testnet requires pre-signed sender
address files, so it is driven through the presigner →
`emit-addresses` → `init_testnet.sh` flow rather than a bare
`make testnet-up` (which would fail because `init_testnet.sh` requires
`--secp-addrs-file` / `--mldsa-addrs-file`). The exact, verified
sequence is documented in [`../REPRODUCE.md`](../REPRODUCE.md) under
"Path B — Full reproduction from scratch".

## Reproducing the paper figures

See `REPRODUCE.md` at the repo root for a figure-by-figure map of commands.
Raw benchmark data used by the paper lives under `../benchmarks/<name>/`
(e.g. `../benchmarks/crypto_micro/results.json`).

## CI

The monorepo's CI lives at the repo root in `.github/workflows/ci.yml`
(builds `simd`, runs the crypto micro-benchmarks once, and compiles the
Ethereum project on every push). Upstream Cosmos SDK workflows under
`.github/workflows/` are left intact but are not the active CI here.

## License

Apache-2.0, inherited from upstream Cosmos SDK. See `LICENSE`.

## Citing

See `CITATION.cff`.
