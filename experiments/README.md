# experiments

Integration experiments that exercise the bridge between the Cosmos chain and
Ethereum end-to-end, on a live devnet.

| Sub-directory             | Direction        | Status               | What it measures                                                                                                  |
|---------------------------|------------------|----------------------|-------------------------------------------------------------------------------------------------------------------|
| `migration_cost/`       | **EVM → Cosmos** | **Current** (paper)  | The migration direction the paper is about. Many independent users each escrow an ERC-20 on Ethereum and are credited a voucher on Cosmos. Varies batch size and the signing key type of the delivery transaction. Real BLS + MPT verification on the measured leg. |
| `migration_throughput/`   | Cosmos → EVM → Cosmos (round trip of `stake`) | **Complete** (paper) | Batching on the live bridge: transfers acknowledged per finality window across N ∈ {1, 5, 10, 20, 40} packets offered per window, 5 repeats each. 1,000 transfers, 0 failures. |
| `batch_scaling/`          | Cosmos → EVM     | Superseded           | Forward-leg batching against `SP1MockVerifier`: transfer-mechanism scaling (time, throughput, gas) as group size grows across {1, 10, 50, 100, 250, 500}, stopping automatically at the first group size that fails. |

**Direction is the axis to check first.** `migration_cost/` and
`batch_scaling/` are opposite directions of the same bridge and are not
symmetric: they are finality-bound on opposite legs, prove with different
machinery, batch through different primitives, and hit different size walls.
The paper's migration claims are about **Ethereum → Cosmos**, which is
`migration_cost/`.

Each experiment's own README states its method, bounds and limitations:
[migration_cost](migration_cost/README.md) ·
[migration_throughput](migration_throughput/README.md) ·
[batch_scaling](batch_scaling/README.md).

## `migration_cost/`

**Ethereum → Cosmos**, the direction the paper's migration claims are about.
Many independent users each escrow an ERC-20 on Ethereum and are credited a
voucher on Cosmos. Every user signs and pays for their own `sendTransfer`; the
whole group is then proven to Cosmos in one batched delivery — one
`eth_getProof` with N storage keys, one `MsgUpdateClient`, one Cosmos
transaction of N `MsgRecvPacket`. The measured leg carries real
`cw-ics08-wasm-eth` BLS and Merkle-Patricia verification; only the
acknowledgement uses proof-api.

Two variables: how many transfers move at once, and the signing key type of the
delivery transaction. A signature is charged once per transaction while
transfers are charged per transfer, so ML-DSA-65 adds a fixed ~146,000 gas and
~5.2 KB per transaction — a share per transfer that falls from +82 % at one
transfer to +0.8 % at 124. Time is unaffected: ~95 % of a migration is waiting
for Ethereum finality, which is the same at every batch size.

Capacity is set by infrastructure, not by the signature algorithm. Delivery
holds ~675 transfers per Cosmos transaction, or 124 at a stock RPC
configuration, and a delivery too large simply splits. The **acknowledgement**
is the binding limit at **~56 transfers**: it is walled by geth's 128 KB
`txMaxSize`, and it cannot be split, because the proof covering the batch is
cached only for the duration of its own transaction. A delivery larger than
that can never be acknowledged. See `migration_cost/LIMITS.md`.

Runner, plotter and results live in the experiment directory. Needs a live
devnet.

## `migration_throughput/`

**Direction: a round trip of Cosmos-native `stake`.** Each transfer goes out
from Cosmos to Ethereum, and the leg measured is its acknowledgement coming
back to Cosmos. It is not the Ethereum-native migration measured in
`migration_cost/`.

Sustained ICS-20 throughput on the **live** bridge — ML-DSA-65 accounts on
Cosmos, packets verified by the real `cw-ics08-wasm-eth` light client against a
Kurtosis Ethereum devnet, with no shortcut around finality.

The headline is *transfers acknowledged per finality window*, swept against N,
the number of packets offered into one window. Scaling is exactly 1:1 across
N = 1 → 40 with zero variance, so amortised gas per transfer falls from 929,688
(339 % of the per-packet marginal cost) to 23,242 (8 %) — the
`MsgUpdateClient`-per-window versus `MsgAcknowledgement`-per-packet asymmetry,
measured directly.

No batching ceiling was found within the tested range. Its README explains why
the round trip is measured rather than the forward leg (that run used the
forward leg on a mock verifier; real SP1 Groth16 proving now works, but at
~10 min per proof it is impractical for a 1,000-transfer run), why the
independent variable is packets-per-window rather than submission rate, and
where the harness's own limits lie.

`results/rate_sweep/` holds a second, independent result: latency is a property
of the finality window, not of the packet.

Re-running it needs a live devnet — a Kurtosis Ethereum enclave plus a running
Cosmos chain with an instantiated light client. Its committed results
regenerate its tables without one.

## `batch_scaling/`

**Superseded — Cosmos → EVM, the opposite direction to the paper's migration
claims.** The code is retained and still runnable, and its results
stand as measurements of that direction, but nothing in the current paper
depends on it. For the migration direction, use `migration_cost/`.

Measures the **forward** leg's batching (Cosmos → EVM) rather than the return
leg, at larger group sizes,
against `SP1MockVerifier` so real Groth16 proving time (~10 min/proof,
measured separately) does not confound the result. A group of transfers is
relayed as one `proof-api` request and one on-chain multicall, so the whole
group shares a single light-client update.

Checks before running that the EVM-side light client is actually bound to the
mock verifier, and fails clearly rather than silently measuring proving time
if the real verifier is bound instead. Group sizes are attempted in ascending
order and the run stops automatically at the first size that fails a real
limit (gas, timeout, revert) — that failure is recorded as data, not retried.
Reusable tooling: no data is committed (see its own `.gitignore`); re-run
against a live devnet to reproduce.

## What lives where

- **Committed raw data**: `migration_cost/results/`, written by
  `measure_data.py`, `measure_delivery.py`, `measure_ack.py`,
  `measure_throughput.py` and `find_recv_ceiling.py`; and
  `migration_throughput/results/`
- **Not committed, regenerated against a devnet**: `batch_scaling/results/`
- **migration_cost figures and cost table**: in `migration_cost/` itself,
  written by `plot_data.py` — see [REPRODUCE.md](../REPRODUCE.md#3-regenerate-the-figures)

---

[Project README](../README.md) · [REPRODUCE](../REPRODUCE.md) · [Architecture](../docs/architecture.md)
