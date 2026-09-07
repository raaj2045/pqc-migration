# Measured receive-transaction ceiling — Ethereum → Cosmos

Measured 2026-09-06 against the live devnet, mock verifier, `check_setup.py`
passing. 1,000 real packets submitted (sequences 11–1010); ceiling found by
bisection on real signed/encoded transactions and confirmed by real broadcast
at the boundary.

## Headline: the binding wall is not the one Phase 0 assumed

Phase 0 projected the ceiling from CometBFT's mempool `max_tx_bytes`
(4,194,304 B) and extrapolated ~712 packets. That limit is real but **never
binds**. `pqchaind tx broadcast` base64-encodes the transaction into a JSON-RPC
body, and CometBFT's RPC caps that body at `max_body_bytes = 1,000,000`. Base64
inflates by 4/3, so the usable raw transaction is ~750 KB — **less than a fifth
of `max_tx_bytes`**.

| Limit | Value | Ceiling it implies | Binds? |
|---|---|---|---|
| mempool `max_tx_bytes` | 4,194,304 B | 699 packets | no |
| RPC `max_body_bytes` | 1,000,000 B (base64) | **124 packets** | **yes** |

Over-limit transactions fail with `HTTP 400 … request body too large` at the
RPC, *before* CheckTx — so they consume nothing and the boundary is cheap to
probe repeatedly.

## Measured ceilings, per signer key type

| | secp256k1 | ML-DSA-65 |
|---|---|---|
| pubkey / signature | 33 B / 64 B | 1,952 B / 3,309 B |
| bytes per packet (slope) | 6,000.2 | 6,000.2 |
| fixed overhead (intercept) | −14 B | 5,155 B |
| size ceiling (`max_tx_bytes`) | 699 | 698 |
| **broadcast ceiling (real)** | **124** | **124** |
| bytes at broadcast ceiling | 745,175 raw / 993,568 b64 | 749,529 raw / 999,372 b64 |

Boundary confirmed by real broadcast in both cases:

```
secp256k1  N=125  1,001,708 b64  rejected (body too large)
secp256k1  N=124    993,568 b64  ACCEPTED  code=0
ML-DSA-65  N=125  1,006,940 b64  rejected (body too large)
ML-DSA-65  N=124    999,372 b64  ACCEPTED  code=0
```

## Capacity difference: 0%

**At the wall that actually binds, ML-DSA-65 and secp256k1 carry the identical
124 packets per transaction — a 0.0% capacity difference.** At the
non-binding `max_tx_bytes` wall the difference is 1 packet (699 vs 698,
0.143%).

The reason is structural, and it is the substantive result: a signature is
charged **once per transaction**, while packets are charged per packet. The
per-packet slope is *bit-identical* (6,000.2 B) between key types, because it
is entirely MPT proof data and contains nothing key-dependent. ML-DSA-65's
entire cost is a 5,169-byte constant, and at the ceiling that constant is
0.7% of a 750 KB transaction — it does not cost a single packet slot.

**A caveat on the framing.** The *destination* key type has no effect on
`MsgRecvPacket` whatsoever: receivers appear in the payload as 20-byte bech32
addresses regardless of algorithm, and a fresh recipient account carries no
pubkey on chain until it first signs. There is no destination-key-type axis to
measure here. What was measured is the **signer (relayer) key type**, which is
the only place a signature enters a receive transaction. The sweep records it
as `Signer_Key_Type` for that reason, and `Dest_Key_Type` as `none`.

## Gas vs N (real broadcasts)

| N | secp256k1 gas | gas/packet | ML-DSA-65 gas | gas/packet | ML-DSA premium |
|---|---|---|---|---|---|
| 1 | 186,449 | 186,449 | 339,011 | 339,011 | +81.8% |
| 10 | 1,479,256 | 147,926 | 1,611,218 | 161,122 | +8.9% |
| 50 | 7,125,946 | 142,519 | 7,284,422 | 145,688 | +2.2% |
| 124 | 17,625,307 | 142,140 | 17,769,960 | 143,306 | **+0.8%** |

```
secp256k1  gas = 50,075 + 141,712·n
ML-DSA-65  gas = 196,233 + 141,728·n
```

Per-packet gas slopes are equal to within 16 gas (0.01%). ML-DSA-65's entire
gas cost is a **~146,000 gas fixed premium per transaction** for verifying one
PQ signature. Amortized across a full batch it falls from +82% at N=1 to
**+0.8% at N=124**.

This is the same amortization story as the byte ceiling, and it is the useful
result for the paper: **post-quantum signature overhead on this path is a
per-transaction constant, not a per-packet tax, so batching amortizes it to
near-zero on both size and gas.**

## How far Phase 0 was off

| Quantity | Phase 0 (n ≤ 6) | Measured (n ≤ 124/800) | Error |
|---|---|---|---|
| bytes/packet | 5,890 | 6,000.2 | −1.8% |
| byte intercept | 186 | −14 | — |
| gas/packet | 145,514 | 141,712 | +2.7% |
| gas intercept | 75,483 | 50,075 | — |
| **ceiling** | **712** | **124** | **5.7× too high** |

The *slopes* extrapolated well — within 3% over a 20× range. The **ceiling did
not**, because it was derived against the wrong limit. Phase 0's stated caveat
("non-size limits may bind first") was right in spirit but wrong in mechanism:
it anticipated CosmWasm gas or execution time, and the actual constraint was an
RPC transport limit two layers above the state machine.

## Implications for the sweep

- **The whole cohort relays in one Cosmos tx only up to N=124.** The sweep runs
  N ∈ {1, 10, 50, 100}, all of which fit; `measure_data.py`'s pre-flight
  re-derives the ceiling from the node's live `config.toml` and refuses to
  start above 90% of the binding wall.
- Above 124, chunking is **required**, not a safety valve. Chunk size should be
  the measured ceiling minus a real margin (mirroring `batch_scaling`'s
  `SAFETY_MARGIN`), since the intercept differs slightly by signer key type and
  the ceiling itself moves between runs (see §5 below).
- One `MsgUpdateClient` still covers all chunks (Phase 0 unknown 3 is
  unaffected — the update is independent of how the receives are split).
- `max_body_bytes` is node configuration, not consensus. A relayer running its
  own node could raise it and reach the 699-packet mempool ceiling. Worth
  stating explicitly in the paper: **124 is a deployment limit, 699 is the
  protocol limit.** Which one to report depends on the claim being made.

## Consensus-state selection

Requiring a consensus state at or after the *current* finality slot races a
beacon that keeps advancing: a client perfectly able to prove a packet is
rejected for lacking a state newer than the packet needs. `build-recv-msgs.js`
and `relay-recv-batch.js` instead select the **newest held state that covers
the send block**, updating the client only if none does. Newest, not oldest —
see §4 on geth's pruning window.

---

# Addendum: raising `max_body_bytes` and the real 4 MB ceiling

## 1. Is it configurable? Yes

`max_body_bytes = 1000000`, **line 178, `[rpc]` section** of
`~/devnet-workdir/chain/config/config.toml`. Ordinary TOML; CometBFT passes it
to an HTTP `MaxBytesReader`. No hard cap in the code. Raised to `16000000`
(base64 of a 4 MB tx is ~5.6 MB) and the node restarted; config backed up at
`config.toml.bak-maxbody`. **The change is still in effect.**

It worked: over-limit transactions no longer fail with HTTP 400 "request body
too large", they now reach the mempool and fail with **CheckTx code 21
(`ErrTxTooLarge`)** — i.e. the 4 MB `max_tx_bytes` wall, which is what we
wanted to expose.

## 2. Measured ceiling at the 4 MB wall — confirmed by real broadcast

| | secp256k1 | ML-DSA-65 |
|---|---|---|
| **ceiling** | **675** | **675** |
| bytes at ceiling | 4,189,965 | 4,193,434 |
| bytes at ceiling+1 | 4,196,327 (rejected) | 4,199,688 (rejected) |
| headroom under 4,194,304 | 4,339 B | 870 B |
| gas at ceiling | 97,203,696 | 97,335,954 |

Both confirmed both ways: N=676 rejected with CheckTx code 21, N=675 accepted
and executed (heights 11669 and 11672, ~97 M gas each). Block `max_gas = -1`,
so 97 M gas executes without issue.

**Capacity difference: 0 packets, 0.000%.**

## 3. Why no difference emerges, and the honest caveat

A packet can only be received once, so the two broadcast confirmations must use
*different* packet sets — which makes them an uncontrolled comparison. Per-packet
proof size varies between sets because storage keys take different MPT paths:

```
pure signature delta (pubkey+sig)   5,164 B
observed size delta at N=675        3,469 B
=> packet-set proof variation offset 1,695 B of the signature cost
```

Per-packet cost differed by 5.1 B between the two sets (6,207.4 vs 6,212.5).
**The packet-to-packet variation in proof size is the same order of magnitude
as a third of the entire ML-DSA-65 signature overhead.**

The *controlled* comparison is the build-only bisection over one identical
message set (no broadcast, so no consumption), reported above: **699 vs 698 —
1 packet, 0.143%**.

So the answer to "does a genuine payload-driven difference emerge underneath":
**no.** ML-DSA-65 costs 0 or at most 1 packet out of ~675–699 (≤0.15%), and
which of the two you observe is decided by where a fixed 5,164-byte constant
happens to land relative to the ~6,200-byte per-packet quantum — not by any
per-packet cost. The per-packet slope is key-type-independent because it is
entirely MPT proof data.

The ML-DSA-65 transaction at its ceiling had only **870 bytes of headroom** under
the 4 MB limit — under 0.02%. That thinness is the result: the signature is not
what fills the transaction.

## 4. A third limit found: geth state pruning

Independently of any size limit, **proofs can only be fetched within ~128 blocks
(~25 min) of head**. geth runs `--gcmode=full` with `TriesInMemory = 128`:

```
block 100 back  -> eth_getProof OK
block 130 back  -> "historical state ... not available"
```

The first 800-packet wave became permanently unprovable after sitting 274 blocks.
Since finality lag is ~70 blocks, the usable window between "finalized" and
"pruned" is roughly 58 blocks (~12 min). **This is a tighter operational
constraint than any byte ceiling** and the sweep harness must relay promptly
after finality rather than batching work up. `build-recv-msgs.js` now selects
the *newest* usable consensus state rather than the oldest, to stay inside it.

## 5. The ceiling is not a constant

It moved from 699 to 675 (−3.4%) between the two runs, because the router's
storage trie deepened as packets accumulated (6,000 → 6,207 B per packet). Any
chunk size must therefore carry a real safety margin and ideally be re-measured
per run, not hard-coded.

## Revised conclusion

The Phase-0 null result **stands, and is now better supported**: capacity on
this path is infrastructure-bound, not crypto-bound. Three separate
infrastructure limits bind before post-quantum signature size matters at all —
RPC body size (124 packets, deployment config), mempool `max_tx_bytes` (675,
protocol), and geth state pruning (a ~12-minute window). ML-DSA-65's cost is a
per-transaction constant that batching amortizes to ≤0.15% of capacity and
+0.8% of gas.

---

# Addendum: the return leg has a much lower, and unsplittable, ceiling

Measured 2026-09-07 against the rebuilt devnet, mock verifier.

The acknowledgement travels Cosmos → Ethereum, so it is walled by geth's
`txMaxSize` (131,072 B) rather than CometBFT's `max_tx_bytes` (4,194,304 B).
That alone is a 32× smaller budget. Measured on the signed transaction:

| acks | signed tx bytes | fits under 131,072? |
|---|---|---|
| 50 | 105,397 | yes |
| 100 | 209,397 | no |

≈ **2,094 B per ack**, so the ceiling is **~56 acks per Ethereum transaction**
at a 0.9 margin, ~62 at the raw limit.

## The ceiling cannot be worked around by chunking

Splitting proof-api's multicall into several smaller multicalls **does not
work**. The first transaction succeeds; the second fails with

```
SP1ICS07Tendermint.KeyValuePairNotInCache(
  ["0x696263", "0x30382d7761736d2d3403000000000000152b"], "0x8460e21f…")
```

proof-api fuses ONE `update_client_and_membership` proof over every packet in
the request, and `SP1ICS07Tendermint` verifies those key/value pairs once and
caches them **for the duration of the transaction**. Acks in a later
transaction refer to pairs verified in a transaction that has already ended.
Proving and acknowledging must share one transaction.

This is the structural difference from the forward leg. There, every
`MsgRecvPacket` carries its own membership proof, so a batch splits freely
across transactions — 1,000 packets deliver as 590 + 410 with no penalty
(145,949 gas/transfer against 145,982 for 500 in one transaction). Here the
batch is atomic.

## Consequence: the ack leg bounds the whole pipeline

The ack batch is whatever one Cosmos delivery produced, so a delivery larger
than ~56 packets **can never be acknowledged**. Delivering 590 in one Cosmos
transaction is possible and cheap, and then the packets are stuck open.

**The effective end-to-end batch size is therefore ~56, set by the return leg,
not the ~590 the forward leg allows.** Reporting the forward-leg ceiling alone
would overstate the usable batch size by an order of magnitude.

Confirmed by real broadcast: 45 acks in one transaction, 2,417,517 gas; 50
acks, 5,126,339–5,129,087 gas. A 100-ack request is refused before broadcast.

## Where the limits now stand

| Leg | Wall | Ceiling | Splittable? |
|---|---|---|---|
| Deliver (EVM → Cosmos) | CometBFT `max_tx_bytes` 4 MB | ~590 packets | **yes** |
| Deliver, stock RPC | CometBFT `max_body_bytes` 1 MB | 124 packets | yes |
| Acknowledge (Cosmos → EVM) | geth `txMaxSize` 128 KB | **~56 acks** | **no** |
| Proof fetch | geth `TriesInMemory` 128 | ~12-minute window | n/a |

All four are infrastructure limits. None is a function of the signature
algorithm.
