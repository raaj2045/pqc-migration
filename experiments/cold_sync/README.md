# Cold-sync experiment

> **Status: scaffolded, not yet run.** This experiment is fully
> scripted (`run_cold_sync.py` + `aggregate.py`) but no measurement
> data has been collected. The `results/` directory is empty. The
> paper does not include cold-sync results; this directory is checked
> in so the methodology can be reviewed and the experiment reproduced
> on demand. Anyone who runs it should expect to debug at least the
> docker-network plumbing — it has not been smoke-tested end-to-end
> in this monorepo layout.

How long does a fresh full node take to catch up to a running chain — and
is ML-DSA-44's faster per-signature verify (Table III: ~0.6 ms vs ~1.2 ms
for secp256k1) actually observable at the consensus layer? Block sync
verifies every commit along the way, so replay cost is dominated by
signature verification. This is one of the few cost axes where ML-DSA-44
is expected to *win*.

## What it does

For each scheme:

1. Bring up an N-validator docker testnet with a fast commit timeout so
   block height climbs quickly.
2. Wait until node0 reports `latest_block_height >= --target-height`
   ("chain saturated" — the thing the fresh node will replay).
3. Prepare a fresh, non-validator node home:
   - `simd init fresh-node` on the host,
   - overwrite `config/genesis.json` with the testnet's,
   - extract every validator's node ID via `simd comet show-node-id` and
     patch `persistent_peers` in `config/config.toml`.
4. Launch the fresh node as a detached container on the testnet's docker
   network (IP `172.28.0.199` so it doesn't clash with validators).
5. Poll `/status` every second for `catching_up`; sample `docker stats`
   every 2 s for CPU / memory / block I/O.
6. Stop when `catching_up == false` (or on `--sync-timeout`), then tear
   down the testnet.

Results land at `results/<scheme>.json`.

## Layout

```
experiments/cold_sync/
  run_cold_sync.py     orchestrator
  aggregate.py         reads results, writes fig + summary.md
  results/             per-scheme JSON (skip-if-exists = resumable)
  logs/                fresh-node docker logs per scheme
  fresh_homes/         simd home dirs for the fresh node, per scheme
```

## Prerequisites

Same as `experiments/validator_scaling/`:

- `make build` on `cosmos/` (host-side `simd` binary used for init)
- docker + docker compose + `jq` + `curl`
- Python: `matplotlib` (aggregator only — orchestrator is stdlib-only)

## Running

```bash
# Both schemes, 4 validators, 1000-block saturation target, 1 s commit timeout
python3 run_cold_sync.py

# Just mldsa44
python3 run_cold_sync.py --schemes mldsa44

# Smoke test: 200 blocks, 10 min sync cap
python3 run_cold_sync.py --target-height 200 --sync-timeout 600

# Aggregate
python3 aggregate.py
```

Useful knobs:

| Flag                   | Default | Notes                                          |
|------------------------|--------:|------------------------------------------------|
| `--validators`         | 4       | must be one of {4, 7, 16, 32}                  |
| `--commit-timeout`     | 1s      | forwarded to `simd testnet init-files`         |
| `--target-height`      | 1000    | saturation minimum before sync starts          |
| `--saturation-timeout` | 3600    | max wait for saturation (seconds)              |
| `--sync-timeout`       | 1800    | max wait for `catching_up == false`            |

## Resuming

Per-scheme JSON files are written atomically at the end of each run. Delete
a file to re-run that scheme:

```bash
rm results/mldsa44.json
python3 run_cold_sync.py --schemes mldsa44
```

If a run crashes mid-flight, an error stub is still written so the slot isn't
silently lost — delete it to retry.

## What the result file contains

```jsonc
{
  "scheme": "mldsa44",
  "validators": 4,
  "commit_timeout": "1s",
  "target_height": 1000,
  "tip_height_at_saturation": 1012,
  "caught_up": true,
  "sync_duration_ms": 47321,
  "telemetry": {
    "status_samples": [{ "ts_ms": ..., "height": 12, "catching_up": true }, ...],
    "stat_samples":   [{ "ts_ms": ..., "CPUPerc": "87.3%", "MemUsage": "...", "BlockIO": "..." }, ...],
    "caught_up_at_ms": ..., "caught_up_at_height": 1012, "first_status_ms": ...
  }
}
```

`aggregate.py` derives: wall-clock sync time, blocks/s, peak CPU/memory,
final BlockIO read/write; renders `fig_cold_sync_time.pdf` (height vs
elapsed, one line per scheme) and a table in `summary.md`.

## Gotchas

- **Fresh node fails to peer.** Usually a container-network mismatch. The
  orchestrator targets `--network testnet_testnet`; if you renamed the
  compose project, update `TESTNET_NETWORK` in `run_cold_sync.py`.
- **Saturation never reaches target.** Check `logs/fresh_*.log` — if the
  chain halted (no validator quorum), the saturation loop will time out.
- **docker stats shows 0% CPU.** cgroup v2 hosts need recent docker;
  upgrade if Block I/O reads as "0B / 0B" the whole run.
- **`simd comet show-node-id` fails.** Older simd forks use
  `simd tendermint show-node-id`. If the orchestrator errors here, change
  the call in `prepare_fresh_home()`.
