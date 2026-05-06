# Security

This repository is a **research prototype** distributed alongside an
academic paper. **It is not intended for production use.** The
implementation has not undergone independent security review, formal
verification, or third-party penetration testing beyond what is
reported in the paper.

Specifically:

- The ML-DSA-44 integration in `cosmos/crypto/keys/mldsa/` wraps
  [cloudflare/circl](https://github.com/cloudflare/circl)'s
  implementation. Treat the integration glue as research code — the
  upstream library is the security boundary.
- The Lock-and-Mint contract in `ethereum/contracts/LockAndMint.sol`
  and the relayer in `ethereum/scripts/relayer.js` are illustrative.
  They are not hardened against adversarial conditions a real bridge
  would have to handle (sequencer failures, reorgs at Ethereum
  finality, validator collusion).
- The pre-funded test accounts and Hardhat default keys in this repo
  are documented as such and **must never** be reused on any network
  where they hold real value.

## Reporting a vulnerability

If you have found a security issue in this artifact specifically (not
in upstream Cosmos SDK or Hardhat — those have their own disclosure
processes):

- **Do not** open a public issue or pull request.
- Email the corresponding author listed in
  [`CITATION.cff`](CITATION.cff) with subject `pqc-migration security
  report`.
- Include a description of the issue and a minimal reproduction.

We will acknowledge receipt within a reasonable time and discuss
disclosure timing.

## Upstream security

For vulnerabilities in unmodified upstream Cosmos SDK code, please
report through the [upstream Cosmos SDK security
process](https://github.com/cosmos/cosmos-sdk/security/policy). For
Hardhat or Solidity-toolchain issues, report upstream to
[Hardhat](https://github.com/NomicFoundation/hardhat).
