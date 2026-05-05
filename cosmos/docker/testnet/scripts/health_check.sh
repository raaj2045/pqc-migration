#!/usr/bin/env bash
# Poll every validator's RPC /status endpoint *concurrently* and confirm that
# every node reached the target height at least once inside the window. This
# is deliberately NOT a "snapshot" check — at N=32 the host can't always keep
# all 32 RPC sockets responsive in the same 100 ms window, so the old loop
# (all nodes healthy in the same iteration) spuriously timed out even while
# blocks were advancing. The relevant question for the experiment is "did
# every validator participate", not "were they all reachable at the same
# instant".
#
# Algorithm:
#   - poll all N validators concurrently via xargs -P (one curl per node,
#     per-endpoint --max-time of PER_REQ_TIMEOUT),
#   - maintain a per-node "max height ever seen" across the whole window,
#   - exit 0 as soon as every node has been observed at height >= MIN_HEIGHT
#     at least once,
#   - on timeout, print a diagnostic table: per-node max height +
#     reachability (successful polls / total polls).

set -euo pipefail

TIMEOUT=180
MIN_HEIGHT=5
VALIDATORS=""
BASE_RPC=26657
PORT_STRIDE=10
HOST="127.0.0.1"
POLL_INTERVAL=2
PER_REQ_TIMEOUT=2
PARALLEL=""   # default: equal to VALIDATORS

usage() {
  cat <<EOF
Usage: $0 --validators N [--timeout SECONDS] [--min-height H] [--host HOST]
          [--poll-interval SECONDS] [--per-request-timeout SECONDS] [--parallel N]

Required:
  --validators N             Number of validators to check

Optional:
  --timeout SECONDS          Total window (default: ${TIMEOUT})
  --min-height H             Target latest_block_height per node (default: ${MIN_HEIGHT})
  --host HOST                Host to reach containers on (default: ${HOST})
  --poll-interval SECONDS    Seconds between polls (default: ${POLL_INTERVAL})
  --per-request-timeout S    curl --max-time per endpoint (default: ${PER_REQ_TIMEOUT})
  --parallel N               Parallel curl workers (default: one per validator)
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --validators)           VALIDATORS="$2"; shift 2 ;;
    --timeout)              TIMEOUT="$2"; shift 2 ;;
    --min-height)           MIN_HEIGHT="$2"; shift 2 ;;
    --host)                 HOST="$2"; shift 2 ;;
    --poll-interval)        POLL_INTERVAL="$2"; shift 2 ;;
    --per-request-timeout)  PER_REQ_TIMEOUT="$2"; shift 2 ;;
    --parallel)             PARALLEL="$2"; shift 2 ;;
    -h|--help)              usage ;;
    *) echo "unknown flag: $1" >&2; usage ;;
  esac
done

if [[ -z "$VALIDATORS" ]]; then usage; fi
if ! command -v jq >/dev/null 2>&1; then
  echo "health_check: jq is required on the host" >&2
  exit 2
fi
if [[ -z "$PARALLEL" ]]; then PARALLEL="$VALIDATORS"; fi

# Scratch state lives under /tmp so we can survive set -e and clean up.
SCRATCH="$(mktemp -d -t health_check.XXXXXX)"
trap 'rm -rf "$SCRATCH"' EXIT

# Per-node accumulators.
for ((i=0; i<VALIDATORS; i++)); do
  echo 0 > "${SCRATCH}/max_h_${i}"
  echo 0 > "${SCRATCH}/ok_${i}"
  echo 0 > "${SCRATCH}/attempts_${i}"
done

probe_one() {
  # Called by xargs: $1 = validator index.
  local i="$1"
  local port=$(( BASE_RPC + i * PORT_STRIDE ))
  local url="http://${HOST}:${port}/status"

  local attempts_file="${SCRATCH}/attempts_${i}"
  local ok_file="${SCRATCH}/ok_${i}"
  local max_file="${SCRATCH}/max_h_${i}"

  # Bump attempts. Race-free enough for this purpose (single-writer per i).
  local attempts
  attempts=$(<"$attempts_file")
  echo $((attempts + 1)) > "$attempts_file"

  local body height
  if ! body="$(curl -fsS --max-time "$PER_REQ_TIMEOUT" "$url" 2>/dev/null)"; then
    return 0
  fi
  height="$(echo "$body" | jq -r '.result.sync_info.latest_block_height // empty' 2>/dev/null || true)"
  if [[ -z "$height" ]] || ! [[ "$height" =~ ^[0-9]+$ ]]; then
    return 0
  fi

  # Success: increment ok, update max.
  local ok; ok=$(<"$ok_file"); echo $((ok + 1)) > "$ok_file"
  local prev; prev=$(<"$max_file")
  if (( height > prev )); then echo "$height" > "$max_file"; fi
}

export -f probe_one
export SCRATCH HOST BASE_RPC PORT_STRIDE PER_REQ_TIMEOUT

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
  printf "  %-6s %-18s %-12s %-12s\n" "node" "rpc" "max_height" "reach%%"
  for ((i=0; i<VALIDATORS; i++)); do
    local port=$(( BASE_RPC + i * PORT_STRIDE ))
    local h; h=$(<"${SCRATCH}/max_h_${i}")
    local ok; ok=$(<"${SCRATCH}/ok_${i}")
    local tot; tot=$(<"${SCRATCH}/attempts_${i}")
    local pct="-"
    if (( tot > 0 )); then
      pct=$(awk -v o="$ok" -v t="$tot" 'BEGIN{printf "%.0f", 100*o/t}')
    fi
    printf "  %-6s %-18s %-12s %-12s\n" "node${i}" "${HOST}:${port}" "$h" "$pct"
  done
}

deadline=$(( $(date +%s) + TIMEOUT ))
echo "health_check: polling ${VALIDATORS} validator(s) concurrently (parallel=${PARALLEL}), min_height=${MIN_HEIGHT}, timeout=${TIMEOUT}s"
poll=0
while :; do
  poll=$((poll + 1))
  # Fire N curls in parallel. xargs keeps the worker pool bounded.
  seq 0 $((VALIDATORS - 1)) \
    | xargs -I{} -P "$PARALLEL" bash -c 'probe_one "$@"' _ {}

  if all_hit_target; then
    echo "health_check: all ${VALIDATORS} validators reached height >= ${MIN_HEIGHT}"
    print_table "final"
    exit 0
  fi

  now=$(date +%s)
  if (( now >= deadline )); then
    echo "health_check: timed out after ${TIMEOUT}s (poll #${poll})" >&2
    print_table "on timeout"
    exit 1
  fi

  # Quiet progress every 5th poll so logs don't flood at N=32.
  if (( poll % 5 == 0 )); then
    echo "  poll #${poll} ($((now - (deadline - TIMEOUT)))s/${TIMEOUT}s elapsed)"
  fi
  sleep "$POLL_INTERVAL"
done
