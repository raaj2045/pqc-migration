# tools

Paper-specific Go tools. Each is a small standalone module with its
own `go.mod` so they can be built independently of the cosmos fork.

| Sub-directory     | What it does                                                                                                                                                      | Used by                                            |
|-------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------|
| `presigner/`      | Builds a pre-signed transaction pool: `emit-addresses` lists deterministic loadgen-sender addresses; `sign` serialises N pre-signed `MsgSend` txs per sender. Imports the cosmos fork via local `replace` directives. | `experiments/validator_scaling_v2/run_sweep.py` (full reproduction only) |
| `loadgen/`        | Pure-stdlib Go RPC loadgen: replays a pre-signed pool against a CometBFT JSON-RPC endpoint, paces submissions at a target rate, and records per-tx submit/commit timings. Block-sampler observes `/block?height=` independently, so commit time is wall-clock when our poller first observes the inclusion block — not the validator-derived header time. | Same as above |
| `storage_sim/`    | Simulator for on-chain account-state growth at 100 k / 1 M / 10 M txs. Computes per-tx wire size and per-account state size for both schemes. Pure-stdlib. | `benchmarks/storage_sim/plot.py` |
| `block_packing/`  | Computes the maximum number of transfers per block for both schemes at default / 2× / 4× block-size limits, using the CometBFT MaxDataBytes overhead model. Pure-stdlib. | `benchmarks/block_packing/plot.py` |

## Building

Each tool builds independently:

```bash
cd tools/presigner   && go build -o ./presigner_bin .   && cd -
cd tools/loadgen     && go build -o ./loadgen_bin .     && cd -
cd tools/storage_sim   && go build -o ./storage_sim_bin .   && cd -
cd tools/block_packing && go build -o ./block_packing_bin . && cd -
```

`presigner` resolves cosmos-sdk via `replace` directives in its
`go.mod` pointing back to `../../cosmos/`. The other three are pure
stdlib and have no cross-module dependencies.

## Why a separate `tools/` directory

These tools sit outside `cosmos/` so they stay paper-specific and don't
clutter the upstream Cosmos SDK directory layout. `cosmos/tools/` retains
upstream tools (`cosmovisor`, `confix`, `benchmark`); only the four
listed above are paper additions.
