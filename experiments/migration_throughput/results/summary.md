# Migration throughput — results

N swept: 1, 5, 10, 20, 40 packets per window. Repeats per N: 5.


## Headline: transfers acknowledged per finality window

| N offered | acks/window mean | sd | 95% CI | gas/transfer amortised |
|---|---|---|---|---|
| 1 | 1.00 | 0.00 | ±0.00 | 929,688 |
| 5 | 5.00 | 0.00 | ±0.00 | 185,938 |
| 10 | 10.00 | 0.00 | ±0.00 | 92,969 |
| 20 | 20.00 | 0.00 | ±0.00 | 46,484 |
| 40 | 40.00 | 0.00 | ±0.00 | 23,242 |

### Saturation

**No saturation detected within the swept range.**

acks/window still scaling at the largest N tested; the batching ceiling is above the swept range


## Secondary: round-trip latency (submission → ack verified on Cosmos)

| N offered | mean latency (s) | sd | 95% CI | sustained tx/s | success |
|---|---|---|---|---|---|
| 1 | 551.7 | 44.8 | ±55.6 | 0.002 | 100% |
| 5 | 563.8 | 12.7 | ±15.8 | 0.009 | 100% |
| 10 | 593.7 | 17.4 | ±21.6 | 0.014 | 100% |
| 20 | 709.0 | 16.5 | ±20.5 | 0.023 | 100% |
| 40 | 896.9 | 4.9 | ±6.1 | 0.032 | 100% |

Load increased 40x (N=1 → N=40); mean latency increased 1.63x (551.7 s → 896.9 s).

Latency growth is **sublinear in load** (1.63x against 40x). Combined with 100 % success and windows_used = 1 at every N, this is consistent with sequential relaying in the load generator rather than congestion at either chain.

