#!/usr/bin/env bash
# Deploy TestERC20 (devnet/deploy/TestERC20.sol) to the running eth-devnet
# enclave and write its address to $DEVNET_DIR/deploy.env as TEST_ERC20.
#
# This is the Ethereum-native asset the native-transfer scripts
# (step-native-{send,recv,ack}.js) escrow: unlike IBCERC20, ICS20Transfer
# never maps it to a denom, so ICS20Transfer.sendTransfer takes the native
# branch (escrow, not burn). See devnet/deploy/TestERC20.sol and
# docs/architecture.md.
#
# Unlike deploy-contracts.sh, this does NOT overwrite deploy.env: it upserts
# only the TEST_ERC20 line, leaving ICS26_ROUTER/ICS20_TRANSFER/etc. from a
# prior deploy-contracts.sh run intact.
#
# Re-running this deploys a brand-new TestERC20 every time (same caveat as
# deploy-contracts.sh) — safe, but not a no-op, and it invalidates any
# native-send.json produced against the old token address.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ENCLAVE="${ENCLAVE:-eth-devnet}"
SIBE_HOME_DIR="${SIBE_HOME:-$HOME/solidity-ibc-eureka}"
IBC_SOLIDITY="$SIBE_HOME_DIR/ibc-solidity"

require_cmd kurtosis "run devnet/scripts/setup-toolchain.sh first."
require_cmd forge "run devnet/scripts/setup-toolchain.sh first."
require_dir "$IBC_SOLIDITY" "run devnet/scripts/setup-eureka-checkout.sh first."

DEVNET_DIR="$(resolve_devnet_dir)"
mkdir -p "$DEVNET_DIR"

# --- inputs --------------------------------------------------------------
log "resolving GETH_RPC"
GETH_RPC="$(kurtosis port print "$ENCLAVE" el-1-geth-lighthouse rpc 2>/dev/null | tr -d '\r')"
[ -n "$GETH_RPC" ] || die "kurtosis port print $ENCLAVE el-1-geth-lighthouse rpc returned nothing — is the enclave up? (kurtosis enclave ls)"
RPC_URL="$GETH_RPC"; case "$RPC_URL" in http*) ;; *) RPC_URL="http://$RPC_URL" ;; esac

log "resolving deployer key"
DEPLOYER_PK="$("$SCRIPT_DIR/get-deployer-key.sh")"

# --- vendor the contract into the checkout (same reason as DevnetDeploy.s.sol:
# it imports @openzeppelin-contracts via that checkout's remappings.txt, so it
# can only be compiled from inside it) --------------------------------------
SRC="$DEVNET_ROOT/deploy/TestERC20.sol"
DEST="$IBC_SOLIDITY/contracts/TestERC20.sol"
require_file "$SRC"
if [ -f "$DEST" ] && cmp -s "$SRC" "$DEST"; then
  ok "TestERC20.sol already vendored into the checkout"
else
  cp "$SRC" "$DEST"
  ok "vendored TestERC20.sol -> $DEST"
fi

# --- deploy ----------------------------------------------------------------
log "running forge create (broadcast to $RPC_URL)"
out="$(cd "$IBC_SOLIDITY" && forge create contracts/TestERC20.sol:TestERC20 \
  --rpc-url "$RPC_URL" --private-key "$DEPLOYER_PK" --broadcast 2>&1)" \
  || { echo "$out"; die "forge create failed; see output above"; }
echo "$out"

test_erc20="$(grep -oE 'Deployed to: 0x[0-9a-fA-F]{40}' <<<"$out" | awk '{print $3}')"
[ -n "$test_erc20" ] || die "did not find a 'Deployed to: 0x...' line in forge's output — deployment may have failed silently"

# --- upsert TEST_ERC20 into deploy.env --------------------------------------
DEPLOY_ENV="$DEVNET_DIR/deploy.env"
touch "$DEPLOY_ENV"
grep -v '^TEST_ERC20=' "$DEPLOY_ENV" > "$DEPLOY_ENV.tmp" || true
mv "$DEPLOY_ENV.tmp" "$DEPLOY_ENV"
echo "TEST_ERC20=$test_erc20" >> "$DEPLOY_ENV"

ok "wrote TEST_ERC20=$test_erc20 to $DEPLOY_ENV"
