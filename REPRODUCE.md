# Reproducing

Two independent tracks live in this repository:

* **The chain and bridge** — building the Cosmos SDK v0.55 chain and
  running the Ethereum bridge cycle against a local devnet.
* **The paper figures** — regenerating every figure from raw data
  committed here. No network or external services required.

> This file describes the tree as it stands after the rebuild
> (`v1-mldsa44-fork` is the previous, fork-based implementation). The
> paper's implementation section is being rewritten to match; expect
> another pass here when that lands. See
> [Known gaps](#known-gaps--ml-dsa-44-modules-retained-for-historical-reference).

---

## §1 — Building the chain

```
Go   1.26.5
```

```bash
go build ./...
go test ./...
```

The 08-wasm BLS verifier links against `herumi/bls-eth-go-binary`,
which needs `libstdc++` at link time. On a host without `g++`, see
[docs/building.md](docs/building.md) for the one-line symlink
workaround.

Further operational notes:

* [docs/building.md](docs/building.md) — toolchain and CGO requirements.
* [docs/ethereum-light-client.md](docs/ethereum-light-client.md) — the
  execution- vs beacon-state-root distinction, the caller-supplied
  `pubkeys_hash`, and the gov-gated `MsgStoreCode`.
* [docs/wasm-data-dir.md](docs/wasm-data-dir.md) — why the wasmvm data
  directory must be deterministic.

## §2 — Running the bridge cycle

The bridge is verified by a real Ethereum light client
(`cw-ics08-wasm-eth`), not a trusted relayer authority. Both directions
are exercised by the scripts in [`devnet/`](devnet/README.md):

* forward transfer — Cosmos escrow, Ethereum voucher mint;
* reverse redemption — Ethereum voucher burn, Cosmos unescrow.

Prerequisites, configuration, the exact step order and realistic timing
(each direction waits on Ethereum finality, roughly five minutes) are
documented in [devnet/README.md](devnet/README.md).

---

## §3 — Reproducing the paper figures

The Python figure scripts depend on `matplotlib` and `numpy` only:

```bash
pip install matplotlib numpy     # or: pip3 / python3 -m pip
```

All raw data is committed, so re-rendering every figure takes well
under a minute:

```bash
cd benchmarks/crypto_micro          && python3 plot.py      && cd ../..
cd benchmarks/block_packing         && python3 plot.py      && cd ../..
cd benchmarks/storage_sim           && python3 plot.py      && cd ../..
cd experiments/validator_scaling_v2 && python3 aggregate.py && cd ../..
```

Each command regenerates the PDFs next to their `caption.txt`
neighbours.

### Regenerating the raw data

`benchmarks/block_packing/results.json` and the storage_sim per-scheme
JSONs come from the simulators in `tools/`:

```bash
cd tools/block_packing && go run . > ../../benchmarks/block_packing/results.json && cd ../..
cd tools/storage_sim   && go run . --out ../../benchmarks/storage_sim/          && cd ../..
```

Runtime: a few seconds each. Re-render with the respective `plot.py`
afterwards. These tools build standalone and are covered by CI.

---

## Known gaps — outstanding verification

**`security/light-client-stress/` has not been re-run since the EVM light
client was moved from the mock SP1 verifier to the real Groth16 one.** That work
did not touch `cw-ics08-wasm-eth`, which is what these tests exercise, so the
published results are expected to hold unchanged — but they have not been
re-executed. The harness needs a live devnet, and rebuilding one solely for this
is not warranted. **Run it the next time a devnet is up for another reason.**

## Known gaps — ML-DSA-44 modules retained for historical reference

**`benchmarks/crypto_micro` and `tools/presigner` measure ML-DSA-44 from
the superseded fork, not the ML-DSA-65 implementation this repository
now builds.** They are kept for historical reference only.

| Module | State |
|---|---|
| `benchmarks/crypto_micro` | `go test -bench` imports `crypto/keys/mldsa` via a `replace` onto the deleted `cosmos/` fork |
| `tools/presigner` | same import, same `replace` |

Both are **excluded from CI** for exactly this reason: the fork they
resolve against is no longer in the tree, so neither can build here.
Their source is unchanged from the fork-based prototype, which remains
available in full at the **`v1-mldsa44-fork`** tag.

The committed `benchmarks/crypto_micro/results.json` likewise reflects
ML-DSA-44, so the `crypto_micro` figures in §3 plot the *previous*
implementation's numbers, not the current chain's.

### Do not port these to ML-DSA-65 yet

Stock SDK v0.55 ships `crypto/keys/mldsa65` — a different package *and*
a different parameter set. Porting is therefore not a mechanical fix
(dropping the `replace` directive is not sufficient); it changes what is
being measured, and the resulting numbers would not be comparable to
those already published. Whether to re-measure under ML-DSA-65, and how
to present both sets of figures, is a separate decision tied to the
paper rewrite. Until that decision is made, leave these two modules as
they are.

Everything else in the tree builds and is covered by CI.
