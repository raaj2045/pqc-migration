# Getting started

Build the chain, run the tests, and bring up the bridge.

> **Looking to regenerate the paper's figures instead?**
> This page is for building and running the system to understand or develop on
> it. To reproduce the published results specifically, use
> [REPRODUCE.md](../REPRODUCE.md).

- [Architecture](architecture.md) — what you are building
- [Testing and verification](testing.md)
- [Back to the project README](../README.md)

## Requirements

`devnet/scripts/setup-toolchain.sh` installs and version-checks everything
below (plus `bun`/`just` for the devnet tooling).

Building and testing the chain:

| | Version |
|---|---|
| Go | 1.26.5 |
| C toolchain | `g++` or `libstdc++-dev` (see [CGO](#cgo-libstdc-is-required)) |
| Node.js | 20 |
| Python | 3 |

Running the bridge additionally needs:

| | Version | Notes |
|---|---|---|
| Docker | — | Required for the Ethereum devnet and the Groth16 wrap |
| Kurtosis CLI | **1.15.2** | Pin is load-bearing, see [below](#kurtosis-version-pins) |
| `ethereum-package` | **6.0.0** | Pin is load-bearing, see [below](#kurtosis-version-pins) |
| Foundry (`forge`, `cast`) | 1.0.0-stable | |
| protoc | 36.0 | Must be >= 3.15; `aggregator.proto` uses proto3 optional |
| SP1 toolchain (`sp1up`) | circuits v6.1.0 | Must match the deployed `SP1VerifierGroth16` |

Reproducing the formal verification also needs a JRE 21 with
[TLC](https://github.com/tlaplus/tlaplus) 2.19 and
[Apalache](https://github.com/apalache-mc/apalache) 0.62.1.

For real SP1 proving, see [Hardware for local proving](#hardware-for-local-proving).

### Kurtosis version pins

Kurtosis publishes no binaries on GitHub releases — only an apt repo
(`https://apt.fury.io/kurtosis-tech/`), whose index stops at 1.15.2 even though
the CLI advertises newer versions. Without root, install with `dpkg-deb -x` of
the 1.15.2 deb into a directory that precedes `/usr/bin` on `PATH`, then run
`kurtosis engine restart` so the engine matches the CLI.

Because the CLI is 1.15.2, `ethereum-package` must be pinned to tag **6.0.0**.
Newer tags pull in the zkboost module, which needs a Starlark `GpuConfig` type
that only newer Kurtosis provides.

The devnet arguments are in
[`devnet/kurtosis/network_params.yaml`](../devnet/kurtosis/network_params.yaml);
[`verify-devnet.sh`](../devnet/kurtosis/verify-devnet.sh) checks the fork
schedule, slot progression, finality and the light-client endpoints.

## Build

```bash
go build ./...
go test ./...
```

`devnet/` holds JavaScript and Python tooling only. It is deliberately not a Go
package and is invisible to `go build ./...`.

### CGO: libstdc++ is required

The 08-wasm module's BLS verifier (`blsverifier.CustomQuerier()`, needed by the
Ethereum light client to check sync-committee signatures) links against
`github.com/herumi/bls-eth-go-binary`, which needs `libstdc++` at link time.

With `g++` installed this is automatic. Where only the runtime library is
present (`libstdc++.so.6` but no `libstdc++.so` development symlink), the link
fails with:

```
/usr/bin/ld: cannot find -lstdc++: No such file or directory
```

Either install `g++`/`libstdc++-dev`, or point the linker at a symlink you
create yourself:

```bash
mkdir -p ~/.local/lib
ln -sf /usr/lib/x86_64-linux-gnu/libstdc++.so.6 ~/.local/lib/libstdc++.so
CGO_LDFLAGS="-L$HOME/.local/lib" go build ./...
```

wasmvm also links dynamically, so `libwasmvm` must be reachable at runtime.

### Building solidity-ibc-eureka

`devnet/scripts/setup-eureka-checkout.sh` automates this section; the manual
steps below are for debugging a build failure.

The bridge's forward leg needs `proof-api` from
[`solidity-ibc-eureka`](https://github.com/srdtrk/solidity-ibc-eureka), built
from a checkout with [this project's patches](../devnet/patches/README.md)
applied.

That build needs four environment overrides:

```bash
PATH="$HOME/.local/gxx11/bin:$HOME/.local/protoc/bin:$PATH" \
PROTOC=$HOME/.local/protoc/bin/protoc \
LIBRARY_PATH="$HOME/.local/gxx11/usr/lib/gcc/x86_64-linux-gnu/11" \
CARGO_TARGET_DIR=$HOME/.cache/sibe/target \
just install-proof-api
```

- **`CARGO_TARGET_DIR`** — `just build-cw-ics08-wasm-eth` runs Docker as root
  and leaves a root-owned `target/`. The override directory must itself be named
  `target`: `sp1-recursion-core`'s `build.rs` walks up from `OUT_DIR` for an
  ancestor named exactly that, and panics otherwise.
- **`PATH`, `LIBRARY_PATH`** — supply a C++ compiler and the `libstdc++.so` dev
  symlink the final link needs. Use `LIBRARY_PATH`, **not `RUSTFLAGS`**:
  `RUSTFLAGS` is part of the build fingerprint, so changing it discards the
  whole build cache. Where gcc is unavailable system-wide, unpack it with
  `dpkg -x` into a user directory. A nix gcc 13.3 is not a substitute — it
  compiles against a newer GLIBCXX than a 11.x system runtime provides, and the
  result links and then segfaults.
- **`PROTOC`** — points at protoc >= 3.15.

With root available, the first two reduce to installing `g++` and taking
ownership of `target/`.

### wasmvm data directory

The wasmvm data directory must be deterministic; a randomized path produces a
chain that cannot reload its own contracts. See
[wasmvm data directory](wasm-data-dir.md).

## Running the bridge

[`devnet/scripts/bring-up-devnet.sh`](../devnet/README.md#bringing-up-a-devnet)
stands up the devnet, ending with the light client live and `Active`. Both
transfer directions are then driven by the scripts in
[`devnet/`](../devnet/README.md): a forward transfer (Cosmos escrow, Ethereum
voucher mint) and a reverse redemption (Ethereum voucher burn, Cosmos
unescrow).

Prerequisites, configuration, step order and timing are documented in
[devnet/README.md](../devnet/README.md). Contract deployment is
[devnet/deploy/](../devnet/deploy/README.md).

Expect each direction to wait on Ethereum finality — roughly two epochs, about
five minutes on a 6-second-slot devnet.

## Hardware for local proving

The Cosmos → Ethereum direction needs an SP1 Groth16 proof. Against
`SP1MockVerifier` this is free and instant. Against the real
`SP1VerifierGroth16` it is a substantial CPU workload:

| | Requirement |
|---|---|
| Memory | **~28 GB RAM plus ~8 GB swap.** 24 GB is not sufficient. |
| Time | ~10 minutes per proof on 6 cores / 12 threads |
| Disk | 5.8 GB one-time Groth16 artifact download, unpacking to 7.9 GB under `~/.sp1/` |
| Docker | Required — the Groth16 wrap runs in a container |

Peak usage reaches roughly 27.5 GB during the recursion phase, so proving
alongside other memory-heavy work on the same host is not advisable. Restart
the prover between proofs; it does not release swapped pages, so consecutive
proofs otherwise start with progressively less headroom.

Proving costs and the verifier choice are covered in
[devnet/README.md](../devnet/README.md#proving).

## Where to go next

- [Architecture](architecture.md) — how transfer, redemption and verification fit together
- [Testing and verification](testing.md) — the test suites and what they establish
- [Devnet runbook](../devnet/README.md) — running a full cycle

---

[Project README](../README.md) · [Architecture](architecture.md) · [Testing](testing.md) · [REPRODUCE](../REPRODUCE.md)
