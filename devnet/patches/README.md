# solidity-ibc-eureka patches

Local changes to the upstream [`solidity-ibc-eureka`](https://github.com/srdtrk/solidity-ibc-eureka)
checkout that this devnet depends on. They are not upstreamed, so they must be
reapplied to a fresh clone.

**Upstream commit:** `604476b11eb2ee5c677f773d2086a352b03bb0a5`

```bash
cd <solidity-ibc-eureka>
git checkout 604476b
git apply /path/to/devnet/patches/solidity-ibc-eureka.patch
```

## What each change does

| File | Change |
|---|---|
| `packages/proof-api/modules/cosmos-to-eth/src/lib.rs` | `FulfillmentStrategy::Hosted` → `Auction` on the public-network branch. sp1-sdk accepts only (Mainnet, Auction) or (Reserved, Hosted\|Reserved), so the public path otherwise fails with `IncompatibleStrategy`. Detailed in [PUBLIC_AUCTION_FIX.md](PUBLIC_AUCTION_FIX.md). |
| `e2e/interchaintestv8/ibc_eureka_test.go` | Adds `case "cpu", "cuda":` to the prover switch, which otherwise accepts only `mock` and `network`. Local proving needs no `NETWORK_PRIVATE_KEY`. |
| `e2e/interchaintestv8/proofapi/proofapi.go` | Replaces a fixed 9-second sleep with a dial-poll bounded at 75s. The gRPC listener binds only after every module's `create_service` returns, and CPU prover init alone exceeds 9s. |
| `packages/sp1-ics07-tendermint-prover/{Cargo.toml,src/lib.rs}` | Adds `bincode`, used by the `cost-estimator` binary to decode an `SP1_DUMP` capture. The `use bincode as _;` satisfies `unused_crate_dependencies` for the library. |
| `Cargo.lock` | Records the `bincode` addition. |

## Helper binaries

Not part of the patch — copy into the checkout separately.

| File | Destination | Purpose |
|---|---|---|
| `bin/cost-estimator.rs` | `packages/sp1-ics07-tendermint-prover/src/bin/` | Estimates proving cost from an `SP1_DUMP` capture, without submitting a job |
| `bin/sp1-account.rs` | `packages/sp1-ics07-tendermint-prover/src/bin/` | Queries prover-network balance and settlement |
| `bin/createclient.go.txt` | `e2e/interchaintestv8/cmd/createclient/main.go` | Calls proof-api `CreateClient` directly and prints the deployment calldata. Carries a `.txt` suffix so it is not compiled as part of this module; rename on copy. |

## Building

The build needs several environment overrides; see
[Getting started](../../docs/getting-started.md#building-solidity-ibc-eureka).

---

[Project README](../../README.md) · [Devnet runbook](../README.md) · [EVM deployment](../deploy/README.md)
