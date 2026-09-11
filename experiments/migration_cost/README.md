# migration_cost

Measures what it costs and how long it takes for many independent users to
migrate a token from **Ethereum to Cosmos** at the same time, and whether using
post-quantum signatures changes either.

Each user escrows an Ethereum-native ERC-20 (`TestERC20`) on Ethereum and is
minted a matching `ibc/<hash>` token on Cosmos — an ICS-20 voucher. The ERC-20
is locked, never burned, so total supply is unchanged and the IBC token is a
claim on what is held. The measured leg carries real BLS and
Merkle-Patricia verification; only the acknowledgement uses proof-api.

`../batch_scaling/` measures the opposite direction and is superseded. The two
are not symmetric — confusing them invalidates the result:

| | migration_cost (**Ethereum → Cosmos**) | batch_scaling (Cosmos → Ethereum) |
|---|---|---|
| Measured leg | forward, real BLS + MPT | forward, mock SP1 |
| Waits on finality | forward leg | return leg |
| Batching | N `MsgRecvPacket` in one Cosmos transaction | `ICS26Router.multicall` |
| Headline | voucher credited on Cosmos | round-trip acknowledgement |

## How a migration works

1. **Escrow (Ethereum).** Each user signs and pays for their own
   `ICS20Transfer.sendTransfer`. The tokens go into escrow and `ICS26Router`
   writes a commitment into Ethereum storage. ~166,000 gas each.
2. **Wait for finality (Ethereum).** ~540 s, and the same whether one transfer
   moves or a thousand — it is Ethereum's epoch clock, not a property of the
   batch. This is ~95 % of the wall clock.
3. **CosmWasm verification (Cosmos).** `MsgUpdateClient` makes the
   `cw-ics08-wasm-eth` light client — a CosmWasm contract — BLS-verify
   Ethereum's sync committee and store its state root. ~780,000 gas, charged
   once per batch.
4. **Mint IBC token (Cosmos).** N `MsgRecvPacket` in one Cosmos transaction:
   the contract verifies each proof, mints an `ibc/<hash>` token — an ICS-20
   *voucher* — to each receiver, and **writes the acknowledgement** — one
   transaction, not two. That acknowledgement is what
   the next two steps carry home. ~146,000 gas per transfer. The
   Merkle-Patricia proofs it carries come from a single `eth_getProof`, an
   off-chain read costing no gas but usable only within a ~5-minute window
   (see [LIMITS.md](LIMITS.md)).
5. **SP1 proof generation (off-chain).** proof-api produces one SP1 Groth16
   proof of the acknowledgement, covering the whole batch. ~627 s — the second
   big wait, on a par with Ethereum finality. It runs on neither chain, so it
   costs no gas.
6. **SP1 verification (Ethereum).** The `SP1ICS07Tendermint` light client — a
   Solidity contract — verifies that proof, and `ICS26Router` clears the packet
   commitments, closing them out. ~101,000 gas per transfer.

The two light clients mirror each other: `cw-ics08-wasm-eth` verifies Ethereum
on Cosmos, `SP1ICS07Tendermint` verifies Cosmos on Ethereum. Neither trusts a
relayer or a committee — each checks the other chain's consensus itself.

Users each keep their own Ethereum account and their own Cosmos receiver.
`ICS20Transfer` supports multicall, so all N transfers *could* share one
Ethereum transaction — this deliberately does not, because that models a single
custodial service and erases the per-user cost being measured.

## What is varied

**Batch size** — how many transfers move together — and the **signing key
type** of the account submitting the delivery transaction, `secp256k1` or
`ML-DSA-65`.

A signature is charged once per transaction while transfers are charged per
transfer, so a post-quantum signer adds a fixed ~5.2 KB and ~146,000 gas per
transaction. Split across a larger batch, its share per transfer falls from
+82 % at one transfer to +0.8 % at 124.

The *destination* key type is not a variable and cannot be one: receivers
appear in the payload as 20-byte bech32 addresses whatever key controls them,
and a fresh recipient holds no public key on chain until it first signs.

## Running it

Needs a live devnet — see [`../../devnet/README.md`](../../devnet/README.md).
Run from the repository root.

```bash
python3 experiments/migration_cost/check_setup.py       # preconditions

# Cosmos accounts to sign with, one per concurrent flow
python3 experiments/migration_cost/setup-signer-pool.py --size 10 --key-type secp256k1
python3 experiments/migration_cost/setup-signer-pool.py --size 10 --key-type mldsa65

# time per step: full migrations, one finality wait each
python3 experiments/migration_cost/measure_data.py --n=1 --trials=50 --concurrency=10

# cost by batch size: one finality wait shared by the whole run
python3 experiments/migration_cost/measure_delivery.py --sizes=10,25,50 --repeats=3

# the return leg, over deliveries already made
python3 experiments/migration_cost/measure_ack.py --concurrency=9

# transfers per second, as more relayers work at once
python3 experiments/migration_cost/measure_throughput.py --repeats=15

python3 experiments/migration_cost/plot_data.py         # figures and table
```

**Running several migrations at once.** `--concurrency=K` runs K together.
Waiting for finality and updating the light client happen **once per group** and
are shared; submitting, proving and delivering happen per migration and run
concurrently, so those timings carry real contention. Each flow needs its own
Cosmos signing account and its own slice of the Ethereum account pool, because
two transactions in flight from one account race for the same nonce.

Shared steps are recorded against the group, and repeats of a shared step are
counted per group rather than per row — otherwise one finality measurement
copied across K rows would read as K independent samples.

**Measuring cost without repeating the wait.** `measure_delivery.py` submits
every packet a run needs, pays the finality wait once, then delivers batch after
batch against that state. The deliveries are real transactions carrying real
proofs; only the shared prelude stops repeating. A row's `T_Deliver_s` is
therefore the delivery step alone, not an end-to-end time.

## Outputs

Raw data goes to `results/`. The files below are committed: they are the data
behind the figures and the cost table. Re-running against your own devnet
adds to them.

| File | Holds |
|---|---|
| `results/latency_by_step.csv` | one row per migration: time and gas per step |
| `results/delivery_*.csv` | one row per delivery: gas, bytes, chunking, by batch size |
| `results/ack_by_batch.csv` | one row per acknowledgement |
| `results/throughput_*.csv` | one row per round: relayers, transfers, seconds, rate |

`plot_data.py` reads all of them and writes:

| Output | Shows |
|---|---|
| `fig_time_by_step.pdf` | time each step takes, both key types |
| `fig_throughput_by_workers.pdf` | transfers credited per second, as more relayers work at once |
| `cost_by_batch.md` | gas per transfer on both legs, by batch size |

The figure covers the whole round trip. Proving is the **real** SP1 Groth16
measurement, ~627 s, taken from
`../migration_throughput/results/real-verifier/`; the mock verifier's proof
check is a no-op and is not a cost worth plotting. Proving is on a par with the
Ethereum finality wait, so the two of them together are almost the entire round
trip: 1,195 s, against 561 s to the point the vouchers are credited.

Fetching the Merkle-Patricia proof is not shown. It is an off-chain read taking
hundredths of a second and costing no gas.

**What the throughput figure calculates.** Each round has N relayers deliver 50
transfers each, at the same time. The rate is

    (N relayers x 50 transfers) / seconds for all of them to finish

Timed from launching the relayers to the last one landing its Cosmos
transaction: fetching proofs, building and signing messages, broadcasting, and
waiting for the Cosmos block. It excludes the Ethereum finality wait, which is
paid once before any of it and is a fixed delay rather than a limit on the rate,
and it excludes the return leg, whose proof takes about ten minutes. Both are in
`fig_time_by_step.pdf`.

The two signing keys sit on top of each other at every relayer count, which is
the result: the key does not change the rate.

Error bars are 95 % confidence intervals. How many runs sit behind each step
differs, because waiting for finality and verifying the light client happen
once for a whole group of migrations, not once each: submitting, delivering
and acknowledging rest on 60 runs with the ordinary key and 50 with the
post-quantum one, while the finality wait and the light-client verification
rest on 6 and 5 groups. Table rows print their own counts.

**Timing uses one clock.** Every step and every total is measured on the relay
host. The Cosmos block header runs on a different clock — CometBFT takes it from
the median of the previous commit's validator timestamps, so it lags by seconds
— and is never one end of a subtraction. `T_Other_s` holds whatever the measured
steps do not cover: process start-up, polling granularity, gaps between steps.

## Limits

| Limit | Set by | Ceiling |
|---|---|---|
| Delivery size | CometBFT `max_tx_bytes` 4 MB | ~675 transfers |
| Delivery over stock RPC | CometBFT `max_body_bytes` 1 MB | 124 transfers |
| **Acknowledgement size** | **geth `txMaxSize` 128 KB** | **~56 transfers** |
| Proof availability | geth `TriesInMemory` 128 | ~5-minute window |

**~56 is the one that binds.** An acknowledgement batch is whatever one
delivery produced and cannot be split, so delivering more than ~56 transfers at
once leaves them impossible to acknowledge. Deliveries above that size are
recorded and skipped rather than attempted.

None of these depend on the signature algorithm. Full measurements in
[LIMITS.md](LIMITS.md).

## Files

| File | Role |
|---|---|
| `check_setup.py` | Verifies preconditions before a run |
| `measure_data.py` | Full migrations; writes `results/latency_by_step.csv` |
| `measure_delivery.py` | Delivery cost by batch size, one shared finality wait |
| `measure_ack.py` | The return leg, over deliveries already made |
| `measure_throughput.py` | Transfers per second, as more relayers work at once |
| `plot_data.py` | Figures and the cost table |
| `setup-signer-pool.py` | Cosmos signing accounts, one per concurrent flow |
| `setup-user-pool.js` | Ethereum accounts: ETH, `TestERC20`, allowance |
| `submit-migrations.js` | Concurrent `sendTransfer` calls under pre-assigned nonces |
| `relay-recv-batch.js` | Finality wait, client update, proof fetch, delivery |
| `relay-ack-batch.js` | One acknowledgement batch back to Ethereum |
| `build-recv-msgs.js` | Builds `MsgRecvPacket` without broadcasting |
| `find_recv_ceiling.py` | Finds the delivery ceiling per signing key type |
| `bech32.js` | Minimal bech32, one distinct Cosmos receiver per user |
