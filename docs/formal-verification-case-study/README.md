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

The specification was checked with **two independent model checkers**, TLC and
Apalache, which agree on all three results. Apalache is the actively maintained
TLA+ model checker and the one standardised on in the Cosmos ecosystem (it is
the TLA+ entry in [`cosmos/awesome-cosmos`](https://github.com/cosmos/awesome-cosmos));
it is used here as independent confirmation of the TLC findings rather than as
a replacement for them.

## Running

### TLC

Needs a JRE and [`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases).

```bash
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto LockAndMint.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto LockAndMintReplay.tla
java -cp /path/to/tla2tools.jar tlc2.TLC -workers auto LockAndMintEventId.tla
```

TLC halts at the first invariant violation, so the three modules are identical
copies differing only in which invariants their `.cfg` enables. That is what
allows `NoDoubleMint` to be checked over the *whole* reachable state space
rather than stopping early on an unrelated failure.

### Apalache

Needs a JRE and [Apalache](https://github.com/apalache-mc/apalache) (0.62.1
used here). One module, `LockAndMintApalache.tla`, serves all three invariants
because Apalache takes the invariant as a flag.

```bash
apalache-mc check --init=Init --next=Next --cinit=ConstInit \
    --inv=NoDoubleMint --length=4 LockAndMintApalache.tla
```

Substitute `--inv=NoUnverifiedMint` / `--inv=NoCreditWithoutEventId`, and
`--cinit=ConstInitLarge` for the larger bound.

Saved output for both tools is in `results/`.

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

### Apalache compatibility changes

`LockAndMintApalache.tla` is a transcription of `LockAndMint.tla` for
Apalache's stricter front end. **The state machine and every invariant are
unchanged**; the differences are entirely mechanical, and are listed in full in
the module header:

1. **Type annotations** (`\* @type: ...;`) on constants, variables and two
   operators. These are comments — TLC ignores them.
2. **`Accounts` and `EventIds` are `Str`** rather than TLC model values, since
   Apalache's type checker needs a concrete element type. They remain opaque
   and are compared only by equality, exactly as model values are.
3. **The `history \in Seq(...)` conjunct of `TypeOK` is dropped.** Apalache
   rejects unbounded `Seq(S)` in a membership test, and its type checker
   enforces the same property statically — which is strictly stronger than
   checking it per state.
4. **`StateConstraint` is dropped.** TLC needs it because `history` grows
   without bound and its explicit-state search would not terminate. Apalache
   bounds trace length directly via `--length=N`, which yields
   `Len(history) <= N`, making the constraint redundant. `--length=4`
   reproduces TLC's `MaxHistory = 4`.
5. **Constants come from `ConstInit`** (`--cinit`) rather than a `.cfg`
   `CONSTANTS` block, which is how Apalache accepts constant values.
   `ConstInitLarge` supplies the larger bound.

## Results

Both checkers agree on all three properties.

| Property | TLC | Apalache | Agree |
|---|---|---|---|
| `NoDoubleMint` | **holds** — 1,101,969 generated / 81,277 distinct, depth 5, 1.7 s | **holds** — no error up to length 4, 11.4 s | yes |
| `NoUnverifiedMint` | **violated** at the first mint | **violated** at the first mint | yes |
| `NoCreditWithoutEventId` | **violated**, after two mints then a `SetBalance` | **violated**, by a `SetBalance` alone | yes |
| `TypeOK`, `LockConserves` | hold | hold (see note 3 below) | yes |

### Counterexamples compared

For `NoUnverifiedMint` the two tools find the **same** path: a single `Mint`
violates the property immediately. They differ only in the arbitrary amount
each solver picked (TLC chose 0, Apalache chose 1), which carries no meaning —
the handler performs no verification at any amount.

For `NoCreditWithoutEventId` Apalache finds a **strictly shorter** path. TLC's
counterexample first exhausts both event ids via two mints and only then calls
`SetBalance`; Apalache goes directly to a single `SetBalance` from the initial
state, with `processed = {}`. Both expose the same defect, and Apalache's is
the more economical demonstration: `SetBalance` credits an account without
consuming an event id regardless of what the mint path has done. TLC's longer
trace is the more illustrative one for a reader, since it shows the credit
still succeeding *after* replay protection has been exhausted.

The difference is a search-strategy artefact, not a disagreement: TLC reports
the first violating state its breadth-first enumeration reaches, whereas
Apalache asks an SMT solver for any satisfying trace within the bound.

### Larger bounds

Apalache's symbolic encoding scales past what TLC enumerated explicitly. At
**3 accounts and 3 event ids** — where TLC's state count would grow sharply —
every result is unchanged:

| Bound | `NoDoubleMint` | `NoUnverifiedMint` | `NoCreditWithoutEventId` |
|---|---|---|---|
| 2 accounts, 2 ids, length 4 | holds (11.4 s) | violated (9.7 s) | violated (7.9 s) |
| 3 accounts, 3 ids, length 6 | holds (11.2 s) | violated (6.1 s) | violated (6.8 s) |
| 3 accounts, 3 ids, length 10 | holds (16.8 s) | — | — |

`NoDoubleMint` therefore holds for all behaviours of up to **ten** operations
over three accounts and three event ids, a bound TLC's explicit-state search
does not reach in comparable time. The replay-protection guarantee is stronger
than the original TLC run established, while the two violations remain
violations at every bound tested.

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
