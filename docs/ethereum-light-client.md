# Ethereum light client

Only the **08-wasm** client route is registered. Ethereum is tracked by
`cw-ics08-wasm-eth` stored via `MsgStoreCode` and instantiated with
`MsgCreateClient`. There is deliberately no 07-tendermint, solomachine, or
attestations route: this chain verifies Ethereum, not other Cosmos chains.

`MsgStoreCode` is gated on the gov module address, so storing the client wasm
requires a passing governance proposal. On a devnet, shorten the voting period
in genesis (`app_state.gov.params.voting_period`) or the proposal will not pass
within a working session.

## Consensus state fields

### `state_root` is the execution root, not the beacon root

`ConsensusState.state_root` must be the **execution** state root. Membership
proofs run `verify_account_storage_root` against it
(`packages/ethereum/light-client/src/membership.rs`), and client updates take it
from `consensus_update.finalized_header.execution.state_root`
(`.../src/update.rs`).

Seeding the beacon state root instead produces a client that instantiates
cleanly and then fails every membership proof. The Beacon API's
`light_client/bootstrap` response carries both, one field apart:

```
.data.header.beacon.state_root       <- WRONG
.data.header.execution.state_root    <- correct
```

### `pubkeys_hash` must be supplied by the caller

`ConsensusState.current_sync_committee` is a `SummarizedSyncCommittee`
(`{pubkeys_hash, aggregate_pubkey}`). The Beacon API serves the 512 public keys
but not their root, and the contract does not compute it — it deserializes the
struct as given. The caller must compute

```
pubkeys_hash = hash_tree_root(Vector[BLSPubkey, 512])
```

`devnet/light-client/pubkeys_hash.py` implements this: each 48-byte key is
packed into two 32-byte chunks and merkleized, then the 512 resulting roots are
merkleized.

Because a wrong hash also instantiates cleanly and only fails later,
`devnet/light-client/verify_pubkeys_hash.py` checks it against consensus data
rather than trusting the implementation: it folds the bootstrap's
`current_sync_committee_branch` up to the finalized header's `state_root`. If
the derivation is correct the fold reproduces the on-chain root exactly.

```bash
cd devnet
python3 light-client/pubkeys_hash.py <pubkeys.json>
python3 light-client/verify_pubkeys_hash.py [bootstrap.json]
```

## Client updates need the BLS querier

`MsgUpdateClient` makes the contract issue an `AggregateVerify` custom query
back to the chain. `app.go` registers `blsverifier.CustomQuerier()` for this;
without it every update fails. See [Getting started](getting-started.md#cgo-libstdc-is-required) for the
`libstdc++` link requirement that comes with it.

---

[Project README](../README.md) · [Architecture](architecture.md) · [Devnet runbook](../devnet/README.md)
