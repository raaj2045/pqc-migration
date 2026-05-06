# STATUS_AUDIT.md

Audit date: **2026-04-21**
Scope: experimental work across `cosmos-latest/` and `experiments/` + two
infrastructure checks (relayer queue integration, Docker Compose testnet
buildability). No new experiments were run.

## 1. Experimental directories

Paths below are relative to the repo root. Sizes are in bytes.
`mtime` is the file's modification time.

### `cosmos-latest/benchmarks/crypto_micro/` — **COMPLETE**

| Check            | Result |
|------------------|--------|
| (a) directory    | ✅ exists |
| (b) data file    | ✅ `results.json` |
| (c) size / mtime | 7,236 B  @ 2026-04-20 04:52 |
| (d) writeup      | ⚠️ `summary_table.txt` only (no `summary.md`) |
| (e) PDF figures  | ✅ 6 PDFs: `fig_keygen_comparison.pdf`, `fig_signing_by_msg_size.pdf`, `fig_verification_by_msg_size.pdf`, `fig_batch_verification.pdf`, `fig_concurrent_signing.pdf`, `fig_memory_allocs.pdf` |

Notes: `parse_results.py` + `plot.py` are present; PDFs regenerable.

### `cosmos-latest/benchmarks/storage_sim/` — **COMPLETE**

| Check            | Result |
|------------------|--------|
| (a) directory    | ✅ exists |
| (b) data file    | ✅ 6 JSONs: `results_{secp256k1,mldsa44}_{100k,1m,10m}.json` |
| (c) size / mtime | 22–25 KB each @ 2026-04-20 15:48 |
| (d) writeup      | ✅ `summary.md` (1,323 B) @ 2026-04-20 15:58 |
| (e) PDF figures  | ✅ `fig_state_growth.pdf` (21 KB) @ 2026-04-20 15:58 |

### `cosmos-latest/benchmarks/block_packing/` — **COMPLETE**

| Check            | Result |
|------------------|--------|
| (a) directory    | ✅ exists |
| (b) data file    | ✅ `results.json` |
| (c) size / mtime | 2,537 B @ 2026-04-21 17:21 |
| (d) writeup      | ✅ `summary.md` (2,352 B) @ 2026-04-20 17:28 |
| (e) PDF figures  | ✅ `fig_block_capacity.pdf` (21 KB) @ 2026-04-20 17:29 |

### `experiments/validator_scaling/` — **SCAFFOLDED, NO DATA**

| Check            | Result |
|------------------|--------|
| (a) directory    | ✅ exists |
| (b) data file    | ❌ `results/` is empty |
| (c) size / mtime | — |
| (d) writeup      | ⚠️ `README.md` is methodology-only; no `summary.md` |
| (e) PDF figures  | ❌ none |

Runner (`run_sweep.py`), collector (`collect.py`), aggregator
(`aggregate.py`) are present and wire-complete; the experiment has
**not been executed** (results/ is empty, no PDFs produced).

### `experiments/saturation/` — **MISSING**

| Check            | Result |
|------------------|--------|
| (a) directory    | ❌ does not exist |
| (b) data file    | — |
| (c) size / mtime | — |
| (d) writeup      | — |
| (e) PDF figures  | — |

No saturation experiment has been built. This matters because
`experiments/cold_sync/run_cold_sync.py` has a saturation phase inline
(it populates the chain itself rather than depending on a separate
`saturation/` directory), so the missing directory isn't blocking
cold-sync, but there is no standalone saturation-throughput writeup.

### `experiments/cold_sync/` — **SCAFFOLDED, NO DATA**

| Check            | Result |
|------------------|--------|
| (a) directory    | ✅ exists |
| (b) data file    | ❌ `results/` is empty |
| (c) size / mtime | — |
| (d) writeup      | ⚠️ `README.md` is methodology-only; no `summary.md` |
| (e) PDF figures  | ❌ none |

Runner (`run_cold_sync.py`, 16 KB) and aggregator (`aggregate.py`,
8.4 KB) are present; experiment has not been executed.

## 2. Relayer queue integration (Phase 2.1)

**Status: ✅ integrated into the main entry point.**

`ethereum-lockandmint/scripts/relayer.js` imports and uses all three
submodules directly — no branch, no separate entry point:

| Module                             | Imported at | Used at |
|------------------------------------|-------------|---------|
| `./relayer/queue.js` (`LockEventQueue`, `correlationIdFor`) | line 8  | instantiated line 47, `enqueue` line 247, `close` line 167, `depth` line 407 |
| `./relayer/worker_pool.js` (`WorkerPool`)                   | line 9  | instantiated line 53, `start` line 124, `stop` line 161 |
| `./relayer/metrics.js` (`createMetrics`, `startMetricsServer`) | line 10 | `createMetrics` line 44, `startMetricsServer` line 121 |

The legacy in-memory path has been removed; every `TokensLocked`
observation flows through the SQLite queue before a worker picks it up.

## 3. Docker Compose testnet buildability (Phase 3.1)

**Command:** `make testnet-up N=4` (from `cosmos-latest/`)
**Budget:** 60 s
**Outcome:** ❌ did **not** complete within 60 s. No validators reached
block production inside the budget.

**Root cause:** the target's critical path is

1. `docker build -t cosmos-testnet:local` — a full
   `golang:1.23.5-bookworm` → `make build` → `debian:bookworm-slim`
   multi-stage build triggered from `init_testnet.sh`.
2. `simd testnet init-files` for N validators (runs *inside* the image,
   so step 1 must finish first).
3. `docker compose up -d` + validator boot + first-block commit.

The image `cosmos-testnet:local` is **not** present in the local Docker
cache (`docker images cosmos-testnet` returns no rows), so step 1 has to
build from scratch. An empty `golang:1.23.5-bookworm` layer download
alone exceeds the 60 s budget on a cold machine; `make build` inside
the container then compiles simd against cgo, which on this host takes
several more minutes.

**What was verified:**
- `init_testnet.sh` exists, is syntactically correct, and short-circuits
  cleanly when `testnet-data/` already exists (observed
  `"testnet-data already exists. Run 'make testnet-down' or set
  FORCE_REBUILD=1."`).
- The local non-Docker `./build/simd` binary exists and is executable
  (`-rwxr-xr-x 111 MB`, built 2025-07-03), so the cold build is a
  Docker-path concern only — the Go code itself compiles.
- No Docker containers from this project are running or exited
  (`docker ps --filter name=testnet` returned no rows before and after
  the attempt).

**To validate end-to-end without a 60 s cap:**
```bash
cd cosmos-latest
make -C docker/testnet build       # one-time, several minutes
make testnet-up N=4                # subsequent runs: ~30 s
make testnet-health N=4            # polls until block ≥ 5 on all nodes
```

Once the image is cached, the 60 s budget should be achievable for
`up` + `health` on N=4; the current audit cannot confirm that without
running the full build.

## Summary

| Area                                 | State |
|--------------------------------------|-------|
| `benchmarks/crypto_micro/`           | ✅ data + 6 PDFs (no `summary.md`, has `summary_table.txt`) |
| `benchmarks/storage_sim/`            | ✅ data + PDF + `summary.md` |
| `benchmarks/block_packing/`          | ✅ data + PDF + `summary.md` |
| `experiments/validator_scaling/`     | ⚠️ scaffolded, not executed |
| `experiments/saturation/`            | ❌ not created |
| `experiments/cold_sync/`             | ⚠️ scaffolded, not executed |
| Relayer queue integration (Phase 2.1)| ✅ integrated in `scripts/relayer.js` |
| Docker Compose testnet (Phase 3.1)   | ⚠️ code complete; not verified under the 60 s budget because the simd image has to be built from cold cache |

**Bottom line:** all three static benchmarks have produced their paper
artefacts. Both dynamic experiments (validator_scaling, cold_sync) have
working orchestrators but empty `results/` directories and no figures
yet. The relayer refactor is fully merged into the main entry point.
The Docker testnet is wire-complete but its first run needs the
multi-minute image build that the 60 s audit budget couldn't absorb.
