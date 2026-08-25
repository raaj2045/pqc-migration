# Live-path formal verification: ICS-20 transfer over IBC v2

A TLA+ model of the transfer path this chain **actually runs** — ICS-20
over IBC v2 (Eureka), with packet commitments verified against a light
client — checked with two independent model checkers.

This is the companion to the
[retired-module case study](../formal-verification-case-study/), and the
contrast is the point. That study modelled `x/lockandmint`, a custom bridge
module, and found real defects: it minted without verifying any proof and
without checking any authority. This one models the code that replaced it.
The invariants that failed there hold here, and the reason they hold is
visible in the model rather than asserted.

## Running

### TLC

Needs a JRE and [`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases).

```bash
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto -config LivePath.cfg             LivePath.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto -config LivePathLarge.cfg        LivePath.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto -config LivePathConservation.cfg LivePath.tla
```

The vacuity checks, which **must** fail:

```bash
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto -config VacuityCredits.cfg LivePath.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto -config VacuityAcks.cfg    LivePath.tla
```

### Apalache

Needs a JRE and [Apalache](https://github.com/apalache-mc/apalache) (0.62.1
here). Constants come from `ConstInit`, not from a `.cfg`.

```bash
apalache-mc check --init=Init --next=Next --cinit=ConstInit \
  --inv=NoCreditWithoutVerifiedPacket --length=12 LivePathApalache.tla
```

Substitute `--cinit=ConstInitConservation --length=10` for the conservation
run, which uses reduced bounds.

## Model

Two chains. **A** is the sender: it escrows and stores a packet commitment.
**B** is the receiver: it tracks A through a light client, verifies the
commitment, stores a packet receipt, and mints a voucher.

State:

| Variable | Meaning |
|---|---|
| `balA`, `escrow` | A's user balance and escrow account |
| `commitments` | packet commitments currently stored on A |
| `committedAt` | height → the commitment set observable at that height |
| `ackedA` | sequences whose acknowledgement A has processed |
| `vouchers`, `credited` | B's voucher balance, and which sequences produced it |
| `receipts` | packet receipts stored on B (replay protection) |
| `trusted` | consensus-state heights B's light client currently trusts |
| `height` | A's current height |

Actions: `SendTransfer`, `Advance` (A produces a block, freezing the current
commitment set at a height), `UpdateClient`, `RecvPacket`,
`RecvPacketRejected`, `AckPacket`.

### Preconditions are transcribed from source

Not from memory or documentation. The three call sites that determined the
model, all in `ibc-go` v11 as pinned in `go.mod`:

| Source | What it fixed in the model |
|---|---|
| `modules/core/04-channel/v2/keeper/packet.go` → `recvPacket` | the **order** of checks: receipt-lookup *before* `VerifyMembership`, `SetPacketReceipt` *after* it |
| `modules/core/04-channel/v2/keeper/msg_server.go` → `RecvPacket` | `writeFn()` is called *before* the application callback, so the receipt commits even when the credit fails |
| `modules/apps/transfer/keeper/relay.go` → `OnRecvPacket` | the unescrow-vs-mint branch, taken on `token.Denom.HasPrefix` |

That third fact is easy to get wrong by reading the happy path only. A
transfer whose application callback fails still leaves a receipt behind, so
it is not replayable — the failure is final, not a retry opportunity.

### The proof abstraction

This is the heart of the model:

```tla
ProofVerifies(s, h) == h \in trusted /\ s \in committedAt[h]
```

Verification succeeds exactly when the commitment really is in A's state at a
height B trusts. `RecvPacketRejected` lets an adversarial relayer submit *any*
`(sequence, height)` pair; the check is what rejects it. Without this action
the model would only ever be fed well-formed input, and
`NoCreditWithoutVerifiedPacket` would be trivially true.

### Apalache compatibility changes

Both are reported here because they are changes to the checked artefact.
Neither touches the transition relation.

1. **`MaxHeight` constant** replaces `Cardinality(Heights)` in `Advance` and
   `TypeOK`. At the configured bounds these are equal (`MaxHeight = 3`,
   `Cardinality({1,2,3}) = 3`).
2. **`ConstInit` / `ConstInitConservation` operators**, since Apalache takes
   constants from an operator rather than a `.cfg`.

## Results

Every invariant holds under both tools. Nothing was violated, so there are no
counterexamples in this directory — unlike the retired-module study, where
there are three.

| Invariant | TLC 2.19 | Apalache 0.62.1 |
|---|---|---|
| `TypeOK` | holds | `NoError` |
| `NoCreditWithoutVerifiedPacket` | holds | `NoError` |
| `NoDoublePacketProcessing` | holds | `NoError` |
| `ConservationAcrossCycle` | holds | `NoError` |

TLC runs are exhaustive breadth-first with the queue fully drained:

| Config | Bounds | Distinct states | Depth |
|---|---|---|---|
| `LivePath.cfg` | 2 seqs, 3 heights | 496 | 12 |
| `LivePathLarge.cfg` | 3 seqs, 4 heights | 28,380 | 17 |
| `LivePathConservation.cfg` | 2 seqs, 2 heights | 76 | 10 |

Apalache is symbolic, bounded by `--length`: 12 for the core invariants and
`TypeOK`, 10 for conservation at its reduced bounds. Both core invariants were
additionally re-run at `--length=20`, past the depth-17 frontier TLC reached,
and still report `NoError`:

| Invariant | `--length=12` | `--length=20` |
|---|---|---|
| `NoCreditWithoutVerifiedPacket` | `NoError` | `NoError` |
| `NoDoublePacketProcessing` | `NoError` | `NoError` |

Agreement between the two matters more than either alone. TLC enumerates
concrete states to depth 17; Apalache reasons symbolically over all values at
each length. They fail in different ways, so two clean runs is a stronger
result than one.

### Why the invariants hold

- **`NoCreditWithoutVerifiedPacket`** — `recvPacket` calls `VerifyMembership`
  before `SetPacketReceipt`, and the credit happens downstream of both. The
  adversarial relayer modelled by `RecvPacketRejected` cannot reach the
  credit. This is precisely the invariant `x/lockandmint` violated: its
  `Mint` handler performed no proof verification at all.
- **`NoDoublePacketProcessing`** — the receipt check precedes verification and
  short-circuits with `ErrNoOpMsg`. Because the receipt is committed even on
  application failure, a failed transfer cannot be replayed.
- **`ConservationAcrossCycle`** — `balA + escrow = InitialA`, and vouchers
  never exceed escrow.

### Vacuity checks

"No violation" is worthless if the model never does anything interesting, so
both tools were also asked to prove invariants that **must** fail. Both fail,
under both tools:

| Check | Asserts | TLC | Apalache |
|---|---|---|---|
| `NeverCredits` | nothing is ever credited | violated, depth 7 | `Error` |
| `NeverAcks` | no packet is ever acknowledged | violated, depth 8 | `Error` |

`NeverAcks` was initially run only under TLC. That was an omission on the
author's part, not a technical limitation — `AckPacket` is modelled as its own
action and translates to Apalache without difficulty. It has since been run
under both, and is recorded here rather than left as a silent asymmetry.

Together these confirm the model reaches a successful `RecvPacket` with a
credit, and completes the full send → receive → acknowledge cycle. The
passing invariants above are therefore checked against a model that actually
exercises the path.

## Scope

**The light client's own consensus verification is axiomatic.** `UpdateClient`
requires only that the height corresponds to one A actually reached; how
validity is established — sync-committee BLS verification in
`cw-ics08-wasm-eth`, or `SP1ICS07Tendermint` on the EVM side — is not
modelled. Admitting a consensus state that does *not* correspond to a real
height of A is exactly the light client being broken, and that is the stated
trust boundary.

So this model proves the transfer logic is correct **given a sound light
client**. It says nothing about whether the light client is sound. That is
the same boundary documented in
[`security/README.md`](../../security/README.md#scope), and it is the honest
limit of this result.

**Also not modelled:**

- **Timeouts.** `recvPacket` checks `currentTimestamp < packet.TimeoutTimestamp`
  first; there is no clock in this spec, so the timeout and refund paths are
  absent. Conservation is therefore checked across the success cycle only.
- **Multiple payloads per packet**, and asynchronous acknowledgements. The
  model carries one transfer per packet.
- **The Rust light-client internals**, for the same reason given in the fuzzing
  scope: they are reached only through the contract boundary.
- **Model checking is not proof.** These are bounded results. TLC is exhaustive
  only within the configured constants; Apalache is exhaustive only within
  `--length`. Neither establishes correctness for unbounded sequence counts.
