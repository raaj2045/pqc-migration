# pqc-migration

[![ci](https://github.com/raaj2045/pqc-migration/actions/workflows/ci.yml/badge.svg)](https://github.com/raaj2045/pqc-migration/actions/workflows/ci.yml)

A framework for migrating legacy blockchain accounts to post-quantum
signatures, plus the artefacts for the associated paper.

The chain is built on **stock Cosmos SDK v0.55 / CometBFT v0.40**, which
ships native ML-DSA-65 account keys — no SDK fork required. It carries
IBC v2 (Eureka), bridged to Ethereum through the `cw-ics08-wasm-eth`
light client running inside 08-wasm, so cross-chain transfers are
verified against real Ethereum consensus rather than a trusted relayer
authority. All asset transfer goes through ICS-20 over those verified
light clients; there is no custom bridge module.

```bash
go build ./...
go test ./...
```

> The previous implementation — a Cosmos SDK fork carrying ML-DSA-44 —
> remains available at the **`v1-mldsa44-fork`** tag.

## Layout

| Path | Contents |
|---|---|
| `app/` | Application wiring, including the `emptyValueDB` store fix |
| `cmd/pqchaind/` | Node binary |
| `devnet/` | Devnet tooling: relayer, redemption cycle, light-client helpers |
| `docs/` | Operational notes, and the formal-verification case study |
| `benchmarks/`, `experiments/` | Raw data and plot scripts for the paper figures |
| `tools/` | Simulators and load-generation tooling |

## Documentation

- [REPRODUCE.md](REPRODUCE.md) — building the chain, running the bridge
  cycle, and regenerating the paper figures.
- [docs/building.md](docs/building.md) — toolchain and the `libstdc++` /
  `CGO_LDFLAGS` requirement introduced by the BLS verifier.
- [docs/ethereum-light-client.md](docs/ethereum-light-client.md) — the
  execution-vs-beacon `state_root` distinction, the caller-supplied
  `pubkeys_hash`, and the gov-gated `MsgStoreCode`.
- [docs/wasm-data-dir.md](docs/wasm-data-dir.md) — why the wasmvm data
  directory must be deterministic.
- [devnet/README.md](devnet/README.md) — the transfer and redemption
  cycle against a local Ethereum devnet.
- [docs/formal-verification-case-study/](docs/formal-verification-case-study/)
  — TLA+/TLC analysis of the retired `x/lockandmint` bridge module, kept as
  a worked example of what formal verification catches.
- [docs/live-path-verification/](docs/live-path-verification/) — TLA+ model of
  the ICS-20 / light-client path the chain actually runs, checked with both
  TLC and Apalache.

`devnet/` is JavaScript and Python only. It is not a Go package and is
invisible to `go build ./...`.

## Status

`x/lockandmint`, the original custom bridge module, has been retired; all
asset transfer now goes through ICS-20 over verified light clients. Its
formal-verification results are kept as a
[case study](docs/formal-verification-case-study/).

`benchmarks/crypto_micro` and `tools/presigner` measure **ML-DSA-44**
from the superseded fork, not the ML-DSA-65 implementation this
repository now builds. They are retained for historical reference only,
are excluded from CI because the fork they resolve against is no longer
in the tree, and should not be ported to ML-DSA-65 without first
deciding how to present re-measured figures — see
[Known gaps](REPRODUCE.md#known-gaps--ml-dsa-44-modules-retained-for-historical-reference).

Everything else in the tree builds and is covered by CI.

## Citing

See [CITATION.cff](CITATION.cff). Licensed under Apache 2.0 — see
[LICENSE](LICENSE).
