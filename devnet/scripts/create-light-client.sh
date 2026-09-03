#!/usr/bin/env bash
# Wrap the collected Ethereum ClientState/ConsensusState into the ibc-go
# 08-wasm proto types and submit `tx ibc client create`, completing the
# light-client instantiation that bring-up-devnet.sh used to stop short of.
#
# This closes the "proto-encoding question" noted in bring-up-devnet.sh and
# devnet/README.md#light-client-helpers. Findings, from getting a real client
# live on a devnet (08-wasm-0, still active — this script must never touch an
# existing client, only ever create new ones):
#
#   - The two wrapper messages (ibc.lightclients.wasm.v1.ClientState /
#     ConsensusState) are protojson, submitted as the `client_state` /
#     `consensus_state` args to `tx ibc client create`. Each MUST carry a
#     top-level "@type" discriminator ("/ibc.lightclients.wasm.v1.ClientState"
#     / "...ConsensusState") — that's how ProtoCodec.UnmarshalInterfaceJSON
#     (what the CLI calls) resolves the concrete type. Without it: "Any JSON
#     doesn't have '@type'", decode failure, nothing gets far enough to hit
#     the chain. This is the bug that blocked earlier attempts.
#   - In protojson, `bytes` fields are base64 (NOT hex): `data` and
#     `checksum` on the wrapper. `data` holds the RAW JSON bytes of the inner
#     Ethereum ClientState/ConsensusState — cw-ics08-wasm-eth's own
#     instantiate handler does `serde_json::from_slice` on it, not any
#     protobuf decode (packages/ethereum/light-client/src/instantiate.rs).
#   - `latest_height` exists only on the ClientState wrapper, absent from
#     ConsensusState. `checksum` lives ONLY on the wrapper — never duplicated
#     inside the inner `data` JSON.
#   - `--client-type` is NOT a flag on `tx ibc client create` in this
#     ibc-go version — client type is inferred entirely from `@type`. Passing
#     it errors with "unknown flag". Do not add it back.
#
# Usage: create-light-client.sh [flags]
#   --router ADDR        0x-prefixed ICS26Router address on the tracked chain.
#                         Default: ICS26_ROUTER from $DEVNET_DIR/deploy.env
#                         (deploy-contracts.sh's output).
#   --pubkeys-hash HASH  0x-prefixed SSZ hash-tree-root of the sync committee's
#                         pubkey vector. Default: computed AND independently
#                         verified against the bootstrap's Merkle branch to the
#                         finalized state_root (light-client/verify_pubkeys_hash.py)
#                         — refuses to proceed if that check fails.
#   --checksum HEX        sha256 of the (decompressed) wasm code. Default:
#                         computed from --wasm-file.
#   --wasm-file PATH      Default: devnet/artifacts/cw_ics08_wasm_eth.wasm.gz
#   --from KEY            Keyring key that signs MsgCreateClient. Default:
#                         RELAYER_KEY from devnet.env, else "validator".
#
# Every flag is optional: with none given, everything is derived from the
# devnet's own generated files, so this is safe to wire into
# bring-up-devnet.sh as a fully automated final stage. Creates a NEW client
# every time it runs (ibc-go assigns 08-wasm-N sequentially) — it never
# touches an existing one, so re-running is safe but not idempotent.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ROUTER="" PUBKEYS_HASH="" CHECKSUM_HEX="" WASM_FILE="" FROM_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --router) ROUTER="$2"; shift 2 ;;
    --pubkeys-hash) PUBKEYS_HASH="$2"; shift 2 ;;
    --checksum) CHECKSUM_HEX="$2"; shift 2 ;;
    --wasm-file) WASM_FILE="$2"; shift 2 ;;
    --from) FROM_KEY="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^#//'; exit 0 ;;
    *) die "unknown flag: $1 (--help for usage)" ;;
  esac
done

require_cmd jq
require_cmd python3

DEVNET_DIR="$(resolve_devnet_dir)"
mkdir -p "$DEVNET_DIR"
eval "$(devnet_cfg CHAIN_HOME CHAIN_ID CHAIN_NODE PQCHAIND_BIN RELAYER_KEY ICS26_ROUTER)"
for v in CHAIN_HOME CHAIN_ID CHAIN_NODE PQCHAIND_BIN; do
  [ -n "${!v:-}" ] || die "$v did not resolve; check devnet.env / run devnet/scripts/init-chain.sh first."
done
[ -x "$PQCHAIND_BIN" ] || die "PQCHAIND_BIN ($PQCHAIND_BIN) is not an executable file."

FROM_KEY="${FROM_KEY:-${RELAYER_KEY:-validator}}"
ROUTER="${ROUTER:-${ICS26_ROUTER:-}}"
[ -n "$ROUTER" ] || die "no router address: pass --router, or run devnet/scripts/deploy-contracts.sh first (writes ICS26_ROUTER to deploy.env)."
case "$ROUTER" in 0x*) ;; *) die "router address must be 0x-prefixed: $ROUTER" ;; esac

WASM_FILE="${WASM_FILE:-$DEVNET_ROOT/artifacts/cw_ics08_wasm_eth.wasm.gz}"

BIN=("$PQCHAIND_BIN" --home "$CHAIN_HOME" --node "$CHAIN_NODE")
tx() { "${BIN[@]}" tx "$@" --chain-id "$CHAIN_ID" --keyring-backend test --gas auto --gas-adjustment 1.5 -y -o json; }
qry() { "${BIN[@]}" query "$@" -o json; }

# --- instantiate-inputs: always re-collect, never reuse ---------------------
# A cached copy from a prior enclave looks valid but isn't: ethereum-package
# reuses the same validator set every rebuild, so pubkeys_hash verification
# passes even against stale data — only genesis_time actually differs, and
# nothing was checking it. Re-collecting is a few seconds of API calls.
INPUTS="$DEVNET_DIR/instantiate-inputs"
log "collecting instantiate-inputs from the live Ethereum devnet"
rm -rf "$INPUTS"
DEVNET_DIR="$DEVNET_DIR" "$DEVNET_ROOT/light-client/collect-instantiate-inputs.sh"
require_file "$INPUTS/client_state.json"
require_file "$INPUTS/consensus_state.json"

# --- pubkeys_hash: compute + independently verify unless overridden --------
if [ -z "$PUBKEYS_HASH" ]; then
  require_file "$INPUTS/bootstrap.json"
  log "computing pubkeys_hash and verifying it against the bootstrap's Merkle branch to the finalized state_root"
  verify_out="$(python3 "$DEVNET_ROOT/light-client/verify_pubkeys_hash.py" "$INPUTS/bootstrap.json")" \
    || die "pubkeys_hash failed independent verification — refusing to submit a client with an unverified sync committee root:
$verify_out"
  sed 's/^/  /' <<<"$verify_out" >&2
  PUBKEYS_HASH="$(grep -oE '^pubkeys_hash *: 0x[0-9a-f]+' <<<"$verify_out" | grep -oE '0x[0-9a-f]+')"
  [ -n "$PUBKEYS_HASH" ] || die "could not parse pubkeys_hash out of verify_pubkeys_hash.py's output"
  ok "pubkeys_hash verified: $PUBKEYS_HASH"
else
  warn "using caller-supplied --pubkeys-hash ($PUBKEYS_HASH) without independent verification"
fi

# --- checksum ----------------------------------------------------------------
if [ -z "$CHECKSUM_HEX" ]; then
  require_file "$WASM_FILE" "run devnet/scripts/build-wasm-light-client.sh first, or pass --checksum/--wasm-file explicitly."
  CHECKSUM_HEX="$( (gunzip -c "$WASM_FILE" 2>/dev/null || cat "$WASM_FILE") | sha256sum | cut -d' ' -f1)"
  ok "checksum computed from $WASM_FILE: $CHECKSUM_HEX"
fi

# --- assemble inner client_state / consensus_state JSON ---------------------
# Delegates to light-client/assemble-instantiate-msg.py rather than
# duplicating its substitution logic: exact text-level substitution for the
# router address (never jq, never json.loads/dumps on client_state — jq 1.6
# and any JSON round-trip through IEEE754 doubles would round
# FULU_FORK_EPOCH's 2^64-1 literal above u64::MAX; that script's own
# docstring calls this out and its asserts guard it), and json.load/dump
# (Python ints are arbitrary-precision, safe) for injecting pubkeys_hash into
# consensus_state. It also independently enforces
# client_state.latest_slot == consensus_state.slot before writing anything.
log "assembling client_state/consensus_state via assemble-instantiate-msg.py"
assemble_out="$(DEVNET_DIR="$DEVNET_DIR" python3 "$DEVNET_ROOT/light-client/assemble-instantiate-msg.py" \
  "$ROUTER" "$PUBKEYS_HASH" "$CHECKSUM_HEX" 2>&1)" \
  || die "assemble-instantiate-msg.py failed:
$assemble_out"
sed 's/^/  /' <<<"$assemble_out" >&2

latest_slot="$(grep -oE 'latest_slot *: [0-9]+' <<<"$assemble_out" | grep -oE '[0-9]+')"
[ -n "$latest_slot" ] || die "could not parse latest_slot out of assemble-instantiate-msg.py's output:
$assemble_out"

require_file "$DEVNET_DIR/instantiate-msg.json" "assemble-instantiate-msg.py should have written this"
data_cs_b64="$(jq -r '.client_state' "$DEVNET_DIR/instantiate-msg.json")"
data_cons_b64="$(jq -r '.consensus_state' "$DEVNET_DIR/instantiate-msg.json")"
checksum_b64="$(jq -r '.checksum' "$DEVNET_DIR/instantiate-msg.json")"

# --- wrap into the real ibc-go 08-wasm proto JSON ---------------------------
# The piece assemble-instantiate-msg.py doesn't do: the @type discriminator,
# latest_height, and checksum-as-wrapper-bytes that `tx ibc client create`
# needs (as opposed to a raw CosmWasm InstantiateMsg). jq here only ever
# handles opaque base64 strings and the small latest_height integer — it
# never parses client_state's own JSON, so the fulu-epoch precision hazard
# above does not apply to this step.
jq -n --arg data "$data_cs_b64" --arg checksum "$checksum_b64" --arg h "$latest_slot" '{
  "@type": "/ibc.lightclients.wasm.v1.ClientState",
  data: $data,
  checksum: $checksum,
  latest_height: { revision_number: "0", revision_height: $h }
}' > "$DEVNET_DIR/client_state_wrapped.json"

jq -n --arg data "$data_cons_b64" '{
  "@type": "/ibc.lightclients.wasm.v1.ConsensusState",
  data: $data
}' > "$DEVNET_DIR/consensus_state_wrapped.json"

ok "wrote $DEVNET_DIR/{client_state,consensus_state}_wrapped.json (latest_slot=$latest_slot)"

# --- submit ------------------------------------------------------------
log "submitting MsgCreateClient (from=$FROM_KEY, router=$ROUTER, checksum=$CHECKSUM_HEX)"
create_out="$(tx ibc client create \
  "$DEVNET_DIR/client_state_wrapped.json" "$DEVNET_DIR/consensus_state_wrapped.json" \
  --from "$FROM_KEY")"
hash="$(jq -r '.txhash' <<<"$create_out")"
[ -n "$hash" ] && [ "$hash" != "null" ] || die "no txhash in create-client response: $create_out"

# The broadcast result above is only mempool-acceptance (height "0", no
# events) — poll `query tx` for the real post-execution result.
tries=30
included=""
while [ "$tries" -gt 0 ]; do
  if included="$(qry tx "$hash" 2>/dev/null)"; then
    break
  fi
  tries=$((tries - 1))
  sleep 2
done
[ -n "$included" ] || die "tx $hash never appeared on-chain after 60s"

code="$(jq -r '.code' <<<"$included")"
[ "$code" = "0" ] || die "MsgCreateClient failed (code $code): $(jq -r '.raw_log' <<<"$included")"

client_id="$(jq -r '.events[] | select(.type=="create_client") | .attributes[] | select(.key=="client_id") | .value' <<<"$included" | head -n1)"
[ -n "$client_id" ] || die "create_client event / client_id not found in tx result: $included"

status="$(qry ibc client status "$client_id" | jq -r '.status')"
[ "$status" = "Active" ] || die "client $client_id created (tx $hash) but status is $status, not Active"

ok "client $client_id created and Active (tx $hash, height $(jq -r '.height' <<<"$included"))"

# Persist alongside deploy-contracts.sh's output so later tooling can
# discover the current Cosmos client without guessing.
DEPLOY_ENV="$DEVNET_DIR/deploy.env"
touch "$DEPLOY_ENV"
grep -v '^COSMOS_CLIENT_ID=' "$DEPLOY_ENV" > "$DEPLOY_ENV.tmp" || true
mv "$DEPLOY_ENV.tmp" "$DEPLOY_ENV"
echo "COSMOS_CLIENT_ID=$client_id" >> "$DEPLOY_ENV"
ok "wrote COSMOS_CLIENT_ID=$client_id to $DEPLOY_ENV"

echo "$client_id"
