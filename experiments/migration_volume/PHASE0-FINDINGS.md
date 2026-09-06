# Phase 0 findings — Ethereum → Cosmos migration, 10 independent users

Run against the live devnet on 2026-09-06. Verifier/prover pair: **mock ↔ mock**
(the ack leg only — see `check_setup.py`). 10 EVM users, 1 ETH + 1,000,000 TERC
each, 2,000 TERC migrated per user, each to its own Cosmos account.

**Result: 10/10 credited on Cosmos**, sequences 1–10, all verified by balance
query (`2000 ibc/629091951E69…` per receiver). Round trip confirmed for 6 of
them (batched ack landed, status 1).

## Unknown 1 — does proof-api batch acknowledgements? **YES**

Given one Cosmos tx containing 6 acknowledgements, `RelayByTx` returned:

```
top-level selector: 0xac9650d8 (multicall)
multicall with 6 inner call(s):   ackPacket: 6
13,764 bytes, built in 3.7–4.7 s (mock prover)
submitted -> status 1, gas 718,688, block 1352
```

One request → one multicall → one EVM transaction, **~119,781 gas per ack**.
There is no separate `updateClient` entry in the multicall, consistent with
proof-api's fused `update_client_and_membership` program (the same behaviour
`batch_scaling/relay-chunk.js` documents for the other direction).

**Consequence:** the ack leg batches natively. It needs no chunking or pooling
at target scale — the unit of work is one recv tx, not one packet.

## Unknown 2 — Cosmos-side batching ceiling

Measured at n = 1, 3, 6 (three separate batches, real submissions):

| n | recv gas_used | tx_bytes | msg JSON |
|---|---|---|---|
| 1 | 226,824 | 5,914 | 7,851 |
| 3 | 502,313 | 18,126 | 24,523 |
| 6 | 952,452 | 35,418 | 48,170 |

Least-squares fits (residuals ≤ 2%, i.e. cleanly linear over this range):

```
gas   =  75,483 + 145,514 · n
bytes =     186 +   5,890 · n
```

The binding wall is CometBFT's `max_tx_bytes = 4,194,304` (mempool). Block
`max_gas` is **-1 (unlimited)** and block `max_bytes` is 55,600,190, so gas is
*not* a consensus-level wall on this chain.

**Implied ceiling ≈ 712 packets per transaction** — over 10× geth's 66. The
geth 128 KB ceiling genuinely does not transfer, as expected.

At every target sweep size the whole cohort fits in **one** transaction:

| N | tx bytes | % of 4 MB cap | recv gas |
|---|---|---|---|
| 50 | 294,686 | 7.0% | 7.4 M |
| 100 | 589,186 | 14.0% | 14.6 M |
| 150 | 883,686 | 21.1% | 21.9 M |
| 200 | 1,178,186 | 28.1% | 29.2 M |

**Caveat — this is extrapolated from n ≤ 6.** The fit is clean but the ceiling
itself was not confirmed at scale. Non-size limits may bind first (CosmWasm
per-call gas metering, proof-verification wall time, block production). Phase 1
must include a `find_recv_ceiling.py` that ramps empirically, exactly as
`find_relay_ceiling.py` does for the other direction. Treat 712 as an upper
bound to be tested, not a measured fact.

**Inefficiency worth recording:** each `MsgRecvPacket` carries its own
`proof_commitment` blob, and every one of them repeats the same ~1,014-byte
account proof (all packets share one account and one block). That is ~17% of
per-packet bytes spent re-sending identical data. Deduplicating it would
require a message-format change, so it is a property of the protocol here, not
something the harness can optimise away.

## Unknown 3 — does one MsgUpdateClient cover all N? **YES, and better**

The n=1 batch submitted one `MsgUpdateClient` (`trusted_slot 416 → 1248`,
**780,304 gas, 57,293 tx_bytes**). The n=3 and n=6 batches were then run with
`--no-update` and **both succeeded**, proving against the already-held slot
1248.

So one update covered all 10 packets **across three separate transactions**.

This is structurally different from the other direction, and materially better.
In `batch_scaling` the update is fused invisibly into proof-api's calldata, so
concurrent chunks each carried their own — hence the 27-vs-35 true-count
discrepancy that `relay_pool.py` exists to measure honestly. Here the update is
an **explicit, separately-submitted message the relayer controls**. The count is
deterministic: relay after finality covers the *last* send, and it is exactly 1
per cohort, regardless of how many transactions the receives are split across.

The one real constraint is ordering, not concurrency: you must wait for a
finalized slot covering the newest send block. Observed finality lag was ~70
blocks (~14 min from send to provable). That wait, not proving, is the
wall-clock floor on this direction.

## Two config bugs found (both break this direction)

Both are now fixed at the source: `USER` is `COSMOS_RECEIVER` and the config
layer ignores ambient values for shell-owned names, and every message signer is
resolved from the keyring via `lib.js`'s `signerAddress`. `check_setup.py`
re-checks both every run.

1. **`env.USER` resolves to the Unix username** (`"raaj2045"`), not the Cosmos
   address in `devnet.env`. `config.js` gives `process.env` top precedence and
   `USER` collides with the standard shell variable. `step-native-send.js:31`
   uses it as the packet receiver and only checks truthiness, so it would build
   a packet with a garbage receiver that fails on Cosmos *after* a full finality
   wait. This harness derives its own receivers via `bech32.js`.

2. **`devnet.env`'s `VALIDATOR` is stale** — `cosmos1gawf…` vs the keyring's
   actual `cosmos14qs0l47…`. `step-native-recv.js` uses it as the
   `MsgRecvPacket` signer while `sendtx.py` signs with `RELAYER_KEY`; signer ≠
   actual signer is rejected by the ante handler. This harness resolves the
   signer live from the keyring.

Also noted: `devnet/abi/TestERC20.json` is a trimmed subset with no
`allowance()`, though the deployed contract is a full ERC20.

## Submission-leg data (10 users, for calibration)

All 10 `sendTransfer` calls landed in **one EVM block (1230)** in 4.2 s wall
clock, submitted concurrently under pre-assigned nonces.

- user 0: 200,201 gas (cold — creates the `Escrow` for the client)
- users 1–9: 166,001 gas each
- total 1,694,210 gas, mean 169,421/user

That all 10 landed in one block means **inter-user contention is entirely
unmeasured at this scale** — 200 concurrent submissions will span blocks and
may contend in the txpool. This is the main thing Phase 1 must characterise on
the submission side.

## What this implies for the harness

The flow is *simpler* than `batch_scaling`, not harder:

- **Phase 2 needs no chunk pool at target scale.** One update, one
  `eth_getProof` with N keys, one Cosmos tx. Keep chunking only as a
  safety valve above the measured ceiling.
- **Phase 3 needs no ack pool.** One proof-api request per recv tx, one EVM
  multicall. Pool only if receives were split across multiple txs.
- **`credited_ts` can be exact.** Because all N packets are credited in one
  Cosmos block, `credited_ts` should be that **block's timestamp**, read from
  the tx result — not a subprocess-completion wall clock. This is strictly
  better than `batch_scaling`'s chunk-granular proxy, and the simultaneity is
  real rather than an artifact.
- **The headline distribution is `credited − submitted`**, and its spread will
  come almost entirely from submission-side block placement plus the shared
  finality wait, since the credit itself is simultaneous for the whole batch.
