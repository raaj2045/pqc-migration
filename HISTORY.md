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

## Moving the measurements to ML-DSA-65

Every figure in the repository now describes the current chain's ML-DSA-65.
The ML-DSA-44 figures published from the fork remain at the
**`v1-mldsa44-fork`** tag.

| Module | What changed |
|---|---|
| `benchmarks/crypto_micro` (Figures 1–6) | Ported to the SDK's own `crypto/keys/mldsa65` on stock SDK v0.55, with the `replace` onto the removed fork dropped. Builds and is in CI |
| `tools/storage_sim` (Figure 7) | ML-DSA-65 key and signature sizes; transaction mix rebuilt around the Ethereum → Cosmos migration message |
| `tools/block_packing` (Figure 8) | ML-DSA-65 key and signature sizes |

**The new crypto_micro figures are not comparable to the ML-DSA-44 ones.** The
port changes three things at once, so neither set is an update of the other:

- **The parameter set.** ML-DSA-65 is a higher security level than ML-DSA-44,
  with larger keys and signatures and more work per operation.
- **The code path.** The benchmark now goes through the SDK key type the chain
  itself uses, which re-parses the packed key on every signature and every
  verification. That is the cost a transaction actually pays.
- **The machine.** Same CPU (AMD Ryzen 5 7600X), but the ML-DSA-44 run had 12
  logical CPUs and this one has 6, which changes the shape of the concurrency
  figures.

The comparison that holds is the one inside each run: both key types were
measured together, on the same machine, in the same session.

One module still carries ML-DSA-44: **`tools/presigner`**, which imports the
fork's `crypto/keys/mldsa` through a `replace` onto the removed `cosmos/`
directory. It does not build and is excluded from CI. Nothing current uses it;
it built transaction pools for an experiment that has since been removed.

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
