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
   proof of the acknowledgement, covering the whole batch. ~637 s for a single
   packet, ~877 s at 10-20 packets and ~1,222 s at 30-40 — the second big wait,
   on a par with Ethereum finality. It steps up with batch size rather than
   growing per packet, and it is where this leg runs out of memory
   (see [Proving cost against packet count](#proving-cost-against-packet-count)).
   It runs on neither chain, so it costs no gas.
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
| `results/ack_by_batch.csv` | one row per acknowledgement, with proving memory where it was sampled |
| `results/throughput_*.csv` | one row per round: relayers, transfers, seconds, rate |

`plot_data.py` reads all of them and writes:

| Output | Shows |
|---|---|
| `fig_time_by_step.pdf` | time each step takes, both key types |
| `fig_throughput_by_workers.pdf` | transfers credited per second, as more relayers work at once |
| `cost_by_batch.md` | gas per transfer on both legs, by batch size |

The figure covers the whole round trip, drawn at one transfer per migration
because that is the only batch size `latency_by_step.csv` holds. Its two SP1
bars come from real-prover runs **on this path only**, and `plot_data.py`
prints which runs fed each bar every time it draws them:

| Bar | Runs behind it | Value |
|---|---|---|
| SP1 proof generation (off-chain) | `redeem-ack` 581.2 s, `native-ack` 693.5 s | 637.4 s |
| SP1 verification (Ethereum) | `d20-r1-validator` 4.2 s and the batch sweep at 10, 30, 40 (8.3, 8.3, 8.4 s) | 7.3 s |

Both were wrong before. Proving averaged all eight proofs in
`../migration_throughput/results/real-verifier/` — but six of those are
deliveries of a Cosmos → Ethereum transfer, the opposite direction and a
different operation, so the bar reported 627.1 s and then 635.4 s for a leg
that actually costs 637.4 s at one packet. Verification came from mock runs,
where the proof check does nothing.

The verification bar is pooled across batch sizes 10-40 because no real
acknowledgement was ever relayed at batch 1: the two single-packet runs
recorded proving time but not submission time. Landing the transaction is
block-inclusion latency and does not depend on how many acknowledgements it
carries, so pooling is defensible — but it is a different batch size from the
rest of the figure, and the run list above says so rather than hiding it.

Proving is on a par with the Ethereum finality wait, so the two of them
together are almost the entire round trip: ~1,200 s, against 561 s to the
point the vouchers are credited.

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

## Proving cost against packet count

Almost every acknowledgement in `results/ack_by_batch.csv` was relayed against
the mock verifier, where no proof is generated and `T_Prove_s` is a few seconds
of plumbing. Those rows say nothing about proving. The question they leave open
is whether an SP1 Groth16 proof costs the same whatever the batch holds, or
grows with the number of packets in it.

A sweep answers it. Every run below is on **one devnet deployment in one
session**, with proof-api restarted before each proof — it does not release
swapped pages, so a reused prover would hand each run less headroom than the
last and the sweep would measure the restart policy instead of the batch size.

| Packets | Proving | Gas | Gas/packet | Relay bytes | Peak resident | Peak swap | Completed |
|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 887.3 s | 1,326,946 | 132,695 | 22,468 | 25.54 GiB | 9.36 GiB | yes |
| 20 | 866.7 s | 2,288,392 | 114,420 | 43,268 | 25.80 GiB | 11.63 GiB | yes |
| 30 | 1,217.4 s | 3,278,784 | 109,293 | 64,068 | 25.58 GiB | 17.36 GiB | yes |
| 40 | 1,226.5 s | 4,262,125 | 106,553 | 84,868 | 25.78 GiB | 21.93 GiB | yes |
| 50 | — | — | — | — | 26.46 GiB | 30.91 GiB | **no — out of memory** |
| 60 | not attempted | | | | | | |

### Proving time is a step, not a slope

It is flat within a range and then jumps:

| | Mean | Spread inside the group |
|---|---:|---:|
| 10 and 20 packets | 877.0 s | 20.6 s |
| 30 and 40 packets | 1,222.0 s | 9.1 s |

Doubling 10 to 20 changed proving by **-20.6 s** — it got slightly *faster* —
and doubling 30 to 40 changed it by 9.1 s. Between the two groups the cost
jumps 345 s. So there is no meaningful per-packet term inside a group, and
dividing the step by packet count to quote "seconds per packet" invents a
linear cost that the data does not show. An earlier two-point comparison here
did exactly that and reported ~12 s per packet; with four points that reading
does not survive.

The shape is what a recursive prover gives: the work is proved in a fixed
number of shards, packets are cheap until they need one more shard, and the
cost of that shard lands all at once. The step falls between 20 and 30 packets.
Where the next one falls is unmeasured — 50 never finished.

### Peak memory does scale, but look at swap, not resident

Resident memory is flat at 25.5-25.8 GiB across every completed run. That is
not proving being frugal, it is the host ceiling: 27.41 GiB total, and the
prover is simply not allowed more. What actually grows is swap, close to
linearly at **0.43 GiB per packet**:

    9.36 -> 11.63 -> 17.36 -> 21.93 GiB, at 10, 20, 30 and 40 packets

Extrapolating gives ~25.9 GiB at 50 packets. The kernel killed proof-api with
30.91 GiB of swap in use — 97 % of the 32 GiB available — so the real curve
bends upward above 40 rather than continuing straight.

Reporting only resident memory would have made this leg look flat in memory
too. It is not; the growth was just hidden below the ceiling.

### What binds first: memory, and not by a little

| Candidate limit | Where it bites | Reached? |
|---|---|---|
| Host memory and swap | 50 packets | **yes — this is the wall** |
| geth `txMaxSize` 128 KB (~56 acks) | ~56 packets | no, never reached |
| CometBFT delivery limits | ~124 and ~675 packets | no |

**Memory binds first.** The 56-packet calldata cap in [LIMITS.md](LIMITS.md) is
real arithmetic — at the measured ~2,122 bytes per ack a 56-ack multicall is
about 119 KB against a 118 KB usable budget — but this host cannot prove a
batch that large, so the cap never gets the chance to reject one. At 40
packets — the largest that worked — the signed transaction was 84,979 B of
117,964 B usable, 72 % of the calldata cap, while resident memory was at 99 %
of the machine's 27.41 GiB and swap at 69 % of its 32 GiB. Calldata had room
to spare; memory did not. The two ceilings are close enough, though, that a
host with more swap would run into the calldata cap soon after clearing the
memory one.

**The largest batch that completed is 40 packets.** 50 was not retried and 60
was not attempted, both by design: a size that exhausts memory tells you where
the ceiling is, and running further up the curve only repeats the answer at
greater cost.

### Reading the 50-packet failure

The figures for 50 come from the kernel's own report of the process it killed
(`journalctl -b -1`), not from `memsample.js`: the sampler writes its reading
through the relay client, and the host went down before that could happen. They
are one snapshot at the moment of the kill rather than a sampled peak, so the
true peak is at least that and possibly higher. `ack-proofFailed-sweep-d50-r1-validator.json`
says so in the file. The proof had been running about 21 minutes when it died,
which is recorded separately from `T_Prove_s` because it is not a proving time
— that proof never produced anything.

The host went down shortly after the kill, which took the Ethereum devnet with
it (its geth datadir is not volume-backed, see
[`../../devnet/README.md`](../../devnet/README.md)). So 60 could not have been
run on this deployment afterwards even had the stopping rule allowed it, and a
60-packet figure would not belong in this table regardless: the whole point of
the sweep was to hold the deployment constant.

Gas per transfer keeps falling across the completed runs, 132,695 down to
106,553, for the reason the mock rows already showed: the proof check is
charged once per transaction, so its share per transfer shrinks as the batch
grows.

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
| `relay-ack-batch.js` | One acknowledgement batch back to Ethereum, sampling memory while it proves |
| `build-recv-msgs.js` | Builds `MsgRecvPacket` without broadcasting |
| `find_recv_ceiling.py` | Finds the delivery ceiling per signing key type |
| `bech32.js` | Minimal bech32, one distinct Cosmos receiver per user |
