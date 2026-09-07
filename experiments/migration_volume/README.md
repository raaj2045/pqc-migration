# migration_volume

Measures what it costs and how long it takes for many independent users to
migrate a token from **Ethereum to Cosmos** at the same time, and whether using
post-quantum signatures changes either.

Each user escrows an Ethereum-native ERC-20 (`TestERC20`) on Ethereum and is
credited a voucher on Cosmos. The measured leg carries real BLS and
Merkle-Patricia verification; only the acknowledgement uses proof-api.

`../batch_scaling/` measures the opposite direction and is superseded. The two
are not symmetric — confusing them invalidates the result:

| | migration_volume (**Ethereum → Cosmos**) | batch_scaling (Cosmos → Ethereum) |
|---|---|---|
| Measured leg | forward, real BLS + MPT | forward, mock SP1 |
| Waits on finality | forward leg | return leg |
| Batching | N `MsgRecvPacket` in one Cosmos transaction | `ICS26Router.multicall` |
| Headline | voucher credited on Cosmos | round-trip acknowledgement |

## How a migration works

1. **Submit.** Each user signs and pays for their own
   `ICS20Transfer.sendTransfer`. The tokens go into escrow and `ICS26Router`
   writes a commitment into Ethereum storage. ~166,000 gas each.
2. **Wait for Ethereum finality.** ~540 s, and the same whether one transfer
   moves or a thousand — it is Ethereum's epoch clock, not a property of the
   batch. This is ~95 % of the wall clock.
3. **Update the light client.** `MsgUpdateClient` makes `cw-ics08-wasm-eth`
   BLS-verify the sync committee and store Ethereum's state root. ~780,000 gas,
   charged once per batch.
4. **Fetch one proof.** A single `eth_getProof` returns proofs for every
   transfer in the batch. Free, and subject to a ~12-minute window (see
   [LIMITS.md](LIMITS.md)).
5. **Deliver.** N `MsgRecvPacket` in one Cosmos transaction; the contract
   verifies each proof and mints the vouchers. ~146,000 gas per transfer.
6. **Acknowledge.** proof-api returns one multicall of N `ackPacket` calls,
   closing the packets on Ethereum. ~101,000 gas per transfer.

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
python3 experiments/migration_volume/check_setup.py       # preconditions

# Cosmos accounts to sign with, one per concurrent flow
python3 experiments/migration_volume/setup-signer-pool.py --size 10 --key-type secp256k1
python3 experiments/migration_volume/setup-signer-pool.py --size 10 --key-type mldsa65

# time per step: full migrations, one finality wait each
python3 experiments/migration_volume/measure_data.py --n=1 --trials=50 --concurrency=10

# cost by batch size: one finality wait shared by the whole run
python3 experiments/migration_volume/measure_delivery.py --sizes=10,25,50 --repeats=3

# the return leg, over deliveries already made
python3 experiments/migration_volume/measure_ack.py --concurrency=9

python3 experiments/migration_volume/plot_data.py         # figures and table
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

Raw data goes to `results/` (not tracked — regenerate against your own devnet).

| File | Holds |
|---|---|
| `results/latency_by_step.csv` | one row per migration: time and gas per step |
| `results/delivery_*.csv` | one row per delivery: gas, bytes, chunking, by batch size |
| `results/ack_by_batch.csv` | one row per acknowledgement |

`plot_data.py` reads all of them and writes:

| Output | Shows |
|---|---|
| `fig_time_by_step.pdf` | time each step takes, both key types |
| `fig_time_by_batch.pdf` | total time against batch size |
| `cost_by_batch.md` | gas per transfer on both legs, by batch size |

Error bars are 95 % confidence intervals, and every figure and table row prints
its repeat count. Counts differ between steps because waiting for finality and
updating the client happen once per group.

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
| Proof availability | geth `TriesInMemory` 128 | ~12-minute window |

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
| `plot_data.py` | Figures and the cost table |
| `setup-signer-pool.py` | Cosmos signing accounts, one per concurrent flow |
| `setup-user-pool.js` | Ethereum accounts: ETH, `TestERC20`, allowance |
| `submit-migrations.js` | Concurrent `sendTransfer` calls under pre-assigned nonces |
| `relay-recv-batch.js` | Finality wait, client update, proof fetch, delivery |
| `relay-ack-batch.js` | One acknowledgement batch back to Ethereum |
| `build-recv-msgs.js` | Builds `MsgRecvPacket` without broadcasting |
| `find_recv_ceiling.py` | Finds the delivery ceiling per signing key type |
| `run_ceiling_4mb.py`, `bisect_confirm.py` | Ceiling confirmation at the 4 MB wall |
| `probe-ack-batching.js` | Decodes what proof-api builds for a multi-ack request |
| `bech32.js` | Minimal bech32, one distinct Cosmos receiver per user |
