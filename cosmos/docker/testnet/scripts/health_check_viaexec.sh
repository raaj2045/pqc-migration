#!/usr/bin/env bash
# Variant of health_check.sh that queries validators via `docker exec ... simd
# status` instead of the host-side RPC port map. This bypasses the host
# networking stack entirely, which matters at N >= 16: with 16+ validators
# sharing one loopback interface, concurrent host-side curls can transiently
# time out even though the chain is producing blocks. docker exec goes
# through the container runtime and is noticeably more tolerant.
#
# Same exit-code contract as health_check.sh:
#   0 — every validator observed latest_block_height >= MIN_HEIGHT in window.
#   1 — timed out (prints per-node diagnostic table).
#   2 — missing required host tool (docker, jq).

set -euo pipefail

TIMEOUT=180
MIN_HEIGHT=5
VALIDATORS=""
POLL_INTERVAL=2
PER_REQ_TIMEOUT=5
PARALLEL=""
CONTAINER_PREFIX="cosmos-testnet-node"
# simd reads --home/COSMOS_HOME when the daemon starts, but client subcommands
# like `status` ignore COSMOS_HOME and fall back to /.simapp — unwritable for
# the non-root user we run as. Passing --home explicitly is the workaround.
SIMD_HOME="/cosmos"

usage() {
  cat <<EOF
Usage: $0 --validators N [--timeout SECONDS] [--min-height H]
          [--poll-interval SECONDS] [--per-request-timeout SECONDS] [--parallel N]
          [--container-prefix PREFIX]

Required:
  --validators N             Number of validators to check

Optional:
  --timeout SECONDS          Total window (default: ${TIMEOUT})
  --min-height H             Target latest_block_height per node (default: ${MIN_HEIGHT})
  --poll-interval SECONDS    Seconds between polls (default: ${POLL_INTERVAL})
  --per-request-timeout S    timeout on each docker exec (default: ${PER_REQ_TIMEOUT})
  --parallel N               Parallel workers (default: one per validator)
  --container-prefix PREFIX  Container name prefix (default: ${CONTAINER_PREFIX})
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --validators)           VALIDATORS="$2"; shift 2 ;;
    --timeout)              TIMEOUT="$2"; shift 2 ;;
    --min-height)           MIN_HEIGHT="$2"; shift 2 ;;
    --poll-interval)        POLL_INTERVAL="$2"; shift 2 ;;
    --per-request-timeout)  PER_REQ_TIMEOUT="$2"; shift 2 ;;
    --parallel)             PARALLEL="$2"; shift 2 ;;
    --container-prefix)     CONTAINER_PREFIX="$2"; shift 2 ;;
    -h|--help)              usage ;;
    *) echo "unknown flag: $1" >&2; usage ;;
  esac
done

if [[ -z "$VALIDATORS" ]]; then usage; fi
command -v docker >/dev/null 2>&1 || { echo "docker required" >&2; exit 2; }
command -v jq     >/dev/null 2>&1 || { echo "jq required"     >&2; exit 2; }
if [[ -z "$PARALLEL" ]]; then PARALLEL="$VALIDATORS"; fi

SCRATCH="$(mktemp -d -t health_check_viaexec.XXXXXX)"
trap 'rm -rf "$SCRATCH"' EXIT

for ((i=0; i<VALIDATORS; i++)); do
  echo 0 > "${SCRATCH}/max_h_${i}"
  echo 0 > "${SCRATCH}/ok_${i}"
  echo 0 > "${SCRATCH}/attempts_${i}"
done

probe_one() {
  local i="$1"
  local container="${CONTAINER_PREFIX}${i}"
  local attempts_file="${SCRATCH}/attempts_${i}"
  local ok_file="${SCRATCH}/ok_${i}"
  local max_file="${SCRATCH}/max_h_${i}"

  local attempts; attempts=$(<"$attempts_file")
  echo $((attempts + 1)) > "$attempts_file"

  # `simd status` emits JSON on stdout. Use timeout(1) to bound the exec.
  local body height
  if ! body="$(timeout "$PER_REQ_TIMEOUT" docker exec "$container" simd status --home "$SIMD_HOME" 2>/dev/null)"; then
    return 0
  fi
  # In v0.50+, `simd status` emits {sync_info:{latest_block_height:...}} at the root.
  # Older layouts nested it under .SyncInfo. Accept both.
  height="$(echo "$body" | jq -r \
    '(.sync_info.latest_block_height // .SyncInfo.latest_block_height // empty)' \
    2>/dev/null || true)"
  if [[ -z "$height" ]] || ! [[ "$height" =~ ^[0-9]+$ ]]; then return 0; fi

  local ok; ok=$(<"$ok_file"); echo $((ok + 1)) > "$ok_file"
  local prev; prev=$(<"$max_file")
  if (( height > prev )); then echo "$height" > "$max_file"; fi
}

export -f probe_one
export SCRATCH PER_REQ_TIMEOUT CONTAINER_PREFIX SIMD_HOME

all_hit_target() {
  for ((i=0; i<VALIDATORS; i++)); do
    local h; h=$(<"${SCRATCH}/max_h_${i}")
    if (( h < MIN_HEIGHT )); then return 1; fi
  done
  return 0
}

print_table() {
  local tag="$1"
  echo "  ${tag} per-validator state:"
  printf "  %-8s %-30s %-12s %-12s\n" "node" "container" "max_height" "reach%%"
  for ((i=0; i<VALIDATORS; i++)); do
    local h; h=$(<"${SCRATCH}/max_h_${i}")
    local ok; ok=$(<"${SCRATCH}/ok_${i}")
    local tot; tot=$(<"${SCRATCH}/attempts_${i}")
    local pct="-"
    if (( tot > 0 )); then
      pct=$(awk -v o="$ok" -v t="$tot" 'BEGIN{printf "%.0f", 100*o/t}')
    fi
    printf "  %-8s %-30s %-12s %-12s\n" "node${i}" "${CONTAINER_PREFIX}${i}" "$h" "$pct"
  done
}

deadline=$(( $(date +%s) + TIMEOUT ))
echo "health_check_viaexec: polling ${VALIDATORS} validator(s) concurrently via docker exec (parallel=${PARALLEL}), min_height=${MIN_HEIGHT}, timeout=${TIMEOUT}s"
poll=0
while :; do
  poll=$((poll + 1))
  seq 0 $((VALIDATORS - 1)) \
    | xargs -I{} -P "$PARALLEL" bash -c 'probe_one "$@"' _ {}

  if all_hit_target; then
    echo "health_check_viaexec: all ${VALIDATORS} validators reached height >= ${MIN_HEIGHT}"
    print_table "final"
    exit 0
  fi

  now=$(date +%s)
  if (( now >= deadline )); then
    echo "health_check_viaexec: timed out after ${TIMEOUT}s (poll #${poll})" >&2
    print_table "on timeout"
    exit 1
  fi

  if (( poll % 5 == 0 )); then
    echo "  poll #${poll} ($((now - (deadline - TIMEOUT)))s/${TIMEOUT}s elapsed)"
  fi
  sleep "$POLL_INTERVAL"
done
