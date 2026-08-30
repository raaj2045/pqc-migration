# History and attribution

The current implementation runs on **stock Cosmos SDK v0.55 / CometBFT v0.40**
with native ML-DSA-65 account keys. It replaced an earlier fork-based
implementation carrying ML-DSA-44, which remains available in full at the
**`v1-mldsa44-fork`** tag.

This page records where the code came from and what remains from the previous
approach. For the system as it stands, see
[Architecture](docs/architecture.md).

## The previous implementation

The first version forked the Cosmos SDK to add ML-DSA-44 (FIPS 204) account
keys, in a `cosmos/` directory that is no longer part of this tree. That fork
added:

- `cosmos/crypto/keys/mldsa/` implementing `cryptotypes.PrivKey` / `PubKey` for
  ML-DSA-44, wrapping [cloudflare/circl](https://github.com/cloudflare/circl);
- `--key-type mldsa44` plumbed through `simd testnet init-files` and `x/genutil`;
- a multi-validator Docker testnet harness used by the paper's experiments.

Stock SDK v0.55 subsequently shipped `crypto/keys/mldsa65` natively, making the
fork unnecessary. The fork was removed and the chain rebuilt on the stock SDK.

The upstream Cosmos SDK git history was **not** carried into this repository.
For history before the fork point, consult the
[upstream repository](https://github.com/cosmos/cosmos-sdk) (Apache-2.0,
copyright the Cosmos SDK contributors).

## ML-DSA-44 modules still in the tree

Two modules measure **ML-DSA-44 from the superseded fork**, not the ML-DSA-65
implementation this repository now builds. They are retained for historical
reference.

| Module | State |
|---|---|
| `benchmarks/crypto_micro` | `go test -bench` imports `crypto/keys/mldsa` via a `replace` onto the removed `cosmos/` fork |
| `tools/presigner` | Same import, same `replace` |

Both are **excluded from CI**: the fork they resolve against is no longer in the
tree, so neither builds here. Their source is unchanged from the fork-based
prototype.

The committed `benchmarks/crypto_micro/results.json` likewise reflects
ML-DSA-44, so those figures plot the previous implementation's numbers rather
than the current chain's.

**These are not to be ported to ML-DSA-65 as a mechanical fix.** Stock SDK v0.55
ships `crypto/keys/mldsa65` — a different package *and* a different parameter
set — so dropping the `replace` directive is not sufficient. Porting changes
what is being measured, and the resulting numbers would not be comparable to
those already published. Whether to re-measure, and how to present both sets of
figures, is tied to the paper rewrite.

## The retired bridge module

`x/lockandmint`, a custom bridge module, was retired in favour of ICS-20 over
verified light clients. No custom bridge module remains; see
[Architecture](docs/architecture.md#asset-transfer).

## Ethereum side

The Ethereum contracts were originally built on the
[Hardhat](https://github.com/NomicFoundation/hardhat) project boilerplate (MIT,
copyright Nomic Foundation), with the contracts, scripts, tests and tooling
original to this work. The current bridge uses the IBC Eureka contracts; see
[EVM deployment](devnet/deploy/README.md).

## Repository assembly

This repository was assembled from two pre-existing private remotes —
`postquantum-cosmos` (the Cosmos fork) and `ethereum-lockandmint` (the Ethereum
side). Their histories were squashed into a single fresh history under this
monorepo, which is now the authoritative source.

---

[Project README](README.md) · [Architecture](docs/architecture.md)
