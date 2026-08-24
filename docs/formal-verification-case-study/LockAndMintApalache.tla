------------------------- MODULE LockAndMintApalache -------------------------
(***************************************************************************)
(* Apalache-compatible transcription of LockAndMint.tla.                   *)
(*                                                                         *)
(* The state machine is IDENTICAL to LockAndMint.tla. Only the following   *)
(* changes were made, all of them for Apalache's stricter front end; none  *)
(* alters the semantics of any action or invariant:                        *)
(*                                                                         *)
(*   1. Type annotations (\* @type: ...;) added to CONSTANTS, VARIABLES    *)
(*      and to operators whose types Apalache cannot infer. These are      *)
(*      comments; TLC ignores them entirely.                               *)
(*                                                                         *)
(*   2. Accounts and EventIds are Str rather than TLC model values, since  *)
(*      Apalache's type checker needs a concrete element type. Both are    *)
(*      still opaque, compared only by equality, exactly as model values   *)
(*      are.                                                               *)
(*                                                                         *)
(*   3. The `history \in Seq(...)` conjunct of TypeOK is dropped. Apalache *)
(*      rejects unbounded Seq(S) in a membership test, and the property is *)
(*      already enforced statically by the type checker, which is strictly *)
(*      stronger than checking it per-state.                               *)
(*                                                                         *)
(*   4. StateConstraint is dropped. TLC needs it because `history` is      *)
(*      append-only and its explicit-state search would not terminate.     *)
(*      Apalache bounds trace length directly with --length=N, which       *)
(*      yields Len(history) <= N and makes the constraint redundant.       *)
(*      --length=4 here reproduces TLC's MaxHistory = 4.                   *)
(*                                                                         *)
(*   5. Constants are supplied by ConstInit (--cinit) rather than a .cfg   *)
(*      CONSTANTS block, which is how Apalache takes constant values.      *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences

CONSTANTS
    \* @type: Set(Str);
    Accounts,
    \* @type: Set(Str);
    EventIds,
    \* @type: Set(Int);
    Amounts,
    \* @type: Int;
    MaxBalance

VARIABLES
    \* @type: Str -> Int;
    balance,
    \* @type: Str -> Int;
    locked,
    \* @type: Set(Str);
    processed,
    \* @type: Seq({ kind: Str, account: Str, amount: Int, event: Str, verified: Bool });
    history

vars == <<balance, locked, processed, history>>

\* Constant values, mirroring LockAndMint.cfg exactly.
ConstInit ==
    /\ Accounts = { "a1", "a2" }
    /\ EventIds = { "e1", "e2" }
    /\ Amounts = { 0, 1, 2 }
    /\ MaxBalance = 4

\* Larger bound, used to test whether the results survive beyond the scale TLC
\* enumerated explicitly: 3 accounts and 3 event ids rather than 2 and 2.
ConstInitLarge ==
    /\ Accounts = { "a1", "a2", "a3" }
    /\ EventIds = { "e1", "e2", "e3" }
    /\ Amounts = { 0, 1, 2 }
    /\ MaxBalance = 4

\* @type: (Str, Str, Int, Str, Bool) => { kind: Str, account: Str, amount: Int, event: Str, verified: Bool };
Entry(k, acct, amt, ev, ver) ==
    [kind |-> k, account |-> acct, amount |-> amt, event |-> ev, verified |-> ver]

TypeOK ==
    /\ balance \in [Accounts -> 0..MaxBalance]
    /\ locked \in [Accounts -> 0..MaxBalance]
    /\ processed \subseteq EventIds

Init ==
    /\ balance = [a \in Accounts |-> 0]
    /\ locked = [a \in Accounts |-> 0]
    /\ processed = {}
    /\ history = <<>>

\* Mint. Transcribed from msgServer.Mint; guards are amount valid, event id
\* non-empty, event id unprocessed. No verification step exists in the handler,
\* so `verified` is FALSE.
Mint(receiver, amt, ev) ==
    /\ ev \in EventIds
    /\ ev \notin processed
    /\ amt \in Amounts
    /\ balance[receiver] + amt <= MaxBalance
    /\ processed' = processed \cup {ev}
    /\ balance' = [balance EXCEPT ![receiver] = @ + amt]
    /\ history' = Append(history, Entry("mint", receiver, amt, ev, FALSE))
    /\ UNCHANGED locked

\* SetBalance. Transcribed from msgServer.SetBalance: validates the amount and
\* nothing else. Consumes no event id, so it never exhausts.
SetBalance(acct, amt) ==
    /\ amt \in Amounts
    /\ amt <= MaxBalance
    /\ balance' = [balance EXCEPT ![acct] = amt]
    /\ history' = Append(history, Entry("setbalance", acct, amt, "none", FALSE))
    /\ UNCHANGED <<locked, processed>>

\* Lock. Transcribed from msgServer.Lock.
Lock(acct, amt) ==
    /\ amt \in Amounts
    /\ balance[acct] >= amt
    /\ locked[acct] + amt <= MaxBalance
    /\ balance' = [balance EXCEPT ![acct] = @ - amt]
    /\ locked' = [locked EXCEPT ![acct] = @ + amt]
    /\ history' = Append(history, Entry("lock", acct, amt, "none", FALSE))
    /\ UNCHANGED processed

Next ==
    \/ \E r \in Accounts, a \in Amounts, e \in EventIds : Mint(r, a, e)
    \/ \E c \in Accounts, a \in Amounts : SetBalance(c, a)
    \/ \E c \in Accounts, a \in Amounts : Lock(c, a)

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(*                            SAFETY PROPERTIES                            *)
(***************************************************************************)

\* @type: (Str) => Set(Int);
MintsOf(ev) ==
    { i \in DOMAIN history :
        /\ history[i].kind = "mint"
        /\ history[i].event = ev }

NoDoubleMint ==
    \A ev \in EventIds : Cardinality(MintsOf(ev)) <= 1

\* @type: Set(Int);
Credits ==
    { i \in DOMAIN history : history[i].kind \in {"mint", "setbalance"} }

NoUnverifiedMint ==
    \A i \in Credits : history[i].verified = TRUE

NoCreditWithoutEventId ==
    \A i \in Credits : history[i].event \in EventIds

LockConserves ==
    \A a \in Accounts : balance[a] + locked[a] >= 0

=============================================================================
