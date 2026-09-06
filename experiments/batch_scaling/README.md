# batch_scaling

> **Direction: Cosmos → EVM. Superseded for the paper.**
>
> This measures the **opposite** direction to the paper's migration claims. The
> code is retained and runnable and its results stand as measurements of this
> direction, but nothing in the current paper depends on it. For Ethereum →
> Cosmos — the direction the paper is about — see
> [`../migration_volume/`](../migration_volume/README.md). The two are not
> symmetric: they are finality-bound on opposite legs, prove with different
> machinery, batch through different primitives, and hit different size walls.

How the bridge's **transfer mechanism** scales as more transfers are grouped
under one light-client update — not how proving time scales. The EVM-side
`SP1ICS07Tendermint` light client is required to be bound to `SP1MockVerifier`
(checked before every run, see [Setup check](#setup-check)); real Groth16
proving costs ~10 min/proof and is already measured separately, in
[`../migration_throughput/README.md`](../migration_throughput/README.md) and
[`../../devnet/README.md#proving`](../../devnet/README.md#proving). Grouping
transfers here means: submit G Cosmos→EVM transfers, then relay all G in one
`proof-api` request and one on-chain multicall, so they share a single
light-client update instead of G separate ones.

This is a companion to `migration_throughput/`, not a replacement: that
experiment measures the **return leg** (EVM→Cosmos, real BLS verification) at
N ∈ {1, 5, 10, 20, 40}. This one measures the **forward leg**'s batching
(Cosmos→EVM, mocked) at larger group sizes, while still carrying every packet
through the same real return-leg acknowledgement so "fully confirmed" means
the same thing in both experiments.

## What it measures, per group size

1. **Total time**, submission of the first transfer to every transfer in the
   group being acknowledged back on Cosmos.
2. **Total gas** for the whole group (forward-leg relay + every return-leg ack
   + return-leg update-client cost).
3. **Light-client-update gas, separate from per-transfer gas**, on both legs
   where the split can be obtained — exact on the return leg always; exact on
   the forward leg when the EVM RPC exposes `debug_traceTransaction` for the
   call trace, otherwise a regression estimate across the sweep. See
   `aggregate.py`'s module docstring for the methodology.
4. **Whether anything failed or reverted**, per packet and per group.

## Pluggable verifier

Which SP1 verifier a new EVM-side light client binds to is an explicit,
required argument — `node ../../devnet/create-eth-client.js --verifier=mock|real`
— not a default anyone has to remember to set. There is no default: omitting
the flag exits with a usage message rather than silently picking one. The
script prints a banner naming the mode and address before doing anything, and
verifies on-chain (via the contract's own `VERIFIER()` accessor) that the
deployed — or reused — client is actually bound to what was asked for,
refusing to reuse a client bound to the other verifier. This exists because a
client's verifier is fixed forever at creation, and running this experiment
against the real verifier by mistake silently turns every result into a
proving-time measurement instead of a mechanism measurement (see
`check_setup.py`'s `check_eth_light_client_uses_mock_verifier` for the
same guarantee enforced again immediately before every run).

## Concurrent load: account pools

Submitting many transfers from a single Cosmos account sequentially is slow,
and submitting them concurrently from a single account fails outright — every
tx needs the account's next sequence number, so two in-flight txs from the
same account collide. `setup_pool.py` creates and funds a pool of independent
ML-DSA-65 accounts; `loadgen.py --pool-size N` divides a group's transfers
into N contiguous shares, one per account, each account submitting its share
**sequentially** (correct, collision-free sequence handling within itself)
while every account in the pool runs **concurrently** with the others:

```bash
python3 setup_pool.py --pool-size 10
python3 loadgen.py --group-size 100 --pool-size 10 --repeat 0 --out /tmp/validate.json
```

Validated at load=100/pool=10: all 100 submissions succeeded with zero
sequence collisions in ~48s (vs. ~400s+ sequential).

## Relay chunk size

A relay-batch transaction cannot grow arbitrarily: go-ethereum defaults to a
128KB (131072-byte) limit on a transaction's signed, RLP-serialized size
(txpool's `txMaxSize`). This is a client default, not a protocol rule, but it
is what essentially every real node runs — it's the same wall real zkRollup
relayers hit in production, not an artifact of this devnet: go-ethereum issue
[#23920](https://github.com/ethereum/go-ethereum/issues/23920) reports Aztec
fitting **~112** of their transactions into one call under this same cap.

`find_relay_ceiling.py` measures OUR real ceiling empirically rather than
assuming it matches Aztec's — different relayers pack different payloads, and
this harness's relay-batch calldata is an ICS26Router multicall carrying one
shared SP1 update-client-and-membership proof plus N `recvPacket` messages,
each with its own membership-proof leaf. Measured result:

```
CEILING: 66 packets fit under 131072B (signed tx 130227B)
  next size up (n=67) is 132179B — over the cap
  marginal cost per additional packet: ~1952B
```

**66, not ~112** — about 59% of Aztec's number. The gap is the payload shape:
each of our packets carries its own SP1 membership-proof leaf (~1952 bytes
marginal), which is heavier than whatever per-item cost Aztec was packing.
Neither number is "right" in general — both are real, measured ceilings for
two different payload shapes under the same client-level cap.

```bash
python3 find_relay_ceiling.py --probe-count 130 --pool-size 10
```

Writes `results/relay_ceiling.json`; `relay_pool.py` reads it and uses
`ceiling - 6` (a small safety margin against per-packet size variance) as the
default chunk size, overridable with `loadgen.py --chunk-size`.

## Chunked, pool-concurrent relay

A group larger than the real per-tx ceiling cannot be relayed in one
transaction at all — chunking is required to reach any group size above ~60,
regardless of chunk size tuning. `relay_pool.py` splits a group into chunks
of `--chunk-size` (default: the measured ceiling above, minus margin) and
relays them using the same pool-concurrency model as Cosmos submission, one
level up: `setup-evm-pool.js` creates and funds a pool of EVM accounts
(`loadgen.py --relay-pool-size N`), the chunk list is divided into N
contiguous shares, and each account relays its share of chunks
**sequentially** (correct nonce handling — `relay-chunk.js` takes an explicit
`--signer-key`) while every account runs **concurrently**:

```bash
node setup-evm-pool.js 5 10   # 5 accounts, 10 ETH each
python3 loadgen.py --group-size 250 --pool-size 10 --relay-pool-size 5 --repeat 0 --out /tmp/validate.json
```

### Chunked relay: true vs idealized amortization

Dispatching chunks concurrently trades away the amortization a single
sequential relay gets almost for free: relaying one chunk, waiting for it to
land, then relaying the next would let every chunk after the first see the
client already updated and skip re-updating — one real update for the whole
group. Concurrent dispatch means every chunk's proof-api request is built
before any other chunk's update has landed, so more than one chunk can carry
its own light-client update. This module counts that directly rather than
assuming an idealized single update: `relay-chunk.js` reads the light
client's own trusted height from chain state immediately before and after
the exact block its transaction landed in (`clientState()` at
`blockNumber - 1` vs `blockNumber`) — ground truth, unaffected by which other
chunks land when. `loadgen.py`'s result JSON records
`batch_relay.numUpdateClientCalls` (forward leg) next to `numChunks`, and
every packet records its own `update_client_gas_return_leg` (return leg) —
`aggregate.py`'s `total_batch_gas` sums both unconditionally, so the reported
gas is the group's real cost, not a single-update assumption.

An earlier version of `relay-chunk.js` tried detecting updates by decoding
the multicall's top-level calls for a standalone `updateClient` selector.
That undercounts: proof-api's SP1 program fuses the update proof into every
`recvPacket` call (`update_client_and_membership` mode) rather than emitting
a separate top-level call, so the decode-based check reported zero updates
even when a real one happened — confirmed by independently decoding a raw
on-chain tx and cross-checking `clientState()` before/after. The
block-straddling state-diff check replaced it for exactly this reason.

**The return leg has the same "true, not idealized" effect, from a different
cause.** Acks are still submitted one at a time, sequentially (this module
only chunks the forward leg — the ask was specifically about the tx-size
wall, which is a forward-leg-only constraint). At small group sizes the whole
ack phase finishes inside one Ethereum finality epoch (~6 min), so only the
first ack triggers a real `MsgUpdateClient` and the rest are free. At larger
group sizes the ack phase's own wall-clock time can exceed one epoch — at
group=250 (250 sequential acks, ~35 min total) this was directly observed:
**~7 real return-leg updates**, not 1, because finality genuinely advanced
several times while acks were still being submitted. This is a real
scaling effect, not a bug: sequential ack throughput becomes the dominant
cost at large N, independent of anything the forward-leg chunking changes.
Pooling the return leg's ack submission (the same pattern, applied to
`step-ack.js`) is the natural next lever if group sizes need to grow further
— not yet implemented.

### Validation at group=250

```
pool_size=10  relay_pool_size=5  chunk_size=60
submitted 250/250 (zero sequence collisions, pool submission)
forward leg:  5 chunks, 0/5 needed a light-client update (already-current
              trusted height from earlier session activity — see caveat
              below), total gas 40,364,346
return leg:   250/250 acked, 1 nominal "window" but ~7 real MsgUpdateClient
              calls (ack phase spanned ~35 min, several real finality epochs)
status: ok — 250/250 acked
TRUE amortized gas/transfer: 319,263 (all legs, real update counts)
```

Whether the forward leg needs any updates at all depends on how much Cosmos
time has passed since the client's trusted height was last advanced — a
function of session history and timing, not a fixed number; a run started
further from the last real forward-leg update will show a nonzero
`numUpdateClientCalls`. `--verifier=mock` was confirmed active and logged
(`check_setup.py`'s precondition check, which every `loadgen.py` run runs by
default) throughout.

## Pooled return-leg acks

The group=250 validation above found the return leg, not the forward leg,
was the actual wall-clock bottleneck at that size: 250 sequential
`MsgAcknowledgement` submissions (one account, one at a time — the forward
leg's tx-size wall has no return-leg equivalent, so nothing had forced this
leg to chunk or pool before) took ~35 minutes, long enough to span ~7 real
Ethereum finality epochs and force ~7 genuine `MsgUpdateClient` calls where
an idealized single-update model would assume 1.

`ack_pool.py` applies the same pattern as the other two legs: `loadgen.py
--ack-pool-size N` reuses the SAME Cosmos account pool as `--pool-size`
(`setup_pool.py` — same mechanism, not a separate one) to submit acks. Every
window's outstanding packets are divided into contiguous per-account shares;
each account acks its share **sequentially** (correct nonce handling) while
every account runs **concurrently**:

```bash
python3 loadgen.py --group-size 250 --pool-size 10 --relay-pool-size 5 \
  --ack-pool-size 10 --repeat 0 --out /tmp/validate.json
```

**Accounting note:** unlike the forward leg, no new ground-truth mechanism
was needed here. Whether a given ack's `MsgUpdateClient` actually broadcast
(vs. `update-eth-client.py` deciding it was a no-op because the client is
already current) was already exact before pooling — `step-ack.js`'s stdout
carries one `sendtx.py` JSON result per Cosmos tx it actually sent, so two
lines means a real update happened, one means it didn't. This was already
direct, not inferred, because a `MsgUpdateClient` is always its own explicit
top-level Cosmos message — there was never a fused-call ambiguity here the
way there was on the EVM side (see "Chunked relay" above). Pooling
preserves this signal unchanged: each pool worker's `step-ack.js` subprocess
call is independent, so its stdout is read the same way regardless of which
account ran it or how many ran concurrently. A pool account signing requires
its own address in the message's `signer` field, so `step-ack.js` and
`update-eth-client.py` resolve the signer's address live from `--signer-key`
via `pqchaind keys show` — a Cosmos tx is rejected if its declared signer
doesn't match whoever actually signed it.

**Validated at group=250** (pool=10 submit, relay-pool=5, ack-pool=10): 923s
total time vs. 2,109s sequential-ack — wall-clock roughly as expected. TRUE
amortized gas/transfer came out to 485,091, *worse* than the 319,263
sequential-ack baseline, not better — real light-client updates went from 7
(sequential) to 10 (10 independent accounts racing the same staleness
check), and the pool's ML-DSA-65 signing keys carry a real ~2.1x gas cost
over the sequential baseline's secp256k1 `validator` key, uniformly across
every ack (not a concurrency artifact — see git history around this run for
the full investigation). No coordination mechanism was built to fix the
update-count redundancy — see the same investigation for why: the
signature-type cost dominates the gap by roughly an order of magnitude,
so fixing update coordination wouldn't materially close it.

## Per-transaction latency

Every packet records two independent timestamps pairs, not just group-level
aggregates:

- **credit latency** (`submit_ts` → `credited_ts`): forward-leg only — when
  did the transfer land on the destination (EVM) chain. Identical between
  the sequential-ack and pooled-ack configurations, since ack pooling never
  touches this leg — `credited_ts` is set to the real completion time of the
  specific chunk carrying that packet (`relay_pool.py`), not a single
  "the whole relay phase finished" timestamp, so chunks landing at
  different real times still show up as different latencies per packet.
- **round-trip latency** (`submit_ts` → `ack_ts`): the full loop back to
  Cosmos. This is the one that differs between configs — pooling the ack
  step changes how long an individual transfer waits for its own ack, and
  early-vs-late spread within a pool worker's sequential share is exactly
  what a group-level average would hide.

Reported as a **distribution** (mean, median, p95, min, max — see
`loadgen._distribution`), not just a mean, in both the live log (SUMMARY
block, each run) and the result JSON's `latency_stats` field. The raw
per-packet timestamps are always in the JSON too (every `Packet`'s
`submit_ts`/`credited_ts`/`ack_ts`), so the full distribution — or any other
statistic — can be recomputed or plotted later without re-running.
`aggregate.py` also writes `credited_ts`/`credit_latency_s` columns into
`results/raw_packets.csv` and a per-cell latency table into `summary.md`.

## Resumability

Two levels, both automatic — nothing extra to pass on a rerun:

- **Cell-level** (already existed): `run_sweep.py` never overwrites an
  existing `results/G{g}_rep{r}[_ack{N}].json` — an interrupted sweep
  resumes at the next un-run cell when re-invoked.
- **Within a cell**: at large group sizes the ack phase alone can run for
  hours (sequentially — see the group=250 finding above), so losing an
  entire cell to a crash near the end would be expensive. `loadgen.py`
  writes an incremental checkpoint (`<out>.checkpoint.json`) as it runs:
  once after submission completes, once after forward relay completes, and
  periodically (~20 times per ack window, plus always at window end) during
  the ack phase. On (re-)start, if a checkpoint exists whose recorded
  config (group size, all four pool/chunk sizes) matches the current
  invocation, `loadgen.py` resumes from it — skipping submission/relay
  entirely if already done, and resuming the ack phase from wherever it
  left off (already-acked packets are simply skipped, since the ack loop
  only ever looks at packets still in "credited" status). A checkpoint
  whose config doesn't match the current invocation is treated as stale and
  ignored, never partially reused. The checkpoint is deleted once the cell
  finishes (success or a clean recorded failure) — only a genuine crash
  leaves one behind.
- **Recovering from an interruption**: just re-run the same command (either
  `run_sweep.py` with the same arguments, or `loadgen.py` directly with the
  same `--out`). Submission/relay redo in full if they didn't finish before
  the crash (a bounded, low-single-digit-minutes cost even at
  group_size=10000); the ack phase picks back up mid-way.

## Setup check

`check_setup.py` confirms, before anything is submitted:

- the Cosmos chain is reachable
- the Cosmos-side light client (tracking Ethereum) is `Active`
- the Ethereum devnet's JSON-RPC is reachable
- the EVM-side light client (`SP1ICS07Tendermint`) has live code and its
  `VERIFIER()` matches `SP1_VERIFIER_MOCK` from `deploy.env`

If the real `SP1VerifierGroth16` is bound instead, it fails with an explicit
message rather than silently running a 10-minutes-per-proof sweep under the
assumption that it's free. Run standalone:

```bash
python3 check_setup.py
```

`run_sweep.py` runs it once up front; `loadgen.py` also runs it by default
(pass `--skip-setup-check` to skip, which `run_sweep.py` does for its own
per-cell invocations since the sweep-level check already covered it).

## Running

```bash
python3 run_sweep.py --group-sizes 1 10 50 100 250 500 --repeats 5
python3 aggregate.py
```

Resumable: an existing `results/G{g}_rep{r}.json` is never overwritten.

**Escalation stops at the first failing group size.** Sizes are attempted in
ascending order; if any repeat at a size fails with a genuine breaking error —
gas limit exceeded, timeout, revert, a mismatched ack count, or anything else
`relay_pool.py`/`loadgen.py` classifies as a failure — the sweep stops and
does not attempt larger sizes. That failure is itself the result: `results/`
and `results/summary.md` record it, they are not silently pruned.

## Layout

| Path | What |
|---|---|
| `check_setup.py` | Devnet-up + SP1MockVerifier precondition check |
| `setup_pool.py` | Creates/funds a pool of Cosmos accounts for concurrent submission |
| `setup-evm-pool.js` | Creates/funds a pool of EVM accounts for concurrent relay chunking |
| `find_relay_ceiling.py` | Bisects for the real per-tx packet ceiling under geth's tx-size cap |
| `probe-relay-size.js` | Builds (never broadcasts) a relay tx, measures its signed size |
| `relay-chunk.js` | Relays one bounded-size chunk (proof-api request + one EVM multicall), signed by an explicit (usually pool) account |
| `relay_pool.py` | Splits a group into chunks, dispatches them across the EVM pool concurrently |
| `ack_pool.py` | Divides outstanding acks across the Cosmos account pool, dispatches concurrently |
| `loadgen.py` | One cell: submit a group (pool), relay it (chunked/pooled), ack every packet, observe |
| `run_sweep.py` | Orchestrator over (group size × repeat), resumable, stops at the first failing size |
| `aggregate.py` | Computes summary statistics → `results/summary.md` |
| `results/`, `logs/`, `sweep_state.json` | Local, git-ignored — regenerate by re-running |

Configuration is resolved through
[`devnet/lib/config.py`](../../devnet/lib/config.py), the same precedence
chain the devnet scripts use.

## Statistics

Mean, sample standard deviation (`ddof=1`), and a 95% confidence interval
using the Student *t* critical value for the actual repeat count (5 repeats:
t=2.571 against z=1.96) — same convention as
[`migration_throughput`](../migration_throughput/README.md#statistics).

## Known limitations

- **All transfers within a group are simple, similar ICS-20 transfers** of
  the same amount — this does not model varied real-world transaction types.
- **Runs assume nothing else is submitting to the chain concurrently.** A
  loaded chain would change both the timing and the gas figures.
- **The validator set is not varied.** Every cell runs against the same
  validator count; this is not a validator-scaling test (see
  `../validator_scaling_v2/` for that axis).
- **The forward-leg light client runs `SP1MockVerifier`.** No claim is made
  about proving cost here — see `../migration_throughput/README.md` and
  `../../devnet/README.md#proving` for the measured real-proving cost.
- **The forward-leg gas split may be a regression estimate, not a direct
  measurement**, when the EVM RPC does not expose `debug_traceTransaction`.
  `aggregate.py`'s output and this README both label which case applies.
- **Single-relayer, devnet-scale measurement**, same caveat as
  `migration_throughput` — see its README's Scope section.

---

[Project README](../../README.md) · [Devnet runbook](../../devnet/README.md) · [migration_throughput](../migration_throughput/README.md)
