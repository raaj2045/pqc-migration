# Formal verification, fuzzing and adversarial testing

Sections III-D and VI-D of the paper summarise this document. Full details are in:

- [`docs/live-path-verification/README.md`](../docs/live-path-verification/README.md): TLA+ model, TLC and Apalache runs, vacuity checks, scope (specifications in the same folder);
- [`security/README.md`](../security/README.md): fuzz targets and findings;
- [`security/light-client-stress/README.md`](../security/light-client-stress/README.md): adversarial light-client tests;
- [`docs/testing.md`](../docs/testing.md): overview, and what each layer does not cover.

## Model checking

We modelled the live transfer path in TLA+ and checked it with both TLC and Apalache against three invariants:

1. no credit without a validated packet;
2. no packet processed twice;
3. conservation throughout the cycle.

All three hold under both checkers. Deliberately false invariants are violated, so the model exercises real behaviour rather than passing vacuously. TLC explored every reachable state of a model with up to three packets and four block heights (about 28,000 states). Apalache checked the same properties for executions of up to 20 steps.

**Scope.** The model assumes light-client verification is correct and does not include timeouts, so conservation is checked only for successful forward transfers. It also excludes failed acknowledgements, redemption, client expiry, freezing and misbehaviour, update reordering, and partial batch failures.

**Correspondence to the implementation.** Each model action corresponds to an implementation entry point:

| Model action | Implementation |
|---|---|
| `SendTransfer` | `sendTransfer` |
| `UpdateClient` | `MsgUpdateClient` |
| `RecvPacket` | `MsgRecvPacket` |
| `AckPacket` | `ackPacket` |

The mapping is not mechanically checked. The model therefore verifies the stated safety properties of the abstracted forward-transfer path under its assumptions, not full bridge safety or implementation equivalence. The adversarial tests and fuzzing below provide implementation-level evidence.

## Fuzzing

Sixteen fuzz targets cover the external-input boundaries and run in continuous integration. They found a reachable panic in the SDK's ML-DSA-65 key handling, triggered by a malformed key before authentication. We mitigated it in the Cosmos application's ante handler.

## Adversarial light-client tests

Tampered submissions against a live client were rejected as expected:

| Submission | Rejected with |
|---|---|
| Tampered verification key | `VerificationKeyMismatch` |
| Tampered proof bytes | `ProofInvalid` (Groth16 pairing check) |
| Premature finality claim | The finality-ordering invariant, named in the error |

An untampered control was accepted once its block was genuinely finalised.
