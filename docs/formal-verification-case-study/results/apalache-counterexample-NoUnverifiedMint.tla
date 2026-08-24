---------------------------- MODULE counterexample ----------------------------

EXTENDS LockAndMintApalache

(* Constant initialization state *)
ConstInit ==
  Accounts = { "a1", "a2" }
    /\ Amounts = { 0, 1, 2 }
    /\ EventIds = { "e1", "e2" }
    /\ MaxBalance = 4

(* Initial state [_transition(0)] *)
State0 ==
  Accounts = { "a1", "a2" }
    /\ Amounts = { 0, 1, 2 }
    /\ EventIds = { "e1", "e2" }
    /\ MaxBalance = 4
    /\ balance = SetAsFun({ <<"a1", 0>>, <<"a2", 0>> })
    /\ history = <<>>
    /\ locked = SetAsFun({ <<"a1", 0>>, <<"a2", 0>> })
    /\ processed = {}

(* State1 [_transition(0)] *)
State1 ==
  Accounts = { "a1", "a2" }
    /\ Amounts = { 0, 1, 2 }
    /\ EventIds = { "e1", "e2" }
    /\ MaxBalance = 4
    /\ balance = SetAsFun({ <<"a1", 1>>, <<"a2", 0>> })
    /\ history
      = <<
        [account |-> "a1",
          amount |-> 1,
          event |-> "e1",
          kind |-> "mint",
          verified |-> FALSE]
      >>
    /\ locked = SetAsFun({ <<"a1", 0>>, <<"a2", 0>> })
    /\ processed = {"e1"}

(* The following formula holds true in the last state and violates the invariant *)
InvariantViolation ==
  Skolem((\E i_4 \in DOMAIN history:
    history[i_4]["kind"] \in { "mint", "setbalance" }
      /\ ~(history[i_4]["verified"] = TRUE)))

================================================================================
(* Created by Apalache on Mon Aug 24 14:04:19 IST 2026 *)
(* https://github.com/apalache-mc/apalache *)
