# Required fix: public auction path for `SP1_PROVER=network`

**File:** `packages/proof-api/modules/cosmos-to-eth/src/lib.rs`
**Change:** `FulfillmentStrategy::Hosted` → `FulfillmentStrategy::Auction`
(non-`private_cluster` branch only)
**Applies to upstream commit:** 604476b

## Why this is required

The `cosmos_to_eth` proof-api module selects the SP1 network mode and the
fulfillment strategy in two separate `if private_cluster` expressions:

```rust
let mut builder = if private_cluster {
    ProverClient::builder().network_for(NetworkMode::Reserved)
} else {
    ProverClient::builder().network()          // -> NetworkMode::Mainnet
};
let strategy = if private_cluster {
    FulfillmentStrategy::Reserved
} else {
    FulfillmentStrategy::Hosted                // <-- wrong for Mainnet
};
```

`ProverClient::builder().network()` leaves `network_mode` unset, which resolves
via `NetworkMode::default()` to `NetworkMode::Mainnet` (it only defaults to
`Reserved` under the `reserved-capacity` cargo feature, which no crate in this
workspace enables).

`sp1-sdk` 6.1.0 validates the (mode, strategy) pair on every proof request, in
`NetworkProveBuilder`'s `IntoFuture` impl, *before* `prove_impl`:

```rust
// sp1-sdk-6.1.0/src/network/validation.rs
match (mode, strategy) {
    (NetworkMode::Mainnet, FulfillmentStrategy::Auction)
    | (NetworkMode::Reserved, FulfillmentStrategy::Hosted | FulfillmentStrategy::Reserved) => Ok(()),
    (mode, strategy) => Err(ValidationError::IncompatibleStrategy { strategy, mode }),
}
```

`(Mainnet, Hosted)` is explicitly an error. So with `SP1_PROVER=network` and
`E2E_PRIVATE_CLUSTER=false`, **every** proof request fails with
`IncompatibleStrategy` before an auction is ever opened. Nothing is spent, but
no Cosmos->Eth relay can ever succeed.

## Why upstream has not hit this

Every CI workflow that sets `SP1_PROVER: network` also sets
`E2E_PRIVATE_CLUSTER: true`:

- `.github/workflows/e2e-full.yml:38-41`
- `.github/workflows/e2e-minimal.yml:39-42`

That takes the `private_cluster` branch, giving `(Reserved, Reserved)`, which is
valid. The `false` branch — the public, permissionless auction network — is
never exercised by upstream CI, so the mismatch is invisible there.

`E2E_PRIVATE_CLUSTER=true` targets `rpc.production.succinct.xyz`, Succinct's
reserved cluster, which requires whitelisted access. It is not an option for
anyone proving against the public network.

## Scope of the change

One line. `(Mainnet, Auction)` is the documented pairing for the public
prover network: descending-auction pricing, settled in PROVE. The
`private_cluster` branch is untouched, so CI behaviour is unchanged.
