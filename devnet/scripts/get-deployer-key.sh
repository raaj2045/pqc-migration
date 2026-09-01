#!/usr/bin/env bash
# Resolve the funded EL deployer private key for the current Kurtosis
# eth-devnet enclave: read the genesis mnemonic straight from the enclave's
# own el_cl_genesis_data artifact, derive account $INDEX, and verify on-chain
# that it is actually funded. Never hardcode the mnemonic — the
# ethereum-package can change its default between versions.
#
# Usage: get-deployer-key.sh [index]   (default index: 0)
# Prints ONLY the 0x-prefixed private key on stdout; everything else goes to
# stderr, so `PK=$(get-deployer-key.sh)` works.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ENCLAVE="${ENCLAVE:-eth-devnet}"
INDEX="${1:-0}"

require_cmd kurtosis "run devnet/scripts/setup-toolchain.sh first."
require_cmd cast "run devnet/scripts/setup-toolchain.sh first (Foundry)."
require_cmd python3

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

log "downloading el_cl_genesis_data from enclave '$ENCLAVE'" >&2
kurtosis files download "$ENCLAVE" el_cl_genesis_data "$tmp" >&2
require_file "$tmp/mnemonics.yaml" "expected inside the el_cl_genesis_data artifact"

MNEMONIC="$(python3 -c '
import sys, yaml
with open(sys.argv[1]) as f:
    docs = yaml.safe_load(f)
print(docs[0]["mnemonic"])
' "$tmp/mnemonics.yaml")"
[ -n "$MNEMONIC" ] || die "could not read a mnemonic out of $tmp/mnemonics.yaml"

PK="$(cast wallet private-key "$MNEMONIC" "$INDEX")"
ADDR="$(cast wallet address --private-key "$PK")"

GETH_RPC="$(kurtosis port print "$ENCLAVE" el-1-geth-lighthouse rpc 2>/dev/null | tr -d '\r')"
[ -n "$GETH_RPC" ] || die "kurtosis port print $ENCLAVE el-1-geth-lighthouse rpc returned nothing — is the enclave up?"
RPC_URL="$GETH_RPC"; case "$RPC_URL" in http*) ;; *) RPC_URL="http://$RPC_URL" ;; esac

BALANCE_WEI="$(cast balance "$ADDR" --rpc-url "$RPC_URL" 2>/dev/null)" \
  || die "cast balance failed against $RPC_URL for $ADDR — is the EL up and reachable?"

if [ "$BALANCE_WEI" = "0" ]; then
  die "derived account $ADDR (mnemonic index $INDEX) has a zero balance on $RPC_URL. Either this enclave's genesis doesn't prefund this index the way earlier ethereum-package versions did, or the wrong enclave/index was used. Try a different index, or inspect $tmp/mnemonics.yaml directly before it's cleaned up."
fi

ok "deployer $ADDR funded with $(cast balance "$ADDR" --rpc-url "$RPC_URL" --ether) ETH" >&2
echo "$PK"
