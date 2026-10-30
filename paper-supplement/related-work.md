# Comparison with related systems

Section II-A of the paper summarises this table.

| Research / project | Year | PQ algorithm | Migration support | Cross-chain | Production ready | Key limitations |
|---|---|---|---|---|---|---|
| DoraFactory PQC-Cosmos | 2024 | ML-DSA | Experimental | No | No | Single-chain focus, no migration tools |
| QRL blockchain | 2018 | XMSS | Native only | No | Yes | New network, no legacy migration |
| NIST PQC standards (FIPS 204 etc.) | 2024 | ML-DSA / ML-KEM | Framework only | No | Standard | Algorithm-level, no deployment guidance |
| zkBridge | 2022 | None | N/A | Yes | Research | Trust-minimised through succinct proofs, but classical signatures throughout; not a migration mechanism |
| **This work** | 2026 | ML-DSA-65 | Asset / authorisation | Yes | Prototype | Post-quantum protection covers authorisation of the target account; consensus, bridge verification and source escrow keep classical dependencies. Account history and contract state are not migrated. |

References are as in the paper: DoraFactory (2024), QRL whitepaper (2018), NIST FIPS 204 (2024), Xie et al., *zkBridge*, ACM CCS 2022.
