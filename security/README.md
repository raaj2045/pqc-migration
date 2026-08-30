# Fuzzing: external-input deserialization boundaries

Go fuzz targets for every point on the live ICS-20 / light-client path where
the chain deserializes input from an untrusted source *before* that input has
been verified.

The contract each target asserts is the same: **malformed input must be
rejected with an error, never with a panic.** A panic on attacker-supplied
bytes is a denial-of-service surface regardless of whether the caller happens
to recover, because it turns "reject this message" into "abort this execution
path".

## Running

```bash
go test ./security/                                    # seed corpora only
go test ./security/ -run=FuzzX -fuzz=FuzzX -fuzztime=5m # one target
```

Seed corpora cover truncation, wrong-length keys and signatures, sub-word and
oversized ABI payloads, bad RLP length prefixes, and empty input where a value
is expected — so `go test ./...` exercises them as regression tests without
`-fuzz`.

## Coverage

| Boundary | Targets |
|---|---|
| ML-DSA-65 keys and signatures | `FuzzMLDSA65PubKeyGuarded`, `FuzzMLDSA65VerifySignature`, `FuzzMLDSA65PubKeyUnmarshalAmino` |
| ICS-20 packet data | `FuzzTransferPacketDataJSON`, `…ABI`, `…Proto` |
| IBC v2 envelopes | `FuzzIBCV2PacketUnmarshal`, `FuzzIBCV2AcknowledgementUnmarshal` |
| ICS23 merkle proofs | `FuzzMerkleProofVerify`, `FuzzCommitmentProofUnmarshal`, `FuzzMerklePathUnmarshal` |
| RLP | `FuzzRLPDecodeReceipt`, `FuzzRLPDecodeProofNodes`, `FuzzRLPSplit` |
| Relayer messages | `FuzzMsgRecvPacketUnmarshal`, `FuzzMsgAcknowledgementUnmarshal` |

`FuzzMerkleProofVerify` drives the real `VerifyMembership` entry point rather
than decoding alone, so a malformed proof must fail verification rather than
merely fail to parse.

## Findings

### 1. A malformed-input defect in the SDK — mitigated here

`mldsa65.PubKey.Address()` requires a key of exactly `PubKeySize` bytes, and
nothing between the wire and the SDK's ante chain constrains the length of a
transaction-supplied key. This chain supplies the missing bound itself.

The mitigation is [`app/ante.go`](../app/ante.go): an ante decorator that runs
ahead of the entire SDK ante chain and rejects wrong-length ML-DSA-65 keys with
an error. `app/ante_guard_test.go` pins the property it depends on — that it
rejects exactly the keys the downstream call cannot accept — so the guard
cannot be weakened without failing a test.

Further detail on the upstream defect is withheld from this repository pending
coordinated disclosure with its maintainers. It is not required to understand
or maintain the mitigation.

### 2. An unresolved defect in an upstream dependency — not mitigated, not reachable

The campaign found a second defect, in a third-party dependency rather than in
this chain's code. The details — the affected component, the failure mode and
the reproducing input — are withheld from this repository pending coordinated
disclosure with that project's maintainers, through the private channel their
security policy requires.

What can be said without assisting an attacker:

- **This chain is not affected.** The defect lies on a Go code path that
  nothing on this chain's live path reaches. The two structural facts that make
  that true are asserted by `TestICS23ProofVerificationNotReachableOnThisChain`,
  which fails loudly if either stops holding.
- **The relevant code is linked into the binary but never called.** It arrives
  as a transitive dependency. Linkage is not reachability.
- **No mitigation was required here**, so — unlike finding 1 — there is no
  corresponding change to `app/`.

This section will be expanded once the upstream issue is public.

### A note on the crashers

Fuzz crashers are not committed to `testdata/`. Go replays committed crashers
as seeds on every plain `go test`, which makes them permanently failing tests —
the correct outcome for a defect we can fix, and the wrong one for a defect in
a dependency we cannot. `security/.gitignore` keeps them out of the tree.

## Live-devnet stress tests

[`light-client-stress/`](light-client-stress/) holds adversarial submissions
against the real Ethereum light client — tampered state roots, tampered block
numbers, and proofs against non-finalized blocks. All were rejected; controls
before and after confirm the client was functional throughout.

Those require a live devnet and are run by hand, unlike the fuzz targets here,
which run in CI. Their README also names what they do *not* establish: the
finality test submits a non-finalized header directly rather than producing a
genuine reorg, which this single-validator-client topology cannot do.

## Scope

Three boundaries are deliberately **not** covered here. Each is a limitation of
this harness, not an assertion that the code behind it is correct.

**1. Wasm contract internals are not fuzzed.** The Ethereum light client
(`cw-ics08-wasm-eth`) is Rust compiled to wasm. Its Merkle-Patricia proof
walker, its RLP decoding and its BLS sync-committee verification are reached
only through the contract boundary, where Go sees opaque bytes. The RLP and
ICS23 targets here exercise the Go implementations — `go-ethereum`'s decoder
and `cosmos/ics23` — which are the same algorithms but *not the same code* that
runs in production for the Ethereum path. Fuzzing the contract's internals
requires a Rust harness (`cargo-fuzz` or `proptest`) against the
`ethereum-light-client` crate, and has not been done.

**2. Upstream consensus verification is a trusted assumption.** As in
[`docs/live-path-verification/`](../docs/live-path-verification/), the
correctness of the light clients' own consensus checking — sync-committee/BLS verification in `cw-ics08-wasm-eth`,
and `SP1ICS07Tendermint` on the EVM side — is assumed rather than re-derived.
Note also that the devnet deployment uses a **mock** SP1 verifier, which
performs no proof checking at all.

**3. Fuzzing is not proof.** These targets establish the absence of crashes on
the inputs explored within the time budget, not the absence of crashes.

---

[Project README](../README.md) · [Testing](../docs/testing.md) · [Architecture](../docs/architecture.md)
