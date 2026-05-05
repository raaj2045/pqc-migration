#!/usr/bin/env bash
# Entrypoint for a single testnet validator (or seed) container.
#
# The init script writes each node's home to /cosmos inside the container by
# mounting testnet-data/node<i>/simd. We do no in-container init: everything
# is prepared on the host by init_testnet.sh so restarts are deterministic.
#
# All extra args are forwarded to `simd start` (e.g. --log_level info).
set -euo pipefail

: "${COSMOS_HOME:=/cosmos}"

if [ ! -f "${COSMOS_HOME}/config/genesis.json" ]; then
  echo "node_entrypoint: no genesis at ${COSMOS_HOME}/config/genesis.json" >&2
  echo "node_entrypoint: run scripts/init_testnet.sh on the host first" >&2
  exit 1
fi

cmd="${1:-start}"
shift || true

exec simd "$cmd" --home "${COSMOS_HOME}" "$@"
