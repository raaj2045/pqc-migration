# Testing and verification

Three independent layers, each establishing something the others cannot: fuzzing
over untrusted input, formal verification of the transfer protocol, and
adversarial submissions against a running light client.

- [Architecture](architecture.md) — what is being verified
- [Getting started](getting-started.md) — building and running
- [Back to the project README](../README.md)

## Test suites

| Layer | Location | Establishes | Runs in CI |
|---|---|---|---|
| Fuzzing | [`security/`](../security/README.md) | Untrusted input cannot crash or bypass deserialization boundaries | Yes |
| Formal verification | [`docs/live-path-verification/`](live-path-verification/README.md) | No credit without a verified packet, on the live transfer path | Yes (models are checked in) |
| Adversarial light client | [`security/light-client-stress/`](../security/light-client-stress/README.md) | The Ethereum light client rejects tampered updates | No — needs a live devnet |

## Fuzzing

Go fuzz targets cover every boundary where untrusted bytes enter: ML-DSA-65 keys
and signatures, ICS-20 packet data in all three encodings, IBC v2 envelopes,
ICS23 merkle proofs, RLP, and relayer messages.

```bash
go test ./security/...                                  # seed corpora
go test ./security/ -run '^$' -fuzz FuzzMerkleProofVerify -fuzztime 60s
```

`FuzzMerkleProofVerify` drives the real `VerifyMembership` entry point rather
than decoding alone, so a malformed proof must fail *verification*, not merely
fail to parse.

`security/README.md` also documents a malformed-input defect in the SDK that is
mitigated here by an ante-handler guard, and an unresolved upstream defect that
is not reachable on this chain.

## Formal verification

A TLA+ model of the transfer path this chain actually runs, checked by two
independent tools — TLC exhaustively, and Apalache symbolically.

```bash
java -cp tla2tools.jar tlc2.TLC -workers auto -config LivePath.cfg LivePath.tla
apalache-mc check --init=Init --next=Next --cinit=ConstInit \
  --inv=NoCreditWithoutVerifiedPacket --length=12 LivePathApalache.tla
```

Four invariants hold under both tools: `TypeOK`,
`NoCreditWithoutVerifiedPacket`, `NoDoublePacketProcessing`, and
`ConservationAcrossCycle`.

Two **vacuity checks** are included and are expected to *fail*: `NeverCredits`
and `NeverAcks` are violated, which is what demonstrates the model actually
reaches the states the invariants constrain. An invariant that holds over an
unreachable state space proves nothing.

See [live-path verification](live-path-verification/README.md) for the model,
its preconditions, and the bounds each configuration explores.

## Adversarial light-client tests

[`security/light-client-stress/`](../security/light-client-stress/README.md)
submits genuine light-client updates with exactly one field tampered — a
corrupted execution state root, a mismatched block number, a non-finalized
header — leaving the BLS signature valid in each case. Tampering one field at a
time is what makes the result discriminating: it distinguishes "the client
checked the thing I broke" from "the client rejected malformed input
generically". Controls run before and after.

These need a live devnet and are run by hand.

**Scope.** They exercise `cw-ics08-wasm-eth`, the Cosmos-side client. They do
not exercise `SP1ICS07Tendermint` on the Ethereum side; its adversarial
evidence is separate, in
[devnet/README.md](../devnet/README.md#evidence-that-verification-is-real).
They confirm that specific adversarial inputs are rejected — not that no
adversarial input is accepted.

## What each layer does not cover

- Fuzzing establishes robustness at deserialization boundaries, not protocol
  correctness.
- The formal model establishes protocol correctness under its stated
  abstraction, not that the implementation matches the model.
- The adversarial tests establish rejection of specific inputs on one client
  implementation, not exhaustiveness.

Each suite's README states its own boundaries in detail.

---

[Project README](../README.md) · [Architecture](architecture.md) · [Getting started](getting-started.md)
