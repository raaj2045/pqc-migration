# Formal verification case study: the retired `x/lockandmint` module

> **This module was retired from the live chain.** The specification and
> results below describe a superseded design and are retained as a worked
> example of what formal verification catches in an unaudited custom bridge
> module. The chain now moves all assets via ICS-20 over verified light
> clients; see [`../../devnet/README.md`](../../devnet/README.md).
>
> Paths to `x/lockandmint/...` below refer to code as it existed before
> removal. It remains readable at the `v1-mldsa44-fork` tag and in the history
> prior to the retirement commit.

A TLA+ model of the `x/lockandmint` message handlers, checked with TLC.

The model is transcribed from `x/lockandmint/keeper/msg_server.go` **as
implemented**, not as designed. Each action's preconditions mirror one
handler's guard clauses; where a handler has no guard, the action has none.

## Running

Needs a JRE and [`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases).

```bash
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto LockAndMint.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto LockAndMintReplay.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto LockAndMintEventId.tla
```

Saved output for each is in `results/`.

TLC halts at the first invariant violation, so the three modules are identical
copies differing only in which invariants their `.cfg` enables. That is what
allows `NoDoubleMint` to be checked over the *whole* reachable state space
rather than stopping early on an unrelated failure.

## Model

| | |
|---|---|
| Accounts | `{a1, a2}` |
| Event ids | `{e1, e2}` |
| Amounts | `{0, 1, 2}` |
| MaxBalance | 4 |
| MaxHistory | 4 |

`history` is append-only, so the reachable state space is infinite without a
bound; `MaxHistory` supplies one. Results therefore hold for all behaviours of
up to four operations — the standard finite-model caveat.

## Results

| Property | Module | Result | States |
|---|---|---|---|
| `NoDoubleMint` | `LockAndMintReplay` | **holds** | 1,101,969 generated / 81,277 distinct, depth 5, 1.7 s |
| `NoUnverifiedMint` | `LockAndMint` | **violated**, depth 2 | 2 generated |
| `NoCreditWithoutEventId` | `LockAndMintEventId` | **violated**, depth 4 | 49 generated |
| `TypeOK`, `LockConserves` | all | hold | — |

### `NoDoubleMint` holds

No event id is ever consumed by two mints. The `HasProcessedEvent` /
`SetProcessedEvent` guard in `Mint` does what it claims, across the full
bounded state space.

### `NoUnverifiedMint` is violated

Violated by the *first* mint in any behaviour, because `Mint` performs no
verification of any kind: it validates the amount, checks the event id is
non-empty and unprocessed, and credits the receiver. It never reads
`msg.Authority`, never consults a stored header, and takes no proof argument.

This is a property of the code, not an artefact of the model. Mechanically
confirmed against the source:

| Handler | reads `msg.Authority` | calls `GetAuthority()` | can return `ErrUnauthorized` |
|---|---|---|---|
| `Mint` | no | no | no |
| `SetBalance` | no | no | no |
| `UpdateParams` | **yes** | **yes** | **yes** |

`UpdateParams` shows the intended pattern; `Mint` and `SetBalance` omit it.
Both declare `option (cosmos.msg.v1.signer) = "authority"` in `tx.proto`, so
the transaction must be *signed* by whatever address the sender places in the
`authority` field — but since the handler never compares that address to
anything, any signer satisfies it.

The module's own comment documents this for `Mint`:

> the `bridge_authority` gate that existed in the original module has
> intentionally NOT been ported here … do not treat this as production-ready

`SetBalance` carries no equivalent warning.

### `NoCreditWithoutEventId` is violated

The counterexample exhausts both event ids via `Mint`, then still credits an
account through `SetBalance`, whose entry carries `event |-> "none"`.
`SetBalance` consumes no event id, so replay protection does not bound it: it
assigns a balance outright and is unconditionally repeatable.

This matters for how the replay-protection result should be read.
`NoDoubleMint` is a real guarantee, but it is local to `Mint`. It does not
bound the balances an unauthorised caller can produce, because `SetBalance`
reaches the same state without touching the event-id keyspace.

## Scope

**In scope.** The state transitions of the three balance-affecting
`x/lockandmint` handlers (`Mint`, `SetBalance`, `Lock`), the processed-event
keyspace, and account balances.

**Out of scope, assumed correct.** The consensus verification performed by the
upstream light clients — `cw-ics08-wasm-eth`'s sync-committee/BLS verification
and its Merkle-Patricia storage proofs, and `SP1ICS07Tendermint` on the EVM
side. This model takes their verdicts as trusted inputs rather than
re-deriving them. Note that `x/lockandmint` does not currently consult those
clients at all, which is precisely what `NoUnverifiedMint` detects.

**Not modelled, because absent from the module.** There is no `SubmitHeader`
action, no stored headers or consensus states, and no burn-proof/redemption
handler in `x/lockandmint`. `grep -riE "proof|header|consensus"` over the
module returns only comments. The IBC transfer path that *is* light-client
verified lives in `x/ibc` + `devnet/`, not here.
