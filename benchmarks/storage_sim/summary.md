# Storage simulation — secp256k1 vs ML-DSA-65

Every transaction is an Ethereum → Cosmos migration: one `MsgRecvPacket` (ICS-20 receive, 4,513 B, measured in `experiments/migration_cost/`), signed by the relayer. Each migration credits a new receiver account — one per migration — and that account holds **no public key** until its owner first signs a Cosmos transaction. The scheme is the key type of every signer: the relayer now, and the migrated users once they sign. Constants and references are in `tools/storage_sim/main.go`.

## Per-tx wire size

| Component | secp256k1 | ML-DSA-65 |
|---|---:|---:|
| Envelope overhead | 110 B | 110 B |
| `MsgRecvPacket` | 4,513 B | 4,513 B |
| Relayer public key | 33 B | 1,952 B |
| Relayer signature | 64 B | 3,309 B |
| **Total per tx** | **4,720 B** | **9,884 B** |

## Account state

| Migrations | Accounts | secp256k1, at migration | ML-DSA-65, at migration | Ratio | secp256k1, after each signs once | ML-DSA-65, after each signs once | Ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| 100 K | 100,001 | 9.54 MB | 9.54 MB | 1.00x | 12.68 MB | 195.70 MB | 15.43x |
| 1 M | 1,000,001 | 95.37 MB | 95.37 MB | 1.00x | 126.84 MB | 1.91 GB | 15.43x |
| 10 M | 10,000,001 | 953.67 MB | 953.68 MB | 1.00x | 1.24 GB | 19.11 GB | 15.43x |

## Transaction history

| Migrations | secp256k1 | ML-DSA-65 | Ratio |
|---|---:|---:|---:|
| 100 K | 450.13 MB | 942.61 MB | 2.09x |
| 1 M | 4.40 GB | 9.21 GB | 2.09x |
| 10 M | 43.96 GB | 92.05 GB | 2.09x |

## Headline result

**At 10 M migrations, account state is 953.7 MiB under either scheme** — 1.00x. A migration stores a keyless account, so the receiver's key type costs nothing; the only difference is the relayer's one stored key.

The post-quantum key is paid when each migrated user first signs: if all 10 M do, state reaches 19.11 GiB (ML-DSA-65) vs 1.24 GiB (secp256k1), 15.43x.

Transaction history at 10 M migrations is 92.05 GiB (ML-DSA-65) vs 43.96 GiB (secp256k1), 2.09x. The Merkle-Patricia proof inside each `MsgRecvPacket` is the same under both schemes, so history roughly doubles rather than growing with the key-size ratio.

Not modelled: the per-migration state ICS-20 writes regardless of key type — the packet receipt, the acknowledgement commitment and the voucher balance. They add the same bytes under both schemes.
