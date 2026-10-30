# Implementation notes

Sections V-B to V-D of the paper summarise this document. Architecture and flows are described in [`docs/architecture.md`](../docs/architecture.md) and [`docs/ethereum-light-client.md`](../docs/ethereum-light-client.md).

## Cosmos light client on Ethereum

Three contracts are deployed on Ethereum:

- **ICS26Router** receives packets and forwards them to registered applications.
- **ICS20Transfer** is the standard fungible-token application. It manages escrow and voucher issuance.
- **SP1ICS07Tendermint** is a light client of the Cosmos chain. Verifying Cosmos signatures on-chain would be too costly, so it instead verifies a Groth16 proof, produced off-chain by the SP1 zkVM, that a valid validator quorum signed a given Cosmos block.

Proofs come from `proof-api`, a service with a gRPC interface. The relayer calls it with a source transaction hash; it fetches the state to be proved from the Cosmos chain, runs the SP1 proving program and returns calldata ready for submission. The contract stores four verification keys derived from the compiled proof programs, so a proof for any other program is rejected. The verifier contract is set when the client is created and cannot be changed afterwards. No custom bridge contract is used: escrow, voucher issuance and packet routing are the standard ICS-20 operations.

## Ethereum light client on Cosmos: initialisation

`cw-ics08-wasm-eth` runs inside the `08-wasm` module. `verify_client_message` checks the BLS aggregate signature of Ethereum's 512-member sync committee, delegating the pairing operations to native Go through a custom query, because running them inside the wasm sandbox would be too slow. `update_state` stores the verified execution-layer state root, and `verify_membership` checks a Merkle-Patricia proof that a packet commitment exists under that root.

Instantiating the client needs two values: the execution-layer state root and a hash of the sync committee's 512 public keys.

- **State root.** Ethereum returns both an execution-layer and a consensus-layer state root in one API response. Membership proofs are checked against the EVM storage trie, so only the execution root applies.
- **Committee hash.** Ethereum's API does not provide it; it returns only the 512 validator public keys, from which the hash must be derived as a Merkle root.

A wrong value for either produces a client that initialises without error but fails at the first membership proof. We therefore check the committee hash separately before instantiation.

## Relayer

The relayer is an off-chain process with funded accounts on both networks, which pay its own transaction fees.

**Ethereum to Cosmos.** The relayer:

1. watches the packet commitment written to ICS26Router's storage (sequence number, source and destination client identifiers, timeout and payload);
2. waits until Ethereum finality covers the block containing it;
3. fetches the beacon-chain finality update and the sync-committee data needed for a client update, and calls `eth_getProof` for a Merkle-Patricia proof of the commitment's storage slot;
4. sends a `MsgUpdateClient` that advances the light client to the finalised Ethereum height, followed by one or more `MsgRecvPacket` messages, each with a packet, its membership proof and the proof height.

All `MsgRecvPacket` messages travel in one Cosmos transaction, and the acknowledgements are written in the same transaction that issues the vouchers.

**Acknowledgement back to Ethereum.** The relayer asks `proof-api` for a proof, passing the hash of the Cosmos transaction that wrote the acknowledgements. The service returns an SP1 Groth16 proof as calldata. The relayer then sends a multicall of `ackPacket` calls to ICS26Router, where SP1ICS07Tendermint verifies the proof and the commitments are cleared. One proof covers every packet in the batch, so the proving cost is paid once per relay transaction rather than once per migration.
