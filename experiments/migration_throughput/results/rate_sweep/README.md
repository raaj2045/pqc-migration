# Rate sweep (superseded as a headline, retained as a result)

Offered submission rate 1.0 transfers/s, 5 repeats. **This is not the
migration-throughput headline result** — it was collected before the
independent variable was changed to packets-per-window (see `../../README.md`,
"Why packets-per-window, and not submission rate"). It is kept because it
answers a different question and answers it cleanly.

## What it establishes

**Latency is a property of the finality window, not of the packet.**

| Source of variance | Magnitude |
|---|---|
| Within-cell (packets sharing one window) | **2.5 s** mean spread |
| Between-cell (across repeats) | **104.9 s** (474.1 → 579.0) |

Between-cell variance is ~42x the within-cell variance. Packets that share a
window are acknowledged within ~3 s of one another regardless of when they
were submitted; which window a packet lands in — finality-phase alignment —
moves its latency by ~105 s.

Per-cell round-trip latencies:

```
cell 0: [472.3, 474.5, 475.4]   spread 3.1 s
cell 1: [562.7, 565.4, 563.7]   spread 2.7 s
cell 2: [578.8, 579.2]          spread 0.4 s
cell 3: [565.1, 563.5, 562.4]   spread 2.8 s
cell 4: [575.4, 574.1, 572.3]   spread 3.1 s
```

Summary: acks/window 2.80 (sd 0.45, 95 % CI ±0.56); round-trip latency 550.9 s
(sd 43.5, 95 % CI ±54.0); 100 % success, 0 failures; every cell used exactly
one finality window.

## Why it is not the headline

The 2.80 acks/window figure is bounded by the harness's **submission ceiling**
of ~0.3 transfers/s, not by the bridge. Requesting 1.0 tx/s for 10 s yielded 3
packets, because each `sendtx.py` call costs ~3-5 s (ML-DSA-65 signing plus
broadcast plus await-commit) and submission is sequential. The bridge showed no
strain at that load: 100 % success, a single window, 2.5 s intra-window spread.

A rate axis therefore could not reach the batching ceiling, which is what
prompted the move to packets-per-window as the independent variable.

`verify_roundtrip.json` is the single-transfer end-to-end verification run,
kept as provenance for the harness.
