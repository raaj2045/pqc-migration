# Threat model

Section VI-D of the paper summarises this document.

## Security coverage by layer

| Layer | Primitive | Post-quantum? | If broken |
|---|---|---|---|
| Voucher account | ML-DSA-65 | Yes | Attacker spends the voucher |
| Source account | secp256k1 | No | Assets moved before escrow |
| Ethereum consensus | BLS12-381 | No | Forged headers mint unbacked vouchers |
| Cosmos consensus | Ed25519 | No | Forged state releases escrow |
| Cosmos light client on Ethereum | Groth16 (BN254) | No | Forged proofs release escrow |
| Escrow contracts | secp256k1 admin keys | No | Escrow drained or upgraded |
| State commitments | Keccak, SHA-256 | Yes | — |
| Relayer signer | secp256k1 or ML-DSA-65 | — | Liveness only |

## Guarantees

The framework's purpose is to move assets securely without introducing new attacks. It provides two guarantees:

- **Verified transfer.** The destination chain hosts a light client of the source chain. A voucher is credited only if that client verifies the transfer's proof against source-chain consensus it has already accepted. A relayer provides proofs, not claims, so a forged or modified proof cannot produce a credit.
- **Replay safety.** The IBC packet receipt makes a resubmitted proof an idempotent no-op instead of a second credit.

## Threats considered

The transfer path is evaluated assuming that the deployed signature and proof systems remain secure and that both chains satisfy their consensus assumptions. Within that setting, adversaries may operate relayers, submit malformed inputs, and attempt replay, double-crediting or premature-finality attacks.

The mechanism is an incremental transition, not a response to a world in which cryptographically relevant quantum computers already exist. It protects assets only if migration, and all later use of the vouchers, happens while the classical layers in the table above remain secure.

Colluding relayers cannot cause a credit, unlike multisignature and committee bridges, where a threshold of signers attests to events. Here nothing is attested: the target chain checks the source chain's consensus itself. Censorship or delay affects liveness only: any participant can relay the same proof, and repeated submissions are rejected as replays. Proofs for blocks the source chain has not yet finalised are refused.

## Trust assumptions

Transfer safety depends on:

- the consensus security of both chains;
- correct light-client initialisation;
- correct contract and light-client execution;
- the soundness of the SP1/Groth16 pipeline under its setup assumptions.

ML-DSA account authorisation does not remove these dependencies. Completing migration before classical signatures become vulnerable therefore does not give lasting post-quantum security: destination consensus and bridge verification remain classical, and vouchers stay backed by source-chain escrow. The framework supports an incremental transition to post-quantum account authorisation, not complete post-quantum protection of the asset.

## Out of scope

- Attacks on the consensus mechanisms of Ethereum or the Cosmos chain, validator cartels, and economic attacks such as price manipulation.
- Hardening of the source chain: Ethereum assets remain protected by classical cryptography until the escrow transaction is final.
- Vulnerabilities in contract logic, relayer infrastructure compromise, and governance misuse, beyond the tests described in [verification-and-testing.md](verification-and-testing.md).
