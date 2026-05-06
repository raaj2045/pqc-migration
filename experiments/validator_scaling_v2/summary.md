# validator_scaling_v2 — summary
Date: 2026-04-26

## TL;DR

30 runs completed: 11 ok, 19 saturated, 0 crashed, 0 timed out, 0 missing.

At every validator-set size measured, mldsa44 user-account signing imposes a real but bounded throughput tax versus secp256k1: mldsa achieves **75% / 77% / 86%** of the secp ceiling at N = 4 / 7 / 16 respectively. The tax narrows as N grows (relative cost falls because absolute secp throughput drops faster than mldsa's), but at N = 16 mldsa already trips the strict-criteria 90 %-commit threshold one rate-tier earlier than secp (mldsa fails at rate = 50; secp passes).

## Framing

PQ tx-signing overhead at validator scale. Consensus layer remains classical (ed25519) in both arms, consistent with this fork's implementation scope and the paper's Section IV-A claims. The scheme axis isolates the cost of verifying user-account PQ signatures in the CheckTx/DeliverTx critical path.

**Does measure:** PQ user-account signature verification overhead on block processing and consensus throughput at N = 4, 7, 16.

**Does not measure:** consensus-layer PQ signatures (out of scope for this fork; mentioned in §IX future work). Validator consensus keys are ed25519 in both arms.

## Headline — saturation TPS per (N, scheme)

Saturation TPS = maximum *achieved* TPS observed across all 5 target rates for that cell. This is the chain's ceiling for that scheme at that validator-set size.

| N | secp256k1 sat. TPS | mldsa44 sat. TPS | mldsa/secp |
|---|---:|---:|---:|
| 4 | 67.0 | 50.0 | 75% |
| 7 | 65.0 | 50.0 | 77% |
| 16 | 50.0 | 43.2 | 86% |

## Per-cell detail

| N | scheme | rate | submitted | committed | committed % | achieved TPS | p50 ms | p99 ms | peak CPU % | status |
|---|--------|-----:|----------:|----------:|------------:|-------------:|-------:|-------:|-----------:|--------|
| 4 | secp256k1 | 10 | 2999 | 2999 | 100.0 | 10.0 | 3101 | 5701 | 14.8 | ok |
| 4 | secp256k1 | 50 | 14999 | 14999 | 100.0 | 50.0 | 3062 | 5662 | 43.3 | ok |
| 4 | secp256k1 | 100 | 29999 | 20098 | 67.0 | 67.0 | 4151 | 12952 | 46.0 | saturated |
| 4 | secp256k1 | 200 | 59999 | 1338 | 2.2 | 4.5 | 7052 | 11781 | 36.1 | saturated |
| 4 | secp256k1 | 500 | 149982 | 677 | 0.5 | 2.3 | 1380 | 10695 | 63.1 | saturated |
| 4 | mldsa44 | 10 | 2999 | 2999 | 100.0 | 10.0 | 3103 | 5704 | 22.7 | ok |
| 4 | mldsa44 | 50 | 14999 | 14999 | 100.0 | 50.0 | 3256 | 5953 | 47.8 | ok |
| 4 | mldsa44 | 100 | 29999 | 12653 | 42.2 | 42.2 | 3796 | 11465 | 52.9 | saturated |
| 4 | mldsa44 | 200 | 59997 | 9071 | 15.1 | 30.2 | 3488 | 7818 | 61.2 | saturated |
| 4 | mldsa44 | 500 | 149947 | 873 | 0.6 | 2.9 | 6058 | 11375 | 47.9 | saturated |
| 7 | secp256k1 | 10 | 2999 | 2999 | 100.0 | 10.0 | 3101 | 5702 | 15.3 | ok |
| 7 | secp256k1 | 50 | 14999 | 14999 | 100.0 | 50.0 | 3101 | 5741 | 31.9 | ok |
| 7 | secp256k1 | 100 | 29996 | 19506 | 65.0 | 65.0 | 3592 | 9931 | 51.6 | saturated |
| 7 | secp256k1 | 200 | 59999 | 1311 | 2.2 | 4.4 | 7375 | 11975 | 50.1 | saturated |
| 7 | secp256k1 | 500 | 149940 | 383 | 0.3 | 1.3 | 749 | 5855 | 56.1 | saturated |
| 7 | mldsa44 | 10 | 2999 | 2999 | 100.0 | 10.0 | 3103 | 5704 | 16.8 | ok |
| 7 | mldsa44 | 50 | 14998 | 14998 | 100.0 | 50.0 | 3278 | 5978 | 54.8 | ok |
| 7 | mldsa44 | 100 | 29999 | 12262 | 40.9 | 40.9 | 3599 | 9148 | 62.0 | saturated |
| 7 | mldsa44 | 200 | 59994 | 8742 | 14.6 | 29.1 | 3284 | 6792 | 55.1 | saturated |
| 7 | mldsa44 | 500 | 149959 | 546 | 0.4 | 1.8 | 1765 | 6183 | 59.4 | saturated |
| 16 | secp256k1 | 10 | 2999 | 2999 | 100.0 | 10.0 | 3102 | 5800 | 21.0 | ok |
| 16 | secp256k1 | 50 | 14999 | 14999 | 100.0 | 50.0 | 3322 | 6023 | 55.0 | ok |
| 16 | secp256k1 | 100 | 29998 | 8463 | 28.2 | 28.2 | 3472 | 7171 | 38.4 | saturated |
| 16 | secp256k1 | 200 | 59996 | 162 | 0.3 | 0.5 | 874 | 5958 | 42.7 | saturated |
| 16 | secp256k1 | 500 | 149810 | 315 | 0.2 | 1.1 | 1664 | 1989 | 43.3 | saturated |
| 16 | mldsa44 | 10 | 2999 | 2999 | 100.0 | 10.0 | 3204 | 5805 | 33.5 | ok |
| 16 | mldsa44 | 50 | 14999 | 12946 | 86.3 | 43.2 | 3680 | 6663 | 58.9 | saturated |
| 16 | mldsa44 | 100 | 29995 | 1129 | 3.8 | 3.8 | 5085 | 7807 | 49.3 | saturated |
| 16 | mldsa44 | 200 | 59994 | 238 | 0.4 | 0.8 | 1693 | 6087 | 61.4 | saturated |
| 16 | mldsa44 | 500 | 149848 | 73 | 0.0 | 0.2 | 1426 | 1497 | 61.1 | saturated |

## Spec deviations

1. **gas_limit = 250 000** per tx (spec said 100 000). Reason: ML-DSA-44 verification physically costs ≈195 000 gas. Same value across arms preserves comparison fairness. This is a measured property of the system, not a tunable.

2. **minimum-gas-prices = `0stake`**. Reason: zero-fee txs simplify the loadgen comparison; fee economics are not the variable under study.

3. **block.max_gas = 100 000 000** (matches spec).

4. **commit_timeout = 5 s** (matches spec).

## Methodology

Per-run flow: tear down → FORCE_REBUILD init at this N → docker compose up → wait for height ≥ 1 → CPU sampler thread (docker stats every 5 s) → loadgen replays the pre-signed pool via Tendermint `/broadcast_tx_sync` for 300 s + 30 s drain → tear down. Validator consensus stays ed25519 in every run.

Pre-signed pools (200 000 txs / arm, 25 000 / sender) are generated once up-front by `tools/presigner sign`. Sender addresses are deterministic from a fixed seed prefix so init_testnet.sh can pre-fund all 16 senders at the same account_numbers in every run (secp 0–7, mldsa 8–15).

Commit time is the wall-clock moment our /block poller first observes the finalising block, bounded above by the polling interval (500 ms). We do **not** use `block.header.time` — that field is the median of the previous round's vote timestamps and runs 2–5 s in the past, biasing latency low.

## Where each cell first fails strict criteria

Strict criteria (rates 10/50/100): committed ≥ 90 %, p99 < 10 s, CPU < 90 %, > 1 tx/block, zero sig-verify errors.

| N | secp first-saturated rate | mldsa first-saturated rate |
|---|---:|---:|
| 4 | 100 | 100 |
| 7 | 100 | 100 |
| 16 | 100 | 50 |

N = 16 / mldsa is the only cell where saturation begins at **rate = 50**; at smaller N both arms only saturate from rate = 100 onward. This is the cleanest evidence that PQ verification cost becomes binding earlier as the validator set grows.

## Unexpected behaviour and methodology notes

1. **Streak-break dynamics under-estimate the chain's real ceiling at high target rates.** At rate = 200 / 500, committed counts collapse to a few hundred or even tens of txs. The cause is not slower verification — it's that the loadgen broadcasts pre-signed sequences in strict order (0, 1, 2 …) and the chain's mempool admits at most a bounded per-sender lookahead window. Once a single sequence is rejected with `account sequence mismatch` (SDK code 32), every subsequent sequence from that sender is also rejected because the gap can never close. In effect, the committed count at rate = 200 / 500 measures the *streak length* before the first rejection, not the chain's throughput. The true ceiling lies between the rate that still passes strict (e.g. 50) and the rate that first saturates (e.g. 100). The aggregate.py saturation-TPS metric uses `max(committed/duration)` across all rates, which conservatively picks the highest sustained throughput we actually observed.

2. **Sweep classification correction mid-sweep.** The first attempt aborted at run 8/30 because run_sweep.py treated `http_errors > 50 % of submissions` as a crash (triggering the 3-consecutive-crash abort rule). After review, those high-error runs were re-classified as *saturated* (the http_errors ARE the saturation signal from sequence-mismatch rejections, not a chain crash). Three result files (N=4 rate=100 mldsa, N=4 rate=200 secp, N=4 rate=200 mldsa) were re-stamped post-hoc; the underlying tx records were unchanged. The sweep was resumed from N=4 rate=500 and ran to completion.

3. **mldsa CPU peak does not always exceed secp at the same target rate.** At rate = 200 / 500 the secp CPU occasionally measures *higher* than mldsa (e.g. N=4 rate=500: secp 63.1 %, mldsa 47.9 %). This inverts the low-rate pattern and is again an artefact of streak-break: secp's loadgen finishes its broadcast burst much faster than mldsa's, so secp validators actually spend more wall time on heavy execution than mldsa validators in a given 300 s window. CPU at the *commit-bounded* rates (10 / 50 / 100) is the right comparison; mldsa is consistently hotter there.

## Figures

* `fig_saturation_curve.pdf` — committed TPS vs submitted TPS
* `fig_p99_vs_rate.pdf` — p99 inclusion latency vs target rate
* `fig_committed_pct.pdf` — commit rate vs target rate
* `fig_peak_cpu.pdf` — peak validator CPU vs target rate

