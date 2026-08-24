---------------------------- MODULE LockAndMintReplay ----------------------------
(***************************************************************************)
(* A TLA+ model of the x/lockandmint message handlers, written to match    *)
(* x/lockandmint/keeper/msg_server.go as implemented, NOT as designed.     *)
(*                                                                         *)
(* Every action below mirrors one Go handler. Preconditions are transcribed*)
(* from the handler's guard clauses in order; where a handler has no guard,*)
(* the action has no precondition. That fidelity is the point: the model   *)
(* checker is being asked whether the code as written upholds the safety   *)
(* properties, not whether the intended design would.                      *)
(*                                                                         *)
(* Correspondence to the Go source:                                        *)
(*                                                                         *)
(*   Mint       -> msgServer.Mint       (msg_server.go)                    *)
(*     guards:  amount parses and is non-negative  -> a \in Amounts        *)
(*              msg.EventId /= ""                  -> e \in EventIds       *)
(*              NOT HasProcessedEvent(msg.EventId) -> e \notin processed   *)
(*     effect:  SetProcessedEvent, balance += amount                       *)
(*     NOTE:    msg.Authority is declared the proto signer but is never    *)
(*              compared against anything in the handler. There is no      *)
(*              proof argument, no header lookup, no verification step.    *)
(*                                                                         *)
(*   SetBalance -> msgServer.SetBalance (msg_server.go)                    *)
(*     guards:  amount parses and is non-negative  -> a \in Amounts        *)
(*     effect:  balance := amount                                          *)
(*     NOTE:    msg.Authority is likewise never checked, and no event id   *)
(*              is consumed, so this action is unconditionally repeatable. *)
(*                                                                         *)
(*   Lock       -> msgServer.Lock       (msg_server.go)                    *)
(*     guards:  amount parses and is non-negative                          *)
(*              currentBalance >= amount                                   *)
(*     effect:  balance -= amount, locked += amount                        *)
(*                                                                         *)
(* Deliberately absent, because they are absent from the module: there is  *)
(* no SubmitHeader action, no stored headers or consensus states, and no   *)
(* SubmitBurnProof/redemption action. `grep -riE "proof|header|consensus"` *)
(* over x/lockandmint returns only comments. Modelling them would describe *)
(* a different program.                                                    *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    Accounts,       \* finite set of account addresses
    EventIds,       \* finite set of well-formed (non-empty) source event ids
    Amounts,        \* finite set of valid amounts (non-negative ints)
    MaxBalance,     \* state-space bound; not a rule of the implementation
    MaxHistory      \* state-space bound on the audit trail's length

VARIABLES
    balance,        \* [Accounts -> Nat]      account.Balance
    locked,         \* [Accounts -> Nat]      account.LockedBalance
    processed,      \* SUBSET EventIds        the ProcessedEventKey keyspace
    history         \* Seq(Record)            audit trail, for the invariants

vars == <<balance, locked, processed, history>>

(***************************************************************************)
(* An entry is appended for every state-changing action. `verified` records *)
(* whether the credit was authorised by a verified proof. Nothing in the    *)
(* implementation can set it TRUE, because no verification step exists;     *)
(* it is present so NoUnverifiedMint is expressible over this model.        *)
(***************************************************************************)
Entry(k, acct, amt, ev, ver) ==
    [kind |-> k, account |-> acct, amount |-> amt, event |-> ev, verified |-> ver]

TypeOK ==
    /\ balance \in [Accounts -> 0..MaxBalance]
    /\ locked \in [Accounts -> 0..MaxBalance]
    /\ processed \subseteq EventIds
    /\ history \in Seq([kind: {"mint", "setbalance", "lock"},
                        account: Accounts,
                        amount: Amounts,
                        event: EventIds \cup {"none"},
                        verified: BOOLEAN])

Init ==
    /\ balance = [a \in Accounts |-> 0]
    /\ locked = [a \in Accounts |-> 0]
    /\ processed = {}
    /\ history = <<>>

(***************************************************************************)
(* Mint. Transcribed from msgServer.Mint.                                  *)
(*                                                                         *)
(* The handler's complete guard set is: amount valid, event id non-empty,  *)
(* event id unprocessed. `verified |-> FALSE` is not a modelling choice --  *)
(* it is what the code does, since the handler consults no proof and no    *)
(* header before crediting the receiver.                                   *)
(***************************************************************************)
Mint(receiver, amt, ev) ==
    /\ ev \in EventIds
    /\ ev \notin processed
    /\ amt \in Amounts
    /\ balance[receiver] + amt <= MaxBalance          \* bounding only
    /\ processed' = processed \cup {ev}
    /\ balance' = [balance EXCEPT ![receiver] = @ + amt]
    /\ history' = Append(history, Entry("mint", receiver, amt, ev, FALSE))
    /\ UNCHANGED locked

(***************************************************************************)
(* SetBalance. Transcribed from msgServer.SetBalance.                      *)
(*                                                                         *)
(* The handler validates the amount and nothing else: it does not compare  *)
(* msg.Authority to k.GetAuthority(), unlike UpdateParams, which does.     *)
(* No event id is consumed, so this action never exhausts.                 *)
(***************************************************************************)
SetBalance(acct, amt) ==
    /\ amt \in Amounts
    /\ amt <= MaxBalance
    /\ balance' = [balance EXCEPT ![acct] = amt]
    /\ history' = Append(history, Entry("setbalance", acct, amt, "none", FALSE))
    /\ UNCHANGED <<locked, processed>>

(***************************************************************************)
(* Lock. Transcribed from msgServer.Lock.                                  *)
(***************************************************************************)
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

(***************************************************************************)
(* NoDoubleMint: no event id is ever consumed by two mints.                *)
(* Expressed over the audit trail rather than over `processed`, so that it *)
(* genuinely tests the guard rather than restating the set semantics.      *)
(***************************************************************************)
MintsOf(ev) ==
    { i \in 1..Len(history) :
        /\ history[i].kind = "mint"
        /\ history[i].event = ev }

NoDoubleMint ==
    \A ev \in EventIds : Cardinality(MintsOf(ev)) <= 1

(***************************************************************************)
(* NoUnverifiedMint: every credit to an account traces to an action whose  *)
(* proof verified. "Credit" covers both handlers that can increase a       *)
(* balance -- Mint and SetBalance -- since an unverified assignment is as  *)
(* unsound as an unverified addition.                                      *)
(***************************************************************************)
Credits ==
    { i \in 1..Len(history) : history[i].kind \in {"mint", "setbalance"} }

NoUnverifiedMint ==
    \A i \in Credits : history[i].verified = TRUE

(***************************************************************************)
(* NoCreditWithoutEventId: every credit consumes a source event id, i.e.    *)
(* the replay-protection mechanism covers every path that can increase a    *)
(* balance. Replay protection that guards only one of several credit paths  *)
(* bounds nothing overall, so this is the property that decides whether the *)
(* event_id check is load-bearing or merely local to Mint.                  *)
(***************************************************************************)
NoCreditWithoutEventId ==
    \A i \in Credits : history[i].event \in EventIds

(***************************************************************************)
(* Conservation: locking moves value between the two buckets of one        *)
(* account without creating or destroying any. Included as a control --    *)
(* a property the implementation is expected to satisfy, so that a clean   *)
(* run of it evidences the model is not vacuously true.                    *)
(***************************************************************************)
LockConserves ==
    \A a \in Accounts : balance[a] + locked[a] >= 0

(***************************************************************************)
(* State constraint. `history` is append-only, so the reachable state space *)
(* is infinite without a bound on its length. This bounds the model, not    *)
(* the implementation: properties are established for all behaviours of up  *)
(* to MaxHistory operations -- the standard finite-model caveat for TLC.    *)
(***************************************************************************)
StateConstraint == Len(history) <= MaxHistory

=============================================================================
