# devnet tooling

Scripts for driving a transfer and redemption cycle between this chain and a
local Ethereum devnet, with the Cosmos side verified by the real
`cw-ics08-wasm-eth` light client — no attestation, no trusted party.

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
- `node` and `python3`. The scripts use `ethers` v6; point `node_modules` at an
  install that has it.

## Which direction is which

`stake` is Cosmos-native, so **Cosmos holds the escrow and Ethereum holds the
IBCERC20 vouchers**. Redemption therefore burns the voucher on Ethereum and
unescrows on Cosmos — the reverse of the outbound transfer.

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
unmoved. This differs from `lockandmint`'s `event_id` check, which hard-errors.

## Timing

Step 2 is gated on Ethereum finality, which is roughly **two epochs** — about
five minutes on a 6-second-slot devnet, longer on a public testnet. The script
polls and prints its progress:

```
waiting for finality to cover block 3549 (at 3488)
```

A full cycle (outbound transfer, then redemption) waits for finality twice, so
budget **~10 minutes**. This is the protocol's latency, not the script's.

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
| `step-redeem-{send,recv,ack}.js` | The three redemption steps, in order |
| `relayer/update-eth-client.py` | Light-client updates from finality updates |
| `light-client/` | `pubkeys_hash` derivation and its verification |
| `cosmos/sendtx.py` | Assemble, ML-DSA-65 sign, broadcast, await a Cosmos tx |
| `lib/` | Config resolution, EVM/Cosmos helpers, IBC packet encoding |
| `abi/` | Contract ABIs, generated from the Eureka contracts |
