# Security

This repository is a **research prototype** distributed alongside an
academic paper. **It is not intended for production use.** The
implementation has not undergone independent security review, formal
verification, or third-party penetration testing beyond what is
reported in the paper.

Specifically:

- ML-DSA-65 account keys come from stock Cosmos SDK v0.55
  (`crypto/keys/mldsa65`), which wraps
  [cloudflare/circl](https://github.com/cloudflare/circl). The SDK is
  the security boundary; this repository does not modify it.
- The cross-chain path is ICS-20 over light clients
  (`cw-ics08-wasm-eth` on the Cosmos side, `SP1ICS07Tendermint` on the
  EVM side). Those clients' own consensus verification is assumed
  correct here and has not been independently reviewed as part of this
  artifact. The EVM-side deployment used for the devnet runs a **mock**
  SP1 verifier, which performs no proof checking and is suitable only
  for local testing.
- A custom bridge module, `x/lockandmint`, was retired from this chain.
  Its `Mint` and `SetBalance` handlers performed no proof verification
  and no authority check. The code is no longer part of the system; all
  asset transfer now goes through ICS-20 over light clients.
- The pre-funded test accounts and devnet default keys in this repo are
  documented as such and **must never** be reused on any network where
  they hold real value.

## Reporting a vulnerability

If you have found a security issue in this artifact specifically (not
in upstream Cosmos SDK, ibc-go, or solidity-ibc-eureka — those have
their own disclosure processes):

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
IBC or Eureka contract issues, report upstream to
[ibc-go](https://github.com/cosmos/ibc-go/security/policy) or
[solidity-ibc-eureka](https://github.com/cosmos/solidity-ibc-eureka).

---

[Project README](README.md) · [Testing](docs/testing.md)
