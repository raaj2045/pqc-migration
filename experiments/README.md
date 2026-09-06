# experiments

Multi-validator integration experiments that exercise the Cosmos
chain end-to-end.

| Sub-directory             | Direction        | Status               | What it measures                                                                                                  |
|---------------------------|------------------|----------------------|-------------------------------------------------------------------------------------------------------------------|
| `validator_scaling_v2/`   | Cosmos only      | **Headline** (paper) | 30-cell sweep across N ∈ {4, 7, 16} validators × target tx-rate ∈ {10, 50, 100, 200, 500} × scheme ∈ {secp256k1, mldsa44}. Produces Figs. 9-12 of the paper. |
| `migration_volume/`       | **EVM → Cosmos** | **Current** (paper)  | The migration direction the paper is about. N independent users each escrow an ERC-20 on Ethereum and are credited a voucher on Cosmos, swept over N ∈ {1, 10, 50, 100} × signer key type ∈ {secp256k1, ML-DSA-65}. Real BLS + MPT verification on the measured leg. |
| `migration_throughput/`   | EVM → Cosmos     | **Complete** (paper) | Batching on the live bridge: transfers acknowledged per finality window across N ∈ {1, 5, 10, 20, 40} packets offered per window, 5 repeats each. 1,000 transfers, 0 failures. |
| `batch_scaling/`          | Cosmos → EVM     | Superseded           | Forward-leg batching against `SP1MockVerifier`: transfer-mechanism scaling (time, throughput, gas) as group size grows across {1, 10, 50, 100, 250, 500}, stopping automatically at the first group size that fails. |
| `cold_sync/`              | Cosmos only      | Scaffolded, not run  | Block-sync replay time on a fresh full node — see the explicit "scaffolded, not yet run" notice in its README.    |

**Direction is the axis to check first.** `migration_volume/` and
`batch_scaling/` are opposite directions of the same bridge and are not
symmetric: they are finality-bound on opposite legs, prove with different
machinery, batch through different primitives, and hit different size walls.
The paper's migration claims are about **Ethereum → Cosmos**, which is
`migration_volume/`.

Each experiment's own README states its method, bounds and limitations:
[validator_scaling_v2](validator_scaling_v2/summary.md) ·
[migration_volume](migration_volume/README.md) ·
[migration_throughput](migration_throughput/README.md) ·
[batch_scaling](batch_scaling/README.md) ·
[cold_sync](cold_sync/README.md).

## `validator_scaling_v2/`

This is the experiment the paper uses for its headline scaling
claims. The 30 result JSONs are committed under `results/`; running
`python3 aggregate.py` regenerates the four figures and `summary.md`
in seconds.

The sweep classifies each cell as `ok` (committed/submitted ≥ 0.9
**and** p99 < 10 s) or `saturated`. There are no "crashed" cells in
the committed dataset.

**Important methodological note.** This fork's ML-DSA-44 support
applies to user-account transaction signing only. Validator consensus
keys remain ed25519 in every cell. The scheme axis varies the
signature algorithm of the loadgen senders, **not** the consensus
algorithm. See `validator_scaling_v2/summary.md` for the full
discussion.

Reproduction: see [`../REPRODUCE.md`](../REPRODUCE.md) §1 (Path A for
the figures from existing data, Path B for the full ~5-hour sweep).

## `migration_volume/`

**Ethereum → Cosmos.** N independent users migrate at the same time: each signs
and pays for its own `sendTransfer` on the EVM, and the whole cohort is proven
to Cosmos in one batched receive transaction (one `eth_getProof` with N storage
keys, one `MsgUpdateClient`, one Cosmos tx of N `MsgRecvPacket`). The measured
leg carries real `cw-ics08-wasm-eth` BLS and MPT verification; only the ack leg
touches proof-api.

The headline is `credited` — the timestamp of the Cosmos block that mints the
vouchers — decomposed into four measured spans plus an explicitly-named
unattributed residual. The finality wait dominates and is measured directly.

The second variable is the **signer key type** of the receive transaction. A
signature is charged once per transaction while packets are charged per packet,
so ML-DSA-65 costs a fixed ~146,000 gas and ~5.2 KB per transaction that
batching amortizes to +0.8 % at N = 124.

Capacity on this path is infrastructure-bound, not crypto-bound. The binding
wall at stock node configuration is CometBFT's RPC `max_body_bytes`, which caps
a receive transaction at **124 packets** — bound by transaction body size, not
by payload size or key type. Raising it exposes the 4 MB mempool `max_tx_bytes`
wall at 675 packets. ML-DSA-65 and secp256k1 carry the identical count at both.
See `migration_volume/CEILING-FINDINGS.md`.

Sweep driver and plotter are at the repository root (`measure_data.py`,
`plot_data.py`); everything else lives in the experiment directory. Needs a
live devnet.

## `migration_throughput/`

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
the round trip is measured rather than the forward leg (that sweep ran the
forward leg on a mock verifier; real SP1 Groth16 proving now works, but at
~10 min per proof it is impractical for a 1,000-transfer sweep), why the
independent variable is packets-per-window rather than submission rate, and
where the harness's own limits lie.

`results/rate_sweep/` holds a second, independent result: latency is a property
of the finality window, not of the packet.

Unlike `validator_scaling_v2/`, this experiment needs a live devnet — a
Kurtosis Ethereum enclave plus a running Cosmos chain with an instantiated
light client — so it cannot be re-run from committed data alone.

## `batch_scaling/`

**Superseded — Cosmos → EVM, the opposite direction to the paper's migration
claims.** The code is retained and still runnable, and its committed results
stand as measurements of that direction, but nothing in the current paper
depends on it. For the migration direction, use `migration_volume/`.

Measures the **forward** leg's batching (Cosmos → EVM) rather than the return
leg, at larger group sizes,
against `SP1MockVerifier` so real Groth16 proving time (~10 min/proof,
measured separately) does not confound the result. A group of transfers is
relayed as one `proof-api` request and one on-chain multicall, so the whole
group shares a single light-client update.

Checks before running that the EVM-side light client is actually bound to the
mock verifier, and fails clearly rather than silently measuring proving time
if the real verifier is bound instead. Group sizes are attempted in ascending
order and the sweep stops automatically at the first size that fails a real
limit (gas, timeout, revert) — that failure is recorded as data, not retried.
Reusable tooling: no data is committed (see its own `.gitignore`); re-run
against a live devnet to reproduce.

## `cold_sync/`

Cold-sync was scaffolded — `run_cold_sync.py` and `aggregate.py` are
present and the methodology is documented — but **no data was
collected**. The `results/` directory is empty. The paper does not
include cold-sync results. The directory is committed so the
methodology can be reviewed and the experiment reproduced on demand.
The README in that directory leads with this status note.

## What lives where

- **Raw run data**: `validator_scaling_v2/results/*.json`,
  `migration_throughput/results/*.json`,
  `migration_volume/results/*.json` (ceiling measurements)
- **migration_volume sweep output**: `migration_metrics_detailed.csv` at the
  repository root, written by `measure_data.py`
- **Per-cell CPU timeseries**: `validator_scaling_v2/cpu_samples/`
- **Per-cell sweep logs**: `validator_scaling_v2/logs/`
- **Sweep state for resume**: `validator_scaling_v2/sweep_state.json`
- **Aggregator + plotter**: `validator_scaling_v2/aggregate.py`
- **Sweep orchestrator**: `validator_scaling_v2/run_sweep.py`
- **Per-cell integrity verifier**: `validator_scaling_v2/verify.py`
  (checks 5 per-file invariants across the committed result JSONs)
- **Pre-signed pools**: not committed (1.1 GB). Regenerate via
  `tools/presigner/`. See REPRODUCE.md §1, Path B.

## Hardware

All `validator_scaling_v2/` data was collected on a single AMD
Ryzen 5 7600X under WSL2 Linux 5.15, Go 1.23.5, with 4-16 Cosmos SDK
validators in Docker containers each capped at 1 CPU. Single 300 s
run per (N, target rate, scheme) cell.

---

[Project README](../README.md) · [REPRODUCE](../REPRODUCE.md) · [Architecture](../docs/architecture.md)
