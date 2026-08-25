-------------------------- MODULE LivePathApalache --------------------------
(***************************************************************************)
(* ICS-20 transfer over IBC v2 (Eureka), as this chain actually runs it.   *)
(*                                                                         *)
(* Chain A is the sender (escrows), chain B is the receiver (mints         *)
(* vouchers). B tracks A through a light client whose consensus            *)
(* verification is a TRUST BOUNDARY: UpdateClient's precondition "the      *)
(* submitted consensus state is valid" is AXIOMATIC here, not modelled.    *)
(* That is the light client's own BLS / zk verification, and modelling it  *)
(* would be modelling a different system.                                  *)
(*                                                                         *)
(* What IS modelled is everything downstream of that boundary: packet      *)
(* commitments, packet receipts, escrow and voucher balances, and the      *)
(* order in which recvPacket checks them.                                  *)
(*                                                                         *)
(* Preconditions are transcribed from:                                     *)
(*   modules/core/04-channel/v2/keeper/packet.go     recvPacket            *)
(*   modules/core/04-channel/v2/keeper/msg_server.go RecvPacket            *)
(*   modules/apps/transfer/keeper/relay.go           OnRecvPacket          *)
(***************************************************************************)

EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    \* @type: Set(Int);
    Seqs,        \* packet sequence numbers
    \* @type: Set(Int);
    Heights,     \* consensus-state heights
    \* @type: Int;
    Amount,      \* every transfer moves this many units
    \* @type: Int;
    InitialA,    \* chain A's user starts with this many units
    \* @type: Int;
    MaxHeight    \* APALACHE COMPAT: replaces Cardinality(Heights) in Advance/TypeOK

VARIABLES
    \* ---- chain A (source) ----
    \* @type: Int;
    balA,
    \* @type: Int;
    escrow,
    \* @type: Int -> Bool;
    commitments,
    \* @type: Int -> Set(Int);
    committedAt,
    \* @type: Set(Int);
    ackedA,

    \* ---- chain B (destination) ----
    \* @type: Int;
    vouchers,
    \* @type: Set(Int);
    receipts,
    \* @type: Set(Int);
    trusted,
    \* @type: Set(Int);
    credited,

    \* ---- height ----
    \* @type: Int;
    height

vars == <<balA, escrow, commitments, committedAt, ackedA,
          vouchers, receipts, trusted, credited, height>>

-----------------------------------------------------------------------------

Init ==
    /\ balA        = InitialA
    /\ escrow      = 0
    /\ commitments = [s \in Seqs |-> FALSE]
    /\ committedAt = [h \in Heights |-> {}]
    /\ ackedA      = {}
    /\ vouchers    = 0
    /\ receipts    = {}
    /\ trusted     = {}
    /\ credited    = {}
    /\ height      = 1

-----------------------------------------------------------------------------
(* Chain A: escrow and commit a packet.                                    *)
(* transfer/keeper/relay.go sendTransfer escrows, then core commits.       *)

SendTransfer(s) ==
    /\ ~commitments[s]
    /\ s \notin ackedA
    /\ balA >= Amount
    /\ balA'        = balA - Amount
    /\ escrow'      = escrow + Amount
    /\ commitments' = [commitments EXCEPT ![s] = TRUE]
    /\ UNCHANGED <<committedAt, ackedA, vouchers, receipts, trusted, credited, height>>

(* A produces a new block: the current commitment set becomes observable   *)
(* at the new height. This is what a proof at that height attests to.      *)
Advance ==
    /\ height < MaxHeight
    /\ height'      = height + 1
    /\ committedAt' = [committedAt EXCEPT ![height + 1] =
                          {s \in Seqs : commitments[s]}]
    /\ UNCHANGED <<balA, escrow, commitments, ackedA,
                   vouchers, receipts, trusted, credited>>

-----------------------------------------------------------------------------
(* B's light client gains a trusted consensus state.                       *)
(*                                                                         *)
(* AXIOMATIC: we do not model how validity is established. We model only   *)
(* that a trusted state corresponds to some real height of A. Admitting a  *)
(* state that does NOT correspond to a real height of A is exactly the     *)
(* light client being broken, which is the stated trust boundary.          *)

UpdateClient(h) ==
    /\ h \in Heights
    /\ h <= height          \* corresponds to a height A has actually reached
    /\ h \notin trusted
    /\ trusted' = trusted \cup {h}
    /\ UNCHANGED <<balA, escrow, commitments, committedAt, ackedA,
                   vouchers, receipts, credited, height>>

-----------------------------------------------------------------------------
(* B receives a packet.                                                    *)
(*                                                                         *)
(* Precondition order transcribed from recvPacket:                         *)
(*   1. timeout not elapsed          (not modelled: no clock in this spec) *)
(*   2. receipt NOT already set      -> ErrNoOpMsg                         *)
(*   3. VerifyMembership succeeds against a TRUSTED consensus state        *)
(*   4. only then: SetPacketReceipt                                        *)
(*                                                                         *)
(* The proof abstraction is the heart of the model: verification succeeds  *)
(* exactly when the commitment really is in A's state at a trusted height. *)
(* A relayer may propose ANY (s, h); the check is what rejects forgeries.  *)

ProofVerifies(s, h) ==
    /\ h \in trusted
    /\ s \in committedAt[h]

RecvPacket(s, h) ==
    /\ s \notin receipts            \* replay protection, checked BEFORE verification
    /\ ProofVerifies(s, h)
    /\ receipts'  = receipts \cup {s}
    /\ vouchers'  = vouchers + Amount
    /\ credited'  = credited \cup {s}
    /\ UNCHANGED <<balA, escrow, commitments, committedAt, ackedA,
                   trusted, height>>

(* A relayer submitting a packet that does NOT verify. Models the          *)
(* adversarial relayer: the message is accepted for processing but must    *)
(* change nothing. If this can credit, NoCreditWithoutVerifiedPacket fails.*)
RecvPacketRejected(s, h) ==
    /\ ~ProofVerifies(s, h)
    /\ UNCHANGED vars

-----------------------------------------------------------------------------
(* A processes the acknowledgement: deletes the commitment.                *)

AckPacket(s) ==
    /\ commitments[s]
    /\ s \in receipts               \* B really did receive it
    /\ s \notin ackedA
    /\ commitments' = [commitments EXCEPT ![s] = FALSE]
    /\ ackedA'      = ackedA \cup {s}
    /\ UNCHANGED <<balA, escrow, committedAt, vouchers, receipts,
                   trusted, credited, height>>

-----------------------------------------------------------------------------

Next ==
    \/ \E s \in Seqs : SendTransfer(s)
    \/ Advance
    \/ \E h \in Heights : UpdateClient(h)
    \/ \E s \in Seqs, h \in Heights : RecvPacket(s, h)
    \/ \E s \in Seqs, h \in Heights : RecvPacketRejected(s, h)
    \/ \E s \in Seqs : AckPacket(s)

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
(* Invariants.                                                             *)

TypeOK ==
    /\ balA \in 0..InitialA
    /\ escrow \in 0..InitialA
    /\ vouchers \in 0..InitialA
    /\ receipts \subseteq Seqs
    /\ credited \subseteq Seqs
    /\ trusted \subseteq Heights
    /\ ackedA \subseteq Seqs
    /\ height \in 1..MaxHeight

(* 1. Nothing is credited on B unless a packet commitment for it really    *)
(*    existed on A at a height B trusts.                                   *)
NoCreditWithoutVerifiedPacket ==
    \A s \in credited :
        \E h \in trusted : s \in committedAt[h]

(* 2. A packet is never processed twice: the credited amount is exactly    *)
(*    Amount per distinct credited sequence, never more.                   *)
NoDoublePacketProcessing ==
    /\ vouchers = Cardinality(credited) * Amount
    /\ credited \subseteq receipts

(* 3. Value is conserved across the cycle: everything that left A's user   *)
(*    balance is either sitting in escrow or represented by a voucher on   *)
(*    B, and vouchers never exceed escrow.                                 *)
ConservationAcrossCycle ==
    /\ balA + escrow = InitialA
    /\ vouchers <= escrow

(* Vacuity check: asserts nothing is ever credited. This invariant MUST    *)
(* fail. If it holds, the model never reaches a successful RecvPacket and  *)
(* the passing invariants above are meaningless.                           *)
NeverCredits == credited = {}

(* Vacuity check: asserts no packet is ever acknowledged on A. Must fail.  *)
NeverAcks == ackedA = {}

(* APALACHE COMPAT: constants supplied via ConstInit rather than a .cfg.  *)
ConstInit ==
    /\ Seqs      = {1, 2}
    /\ Heights   = {1, 2, 3}
    /\ Amount    = 10
    /\ InitialA  = 30
    /\ MaxHeight = 3

(* APALACHE COMPAT: reduced bounds for the conservation run, mirroring    *)
(* LivePathConservation.cfg on the TLC side.                              *)
ConstInitConservation ==
    /\ Seqs      = {1, 2}
    /\ Heights   = {1, 2}
    /\ Amount    = 10
    /\ InitialA  = 20
    /\ MaxHeight = 2

=============================================================================
