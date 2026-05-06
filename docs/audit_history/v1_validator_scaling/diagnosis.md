# Validator-scaling sweep — diagnosis of the "ML-DSA-44 outperforms secp256k1" finding

**Conclusion: (b) Finding is biased by two compounding confounds. The headline
comparison does not measure what the paper claims to measure, and the
"ML-DSA-44 is faster" claim has no support in this data.**

The corrected read is below.

---

## 1. Raw comparison table (N ∈ {4,7,16}, rate ∈ {200, 500}, both schemes)

Pulled from `results/N<n>_rate<r>_<scheme>.json` → `loadgen_summary` and
`telemetry.blocks`. Target wall time was always 300 s (`--duration 300`).

| N  | rate | scheme    | started (UTC, local) | wall_s  | submitted | % of target | mined   | ach_sub tx/s | ach_mine tx/s | blocks | mean_bi ms | p99 ms |
|---:|-----:|-----------|----------------------|--------:|----------:|------------:|--------:|-------------:|--------------:|-------:|-----------:|-------:|
|  4 |  200 | secp256k1 | 2026-04-22 09:23     |  394.6  |    59,963 | 99.9 %      |  59,963 | 152.66       | 152.71        |   86   | 5101       | 1000   |
|  4 |  200 | mldsa44   | 2026-04-23 21:42     |  300.1  |    59,978 | 100.0 %     |  59,978 | 200.67       | 200.93        |   66   | 5223       |  619   |
|  4 |  500 | secp256k1 | 2026-04-23 08:35     | ~706*   |   133,701 | 89.1 %      | 133,701 | 189.37       | 189.33        |  144   | 5219       |  730   |
|  4 |  500 | mldsa44   | 2026-04-23 21:48     |  668.2  |   149,876 | 99.9 %      | 149,876 | 224.82       | 224.87        |  137   | 5216       |  618   |
|  7 |  200 | secp256k1 | 2026-04-22 09:57     |  399.2  |    59,953 | 99.9 %      |  59,953 | 150.79       | 150.92        |   86   | 5142       | 1020   |
|  7 |  200 | mldsa44   | 2026-04-23 22:17     |  300.1  |    59,974 | 100.0 %     |  59,974 | 200.67       | 200.95        |   66   | 5229       |  617   |
|  7 |  500 | secp256k1 | 2026-04-23 08:48     | ~706*   |   127,501 | 85.0 %      | 127,501 | 180.55       | 180.51        |  143   | 5236       |  778   |
|  7 |  500 | mldsa44   | 2026-04-23 22:22     |  688.5  |   149,880 | 99.9 %      | 149,880 | 218.17       | 218.14        |  142   | 5224       |  630   |
| 16 |  200 | secp256k1 | 2026-04-22 10:30     |  432.5  |    59,962 | 99.9 %      |  59,962 | 139.20       | 139.35        |   91   | 5231       | 1131   |
| 16 |  200 | mldsa44   | 2026-04-23 22:52     |  304.6  |    59,971 | 100.0 %     |  59,971 | 197.89       | 197.98        |   67   | 5183       |  729   |
| 16 |  500 | secp256k1 | 2026-04-23 20:53     | ~706*   |   108,803 | 72.5 %      | 108,803 | 154.22       | 154.16        |  141   | 5317       | 1129   |
| 16 |  500 | mldsa44   | 2026-04-23 22:57     | ~694*   |   138,801 | 92.5 %      | 138,801 | 200.00       | 200.00        |  142   | 5175       |  731   |

\* Wall-time field missing in the JSON for partial-drain runs; estimated as
`submitted / achieved_submit_rate_tx_s`.

### Flags

**Timestamp flag.** Every secp256k1 rate ∈ {200, 500} file with a sub-300 s
target lines up to 04-22 (Hardhat era), while every mldsa44 rate ∈ {200, 500}
file lines up to 04-23 (post-Anvil-migration era). The loadgen/relayer code
path is the same, but the **Ethereum dev node underneath the loadgen differs
by scheme**:

- secp256k1 N=4/7/16 at rate ≤ 200 → **Hardhat**, ~150 tx/s RPC ceiling.
- secp256k1 N=4/7/16 at rate = 500 → **Anvil** (re-run once rate=500 needed it).
- all mldsa44 → **Anvil**.

That alone explains most of the rate=200 gap in the aggregate summary
(secp256k1 ~140–152 vs mldsa44 ~198–201 tx/s). At rate=200 you are
directly seeing Hardhat's single-threaded RPC cap vs Anvil's multi-threaded
RPC — not chain-side signature speed.

**Submission-shortfall flag.** Even in the Anvil-vs-Anvil rate=500 rows, the
two schemes were not given the same workload:

- secp256k1 rate=500 runs submitted 72.5–89.1 % of target (108k–133k of 150k).
- mldsa44 rate=500 runs submitted 92.5–99.9 % of target (138k–149k of 150k).

All three secp256k1 rate=500 files are `partial_drain=True`; the loadgen
hit its subprocess timeout before completing 300 s at target rate. Two of
three mldsa44 rate=500 runs were clean to near-target. Dividing mined by
wall_seconds on different volumes of submitted-but-not-yet-mined work gives
numerically different "achieved TPS" even if the underlying chain has no
difference.

**Block-content flag (most important).** `sum(num_txs) over all blocks` for
every one of the 31 real runs is **0**. No cosmos-side bridge tx landed on
chain. See §2.

---

## 2. Hypothesis check: verification speed vs measurement bias

The task asked:

> Average transactions per block, block size, verification time contribution.
> If mldsa44 fits more per block → bias. If fewer/equal but higher total →
> verification-speed hypothesis holds.

**The test is inapplicable, because the cosmos chain never saw any bridge
txs in any run.** The telemetry and relayer metrics both agree on this:

### Cosmos blocks are empty across all runs

```
file                                  blocks  nonempty  sum_num_txs  sum_tx_bytes
N4_rate500_secp256k1.json               144       0         0            0
N4_rate500_mldsa44.json                 137       0         0            0
N7_rate500_secp256k1.json               143       0         0            0
N7_rate500_mldsa44.json                 142       0         0            0
N16_rate500_secp256k1.json              141       0         0            0
N16_rate500_mldsa44.json                142       0         0            0
...all 31 real runs...                  ...       0         0            0
```

### Relayer never processed an event

Pulled the *last* sample of `telemetry.relayer_samples` from each file:

```
file                          observed   processed   failed     queue_depth
N4_rate500_secp256k1            26,126         0      5,859        24,944
N4_rate500_mldsa44              67,669         0     19,157        63,826
N7_rate500_secp256k1            25,731         0      5,764        24,571
N7_rate500_mldsa44              68,588         0     20,162        64,546
N16_rate500_secp256k1           24,217         0      4,813        23,225
N16_rate500_mldsa44             67,644         0     19,225        63,783
...all 31 real runs...        tens of k          0        k            k
```

Every single run: `processed = 0`.

### Root cause (from `logs/relayer_mldsa44_N4.log`)

```
❌ Failed to mint on Cosmos: Error: Command failed: cd /…/cosmos-latest &&
   ./build/simd tx lockandmint mint "cosmos1test0000000006" 1 --from node0
   …
error in json rpc client … RPC error -32603 - Internal error:
broadcast error on transaction validation: tx 999D…74EC is invalid:
code=13, data=,
log='insufficient fees; got:  required: 2stake: insufficient fee',
codespace='sdk'
```

Every relayer-driven `simd tx lockandmint mint` submits with `fees=""` and
the testnet's mempool rejects it for `insufficient fees; required: 2stake`.
The cosmos validators then produce only empty blocks for 5–11 minutes per
run, which the collector faithfully records.

### Implication for the verification-speed hypothesis

Cosmos signature verification of bridge mint txs **never happened** in any
run. The per-tx verify costs (0.6 ms for ML-DSA-44, 1.2 ms for secp256k1)
you would multiply against `tx_per_block × block_count` resolve to `0 × 0
× anything = 0` for both schemes. There is no cosmos-side verification
work to explain either scheme's TPS — because there was no cosmos-side
verification work at all.

The verification-speed hypothesis therefore cannot be supported from this
data. It also cannot be refuted: we simply did not run that experiment.

---

## 3. External confounds (resources.jsonl)

The `_resources.jsonl` files track the cosmos validator containers via
`docker stats --no-stream --format json` every 10 s. Note: **these files
do not track the Anvil, Hardhat, relayer, or loadgen host processes**, so
the requested "Anvil vs loadgen CPU %" comparison is not directly in the
data. What we do have:

### Peak CPU % (docker-normalized, 1 core = 100 %) per container, rate=500

| N  | scheme    | peak_cpu % | mean_cpu % | peak_mem % | cap (cores) |
|---:|-----------|-----------:|-----------:|-----------:|------------:|
|  4 | secp256k1 | 16.1       | 9.3        | 7.9        | 1.00        |
|  4 | mldsa44   | 15.4       | 8.3        | 10.3       | 1.00        |
|  7 | secp256k1 | 15.7       | 8.9        | 8.7        | 1.00        |
|  7 | mldsa44   | 18.4       | 7.4        | 11.6       | 1.00        |
| 16 | secp256k1 | 31.9       | 8.7        | 10.6       | 0.60        |
| 16 | mldsa44   | 18.7       | 6.9        | 13.5       | 0.60        |

Observations:

- No cosmos container comes close to its CPU cap in any run. The box is
  *not* starving the cosmos side — consistent with §2 (nothing arrived to
  process).
- Peak cosmos CPU is if anything *higher* for secp256k1 at N=16 (31.9 %)
  than for mldsa44 (18.7 %), which contradicts the "ML-DSA-44 verifies
  faster, so it needs less CPU" narrative — but since neither scheme
  actually verified mint txs, this is just noise in idle-consensus CPU.
- Peak mem% is slightly higher for mldsa44 at every N — consistent with
  the larger ML-DSA-44 key/state footprint, but well below limits.

### What we cannot observe

No `_resources.jsonl` exists for the loadgen or the Ethereum dev-node
process. The two confounds that would actually matter —
  (i) Anvil / Hardhat RPC saturation,
  (ii) ethers.js-side submission cap on the loadgen process —
are invisible to the collector. This is a known gap; the earlier
rate=300 smoke test directly measured the loadgen+ethers+Anvil stack
ceiling at ~190 tx/s on this host, independent of which cosmos chain
was running. That matches every secp256k1 Anvil rate=500 row here.

---

## 4. Diagnosis

### (b) Finding is biased by (at least) two compounding confounds

**Confound A — Ethereum backend differs by scheme for most rows.**
Every secp256k1 row at rate=200 used **Hardhat** (caps at ~150 tx/s
single-threaded RPC), while every mldsa44 row and the newer secp256k1
rate=500 rows used **Anvil** (multi-threaded, ~500+ tx/s). The
aggregate-table ratio of 1.32–1.42× at rate=200 is largely the
Hardhat-vs-Anvil delta. This is a measurement-methodology bug, not a
chain property.

**Confound B — Cosmos chain never processed any bridge tx in any run.**
The relayer's `simd tx lockandmint mint` command submits with no fee
and the testnet rejects every attempt with `insufficient fees;
required: 2stake`. Across all 31 real runs, the cosmos validators
produced **0** non-empty blocks and the relayer processed **0** events
(observed many, failed many, queue_depth climbed into the tens of
thousands). The "achieved TPS" number in `loadgen_summary` is solely
the rate at which Ethereum-side `lock()` txs were mined by the
Ethereum dev node. The cosmos validator signature scheme is never
exercised. Swapping ML-DSA-44 for secp256k1 could only move this
number through indirect CPU contention on the shared host — and §3
shows no such contention exists at these loads on this box.

### Corrected read

> Once you strip out the Hardhat-vs-Anvil confound, the remaining
> Anvil-vs-Anvil rate=500 residual (secp256k1 ~155–189 tx/s vs
> mldsa44 ~200–225 tx/s) is explained by the submitted-volume
> imbalance: secp256k1 runs got partial_drain=True with 72.5–89.1 %
> of target txs submitted, mldsa44 got 92.5–99.9 %. No
> chain-side cause is observable, because no cosmos chain-side
> processing occurred in either arm. The sweep in its current form
> is not a validator-scaling experiment; it is a "Hardhat vs Anvil
> + loadgen timeout behaviour" experiment.

### What a valid re-run would need

This is outside the "no new experiments" scope of the task, but a
valid re-run requires three independent fixes:

1. **Relayer fee fix.** Pass `--fees 5000stake` (or whatever the
   genesis minimum-gas-price resolves to) on the relayer's `simd tx
   lockandmint mint` invocation, and add a startup check that asserts
   `relayer_events_processed_total > 0` within 30 s of load start.
   Without this, the cosmos validator scheme is not in the
   measurement path at all.
2. **Single Ethereum backend across the sweep.** Re-run every cell
   with the same backend (Anvil). The existing 04-22 secp256k1 files
   at rates 10/50/100/200 must be discarded, not mixed with 04-23
   mldsa44 files.
3. **Equal submitted volume per (N, rate) cell.** Extend the
   subprocess timeout so partial_drain doesn't bias the comparison,
   or report `mined_rate = mined / min(wall_A, wall_B)` over the
   common interval instead of `mined / wall_scheme`. Alternatively,
   sample the TPS from the cosmos chain's block-size-per-second (once
   the fee fix lets blocks actually be non-empty), which is
   independent of the loadgen's submission timeline.

Until all three are in place, no TPS comparison between signature
schemes is interpretable.

---

## Appendix — what *can* honestly be said from the existing data

- **Mean block interval is ~5.1–5.3 s for every (N, scheme, rate)**,
  with at most ~0.2 s variation between N=4 and N=16, and no
  meaningful scheme difference. This is governed by the chain's
  `commit_timeout` (default 5 s in `simd testnet init-files`) and is
  insensitive to the tx load because there is no tx load on the
  cosmos side. This one measurement is robust but also uninformative
  about validator scaling under actual work.
- **N=32 does not run to completion on a 12-core WSL2 host** under
  the per-validator CPU cap of `num_cpus / (N+4) ≈ 0.33`. 4 of 5
  secp256k1 rate points and all 5 mldsa44 rate points stubbed out.
  This is a hardware-budget statement, not a chain statement.
- **All other "TPS" and "latency" numbers in `summary.md` should be
  treated as Ethereum-dev-node properties, not Cosmos-chain
  properties, until the fee bug is fixed and a clean re-run is
  performed.**
