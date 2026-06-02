# History and attribution

This repository was assembled from two source repositories prior to
the public release.

## `cosmos/`

`cosmos/` is a fork of the upstream
[cosmos-sdk](https://github.com/cosmos/cosmos-sdk) (Apache-2.0,
copyright the Cosmos SDK contributors). The fork base is approximately
v0.50 / v0.53 — see [`cosmos/CHANGELOG.md`](cosmos/CHANGELOG.md) for
the upstream changelog and
[`cosmos/README.upstream.md`](cosmos/README.upstream.md) for the
unmodified upstream README.

Modifications introduced for this paper (a non-exhaustive list — the
diff against the closest upstream tag is the source of truth):

- New package `cosmos/crypto/keys/mldsa/` implementing
  `cryptotypes.PrivKey` / `PubKey` for ML-DSA-44 (FIPS 204), wrapping
  [cloudflare/circl](https://github.com/cloudflare/circl)'s
  implementation.
- `--key-type mldsa44` plumbed through `simd testnet init-files` and
  `x/genutil`.
- A multi-validator Docker testnet harness at
  `cosmos/docker/testnet/`, used by the paper's experiments.
- The `cosmos/docker/testnet/scripts/init_testnet.sh` deterministic
  genesis logic, including pre-funded loadgen-sender accounts.

The upstream Cosmos SDK git history was **not** carried across into
this monorepo. To see the upstream history before the fork point,
consult the
[upstream repository](https://github.com/cosmos/cosmos-sdk).

## `ethereum/`

`ethereum/` is built on top of the
[Hardhat](https://github.com/NomicFoundation/hardhat) project
boilerplate (MIT, copyright Nomic Foundation). All files in
`ethereum/contracts/`, `ethereum/scripts/`, `ethereum/test/`,
`ethereum/tools/`, and `ethereum/ignition/modules/` are original to
this work. The Hardhat-toolchain configuration (`hardhat.config.js`,
`package.json`, the `.gitignore` template) is the standard Hardhat
setup with paper-specific dependencies added.

## Repository assembly

This repository was assembled from two pre-existing private GitHub
remotes:

- `postquantum-cosmos` — the cosmos fork
- `ethereum-lockandmint` — the ethereum side

Their histories were squashed and folded into a single fresh git
history under this monorepo. The pre-existing remotes are no longer
the authoritative source; this monorepo is.
