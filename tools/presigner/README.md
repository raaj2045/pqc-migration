# presigner

Builds a pool of pre-signed `MsgSend` transactions for the
validator-scaling experiment. Two subcommands:

| Subcommand        | Purpose                                                                                                                                                                                              |
|-------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `emit-addresses`  | Deterministically derives N loadgen-sender keys from a fixed seed (per-scheme, per-index) and writes a JSONL listing of `{name, index, address}` records. Used by `init_testnet.sh` to fund the pre-determined accounts in genesis. |
| `sign`            | Re-derives the same keys, builds and signs `MsgSend` transactions in `SIGN_MODE_DIRECT`, and emits a JSONL of `{sender_idx, sequence, tx_b64}` records. The pool is large (~1 GB for ML-DSA-44 at 25 k txs × 8 senders).             |

## Why pre-signed

The validator-scaling experiment treats the **scheme** axis as the
user-account tx-signing algorithm. We sidestep cosmjs's lack of
ML-DSA-44 support by signing transactions out-of-band with this Go
tool, then having the loadgen replay them via
`/broadcast_tx_sync`. This decouples signing throughput from broadcast
throughput and makes the sweep reproducible at any rate without
re-keying.

## Building

```bash
go build -o presigner_bin .
```

The local `replace` directives in `go.mod` point at `../../cosmos/`
for `github.com/cosmos/cosmos-sdk` (and the four `cosmossdk.io/*`
sub-modules). Building from outside this directory will not pick up
the fork.

## Usage

```bash
# Emit address files for genesis funding
./presigner_bin emit-addresses --scheme secp256k1 --count 8 --out secp_addrs.jsonl
./presigner_bin emit-addresses --scheme mldsa44   --count 8 --out mldsa_addrs.jsonl

# Build the actual signed-tx pool (this is the big one)
./presigner_bin sign --scheme secp256k1 --senders 8 --per-sender 25000 \
  --chain-id testnet --out secp.jsonl
./presigner_bin sign --scheme mldsa44   --senders 8 --per-sender 25000 \
  --chain-id testnet --out mldsa.jsonl
```

Total disk: ~86 MB (secp) + ~1.0 GB (mldsa44).

The `address-deriving seed` is `SHA-256("loadgen|" + scheme + "|" + index)`,
so emit-addresses and sign always produce matching keys for the same
(scheme, index). This is reproducibility-critical: the same fixed
seed means `account_num` 0–15 in genesis is stable across N (4 / 7 /
16 validators).

## Gas defaults

`sign` defaults to `gas_limit=250000` per tx — enough for ML-DSA-44
verification (measured at ~195 k gas) plus a 25 % margin. Override
with `--gas-limit` if you need to experiment.
