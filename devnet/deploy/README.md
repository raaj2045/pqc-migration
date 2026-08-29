# EVM deployment

`Phase4aDeploy.s.sol` deploys the IBC Eureka contracts this devnet drives:
`AccessManager`, `ICS26Router`, `ICS20Transfer` (with Escrow and IBCERC20
beacons), and both SP1 verifiers.

## Building it

The script uses **relative imports into the `solidity-ibc-eureka` contract
tree** (`../contracts/...`, `./deployments/...`) and its `@sp1-contracts/` and
`@openzeppelin-contracts/` remappings. It therefore cannot be compiled from this
repository. Copy it into that checkout's script directory and build there:

```bash
cp devnet/deploy/Phase4aDeploy.s.sol <solidity-ibc-eureka>/ibc-solidity/scripts/
cd <solidity-ibc-eureka>/ibc-solidity
forge script scripts/Phase4aDeploy.s.sol:Phase4aDeploy \
  --rpc-url "$GETH_RPC" --private-key "$DEPLOYER_PK" --broadcast
```

It prints a `PHASE4A_RESULT` JSON line with the deployed addresses. Those feed
`$DEVNET_DIR/deploy.env`, which `devnet/lib/config.js` reads.

The copy here is the authoritative one; it is vendored so the deployment is
preserved with the code that depends on it.

## What it does not do

It does not create the `SP1ICS07Tendermint` light client, and does not call
`ICS26Router.addClient`. Client creation is a separate step that goes through
proof-api, because the client's four program verification keys must be derived
from the SP1 program ELFs and its trusted state read from a live light block —
neither of which a deploy script can supply. See
[Proving](../README.md#proving).

## Verifier paths

Both verifiers are deployed; exactly one is bound to a client, and the binding
is fixed at client creation by the `sp1_verifier` parameter passed to proof-api.

| Path | Verifier | Behaviour |
|---|---|---|
| Real | `SP1VerifierGroth16` (v6.1.0) | Proofs are cryptographically verified on chain. Requires a running prover; ~10 min per proof. |
| Mock | `SP1MockVerifier` | Accepts any public values with an empty proof. No verification. For fast iteration only. |

A client cannot be switched between them after creation — create a second
client instead.

**Deploy-time difference between the paths: none.** This script deploys both
verifiers unconditionally and creates no client, so it serves either path
unchanged. The divergence is entirely in the client-creation step that follows.

A client bound to the mock verifier can be constructed directly in Solidity,
because a mock verifier ignores both the verification keys and the proof body —
`bytes32(0)` keys and trusted state supplied from environment variables are
sufficient. That shortcut is not available for the real verifier, which rejects
a zero verification key, so the real path must obtain its client-creation
calldata from proof-api.

## See also

- [devnet/README.md](../README.md) — running the bridge, proving, costs
- [../../docs/ethereum-light-client.md](../../docs/ethereum-light-client.md) — the Cosmos-side light client
