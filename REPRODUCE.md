# Reproducing the paper figures

Every figure in the paper is generated from raw data committed to this
repository — no network or external services required. Two rough
tracks:

* **Verifying existing results** — runs each figure's plot script
  against the JSON files that ship with the repo. Total wall time:
  about a minute for all six figures. Ideal for a reviewer checking
  that the published numbers match the published figures.
* **Full reproduction from scratch** — rebuilds raw data (Go
  benchmarks, simulators, multi-validator sweep) and then re-renders
  every figure. Total wall time: about 5 hours on a 12-core host,
  most of it in the validator sweep.

The six paper figures are listed in §1. The full-reproduction recipes
are in §2.

## Prerequisites

```
Go         1.23.5   (matches CI)
Node.js    ≥ 20
GNU Make   any recent version (used by cosmos/Makefile)
Docker     any recent version with `docker compose` (full reproduction only)
Python     3.9+
```

The Python figure scripts depend on `matplotlib` and `numpy` only.
Install them once before running any §1 command:

```bash
pip install matplotlib numpy     # or: pip3 / python3 -m pip
```

All commands below are run from the repo root.

---

## §1 — Per-figure reproduction (existing results)

### Figure 4 — Signing latency vs message size

* **File:** `benchmarks/crypto_micro/fig_signing_by_msg_size.pdf`
* **Description:** log-log plot, sign latency in microseconds vs
  message size at 100 B / 1 KiB / 10 KiB / 100 KiB, one line per
  scheme.
* **Prereq:** Python with matplotlib + numpy.
* **Command:**
  ```bash
  cd benchmarks/crypto_micro && python3 plot.py
  ```
* **Runtime:** ~5 s (regenerates all 6 crypto_micro figures from
  `results.json`).
* **Output:** `benchmarks/crypto_micro/fig_signing_by_msg_size.pdf`
  (and 5 other crypto_micro figures alongside).

### Figure 5 — Verification latency vs message size

* **File:** `benchmarks/crypto_micro/fig_verification_by_msg_size.pdf`
* **Description:** companion to Figure 4 — verify latency vs message
  size, one line per scheme. The blue secp256k1 line sits above the
  red ML-DSA-44 line — the only headline metric where ECDSA loses
  outright.
* **Prereq / Command / Runtime:** same as Figure 4 (single
  `plot.py` produces all six).
* **Output:** `benchmarks/crypto_micro/fig_verification_by_msg_size.pdf`

### Figure 6 — Concurrent signing throughput

* **File:** `benchmarks/crypto_micro/fig_concurrent_signing.pdf`
* **Description:** log-y line plot of total throughput vs goroutine
  count {1, 4, 8, 16}. Both lines bend flatward past 8, exposing the
  6-physical-core ceiling.
* **Prereq / Command / Runtime:** same as Figure 4.
* **Output:** `benchmarks/crypto_micro/fig_concurrent_signing.pdf`

### Figure 7 — Block capacity

* **File:** `benchmarks/block_packing/fig_block_capacity.pdf`
* **Description:** grouped bar chart, txs per block at default /
  2× / 4× block-size (4 / 8 / 16 MiB), two bars per group. ML-DSA-44
  bars are ~14× shorter than secp256k1 across all three groups.
* **Prereq:** Python with matplotlib + numpy.
* **Command:**
  ```bash
  cd benchmarks/block_packing && python3 plot.py
  ```
* **Runtime:** ~3 s (reads `results.json`, writes one figure).
* **Output:** `benchmarks/block_packing/fig_block_capacity.pdf`

### Figure 8 — Account-state growth

* **File:** `benchmarks/storage_sim/fig_state_growth.pdf`
* **Description:** log-log plot, on-chain account-state size in MiB
  vs transaction count at 100 k / 1 M / 10 M. The right-edge label
  reads `10.62×` — ML-DSA-44 leaves about an order of magnitude more
  state per account.
* **Prereq:** Python with matplotlib + numpy.
* **Command:**
  ```bash
  cd benchmarks/storage_sim && python3 plot.py
  ```
* **Runtime:** ~3 s (reads six per-scheme/size JSONs, writes one
  figure).
* **Output:** `benchmarks/storage_sim/fig_state_growth.pdf`

### Figure 11 — Commit success rate vs submitted load

* **File:** `experiments/validator_scaling_v2/fig_committed_pct.pdf`
* **Description:** three-panel plot (one per N ∈ {4, 7, 16}) of
  committed÷submitted as a function of target rate, with a 90 %
  threshold reference line. ML-DSA-44 first falls below 90 % at
  rate=50 for N=16 while secp256k1 still passes — one tier earlier.

This figure has **two reproduction paths**:

#### Path A — Verify existing results (fast, no testnet)

Regenerates the figure from the 30 committed result JSONs at
`experiments/validator_scaling_v2/results/`. This is what a reviewer
runs to confirm the figure matches the published numbers.

* **Prereq:** Python with matplotlib + numpy.
* **Command:**
  ```bash
  cd experiments/validator_scaling_v2 && python3 aggregate.py
  ```
* **Runtime:** ~10 s.
* **Outputs:** `fig_saturation_curve.pdf`, `fig_p99_vs_rate.pdf`,
  `fig_committed_pct.pdf`, `fig_peak_cpu.pdf`, plus `summary.md`.

#### Path B — Full reproduction from scratch (slow, multi-hour)

Rebuilds the entire 30-run sweep from a fresh Docker testnet. Reuses
the orchestrator at `experiments/validator_scaling_v2/run_sweep.py`,
which expects a built `simd` binary, pre-built `presigner_bin`/
`loadgen_bin`, and pre-generated presigned pools. Useful only if you
want to reproduce the headline numbers from machine code, not just
verify the figure rendering.

* **Prereqs:** all of §Prerequisites, plus Docker + ≥12 CPU cores
  + ≥16 GB RAM + ~10 GB free disk for the presigned pool.
* **Approximate runtime:** ~5 hours wall-clock for the sweep (30
  cells × ~5–6 min each, plus per-cell init/teardown overhead).
* **Command sequence:**
  ```bash
  # Build the chain
  cd cosmos && make build && cd ..

  # Build the paper-specific Go tools
  cd tools/presigner && go build -o ../../experiments/validator_scaling_v2/tools/presigner_bin . && cd ../..
  cd tools/loadgen   && go build -o ../../experiments/validator_scaling_v2/tools/loadgen_bin   . && cd ../..

  # Generate presigned pools (~5 min, ~1.1 GB on disk)
  cd experiments/validator_scaling_v2
  ./tools/presigner_bin emit-addresses --scheme secp256k1 --count 8  --out presigned/secp_addrs.jsonl
  ./tools/presigner_bin emit-addresses --scheme mldsa44   --count 8  --out presigned/mldsa_addrs.jsonl
  ./tools/presigner_bin sign --scheme secp256k1 --senders 8 --per-sender 25000 --out presigned/secp.jsonl
  ./tools/presigner_bin sign --scheme mldsa44   --senders 8 --per-sender 25000 --out presigned/mldsa.jsonl

  # Run the 30-cell sweep (~5 hours)
  python3 run_sweep.py

  # Aggregate to figures + summary
  python3 aggregate.py
  ```
* **Output:** same four PDFs as Path A, regenerated from
  freshly-collected data; the 30 result JSONs are overwritten in
  place.

> **Note on Path B:** the `run_sweep.py` orchestrator brings the
> Docker testnet up and tears it down per cell. Cell timing,
> classification, and methodology details are documented in
> `experiments/validator_scaling_v2/summary.md`.

---

## §2 — Reproducing all six figures end-to-end (existing results)

The minimal "verify everything" command sequence:

```bash
# Three crypto_micro figures + 3 others from the same plot.py run
cd benchmarks/crypto_micro && python3 plot.py     && cd ../..

# Block capacity
cd benchmarks/block_packing && python3 plot.py    && cd ../..

# State growth
cd benchmarks/storage_sim && python3 plot.py      && cd ../..

# Committed % (and three other validator-scaling figures alongside)
cd experiments/validator_scaling_v2 && python3 aggregate.py && cd ../..
```

Total wall time: well under a minute. After running, every PDF
referenced in §1 is regenerated next to its `caption.txt` neighbour.

---

## §3 — Re-running the local Go benchmarks (optional)

`benchmarks/crypto_micro/results.json` is checked in. If you want to
regenerate it from the Go bench (e.g., to confirm the numbers on your
hardware before re-rendering the figures):

```bash
cd benchmarks/crypto_micro
go test -bench=. -benchtime=1x -run=^$ -timeout 120s > raw_benchmark.txt
python3 parse_results.py     # reads raw_benchmark.txt, writes results.json
python3 plot.py
```

`parse_results.py` is hard-coded to read `raw_benchmark.txt` and write
`results.json` next to itself; no CLI arguments. Pipe the bench
output to that filename exactly.

Runtime: ~30 s on the reference platform.

`benchmarks/block_packing/results.json` and the storage_sim per-scheme
JSONs come from the simulators in `tools/block_packing/` and
`tools/storage_sim/`:

```bash
# Block packing
cd tools/block_packing && go run . > ../../benchmarks/block_packing/results.json && cd ../..

# Storage simulator (writes 6 per-scheme/size JSONs into the benchmark dir)
cd tools/storage_sim && go run . --out ../../benchmarks/storage_sim/ && cd ../..
```

Runtime: a few seconds each. Re-render the figures with their
respective `plot.py` afterwards.
