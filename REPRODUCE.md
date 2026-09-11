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

## 2. Run a migration

The paper's migration is **Ethereum → Cosmos**: an Ethereum-native ERC-20
(`TestERC20`) is escrowed on Ethereum and a matching `ibc/<hash>` voucher is
minted on Cosmos. One migration by hand, after bringing up the native-asset
devnet ([devnet/README.md](devnet/README.md#bringing-up-the-native-asset-devnet)):

```bash
cd devnet
node step-native-send.js 2000000              # escrow on Ethereum
node step-native-recv.js                      # prove to Cosmos, mint the voucher
node step-native-ack.js <recv-txhash>         # prove the acknowledgement back
```

The second step waits for Ethereum finality, about nine minutes on this
devnet. The third makes a real SP1 proof, about ten minutes. See
[devnet/README.md](devnet/README.md#native-asset-cycle) for what each step
prints.

To measure many migrations at once — the paper's cost and time figures — use
[`experiments/migration_cost/`](experiments/migration_cost/README.md#running-it).

The devnet also runs the opposite direction, sending Cosmos-native `stake` to
Ethereum and redeeming it back
([forward leg](devnet/README.md#forward-leg),
[redemption cycle](devnet/README.md#redemption-cycle)). That direction is not
the paper's migration result.

## 3. Regenerate the figures

The figure scripts need `matplotlib`, `numpy` and `pandas`:

```bash
pip install matplotlib numpy pandas     # or: pip3 / python3 -m pip
```

The benchmark figures are drawn from data committed in the repository and take
seconds:

```bash
cd benchmarks/crypto_micro   && python3 plot.py && cd ../..
cd benchmarks/block_packing  && python3 plot.py && cd ../..
cd benchmarks/storage_sim    && python3 plot.py && cd ../..
```

Each command regenerates the PDFs next to their `caption.txt` neighbours.

The migration figures — `fig_time_by_step.pdf`, `fig_throughput_by_workers.pdf`
and `cost_by_batch.md` in `experiments/migration_cost/` — come from:

```bash
python3 experiments/migration_cost/plot_data.py
```

It reads `experiments/migration_cost/results/`, which is **not committed**: run
the measurements in section 2 against a devnet first. It also reads the real
SP1 proving times committed under
`experiments/migration_throughput/results/real-verifier/`.

What each tree contains: [benchmarks/](benchmarks/README.md) (micro-benchmarks
and simulator output), [experiments/](experiments/README.md) (live-chain
measurements), [tools/](tools/README.md) (the simulators and load generator).

## 4. Regenerate the raw data (optional)

`benchmarks/block_packing/results.json` and the `storage_sim` per-scheme JSONs
come from the simulators in `tools/`:

```bash
cd tools/block_packing
go run . --max-bytes 4194304 --output ../../benchmarks/block_packing/results.json
cd ../storage_sim
for s in secp256k1 mldsa65; do
  go run . --scheme $s --num-tx 100000   --output ../../benchmarks/storage_sim/results_${s}_100k.json
  go run . --scheme $s --num-tx 1000000  --output ../../benchmarks/storage_sim/results_${s}_1m.json
  go run . --scheme $s --num-tx 10000000 --output ../../benchmarks/storage_sim/results_${s}_10m.json
done
cd ../..
```

Under a minute in total. Re-render with the respective `plot.py` afterwards.
These tools build standalone and are covered by CI. The block-packing limit is
pinned at 4 MiB so the figure does not depend on whichever `genesis.json` the
tool finds on the machine.

## Scope

Two modules report **ML-DSA-44 from the superseded fork**, not the current
chain's ML-DSA-65: `benchmarks/crypto_micro` and `tools/presigner`. Neither
builds in this tree, and both are excluded from CI. The crypto_micro figure
above re-renders their committed ML-DSA-44 results; it does not describe the
current chain. See
[HISTORY.md](HISTORY.md#ml-dsa-44-modules-still-in-the-tree).

The adversarial light-client suite in
[`security/light-client-stress/`](security/light-client-stress/README.md)
requires a live devnet and is run by hand rather than as part of reproduction.

---

[Project README](README.md) · [Getting started](docs/getting-started.md) · [Testing](docs/testing.md)
