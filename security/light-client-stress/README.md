# Light-client stress tests

Adversarial submissions against the **live** Ethereum light client
(`cw-ics08-wasm-eth`, running as `08-wasm-3` inside 08-wasm) on a Kurtosis
devnet. Unlike [`../`](../), which is a Go fuzz harness that runs in CI, these
require a live devnet and are run by hand.

Every adversarial submission was **rejected**; every control **succeeded**.

## Method: tamper one field at a time

Each mode builds a genuine `MsgUpdateClient` from the live beacon chain and
corrupts exactly one field. That is the whole design: a harness that corrupts
everything at once cannot distinguish *"the client checked the thing I broke"*
from *"the client rejects malformed input generically"*.

Critically, the BLS sync-committee signature stays **genuinely valid** in every
mode. A pass therefore means the client verifies the binding between the signed
beacon header and the execution payload — not merely that it checks a
signature.

```bash
python3 adversarial_update.py baseline       # control: must SUCCEED
python3 adversarial_update.py state_root
python3 adversarial_update.py block_number
python3 adversarial_update.py nonfinal
```

Configuration resolves through [`../../devnet/lib/config.py`](../../devnet/lib/config.py),
the same precedence chain the devnet scripts use. Nothing is hardcoded.

## Results

### Adversarial header / consensus-state submission — PASS

**Mismatched state root** (valid signatures, wrong root) → **rejected**

```
tampered : 0xdededede…dede
actual   : 0xa1cdbd85fea6a05cc47f609bbd2aa1fc4a149e77d60234a13ec38084f007080d

code 14: verify client message failed: invalid merkle branch
  leaf  0x0cc38045444693ca44f82efcf580e8978d740eaa2854564c16fd34204186d282
  root  0x60615025d1e0ec55059bec1a9156705072c3dbc5313d99c2431944c9f6131418
  found 0x154bf3cb15a85c2e716a7e6844997ed54b8f26c16ecb50ace44f33cdebd5ce73
```

**Block number not matching the synced chain** → **rejected**

```
tampered : 107831        actual chain: 7831

code 14: verify client message failed: invalid merkle branch
  leaf  0x1912348da81d06a3ee3747bae3c3ec4cddb8781c339874e9c0ae44c8829e6201
```

**The two leaf hashes differ** (`0x0cc38045…` against `0x1912348d…`) while the
branch and root are identical. That is the evidence each tampered field was
independently detected, rather than both hitting one generic "malformed input"
path or a cached rejection.

Both are caught by `execution_branch` verification: the signature over the
beacon header is valid, but the execution payload fields do not hash into the
signed body root. Signature validity alone buys an attacker nothing.

### Finality-depth check — PASS

**Proof against a non-finalized block** → **rejected**

```
claimed finalized : slot 7917 (execution block 7908)
real finality at  : slot 7840

code 14: (update_signature_slot > update_attested_slot >= update_finalized_slot)
         must hold, found: (7917 > 7916 >= 7917)
```

The client names the exact ordering invariant: a block cannot be claimed
finalized when it is *newer* than the header the sync committee attested to.

**The same slot, once genuinely finalized** → **accepted**

```
finality advanced : epoch 245 (slot 7840) → epoch 248 (slot 7936), covering 7917
legitimate update : code 0, gas 929,751
client height     : 7840 → 7936        (now past the previously-rejected 7917)
```

### Controls

A `baseline` run before the adversarial modes succeeded (code 0, **929,771
gas**), and another after them succeeded (code 0, **929,751 gas**), advancing
the client both times. The client was therefore demonstrably functional
throughout: the rejections are **discrimination, not breakage** — which is the
one thing a "everything was rejected" result must rule out.

The gas figures also match the ~929,688 recorded for `MsgUpdateClient`
elsewhere in this repository, confirming the BLS verification of all 512
sync-committee keys actually ran.

## Limitation: no genuine reorg test

The finality test above submits a **non-finalized header directly**. It does
**not** produce an orphaned block by forking the chain.

**This devnet topology cannot produce a fork.** It runs one geth execution
client, one Lighthouse beacon node, and a single validator client holding
**all 128 validators**. There is no second EL/CL pair to build a competing
chain on, and attestations cannot be withheld from a *subset* of validators —
stopping the validator client halts finality entirely rather than orphaning a
specific block.

What is tested is the security property a reorg scenario targets: **a proof
against a non-finalized block must be rejected by the finality-depth check.**
An orphaned block is one way to obtain a non-finalized block, and the check
does not distinguish how a block failed to finalize. But that is a
substitution, and it is named here rather than presented as a completed reorg
test.

A genuine reorg test needs a multi-node devnet with a partitionable validator
set. That has not been done.

## Operational note: sync-committee period boundaries

The harness builds each update from a beacon `light_client/bootstrap` at the
current finalized checkpoint. A beacon node only serves a bootstrap for a
sync-committee period it still holds, so once a devnet runs past a period
boundary (256 epochs) the endpoint returns:

```
404 NOT_FOUND: Sync committee for period N not found
```

At that point neither this harness nor `devnet/relayer/update-eth-client.py`
can advance the client, because both null out `next_sync_committee` and so
cannot carry it across the transition. The harness exits with that explanation
rather than a stack trace.

**This is a relayer-tooling gap, not a light-client defect.**
`cw-ics08-wasm-eth` supports sync-committee period transitions; the relayer
simply does not supply `next_sync_committee` in the update it builds, so the
client cannot be carried across a period boundary. Nothing about the client's
verification logic is implicated, and none of the results above are affected —
they were collected while the devnet was within period 0.

**Implication for production.** A deployment would need this addressed to
operate continuously beyond a single sync-committee period — **256 epochs,
which is ~27 hours on mainnet** (256 x 32 slots x 12 s). Past that point an
un-extended relayer can no longer advance the client, and the bridge stalls
until it is rebuilt from a fresh bootstrap.

**Scoped work item.** Carry `next_sync_committee` and
`next_sync_committee_branch` through the update path, rather than nulling them
as `devnet/relayer/update-eth-client.py` does today, and select the bootstrap
for the period being updated *into* rather than the current finalized
checkpoint. This is a bounded change to the relayer with no contract-side work,
and it is a prerequisite for any long-running deployment — not an open research
question.

Re-running these tests on an aged devnet therefore requires either that change
or a devnet rebuild.

## Scope

- **One client implementation.** These test `cw-ics08-wasm-eth`. The EVM-side
  `SP1ICS07Tendermint` is not exercised here. It can now be bound to the **real**
  SP1 Groth16 verifier rather than the mock — that path has its own adversarial
  evidence, recorded in [`../../devnet/README.md`](../../devnet/README.md) — but
  these tests do not touch it either way.
- **Run by hand, not in CI.** The harness drives a live devnet, so these are not
  part of the automated suite and are not required for
  [reproduction](../../REPRODUCE.md).
- **Not re-run since the EVM client moved to the real Groth16 verifier.** That
  change did not touch `cw-ics08-wasm-eth`, so these results are expected to
  hold unchanged, but they have not been re-executed. Doing so needs a **full
  devnet rebuild**, not a restart: the Kurtosis enclave's execution layer does
  not persist (see
  [devnet/README.md](../../devnet/README.md#the-evm-devnet-does-not-survive-a-restart)),
  so contracts must be redeployed and the light client recreated first. Best
  bundled with a rebuild done for another reason.
- **Rejection, not exhaustiveness.** These confirm that four specific
  adversarial inputs are rejected. They do not establish that no adversarial
  input is accepted.
- **Devnet, not mainnet.** A 128-validator devnet sync committee is far smaller
  than mainnet's 512-of-a-much-larger-set.

---

[Project README](../../README.md) · [Testing](../../docs/testing.md) · [Architecture](../../docs/architecture.md)
