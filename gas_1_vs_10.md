# Gas per transfer: 1 transfer at once against 10

Signed with secp256k1. n=3 and n=3 repeats; ± is a 95% confidence interval.

| Step | 1 at once | 10 at once | Change |
|---|---:|---:|---:|
| Submit on Ethereum | 166,001 ± 0 | 166,001 ± 0 | +0.0% |
| Update light client | 780,368 ± 6 | 78,037 ± 1 | -90.0% |
| Deliver on Cosmos | 181,893 ± 2,052 | 140,936 ± 642 | -22.5% |
| **Total per transfer** | **1,128,262** | **384,974** | **-65.9%** |

Submitting on Ethereum does not change: each user sends their own
transaction either way. The light-client update is charged once per
batch, so 10 transfers split one bill. Delivery carries a fixed cost
per Cosmos transaction on top of a per-packet cost, and that fixed part
is split the same way.

Batch size here is how many migrations move together, not how busy the
chain is — a single migration still shares its blocks with other traffic.
