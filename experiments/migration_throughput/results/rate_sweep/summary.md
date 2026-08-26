# Migration throughput — results

Rates swept: 1.0 transfers/s. Repeats per rate: 5.


## Headline: transfers acknowledged per finality window

| offered rate (tx/s) | per-window mean | sd | 95% CI |
|---|---|---|---|
| 1.0 | 2.80 | 0.45 | ±0.56 |

### Saturation

**No saturation detected within the swept range.**

per-window count still scaling at the highest rate tested; saturation not reached within the swept range


## Secondary: round-trip latency (submission → ack verified on Cosmos)

| offered rate (tx/s) | mean latency (s) | sd | 95% CI | sustained tx/s | success |
|---|---|---|---|---|---|
| 1.0 | 550.9 | 43.5 | ±54.0 | 0.005 | 100% |
