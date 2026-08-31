#!/usr/bin/env bash
# Verify the Kurtosis eth devnet is usable for cw-ics08-wasm-eth:
# real slot progression, finality, and a light client bootstrap with data.
set -uo pipefail

BEACON="${BEACON:-}"
if [ -z "$BEACON" ]; then
  PORT=$(kurtosis port print eth-devnet cl-1-lighthouse-geth http 2>/dev/null | tr -d '\r')
  BEACON="${PORT:-http://127.0.0.1:4000}"
fi
echo "beacon endpoint: $BEACON"
echo

echo "=== fork schedule ==="
curl -s "$BEACON/eth/v1/config/spec" | jq -r '.data | {
  ALTAIR_FORK_EPOCH, BELLATRIX_FORK_EPOCH, CAPELLA_FORK_EPOCH,
  DENEB_FORK_EPOCH, ELECTRA_FORK_EPOCH, FULU_FORK_EPOCH, SECONDS_PER_SLOT
}'
echo

echo "=== slot progression (sampled over ~30s) ==="
s1=$(curl -s "$BEACON/eth/v1/beacon/headers/head" | jq -r '.data.header.message.slot')
echo "slot at t=0 : $s1"
sleep 30
s2=$(curl -s "$BEACON/eth/v1/beacon/headers/head" | jq -r '.data.header.message.slot')
echo "slot at t=30: $s2"
if [ "$s2" -gt "$s1" ] 2>/dev/null; then
  echo "RESULT: chain IS producing blocks (+$((s2 - s1)) slots)"
else
  echo "RESULT: chain is NOT progressing"
fi
echo

echo "=== finality ==="
curl -s "$BEACON/eth/v1/beacon/states/head/finality_checkpoints" \
  | jq -r '.data | "finalized epoch: \(.finalized.epoch)  justified: \(.current_justified.epoch)"'
FIN_ROOT=$(curl -s "$BEACON/eth/v1/beacon/states/head/finality_checkpoints" | jq -r '.data.finalized.root')
echo "finalized root : $FIN_ROOT"
echo

echo "=== light client bootstrap (the endpoint cw-ics08-wasm-eth needs) ==="
code=$(curl -s -o /tmp/lc_bootstrap.json -w "%{http_code}" \
  "$BEACON/eth/v1/beacon/light_client/bootstrap/$FIN_ROOT")
echo "GET /eth/v1/beacon/light_client/bootstrap/$FIN_ROOT -> HTTP $code"
if [ "$code" = "200" ]; then
  jq -r '{
    version: .version,
    header_slot: .data.header.beacon.slot,
    sync_committee_pubkeys: (.data.current_sync_committee.pubkeys | length),
    aggregate_pubkey: .data.current_sync_committee.aggregate_pubkey,
    branch_len: (.data.current_sync_committee_branch | length)
  }' /tmp/lc_bootstrap.json
else
  head -c 300 /tmp/lc_bootstrap.json; echo
fi
echo

echo "=== other light client endpoints ==="
for ep in finality_update optimistic_update; do
  c=$(curl -s -o /tmp/lc_$ep.json -w "%{http_code}" "$BEACON/eth/v1/beacon/light_client/$ep")
  v=$(jq -r '.version // "-"' /tmp/lc_$ep.json 2>/dev/null)
  echo "$ep -> HTTP $c (version: $v)"
done
c=$(curl -s -o /tmp/lc_updates.json -w "%{http_code}" "$BEACON/eth/v1/beacon/light_client/updates?start_period=0&count=1")
echo "updates?start_period=0&count=1 -> HTTP $c (entries: $(jq 'length' /tmp/lc_updates.json 2>/dev/null))"
