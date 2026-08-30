# Devnet runbook

Scripts for driving a transfer and redemption cycle between this chain and a
local Ethereum devnet, with the Cosmos side verified by the real
`cw-ics08-wasm-eth` light client — no attestation, no trusted party.

For the design these scripts exercise, see
[Architecture](../docs/architecture.md). For build requirements and the
hardware needed for local proving, see
[Getting started](../docs/getting-started.md).

These are JavaScript and Python only: `devnet/` is not a Go package and is
invisible to `go build ./...`.

## Configuration

Every host path and endpoint is resolved by `lib/config.js` and `lib/config.py`,
which read, highest precedence first:

1. the process environment
2. `devnet.env` (git-ignored, create it from the example)
3. `devnet.env.example` defaults
4. the generated env files inside `$DEVNET_DIR` — `ports.env`, `cosmos.env`,
   `deploy.env` — which the devnet tooling itself writes

```bash
cp devnet.env.example devnet.env   # then edit
# or override per-invocation:
DEVNET_DIR=/path/to/devnet node step-redeem-send.js 500000
```

`$DEVNET_DIR` is the working directory holding those generated env files and
the JSON hand-off files the steps write between each other
(`redeem-send.json`, `redeem-ack.json`).

## Prerequisites

- A Cosmos node running this chain, with the Ethereum light client already
  instantiated (see [../docs/ethereum-light-client.md](../docs/ethereum-light-client.md)).
- A local Ethereum devnet at **Electra or later** with the Beacon API's
  light-client endpoints enabled, and the IBC Eureka contracts deployed
  (`ICS26Router`, `ICS20Transfer`, escrow, `SP1ICS07Tendermint`).
- A running **proof-api** (solidity-ibc-eureka) with a `cosmos_to_eth` module
  pointed at this chain and this EVM. `step-recv.js` and `step-redeem-ack.js`
  request their proofs from it; see [Proving](#proving) below.
- `node` and `python3`. The scripts use `ethers` v6 plus `@grpc/grpc-js` and
  `@grpc/proto-loader`; point `node_modules` at an install that has them.
  Note that `node_modules` here is typically a **symlink** to a shared install —
  running `npm install` in this directory replaces the symlink with a real
  directory and silently strips everything except the newly-installed packages.
  Install into the symlink's target instead.

## The EVM devnet does not survive a restart

The Kurtosis Ethereum enclave's geth datadir (`--datadir=/data/geth/execution-data`)
is **not volume-backed**, so any container restart or host reboot discards the
execution layer's state and re-initialises it from genesis. The consensus layer
resumes from where it was, leaving the two irreconcilable.

The practical consequence: after a restart, `eth_getCode` returns `0x` for every
deployed contract and the chain reports block 0. Restarting the containers does
not recover it. A new enclave is required, followed by a redeployment via
[`deploy/DevnetDeploy.s.sol`](deploy/README.md) and creation of a new light
client — the previous one references contract addresses that no longer hold
code, and a beacon chain that no longer exists.

Docker also reassigns host ports on each enclave, so `ports.env` and any
addresses in `deploy.env` are stale afterwards.

The Cosmos chain is unaffected: its data persists on disk, including stored
wasm code, so it restarts normally.

## Proving

The EVM-side light client (`SP1ICS07Tendermint`) verifies Cosmos state with SP1
Groth16 proofs. Which verifier it uses is fixed **at client creation**, by the
address passed as proof-api's `sp1_verifier` parameter:

| Verifier | Use |
|---|---|
| `SP1VerifierGroth16` (v6.1.0) | Real proving. Proofs are cryptographically checked on chain. |
| `SP1MockVerifier` | Fast iteration. Accepts any public values with an empty proof. **No checking at all.** |

Both are deployed; only one is bound. A client bound to the mock cannot be
switched to the real verifier — create a new client instead.

### Why client creation goes through proof-api

The client's four program verification keys must match the SP1 program ELFs.
proof-api derives them from the ELF bytes (`SP1Program::get_vkey`) and returns
the full deployment calldata from `CreateClient`, along with client and
consensus state read live from this chain. Constructing the client by hand
means faking both — which is only survivable against the mock verifier, because
a real verifier rejects a zero verification key.

Pass `role_manager` = the `ICS26Router` address. Omitting it defaults to
`address(0)`, which takes the contract's **permissionless** branch
(`_grantRole(PROOF_SUBMITTER_ROLE, address(0))`) and lets anyone submit proofs.

### Cost

Measured on 6 cores / 12 threads, CPU prover, artifacts already cached:

| | |
|---|---|
| Time per proof | **~10 min** (~8-9 min STARK/recursion, ~60-90 s Groth16 wrap) |
| Peak memory | **~27.5 GB** of a 28 GB ceiling, plus 5-8 GB swap |
| Groth16 artifacts | 5.8 GB download once, unpacking to 7.9 GB in `~/.sp1/circuits/` |

Two operational requirements:

- **Restart proof-api between proofs.** It does not release swapped pages. After
  one proof it held 6.1 GB of swap — 78 % of all swap in use — leaving 113 MB
  free, so the next proof started from far worse headroom and saturated swap
  during recursion. Restarting is safe: proof-api holds no chain or client
  state, and costs ~30 s of prover re-init.
- **Alarm on the recursion phase, not the wrap.** Recursion is where headroom is
  actually consumed. A threshold that first fires during the wrap fires too
  late: by then the proof is seconds from completing, and acting on the warning
  discards ~10 minutes of work for nothing.

### Evidence that verification is real

Against a client bound to `SP1VerifierGroth16`, on a relay transaction replayed
at the block before it landed:

| Input | Result | Kind |
|---|---|---|
| Untampered (control) | succeeds | — |
| Proof bytes altered | `ProofInvalid()` `0x7fcdd1f4` | **cryptographic** — the Groth16 pairing check fails |
| Verification key altered | `VerificationKeyMismatch(bytes32,bytes32)` `0xd56bdc26` | **cryptographic** — client enforces its stored vkeys |
| Bytes altered mid-calldata | `FailedCall()` `0xd6bda275` | *structural* — the multicall's inner encoding broke |

Only the first two are evidence about proof verification. `FailedCall()` means
the transaction never reached the verifier, so it says nothing about whether
proofs are checked; it is listed here so it is not mistaken for a
cryptographic rejection. A call trace of the same transaction shows two calls
to the Groth16 verifier — the client update and the membership proof — and none
to the mock.

## Which direction is which

`stake` is Cosmos-native, so **Cosmos holds the escrow and Ethereum holds the
IBCERC20 vouchers**. Redemption therefore burns the voucher on Ethereum and
unescrows on Cosmos — the reverse of the outbound transfer. See
[Architecture](../docs/architecture.md#asset-transfer).

## Forward leg

A Cosmos→EVM transfer is submitted as a `MsgTransfer` with
`encoding: application/x-solidity-abi` (the CLI cannot set that field), then
relayed:

```bash
# Relay the packet to the EVM side. Requests the proof from proof-api, which
# returns one ICS26Router multicall carrying both the client update and the
# packet membership proof. With the real verifier this takes ~10 minutes.
node step-recv.js <cosmos-tx-hash>

# Acknowledge it back on Cosmos.
node step-ack.js
```

## Redemption cycle

Run in order. Each step depends on the previous one's output.

```bash
cd devnet

# 1. Burn the voucher on Ethereum and emit an IBC packet back to Cosmos.
#    Writes $DEVNET_DIR/redeem-send.json.
node step-redeem-send.js 500000

# 2. Prove that burn to Cosmos and unescrow. Waits for the Ethereum devnet to
#    finalize the block containing the burn, advances the light client, then
#    submits MsgRecvPacket. Note the returned txhash.
node step-redeem-recv.js

# 3. Prove the Cosmos acknowledgement back to Ethereum, closing the packet.
node step-redeem-ack.js <recv-txhash-from-step-2>
```

### Replay protection

```bash
node step-redeem-recv.js --replay
```

Resubmitting an identical proof is an **idempotent no-op, not an error**: the
transaction returns `code 0` and consumes gas, but the packet receipt prevents
any state change — no token callback, no second acknowledgement, balances
unmoved. Replay safety here comes from the packet receipt, not from an
application-level identifier check.

## Timing

Step 2 is gated on Ethereum finality, which is roughly **two epochs** — about
five minutes on a 6-second-slot devnet, longer on a public testnet. The script
polls and prints its progress:

```
waiting for finality to cover block 3549 (at 3488)
```

A full cycle (outbound transfer, then redemption) waits for finality twice, so
budget **~10 minutes**. This is the protocol's latency, not the script's.

With the **real** Groth16 verifier bound, proving dominates instead: each of the
two SP1 legs (`step-recv.js` and `step-redeem-ack.js`) adds ~10 minutes of CPU
proving on top, so a full cycle is closer to **~30 minutes**. Against the mock
verifier those legs are near-instant. See [Proving](#proving).

## Light-client helpers

```bash
python3 light-client/pubkeys_hash.py <pubkeys.json>   # SSZ HTR of the sync committee
python3 light-client/verify_pubkeys_hash.py [bootstrap.json]
```

The second validates the first against consensus data by folding the
bootstrap's sync-committee branch to the finalized header's `state_root`. See
[../docs/ethereum-light-client.md](../docs/ethereum-light-client.md).

## Relayer

`relayer/update-eth-client.py` polls the Beacon API's
`light_client/finality_update` and submits `MsgUpdateClient`, performing real
on-chain BLS verification of the 512-key sync committee. Step 2 invokes it
automatically; run it standalone to keep the client current:

```bash
python3 relayer/update-eth-client.py 08-wasm-1
```

It retries when the Cosmos block clock briefly lags the beacon's signature
slot, which is expected rather than an error.

## Layout

| Path | Purpose |
|---|---|
| `step-recv.js` | Forward leg: relay a Cosmos→EVM packet, with a real SP1 proof |
| `step-ack.js` | Forward leg: acknowledge that packet back on Cosmos |
| `step-redeem-{send,recv,ack}.js` | The three redemption steps, in order |
| `relayer/update-eth-client.py` | Light-client updates from finality updates |
| `light-client/` | `pubkeys_hash` derivation and its verification |
| `cosmos/sendtx.py` | Assemble, ML-DSA-65 sign, broadcast, await a Cosmos tx |
| `lib/` | Config resolution, EVM/Cosmos helpers, IBC packet encoding |
| `lib/proofapi.js` | gRPC client for proof-api (`RelayByTx`, `CreateClient`, `Info`) |
| `abi/` | Contract ABIs, generated from the Eureka contracts |

---

[Project README](../README.md) · [Architecture](../docs/architecture.md) · [Getting started](../docs/getting-started.md) · [EVM deployment](deploy/README.md)
