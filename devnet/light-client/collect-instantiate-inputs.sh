#!/usr/bin/env bash
# Collect every field cw-ics08-wasm-eth's InstantiateMsg needs, sourced from the
# live Electra devnet. Writes ./instantiate-inputs/ and does NOT instantiate.
#
# InstantiateMsg { client_state: Binary, consensus_state: Binary, checksum: Binary }
# where client_state / consensus_state are JSON-serialized per
#   packages/ethereum/light-client/src/{client_state,consensus_state}.rs
set -uo pipefail
# Runs in $DEVNET_DIR: that is where the devnet tooling writes ports.env and
# where the collected inputs belong, alongside the other generated files.
DEVNET_DIR="${DEVNET_DIR:?set DEVNET_DIR (see devnet/devnet.env.example)}"
cd "$DEVNET_DIR"
if [ ! -f ports.env ]; then
  echo "ports.env missing in $DEVNET_DIR; generating it (kurtosis port print)..." >&2
  "$(dirname "${BASH_SOURCE[0]}")/../scripts/write-ports-env.sh"
fi
source ports.env
OUT=instantiate-inputs
mkdir -p "$OUT"

echo "beacon=$BEACON geth=$GETH_RPC"

rpc() { curl -s -X POST -H 'Content-Type: application/json' \
  --data "{\"jsonrpc\":\"2.0\",\"method\":\"$1\",\"params\":$2,\"id\":1}" "http://$GETH_RPC"; }

# --- raw sources -----------------------------------------------------------
curl -s "$BEACON/eth/v1/config/spec"    > "$OUT/spec.json"
curl -s "$BEACON/eth/v1/beacon/genesis" > "$OUT/genesis.json"
curl -s "$BEACON/eth/v1/beacon/states/head/finality_checkpoints" > "$OUT/finality.json"

FIN_ROOT=$(jq -r '.data.finalized.root' "$OUT/finality.json")
curl -s "$BEACON/eth/v1/beacon/light_client/bootstrap/$FIN_ROOT" > "$OUT/bootstrap.json"
curl -s "$BEACON/eth/v1/beacon/light_client/finality_update"     > "$OUT/finality_update.json"

VERSION=$(jq -r '.version' "$OUT/bootstrap.json")
SLOT=$(jq -r '.data.header.beacon.slot' "$OUT/bootstrap.json")
# ConsensusState.state_root is the EXECUTION state root, not the beacon one:
# membership verification runs verify_account_storage_root against it
# (packages/ethereum/light-client/src/{membership,update}.rs).
STATE_ROOT=$(jq -r '.data.header.execution.state_root' "$OUT/bootstrap.json")

# Execution payload at that finalized slot: block number + timestamp.
curl -s "$BEACON/eth/v2/beacon/blocks/$SLOT" > "$OUT/beacon_block.json"
EXEC_NUM=$(jq -r '.data.message.body.execution_payload.block_number' "$OUT/beacon_block.json")
EXEC_TS=$(jq -r '.data.message.body.execution_payload.timestamp' "$OUT/beacon_block.json")

CHAIN_ID=$(rpc eth_chainId '[]' | jq -r '.result')
CHAIN_ID_DEC=$((CHAIN_ID))

S() { jq -r ".data.$1" "$OUT/spec.json"; }

# --- client_state ----------------------------------------------------------
# ibc_contract_address / ibc_commitment_slot describe the ICS26Router deployed
# on THIS chain. The slot is a contract constant (ics26router.IbcStoreStorageSlot);
# the address is a placeholder until the Eureka stack is deployed here.
IBC_SLOT="0x1260944489272988d9df285149b5aa1b0f48f2136d6f416159f840a3e0747600"
IBC_ADDR="${ICS26_ROUTER_ADDR:-0x0000000000000000000000000000000000000000}"

jq -n \
  --argjson chain_id "$CHAIN_ID_DEC" \
  --arg gvr "$(jq -r '.data.genesis_validators_root' "$OUT/genesis.json")" \
  --argjson min_participants 1 \
  --argjson cs_size "$(S SYNC_COMMITTEE_SIZE)" \
  --argjson genesis_time "$(jq -r '.data.genesis_time' "$OUT/genesis.json")" \
  --argjson genesis_slot 0 \
  --arg gfv "$(S GENESIS_FORK_VERSION)" \
  --arg altair_v "$(S ALTAIR_FORK_VERSION)"       --argjson altair_e "$(S ALTAIR_FORK_EPOCH)" \
  --arg bellatrix_v "$(S BELLATRIX_FORK_VERSION)" --argjson bellatrix_e "$(S BELLATRIX_FORK_EPOCH)" \
  --arg capella_v "$(S CAPELLA_FORK_VERSION)"     --argjson capella_e "$(S CAPELLA_FORK_EPOCH)" \
  --arg deneb_v "$(S DENEB_FORK_VERSION)"         --argjson deneb_e "$(S DENEB_FORK_EPOCH)" \
  --arg electra_v "$(S ELECTRA_FORK_VERSION)"     --argjson electra_e "$(S ELECTRA_FORK_EPOCH)" \
  --arg fulu_v "$(S FULU_FORK_VERSION)"           --arg fulu_e "$(S FULU_FORK_EPOCH)" \
  --argjson sps "$(S SECONDS_PER_SLOT)" \
  --argjson spe "$(S SLOTS_PER_EPOCH)" \
  --argjson epscp "$(S EPOCHS_PER_SYNC_COMMITTEE_PERIOD)" \
  --argjson latest_slot "$SLOT" \
  --argjson latest_exec "$EXEC_NUM" \
  --arg ibc_addr "$IBC_ADDR" \
  --arg ibc_slot "$IBC_SLOT" \
'{
  chain_id: $chain_id,
  genesis_validators_root: $gvr,
  min_sync_committee_participants: $min_participants,
  sync_committee_size: $cs_size,
  genesis_time: $genesis_time,
  genesis_slot: $genesis_slot,
  fork_parameters: {
    genesis_fork_version: $gfv,
    genesis_slot: $genesis_slot,
    altair:    { version: $altair_v,    epoch: $altair_e },
    bellatrix: { version: $bellatrix_v, epoch: $bellatrix_e },
    capella:   { version: $capella_v,   epoch: $capella_e },
    deneb:     { version: $deneb_v,     epoch: $deneb_e },
    electra:   { version: $electra_v,   epoch: $electra_e },
    fulu:      { version: $fulu_v,      epoch: "__FULU_EPOCH__" }
  },
  seconds_per_slot: $sps,
  slots_per_epoch: $spe,
  epochs_per_sync_committee_period: $epscp,
  latest_slot: $latest_slot,
  latest_execution_block_number: $latest_exec,
  is_frozen: false,
  ibc_contract_address: $ibc_addr,
  ibc_commitment_slot: $ibc_slot
}' > "$OUT/client_state.json.tmp"

# jq 1.6 stores numbers as doubles, so FULU_FORK_EPOCH (2^64-1) round-trips as
# 18446744073709552000 — above u64::MAX, which would fail to deserialize on the
# Rust side. Splice the exact integer back in as a raw literal.
FULU_EPOCH=$(S FULU_FORK_EPOCH)
sed "s/\"__FULU_EPOCH__\"/$FULU_EPOCH/" "$OUT/client_state.json.tmp" > "$OUT/client_state.json"
rm -f "$OUT/client_state.json.tmp"

# --- consensus_state -------------------------------------------------------
# pubkeys_hash is the SSZ hash-tree-root of the 512-pubkey vector; it is NOT
# served by the Beacon API and must be computed. Left null and flagged.
jq -n \
  --argjson slot "$SLOT" \
  --arg state_root "$STATE_ROOT" \
  --argjson timestamp "$EXEC_TS" \
  --arg agg "$(jq -r '.data.current_sync_committee.aggregate_pubkey' "$OUT/bootstrap.json")" \
'{
  slot: $slot,
  state_root: $state_root,
  timestamp: $timestamp,
  current_sync_committee: { pubkeys_hash: null, aggregate_pubkey: $agg },
  next_sync_committee: null
}' > "$OUT/consensus_state.json"

jq -r '.data.current_sync_committee.pubkeys' "$OUT/bootstrap.json" > "$OUT/current_sync_committee_pubkeys.json"

echo
echo "bootstrap version : $VERSION"
echo "finalized root    : $FIN_ROOT"
echo "finalized slot    : $SLOT   exec block: $EXEC_NUM   exec ts: $EXEC_TS"
echo "pubkeys captured  : $(jq 'length' "$OUT/current_sync_committee_pubkeys.json")"
echo "written           : $OUT/{client_state,consensus_state,bootstrap,spec,genesis}.json"
