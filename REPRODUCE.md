# Reproducing the published results

Step-by-step regeneration of the paper's figures from the raw data committed in
this repository. No network or external services are required.

> **Looking to build or develop on the system instead?**
> Use [docs/getting-started.md](docs/getting-started.md). This page covers
> reproduction of published results specifically.

## 1. Build the chain

```
Go 1.26.5
```

```bash
go build ./...
go test ./...
```

The 08-wasm BLS verifier needs `libstdc++` at link time. On a host without
`g++`, see [Getting started](docs/getting-started.md#cgo-libstdc-is-required).

## 2. Run the bridge cycle

Both directions are exercised by the scripts in
[`devnet/`](devnet/README.md): a forward transfer (Cosmos escrow, Ethereum
voucher mint) and a reverse redemption (Ethereum voucher burn, Cosmos
unescrow).

Prerequisites, configuration, step order and timing are in
[devnet/README.md](devnet/README.md). Each direction waits on Ethereum
finality, roughly five minutes on a 6-second-slot devnet.

## 3. Regenerate the figures

The figure scripts need `matplotlib` and `numpy`:

```bash
pip install matplotlib numpy     # or: pip3 / python3 -m pip
```

All raw data is committed, so re-rendering every figure takes under a minute:

```bash
cd benchmarks/crypto_micro          && python3 plot.py      && cd ../..
cd benchmarks/block_packing         && python3 plot.py      && cd ../..
cd benchmarks/storage_sim           && python3 plot.py      && cd ../..
cd experiments/validator_scaling_v2 && python3 aggregate.py && cd ../..
```

Each command regenerates the PDFs next to their `caption.txt` neighbours.

What each tree contains: [benchmarks/](benchmarks/README.md) (micro-benchmarks
and simulator output), [experiments/](experiments/README.md) (live-chain
measurements), [tools/](tools/README.md) (the simulators and load generator).

## 4. Regenerate the raw data (optional)

`benchmarks/block_packing/results.json` and the `storage_sim` per-scheme JSONs
come from the simulators in `tools/`:

```bash
cd tools/block_packing && go run . > ../../benchmarks/block_packing/results.json && cd ../..
cd tools/storage_sim   && go run . --out ../../benchmarks/storage_sim/          && cd ../..
```

A few seconds each. Re-render with the respective `plot.py` afterwards. These
tools build standalone and are covered by CI.

## Scope

Two figure sets are **out of scope for reproduction against the current
chain**: `benchmarks/crypto_micro` and `tools/presigner` measure ML-DSA-44 from
the superseded fork, are excluded from CI, and do not build in this tree. Their
committed results are retained as published. See
[HISTORY.md](HISTORY.md#ml-dsa-44-modules-still-in-the-tree).

The adversarial light-client suite in
[`security/light-client-stress/`](security/light-client-stress/README.md)
requires a live devnet and is run by hand rather than as part of reproduction.

---

[Project README](README.md) · [Getting started](docs/getting-started.md) · [Testing](docs/testing.md)
