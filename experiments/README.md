# experiments

Multi-validator integration experiments that exercise the Cosmos
chain end-to-end.

| Sub-directory             | Status               | What it measures                                                                                                  |
|---------------------------|----------------------|-------------------------------------------------------------------------------------------------------------------|
| `validator_scaling_v2/`   | **Headline** (paper) | 30-cell sweep across N ∈ {4, 7, 16} validators × target tx-rate ∈ {10, 50, 100, 200, 500} × scheme ∈ {secp256k1, mldsa44}. Produces Figs. 9-12 of the paper. |
| `cold_sync/`              | Scaffolded, not run  | Block-sync replay time on a fresh full node — see the explicit "scaffolded, not yet run" notice in its README.    |

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

## `cold_sync/`

Cold-sync was scaffolded — `run_cold_sync.py` and `aggregate.py` are
present and the methodology is documented — but **no data was
collected**. The `results/` directory is empty. The paper does not
include cold-sync results. The directory is committed so the
methodology can be reviewed and the experiment reproduced on demand.
The README in that directory leads with this status note.

## What lives where

- **Raw run data**: `validator_scaling_v2/results/*.json`
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
