# migration_volume

Volume harness for the **Ethereum → Cosmos** direction — the migration
direction the paper is about. Many *independent* users each escrow an
Ethereum-native ERC-20 (`TestERC20`) on Ethereum and receive a voucher on
Cosmos.

`../batch_scaling/` measures the opposite direction (Cosmos → Ethereum) and is
superseded for the paper. The two are not symmetric, and mistaking one for the
other invalidates the result:

| | migration_volume (**EVM → Cosmos**) | batch_scaling (Cosmos → EVM) |
|---|---|---|
| Measured leg | forward, **real BLS + MPT** | forward, **mock SP1** |
| Finality-bound | **forward leg** | return (ack) leg |
| proof-api / SP1 | ack leg only | forward leg |
| Batching primitive | N `MsgRecvPacket` in one Cosmos tx | `ICS26Router.multicall` from proof-api |
| Per-tx wall | see [Capacity](#capacity) | geth `txMaxSize`, 128 KB |
| Headline number | **`credited`** — voucher minted on Cosmos | round-trip ack |

## What it measures

One cell is *N independent users migrating at the same time*, run as three
phases and recorded end to end:

1. **Submission.** N distinct EVM accounts each sign and pay for their own
   `ICS20Transfer.sendTransfer`, broadcast concurrently under pre-assigned
   nonces. `ICS20Transfer` *is* `MulticallUpgradeable`, so N transfers could be
   packed into one EVM transaction — the harness deliberately does not, because
   that models a single custodial service and collapses exactly the per-user
   submission cost being measured.
2. **Finality wait.** Beacon finality has to advance past the newest send
   block. This is the wall-clock floor on this direction and is **measured
   directly**, not inferred.
3. **Batched relay.** One `eth_getProof` with N storage keys, one
   `MsgUpdateClient`, one Cosmos transaction of N `MsgRecvPacket` — the places
   a relayer legitimately batches. The update and the receive are **separate
   transactions**, so their gas is measured separately and summed, never
   subtracted one from the other.

The swept variables are the cohort size **N** and the **signer key type** of
the account that submits the receive transaction (`secp256k1` vs `ML-DSA-65`).
A signature is charged once per transaction while packets are charged per
packet, so the post-quantum signer costs a fixed ~5.2 KB and ~146,000 gas per
transaction that batching amortizes — from +82 % per transfer at N = 1 to
+0.8 % at N = 124.

The **destination** key type is not a variable, because it cannot be one:
receivers appear in the packet payload as 20-byte bech32 addresses whatever key
would control them, and a fresh recipient account carries no pubkey on chain
until it first signs. `Dest_Key_Type` is recorded as `none` for this reason.

## Running it

Needs a live devnet (Kurtosis Ethereum enclave, Cosmos chain, instantiated
`cw-ics08-wasm-eth` client) — see [`../../devnet/README.md`](../../devnet/README.md).

```bash
python3 experiments/migration_volume/check_setup.py    # preconditions
python3 measure_data.py                                # the sweep
python3 plot_data.py                                   # the figures
```

`measure_data.py` defaults to N ∈ {1, 10, 50, 100} × 3 trials × both signer key
types, and takes `--n`, `--trials`, `--signers`, `--amount`, `--out` and
`--resume` (which skips cells already in the CSV). It runs a capacity
pre-flight against the node's live `config.toml` before the first cell and
refuses to start if the largest N would not fit — a cell that fails on size
mid-sweep wastes every trial before it.

Every cell carries a run label (`n<N>-t<trial>-<key>`) that both scripts write
into their output filenames and into the JSON itself. Nothing is selected by
timestamp, so a file left over from an earlier trial can never be read as this
one's result.

Individual phases can be run by hand:

```bash
node setup-user-pool.js 100                     # fund 100 EVM users
node submit-migrations.js 100 2000 --label=x    # phase 1
node relay-recv-batch.js "$DEVNET_DIR/migration-volume/send-x.json" \
     --count=100 --label=x --signer-key=relayer # phases 2-3
```

## Outputs

`migration_metrics_detailed.csv`, one row per cell:

| Column | Meaning |
|---|---|
| `N_Users`, `Trial`, `Run_Label` | the cell |
| `Signer_Key_Type`, `Signer_Key` | algorithm and keyring name of the receive-tx signer |
| `Dest_Key_Type` | always `none` — see above |
| `EVM_Gas_Total`, `EVM_Gas_Per_User` | phase-1 submission gas |
| `Cosmos_Gas_Total`, `Cosmos_Gas_Per_Transfer` | update + receive gas, and that per migration |
| `Recv_Gas_Per_Transfer` | receive gas alone, per migration |
| `Update_Client_Gas`, `Recv_Packet_Gas` | the two operations, measured separately |
| `Recv_Tx_Bytes` | on-wire size of the receive transaction |
| `Credited_Height`, `Credited_Block_Ts` | the Cosmos block that credited the cohort |
| `Chain_Host_Skew_s` | block header time minus relay-host clock at that moment |
| `T_Submit_s` | phase 1, measured |
| `T_Finality_Wait_s` | phase 2, measured — real time blocked on beacon finality |
| `T_Proof_and_Relay_s` | phase 3, measured (`eth_getProof` + update + tx) |
| `T_Unattributed_s` | wall clock the four measured spans do not account for |
| `T_Total_Latency_s` | first submission → the Cosmos block that credited the vouchers |
| `Throughput_TPS` | `N / T_Total_Latency_s` |

`T_Unattributed_s` is named for what it is — process startup, poll
granularity, gaps between phases. It is deliberately *not* folded into the
finality wait, which the paper quotes.

**Every span and the total are on the relay host's clock.** The Cosmos block
header time is a different clock — CometBFT derives it from the median of the
previous commit's validator timestamps, so it lags the host by seconds (−6.4 s
observed) — and is carried separately as `Credited_Block_Ts`, with the offset
in `Chain_Host_Skew_s`. Mixing the two books that skew as negative
unattributed time. The whole cohort is still credited in one Cosmos block, so
`Credited_Block_Ts` remains the exact chain-side instant; it just is not
subtracted from a host timestamp.

The driver aborts if the measured phases sum to more than the total, rather
than recording a negative residual.

`plot_data.py` writes `migration_latency_ci.pdf`,
`migration_throughput.pdf`, `cosmos_gas_per_transfer.pdf`,
`cosmos_operation_gas_breakdown.pdf` and `migration_latency_phases.pdf`.

## Capacity

Three infrastructure limits bind before post-quantum signature size matters at
all. Full measurements in [`CEILING-FINDINGS.md`](CEILING-FINDINGS.md).

| Limit | Ceiling | Nature |
|---|---|---|
| RPC `max_body_bytes` (1 MB default) | **124 packets/tx** | node config |
| mempool `max_tx_bytes` (4 MB) | 675 packets/tx | protocol |
| geth state pruning (`TriesInMemory` 128) | ~12-minute relay window | node config |

**124 is a deployment limit, 675 is the protocol limit.** With
`max_body_bytes` at its 1 MB default it is the binding one: `pqchaind tx
broadcast` base64-encodes the transaction into a JSON-RPC body, and base64
inflates by 4/3, so the usable raw transaction is ~750 KB — under a fifth of
`max_tx_bytes`. Over-limit transactions are refused with HTTP 400 at the RPC,
before CheckTx, so they consume nothing.

The ceiling is bound by **transaction body size, not payload size or key
type**. The per-packet slope is bit-identical (6,000.2 B) between signer key
types because it is entirely MPT proof data; ML-DSA-65 and secp256k1 carry the
same 124 packets, a 0.0 % capacity difference. The ceiling also moves between
runs (699 → 675, −3.4 %) as the router's storage trie deepens, so any chunk
size needs a real safety margin and is best re-measured per run.

The relay must run promptly after finality: geth serves `eth_getProof` only
within ~128 blocks of head, and finality lags ~70 blocks, leaving a usable
window of roughly 58 blocks.

## Scripts

| Script | Role |
|---|---|
| `check_setup.py` | Preconditions, re-derived for this direction (see its docstring) |
| `setup-user-pool.js` | Creates/funds N independent EVM users: ETH, self-minted `TestERC20`, allowance |
| `submit-migrations.js` | Phase 1 — N concurrent `sendTransfer` calls, pre-assigned nonces |
| `relay-recv-batch.js` | Phases 2–3 — measured finality wait, then batched forward relay |
| `build-recv-msgs.js` | Builds the `MsgRecvPacket` array without broadcasting, for the ceiling search |
| `find_recv_ceiling.py` | Bisects the per-transaction ceiling per signer key type |
| `run_ceiling_4mb.py`, `bisect_confirm.py` | Ceiling confirmation at the `max_tx_bytes` wall |
| `probe-ack-batching.js` | Phase 0 probe — decodes what proof-api builds for a multi-ack tx |
| `bech32.js` | Minimal bech32, to mint one distinct Cosmos receiver per user |

The sweep driver and plotter live at the repository root: `measure_data.py`
and `plot_data.py`.

## Findings

- [`PHASE0-FINDINGS.md`](PHASE0-FINDINGS.md) — unknowns resolved at 10 accounts.
- [`CEILING-FINDINGS.md`](CEILING-FINDINGS.md) — the measured per-transaction
  ceiling, per signer key type, and the three limits that bind before
  signature size does.
