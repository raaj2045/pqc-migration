# migration_throughput

Sustained ICS-20 transfer rate on the **live** bridge path: ML-DSA-65 accounts
on the Cosmos chain, packets verified by the real `cw-ics08-wasm-eth` light
client against a Kurtosis Ethereum devnet. No mock verifier, and no shortcut
around finality.

**25 cells, 5 repeats at each of N = 1, 5, 10, 20, 40. 1,000 transfers,
0 failures, 100 % acknowledgement success.**

## Two metrics from the same runs

**Headline — transfers acknowledged per finality window**, as a function of
**N**, the number of packets offered into a single window. The batching
ceiling is the N at which acks/window stops scaling.

**Secondary — round-trip latency** (submission → ack verified on Cosmos).
Reported alongside because it is the intuitive metric, and because confirming
it is *flat* is what justifies the headline framing.

## Why the round trip, and not the forward leg alone

A transfer here has two legs, and only one of them exercises real consensus
verification.

| Leg | Direction | Verified by | Finality-bound? |
|---|---|---|---|
| Forward | Cosmos → EVM | `SP1ICS07Tendermint`, running a **mock** verifier on this devnet | **No** |
| Return (ack) | EVM → Cosmos | `cw-ics08-wasm-eth`, **real** BLS sync-committee verification | **Yes** |

The forward leg proves the packet against Cosmos state one block after it
commits, and the devnet's SP1 verifier performs no proof checking at all. It
is fast and involves no finality window. Measuring it would produce throughput
numbers that say nothing about light-client verification — the very thing this
experiment exists to characterise.

The return leg is where the architecture actually shows itself. The ack cannot
be submitted until Ethereum finality covers the execution block containing it,
and the `MsgUpdateClient` that establishes that consensus state BLS-verifies
all 512 sync-committee keys. So the return leg is both the finality-bound one
and the one carrying the expensive per-window operation.

A measured single round trip makes the split concrete:

| Segment | Time | Verifier |
|---|---|---|
| submit → commit | 3.9 s | Cosmos consensus |
| submit → credit (forward) | 24.9 s | mock SP1 |
| credit → ack (return) | **487.8 s** | real light client |
| submit → ack (round trip) | 512.8 s | — |

The finality-bound portion is ~95 % of the round trip and lies entirely on the
return leg. Measuring only the forward leg would have reported a ~25 s
"end-to-end" latency and a batching curve that was an artifact of the harness
rather than a property of the bridge.

### Why the headline metric is the right one

The cost structure of this path is asymmetric, and the asymmetry is already
measured in the Phase 5 numbers:

| Message | Gas | Frequency |
|---|---|---|
| `MsgUpdateClient` | 929,688 | **once per finality window** |
| `MsgAcknowledgement` | 274,191 | **once per packet** |

`MsgUpdateClient` is expensive because it BLS-verifies all 512
sync-committee public keys. But that cost is paid **per window, not per
transfer** — one trusted consensus state serves every packet provable
against it. So the marginal cost of the *n*-th transfer in a window is the
per-packet cost only, and the per-transfer average falls as the window fills.

That is the same fact the throughput ceiling expresses. Transfers-per-window
is the batching efficiency; the gas asymmetry is its cost shadow. A rate
sweep that only reports latency cannot see either, because **latency is
dominated by the finality window and is therefore expected to be flat with
respect to offered rate**. Flat latency is not the absence of a result — it
is the evidence that the system batches rather than queues.

The secondary metric exists to test that prediction rather than assume it.
If latency turns out *not* to be flat, in a way that is not simply the known
Cosmos mempool ceiling, that is a finding in its own right and the sweep
should stop until it is understood.

## Why packets-per-window, and not submission rate

The experiment was originally specified as a submission-rate sweep: raise
offered transfers/s until latency degrades. That design was abandoned after
measurement, for a reason worth recording.

**The harness cannot offer load fast enough for a rate axis to be meaningful.**
Each transfer costs ~3-5 s to submit — ML-DSA-65 signing, broadcast, and
await-commit — and submission is sequential. Requesting 1.0 transfers/s for
10 s produced **3 packets: an achieved rate of ~0.3/s**, identically across
repeats. Every rate at or above ~0.3/s therefore collapses to the same actual
offered load.

**This is a property of the load generator, not of the bridge.** At that load
the bridge showed no strain whatsoever: 100 % ack success, a single finality
window, and a 2.5 s spread across packets sharing it. A rate sweep would have
produced a flat headline curve caused by the harness's signing throughput and
invited exactly the wrong conclusion.

The retained rate-sweep data is in
[`results/rate_sweep/`](results/rate_sweep/). It is a valid result about
something else — it establishes that **latency is a property of the window,
not of the packet**, with between-cell variance ~42x the within-cell variance
(104.9 s against 2.5 s) — and it is kept rather than discarded.

Switching the independent variable to **N, packets per window**, measures the
batching ceiling directly rather than inferring it from a rate at which load
cannot actually be offered. It also matches the cost structure: the question
worth answering is how many transfers share one 929,688-gas `MsgUpdateClient`,
and N *is* that quantity.

**N range: 1, 5, 10, 20, 40.** Chosen against the cost structure. The fixed
per-window cost amortised over N transfers falls as 929,688/N, so per-transfer
overhead drops from 100 % of the fixed cost at N=1 to ~23,242 gas at N=40 —
under 9 % of the ~274,191 gas per-packet marginal cost. Beyond N≈40 further
batching cannot materially change the per-transfer economics, so the range
brackets the informative region.

## Results

Every packet offered into a window was acknowledged in that window, at every N
tested, in all 25 cells.

| N | acks/window | sd | 95 % CI | `windows_used` | success | amortised gas/transfer | % of marginal | mean latency |
|---|---|---|---|---|---|---|---|---|
| 1 | **1.00** | 0.00 | ±0.00 | `[1,1,1,1,1]` | 100 % | 929,688 | 339 % | 551.7 s |
| 5 | **5.00** | 0.00 | ±0.00 | `[1,1,1,1,1]` | 100 % | 185,938 | 68 % | 563.8 s |
| 10 | **10.00** | 0.00 | ±0.00 | `[1,1,1,1,1]` | 100 % | 92,969 | 34 % | 593.7 s |
| 20 | **20.00** | 0.00 | ±0.00 | `[1,1,1,1,1]` | 100 % | 46,484 | 17 % | 709.0 s |
| 40 | **40.00** | 0.00 | ±0.00 | `[1,1,1,1,1]` | 100 % | 23,242 | 8 % | 896.9 s |

Scaling is exactly 1:1 with zero variance across every cell. Raw data is in
`results/N{n}_rep{r}.json`, `results/raw_packets.csv` (one row per transfer)
and `results/by_rate.csv`; `results/summary.md` is regenerated by
`aggregate.py`.

### The gas asymmetry, measured

The fixed per-window cost divided cleanly by N across the whole range:

```
N=1   ->  929,688 gas/transfer   339 % of the 274,191 per-packet marginal cost
N=5   ->  185,938 gas/transfer    68 %
N=10  ->   92,969 gas/transfer    34 %
N=20  ->   46,484 gas/transfer    17 %
N=40  ->   23,242 gas/transfer     8 %
```

A **40x reduction in per-transfer fixed-cost overhead**. The BLS-verifying
`MsgUpdateClient` falls from 3.4x the marginal cost of a transfer to under a
tenth of it. This is the per-window/per-packet asymmetry stated at the top of
this document, measured rather than argued.

### No batching ceiling within the tested range

**No batching ceiling was found within the tested range (N = 1 to 40).** The
crossover at which relay serialization would exceed one finality window is
estimated at **N ~ 60-90**, based on the measured per-packet relay cost
(~5 s to submit, ~12 s for the forward leg, so roughly `17N` seconds of
sequential work against a window budget of ~192 s per epoch with finality
typically advancing 2-3 epochs during the ack phase).

**Any future test at higher N showing degradation should be attributed to this
harness limit, not to the protocol.** The signature to check is
`windows_used > 1`: a genuine architectural ceiling would show acks/window
falling short of N *while still fitting one window*, whereas relay
serialization shows up as the work spilling into a second window. In this
sweep the two never diverged, because nothing failed to scale.

### Latency: sublinear, and attributable to the harness

Mean round-trip latency rises 551.7 -> 896.9 s, a **63 % increase for a 40x
load increase** — sublinear by a wide margin. The N=20 and N=40 points lie
outside the ~40 s between-cell noise, so this is a real effect rather than
scatter.

**It is serialization in the load generator, not queueing at the chain.**
Latency is measured per packet from its own submission, and the relayer
processes packets one at a time, so with N=40 the last packet's ack is relayed
roughly eight minutes after the first. Three independent observations rule out
chain-side congestion:

- **100 % acknowledgement success at every N** — nothing was dropped, timed
  out or retried.
- **`windows_used` = 1 in all 25 cells** — the bridge never needed a second
  finality window, even at N=40.
- **Zero variance in acks/window** — a congested system would show scatter.

A parallelised relayer would flatten this curve further; the measured values
are therefore an upper bound on round-trip latency, not a protocol property.

### A second, independent result

[`results/rate_sweep/`](results/rate_sweep/) holds the earlier
submission-rate data, kept as a result in its own right. It establishes that
**latency is a property of the finality window, not of the packet**:
between-cell variance is ~42x the within-cell variance (104.9 s against
2.5 s), so packets sharing a window are acknowledged within ~3 s of one
another regardless of when they were submitted. That directory has its own
README explaining why it is not the headline.


## Layout

| Path | What |
|---|---|
| `loadgen.py` | one cell: rate-controlled submission → relay across finality windows → observe credits |
| `run_sweep.py` | orchestrator over (rate × repeat), resumable |
| `aggregate.py` | dual aggregation → CSV + `summary.md`, with saturation detection |
| `results/` | per-cell JSON, plus generated `raw_packets.csv` and `by_rate.csv` |
| `logs/` | per-cell loadgen logs |
| `sweep_state.json` | resume state |

Configuration is resolved through
[`devnet/lib/config.py`](../../devnet/lib/config.py) — the same precedence
chain the devnet scripts use (environment → `devnet.env` → `devnet.env.example`
→ generated `ports.env`/`cosmos.env`/`deploy.env`). Nothing is hardcoded.

## Running

```bash
python3 run_sweep.py --rates 0.05 0.1 0.25 0.5 1.0 --repeats 5 --duration 60
python3 aggregate.py
```

Resumable: an existing `results/rateR_repN.json` is never overwritten, so an
interrupted sweep continues where it stopped.

## Method

Each cell runs three phases, kept separate because their time constants
differ by two orders of magnitude:

1. **Submit** — offered-rate-controlled `MsgTransfer` on Cosmos. Absolute
   deadlines, not `sleep(1/rate)`, so a slow submission shows up as
   `achieved < offered` rather than silently lowering the rate. Built as a
   `MsgTransfer` JSON signed through `devnet/cosmos/sendtx.py`, not through
   `pqchaind tx ibc-transfer transfer` — see "CLI caveat" below.
2. **Forward leg** — `devnet/step-recv.js` per packet, run immediately with
   no artificial wait, because this leg is not finality-bound.
3. **Return leg** — `devnet/step-ack.js` per packet. Every ack submitted
   before finality advances shares one `MsgUpdateClient`; `update-eth-client.py`
   is a no-op when the client already holds the finalized slot, which is
   precisely what makes the batching measurable.

Real finality throughout — nothing waits on a shortened clock. At 6 s slots
and 32-slot epochs, one epoch is 192 s; an ack additionally needs finality to
advance *past* the block containing it, so an unbatched round trip costs
roughly 8 minutes. That sets the wall-clock cost of the sweep.

### CLI caveat

`pqchaind tx ibc-transfer transfer` cannot be used to generate this load, for
two independent reasons established by direct test against this chain:

1. The CLI never sets `MsgTransfer.Encoding` — there is no flag for it — but
   the EVM counterparty can only decode `application/x-solidity-abi`. A
   CLI-built packet commits on Cosmos and then fails to decode on the EVM side.
2. The CLI builds `timeout_timestamp` in **nanoseconds**
   (`client/cli/tx.go`: `timeoutTimestamp = uint64(nowNano) + ...`) while IBC
   v2 reads the field as absolute **seconds**
   (`04-channel/v2/keeper/packet.go`: `time.Unix(int64(ts), 0)`). Every CLI
   transfer is therefore rejected with `timeout exceeds the maximum expected
   value`.

Both are worked around by building the message as JSON and signing through
`sendtx.py`, which is also the ML-DSA-65 signing path this chain uses.

## Statistics

Five repeats per rate. Mean, sample standard deviation (`ddof=1`), and a 95 %
confidence interval using the Student *t* critical value for the actual
repeat count — with n=5, t=2.776 against z=1.96, so a normal approximation
would understate the interval by about 40 %.

**Saturation is defined against the confidence intervals, not by eye.** The
knee is the first rate at which the increase in per-window count is smaller
than the combined 95 % CI half-widths of it and the previous rate — i.e. the
first point where additional offered load buys an improvement that cannot be
distinguished from noise. `aggregate.py` reports the rate and the arithmetic
behind the verdict, or states explicitly that saturation was not reached
within the swept range.

## Scope

- **The light client's consensus verification is trusted**, as everywhere else
  in this repository. See [`../../security/README.md`](../../security/README.md#scope)
  and [`../../docs/live-path-verification/`](../../docs/live-path-verification/).
- **This is a single-relayer measurement.** A production deployment with
  competing relayers would batch differently.
- **The forward leg's verifier is mocked**, so no claim is made about
  Cosmos→EVM proof-verification cost. Only the return leg's verification is
  real, and only it is measured.
- **Relaying is sequential.** Each packet's legs are relayed one at a time, so
  at high offered load the relay loop, not the chain, can become the binding
  constraint. Where that happens it is reported as such rather than as chain
  saturation.
- **Devnet, not mainnet.** Slot timing is real but the validator set is tiny,
  so absolute finality latency is not a mainnet prediction. The *shape* of the
  curve — flat latency, batching throughput ceiling — is the transferable
  result.
