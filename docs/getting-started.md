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

| | |
|---|---|
| Go | 1.26.5 |
| C toolchain | `g++` or `libstdc++-dev` (see [CGO](#cgo-libstdc-is-required)) |
| Node.js, Python 3 | For the devnet tooling |

Running the **bridge** additionally needs a local Ethereum devnet and, for real
SP1 proving, a prover host — see [Hardware for local proving](#hardware-for-local-proving).

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

### wasmvm data directory

The wasmvm data directory must be deterministic; a randomized path produces a
chain that cannot reload its own contracts. See
[wasmvm data directory](wasm-data-dir.md).

## Running the bridge

Both directions are driven by the scripts in [`devnet/`](../devnet/README.md):
a forward transfer (Cosmos escrow, Ethereum voucher mint) and a reverse
redemption (Ethereum voucher burn, Cosmos unescrow).

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
