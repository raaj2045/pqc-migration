#!/usr/bin/env bash
# Store the cw-ics08-wasm-eth light client code via governance and see the
# proposal through to PROPOSAL_STATUS_PASSED, failing loudly otherwise.
#
# This exists because doing it by hand is exactly how proposal 1 on this
# devnet ended up PROPOSAL_STATUS_REJECTED: MsgStoreCode was submitted but
# nobody voted before the (short, devnet-tuned) voting period elapsed, so it
# was rejected on quorum. This script votes immediately after submitting and
# then polls to confirm, instead of leaving that as a manual follow-up step.
#
# Usage: store-and-vote-wasm-code.sh [wasm-file]
#   default wasm-file: devnet/artifacts/cw_ics08_wasm_eth.wasm.gz (Script 3's output)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

WASM_FILE="${1:-$DEVNET_ROOT/artifacts/cw_ics08_wasm_eth.wasm.gz}"
GOV_KEY="${GOV_KEY:-validator}"
DEPOSIT="${DEPOSIT:-10000000stake}"
FEES="${FEES:-5000stake}"

require_cmd jq
require_file "$WASM_FILE" "run devnet/scripts/build-wasm-light-client.sh first, or pass a path explicitly."

DEVNET_DIR="$(resolve_devnet_dir)"
mkdir -p "$DEVNET_DIR"
eval "$(devnet_cfg CHAIN_HOME CHAIN_ID CHAIN_NODE PQCHAIND_BIN)"
for v in CHAIN_HOME CHAIN_ID CHAIN_NODE PQCHAIND_BIN; do
  [ -n "${!v:-}" ] || die "$v did not resolve; check devnet.env / run devnet/scripts/init-chain.sh first."
done
[ -x "$PQCHAIND_BIN" ] || die "PQCHAIND_BIN ($PQCHAIND_BIN) is not an executable file. Run devnet/scripts/init-chain.sh first."
BIN=("$PQCHAIND_BIN" --home "$CHAIN_HOME" --node "$CHAIN_NODE")

tx() { "${BIN[@]}" tx "$@" --chain-id "$CHAIN_ID" --keyring-backend test --gas auto --gas-adjustment 1.3 --fees "$FEES" -y -o json; }
qry() { "${BIN[@]}" query "$@" -o json; }

wait_for_tx() {
  local hash="$1" tries=30
  while [ "$tries" -gt 0 ]; do
    if out="$(qry tx "$hash" 2>/dev/null)"; then
      echo "$out"
      return 0
    fi
    tries=$((tries - 1))
    sleep 2
  done
  die "tx $hash never appeared on-chain after 60s (query tx kept failing)"
}

# --- store-code (creates a gov proposal) ------------------------------
log "submitting MsgStoreCode for $WASM_FILE (deposit $DEPOSIT)"
store_out="$(tx ibc-wasm store-code "$WASM_FILE" --from "$GOV_KEY" \
  --title "Store cw-ics08-wasm-eth" \
  --summary "Store the Ethereum light client wasm code" \
  --deposit "$DEPOSIT")"
hash="$(jq -r '.txhash' <<<"$store_out")"
[ -n "$hash" ] && [ "$hash" != "null" ] || die "no txhash in store-code response: $store_out"

included="$(wait_for_tx "$hash")"
code="$(jq -r '.code' <<<"$included")"
[ "$code" = "0" ] || die "MsgStoreCode failed (code $code): $(jq -r '.raw_log' <<<"$included")"

proposal_id="$(jq -r '.events[] | select(.type=="submit_proposal") | .attributes[] | select(.key=="proposal_id") | .value' <<<"$included" | head -n1)"
[ -n "$proposal_id" ] || die "could not find a submit_proposal/proposal_id event in the tx result: $included"
ok "created proposal $proposal_id"

# --- vote immediately ----------------------------------------------------
log "voting yes on proposal $proposal_id (from $GOV_KEY)"
vote_out="$(tx gov vote "$proposal_id" yes --from "$GOV_KEY")"
vote_hash="$(jq -r '.txhash' <<<"$vote_out")"
vote_included="$(wait_for_tx "$vote_hash")"
vote_code="$(jq -r '.code' <<<"$vote_included")"
[ "$vote_code" = "0" ] || die "vote failed (code $vote_code): $(jq -r '.raw_log' <<<"$vote_included")"
ok "vote recorded"

# --- poll until the voting period elapses --------------------------------
voting_period_raw="$(qry gov params | jq -r '.params.voting_period')"
# Duration strings look like "30s" — parse the leading integer seconds.
voting_period_s="$(sed -E 's/[^0-9].*$//' <<<"$voting_period_raw")"
[ -n "$voting_period_s" ] || voting_period_s=30
deadline=$(( $(date +%s) + voting_period_s * 3 + 30 ))

log "polling proposal $proposal_id until the voting period ends (chain voting_period=${voting_period_raw})"
status="PROPOSAL_STATUS_VOTING_PERIOD"
while [ "$status" = "PROPOSAL_STATUS_VOTING_PERIOD" ] || [ "$status" = "PROPOSAL_STATUS_DEPOSIT_PERIOD" ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    die "proposal $proposal_id is still $status after waiting ${voting_period_s}s x3 — something is stuck (chain not producing blocks?)."
  fi
  sleep 5
  status="$(qry gov proposal "$proposal_id" | jq -r '.proposal.status')"
  log "  status: $status"
done

if [ "$status" != "PROPOSAL_STATUS_PASSED" ]; then
  proposal="$(qry gov proposal "$proposal_id" 2>/dev/null || echo '{}')"
  reason="$(jq -r '.proposal.failed_reason // empty' <<<"$proposal")"
  tally="$(qry gov tally "$proposal_id" 2>/dev/null || echo '(tally query failed)')"
  die "proposal $proposal_id ended as $status, not PASSED.${reason:+ Reason: $reason.} Tally: $tally"
fi

ok "proposal $proposal_id PASSED — cw-ics08-wasm-eth code is now stored on-chain"
