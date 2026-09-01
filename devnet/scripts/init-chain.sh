#!/usr/bin/env bash
# Full Cosmos chain initialization: init, genesis edits, keys, funding,
# gentx, collect-gentxs, validate.
#
# Idempotent guard: if CHAIN_HOME already exists and has produced blocks,
# this refuses to touch it — pass --force to wipe and reinitialize. This is
# the check the manual process this session lacked: editing genesis.json (in
# particular the gov voting_period) AFTER the chain has already started is
# silently ignored by CometBFT/cosmos-sdk (genesis is only read once, at
# height 0), which is how a running chain's params got corrupted by a
# well-intentioned re-edit. The gov edit here always happens between `init`
# and the first `start`, never after.
#
# Refuses outright, --force or not, if pqchaind is currently running against
# CHAIN_HOME — this script never touches a live node's data directory.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    *) die "unknown argument: $arg (only --force is accepted)" ;;
  esac
done

require_cmd python3
require_cmd go "run devnet/scripts/setup-toolchain.sh first."

DEVNET_DIR="$(resolve_devnet_dir)"
mkdir -p "$DEVNET_DIR"
eval "$(devnet_cfg CHAIN_HOME CHAIN_ID PQCHAIND_BIN)"
CHAIN_ID="${CHAIN_ID:-pqc-5-1}"
[ -n "${CHAIN_HOME:-}" ] || die "CHAIN_HOME did not resolve; set it in devnet.env"
[ -n "${PQCHAIND_BIN:-}" ] || die "PQCHAIND_BIN did not resolve; set it in devnet.env"

GOV_VOTING_PERIOD="${GOV_VOTING_PERIOD:-30s}"
GOV_EXPEDITED_VOTING_PERIOD="${GOV_EXPEDITED_VOTING_PERIOD:-15s}"
MONIKER="${MONIKER:-validator}"
VALIDATOR_KEY="${VALIDATOR_KEY:-validator}"
TEST_KEYS_ML_DSA=(relayer loadgen)
GENTX_AMOUNT="${GENTX_AMOUNT:-1000000000stake}"
FUND_AMOUNT="${FUND_AMOUNT:-100000000000stake}"

# --- refuse to touch a live node ------------------------------------------
if pgrep -f "pqchaind[[:space:]].*--home[[:space:]=]*$CHAIN_HOME" >/dev/null 2>&1; then
  die "pqchaind is currently running against $CHAIN_HOME — stop it first. This script never touches a live node's data directory, --force or not."
fi

# --- build pqchaind if missing ---------------------------------------------
if [ ! -x "$PQCHAIND_BIN" ]; then
  log "building pqchaind -> $PQCHAIND_BIN"
  mkdir -p "$(dirname "$PQCHAIND_BIN")"
  (cd "$REPO_ROOT" && go build -o "$PQCHAIND_BIN" ./cmd/pqchaind)
fi
BIN=("$PQCHAIND_BIN")

# --- idempotency guard -----------------------------------------------------
chain_has_blocks() {
  local pvs="$CHAIN_HOME/data/priv_validator_state.json"
  [ -f "$pvs" ] || return 1
  local h
  h="$(python3 -c "import json;print(json.load(open('$pvs')).get('height','0'))" 2>/dev/null || echo 0)"
  [ -n "$h" ] && [ "$h" != "0" ]
}

if [ -d "$CHAIN_HOME" ]; then
  if chain_has_blocks; then
    [ "$FORCE" = "1" ] || die "$CHAIN_HOME already has a chain with blocks (height > 0). Refusing to touch it. Re-run with --force to wipe and reinitialize — this destroys all chain state."
    warn "wiping $CHAIN_HOME (--force)"
    rm -rf "$CHAIN_HOME"
  else
    log "$CHAIN_HOME exists but has produced no blocks; reinitializing in place"
    rm -rf "$CHAIN_HOME"
  fi
fi

# --- init --------------------------------------------------------------
log "pqchaind init"
"${BIN[@]}" init "$MONIKER" --home "$CHAIN_HOME" --chain-id "$CHAIN_ID" --default-denom stake >&2

# --- genesis edits, BEFORE anything is ever started -------------------
# (order matters: see the header comment.)
log "editing genesis gov params (voting_period=$GOV_VOTING_PERIOD, expedited_voting_period=$GOV_EXPEDITED_VOTING_PERIOD)"
python3 - "$CHAIN_HOME/config/genesis.json" "$GOV_VOTING_PERIOD" "$GOV_EXPEDITED_VOTING_PERIOD" <<'PYEOF'
import json, sys
path, voting_period, expedited = sys.argv[1:4]
with open(path) as f:
    doc = json.load(f)
gov = doc["app_state"]["gov"]["params"]
gov["voting_period"] = voting_period
gov["expedited_voting_period"] = expedited
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PYEOF

# --- keys ------------------------------------------------------------------
log "creating keys: $VALIDATOR_KEY (default/secp256k1), ${TEST_KEYS_ML_DSA[*]} (ml_dsa_65)"
"${BIN[@]}" keys add "$VALIDATOR_KEY" --home "$CHAIN_HOME" --keyring-backend test >&2
for k in "${TEST_KEYS_ML_DSA[@]}"; do
  "${BIN[@]}" keys add "$k" --home "$CHAIN_HOME" --keyring-backend test --key-type ml_dsa_65 >&2
done

# --- fund genesis accounts -------------------------------------------------
log "funding genesis accounts ($FUND_AMOUNT each)"
"${BIN[@]}" genesis add-genesis-account "$VALIDATOR_KEY" "$FUND_AMOUNT" --home "$CHAIN_HOME" --keyring-backend test >&2
for k in "${TEST_KEYS_ML_DSA[@]}"; do
  "${BIN[@]}" genesis add-genesis-account "$k" "$FUND_AMOUNT" --home "$CHAIN_HOME" --keyring-backend test >&2
done

# --- gentx / collect / validate --------------------------------------------
log "gentx ($GENTX_AMOUNT self-delegation)"
"${BIN[@]}" genesis gentx "$VALIDATOR_KEY" "$GENTX_AMOUNT" --home "$CHAIN_HOME" --keyring-backend test --chain-id "$CHAIN_ID" >&2

log "collect-gentxs"
"${BIN[@]}" genesis collect-gentxs --home "$CHAIN_HOME" >&2

log "validate"
"${BIN[@]}" genesis validate --home "$CHAIN_HOME" >&2

ok "chain initialized at $CHAIN_HOME (chain-id $CHAIN_ID)"
ok "keys: $VALIDATOR_KEY (secp256k1, self-bonded), ${TEST_KEYS_ML_DSA[*]} (ml_dsa_65, funded, not bonded)"
ok "next: pqchaind start --home $CHAIN_HOME"
