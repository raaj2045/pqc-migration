#!/usr/bin/env bash
# Single entry point that brings up a full devnet from a bare checkout:
# toolchain, Ethereum devnet, solidity-ibc-eureka checkout + proof-api, the
# wasm light client, the Cosmos chain, contract deployment, the governance
# flow to store the light client code, and creating the 08-wasm light client
# itself. Hard-stops on the first failure with a clear message naming which
# stage failed.
#
# Every stage is individually skippable, so a partial failure can be
# resumed by re-running with --skip-<earlier stages> instead of starting
# over.
#
# Usage: bring-up-devnet.sh [flags]
#   --skip-toolchain      skip devnet/scripts/setup-toolchain.sh
#   --skip-eth-devnet     skip `kurtosis run` + wait-for-finality (assumes the
#                         enclave is already up and finalized)
#   --skip-ports          skip devnet/scripts/write-ports-env.sh
#   --skip-checkout       skip devnet/scripts/setup-eureka-checkout.sh
#   --skip-wasm-build     skip devnet/scripts/build-wasm-light-client.sh
#   --skip-chain-init     skip devnet/scripts/init-chain.sh
#   --force-chain-init    pass --force through to init-chain.sh
#   --skip-chain-start    skip starting pqchaind in the background
#   --skip-deploy         skip devnet/scripts/deploy-contracts.sh
#   --skip-store-vote     skip devnet/scripts/store-and-vote-wasm-code.sh
#   --skip-light-client   skip devnet/scripts/create-light-client.sh (creates
#                         a NEW 08-wasm-N client every run — never re-run this
#                         stage against a devnet that already has the client
#                         you want unless a fresh one is actually intended)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

SKIP_TOOLCHAIN=0 SKIP_ETH_DEVNET=0 SKIP_PORTS=0 SKIP_CHECKOUT=0 SKIP_WASM_BUILD=0
SKIP_CHAIN_INIT=0 FORCE_CHAIN_INIT=0 SKIP_CHAIN_START=0 SKIP_DEPLOY=0 SKIP_STORE_VOTE=0
SKIP_LIGHT_CLIENT=0
for arg in "$@"; do
  case "$arg" in
    --skip-toolchain) SKIP_TOOLCHAIN=1 ;;
    --skip-eth-devnet) SKIP_ETH_DEVNET=1 ;;
    --skip-ports) SKIP_PORTS=1 ;;
    --skip-checkout) SKIP_CHECKOUT=1 ;;
    --skip-wasm-build) SKIP_WASM_BUILD=1 ;;
    --skip-chain-init) SKIP_CHAIN_INIT=1 ;;
    --force-chain-init) FORCE_CHAIN_INIT=1 ;;
    --skip-chain-start) SKIP_CHAIN_START=1 ;;
    --skip-deploy) SKIP_DEPLOY=1 ;;
    --skip-store-vote) SKIP_STORE_VOTE=1 ;;
    --skip-light-client) SKIP_LIGHT_CLIENT=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^#//'; exit 0 ;;
    *) die "unknown flag: $arg (--help for usage)" ;;
  esac
done

ENCLAVE="${ENCLAVE:-eth-devnet}"
ETHEREUM_PACKAGE_VERSION="$(table_version '`ethereum-package`' '6.0.0')"

stage() { printf '\n\033[1;36m### %s\033[0m\n' "$*" >&2; }

# --- 1: toolchain ------------------------------------------------------
if [ "$SKIP_TOOLCHAIN" = 1 ]; then
  stage "1/10 setup-toolchain.sh — skipped"
else
  stage "1/10 setup-toolchain.sh"
  "$SCRIPTS_DIR/setup-toolchain.sh"
fi

# --- 2: Ethereum devnet + finality ---------------------------------------
if [ "$SKIP_ETH_DEVNET" = 1 ]; then
  stage "2/10 kurtosis eth-devnet — skipped"
else
  stage "2/10 kurtosis eth-devnet"
  require_cmd kurtosis
  # An enclave is only usable if its SERVICES are up — the enclave-level
  # status is not sufficient evidence of that, in either direction:
  #
  #   * `kurtosis enclave ls` reports STOPPED after a host reboot or an
  #     explicit `kurtosis enclave stop`, and there is no `kurtosis enclave
  #     start` to resume one.
  #   * It reports RUNNING while every container inside is STOPPED or gone
  #     outright — the state an unclean host shutdown leaves behind. Here
  #     `kurtosis enclave inspect` itself fails ("couldn't find service with
  #     uuid ... in backend") and `kurtosis port print` returns nothing.
  #
  # Either way the containers, and the EVM state inside them, are gone for
  # good (see "The EVM devnet does not survive a restart" in ../README.md),
  # so both cases get the same treatment: remove and recreate. Treating
  # "exists" — or even "exists and says RUNNING" — as "usable" made this
  # stage silently skip creation and then hang for the full 20 minutes
  # polling finality from a beacon node that was not running.
  #
  # The helper retries before returning false, because a failed inspect is AMBIGUOUS: it
  # means the services are unrecoverable, but it equally means the kurtosis
  # engine is still warming up and cannot answer yet (observed: "Failed to get
  # service info from API container ... couldn't find service with uuid" a few
  # seconds after the engine starts, then a clean inspect moments later).
  # Recreating is destructive and costs a fresh 20-minute finality wait, so a
  # transient engine hiccup must not trigger it. A genuinely stopped service is
  # definitive and answers immediately; only the ambiguous cases pay the retry.
  enclave_services_running() {
    local out svcs attempt
    for attempt in 1 2 3; do
      # Only the User Services table — the enclave's own "Status: RUNNING"
      # line sits above it and is exactly what must not be trusted here. Note
      # a service's extra port lines continue below its row with an empty
      # status column, so match on the status words, not on line counts.
      if out="$(kurtosis enclave inspect "$1" 2>/dev/null)"; then
        svcs="$(awk '/^=+ User Services =+$/ {f=1; next} /^=+ .+ =+$/ {f=0} f' <<<"$out")"
        if grep -qw STOPPED <<<"$svcs"; then
          return 1                            # definitive: a service is down
        elif grep -qw RUNNING <<<"$svcs"; then
          return 0                            # every listed service is up
        fi
        # else: inspect succeeded but listed no services — ambiguous, retry.
      fi
      if [ "$attempt" -lt 3 ]; then sleep 5; fi
    done
    return 1
  }

  enclave_line="$(kurtosis enclave ls 2>/dev/null | awk -v e="$ENCLAVE" '$2 == e')"
  if [ -z "$enclave_line" ]; then
    recreate_reason=""
  elif grep -qw STOPPED <<<"$enclave_line"; then
    recreate_reason="it is stopped (containers gone, not resumable)"
  elif ! enclave_services_running "$ENCLAVE"; then
    recreate_reason="the enclave reports RUNNING but its services are stopped or missing"
  else
    recreate_reason="none"
  fi

  if [ "$recreate_reason" = "none" ]; then
    ok "enclave '$ENCLAVE' already exists and its services are running"
  else
    if [ -n "$recreate_reason" ]; then
      log "enclave '$ENCLAVE' exists but $recreate_reason — removing it"
      kurtosis enclave rm "$ENCLAVE" -f
    fi
    log "running ethereum-package@$ETHEREUM_PACKAGE_VERSION into enclave '$ENCLAVE'"
    kurtosis run "github.com/ethpandaops/ethereum-package@$ETHEREUM_PACKAGE_VERSION" \
      --args-file "$DEVNET_ROOT/kurtosis/network_params.yaml" --enclave "$ENCLAVE"
  fi
  "$DEVNET_ROOT/kurtosis/verify-devnet.sh" wait-for-finality
fi

# --- 3: ports.env ----------------------------------------------------------
if [ "$SKIP_PORTS" = 1 ]; then
  stage "3/10 write-ports-env.sh — skipped"
else
  stage "3/10 write-ports-env.sh"
  "$SCRIPTS_DIR/write-ports-env.sh"
fi

# --- 4: solidity-ibc-eureka checkout + proof-api --------------------------
if [ "$SKIP_CHECKOUT" = 1 ]; then
  stage "4/10 setup-eureka-checkout.sh — skipped"
else
  stage "4/10 setup-eureka-checkout.sh"
  "$SCRIPTS_DIR/setup-eureka-checkout.sh"
fi

# --- 5: wasm light client --------------------------------------------------
if [ "$SKIP_WASM_BUILD" = 1 ]; then
  stage "5/10 build-wasm-light-client.sh — skipped"
else
  stage "5/10 build-wasm-light-client.sh"
  "$SCRIPTS_DIR/build-wasm-light-client.sh"
fi

# --- 6: Cosmos chain init ---------------------------------------------------
if [ "$SKIP_CHAIN_INIT" = 1 ]; then
  stage "6/10 init-chain.sh — skipped"
else
  stage "6/10 init-chain.sh"
  if [ "$FORCE_CHAIN_INIT" = 1 ]; then
    "$SCRIPTS_DIR/init-chain.sh" --force
  else
    "$SCRIPTS_DIR/init-chain.sh"
  fi
fi

# --- 6b: start the chain (not a numbered script — needed so 9/10 below has
# something to submit transactions to) ---------------------------------
DEVNET_DIR="$(resolve_devnet_dir)"
mkdir -p "$DEVNET_DIR"
if [ "$SKIP_CHAIN_START" = 1 ]; then
  stage "6b/10 start pqchaind — skipped"
else
  stage "6b/10 start pqchaind"
  eval "$(devnet_cfg CHAIN_HOME CHAIN_NODE PQCHAIND_BIN)"
  [ -n "${CHAIN_HOME:-}" ] && [ -n "${PQCHAIND_BIN:-}" ] || die "CHAIN_HOME/PQCHAIND_BIN did not resolve; check devnet.env"
  status_url="$(sed -E 's#^tcp://#http://#' <<<"${CHAIN_NODE:-tcp://127.0.0.1:26657}")/status"
  if pgrep -f "pqchaind[[:space:]].*--home[[:space:]=]*$CHAIN_HOME" >/dev/null 2>&1; then
    ok "pqchaind already running against $CHAIN_HOME"
  else
    [ -x "$PQCHAIND_BIN" ] || die "PQCHAIND_BIN ($PQCHAIND_BIN) is not executable — run 6/10 (init-chain.sh) first."
    log "starting pqchaind in the background (log: $DEVNET_DIR/pqchaind.log)"
    nohup "$PQCHAIND_BIN" start --home "$CHAIN_HOME" > "$DEVNET_DIR/pqchaind.log" 2>&1 &
    echo $! > "$DEVNET_DIR/pqchaind.pid"
    ok_height=""
    for _ in $(seq 1 30); do
      h="$(curl -fsS "$status_url" 2>/dev/null | jq -r '.result.sync_info.latest_block_height // "0"' 2>/dev/null || echo 0)"
      if [ -n "$h" ] && [ "$h" != "0" ] 2>/dev/null; then ok_height="$h"; break; fi
      sleep 2
    done
    [ -n "$ok_height" ] || die "pqchaind did not produce a block within 60s; check $DEVNET_DIR/pqchaind.log"
    ok "chain producing blocks (height $ok_height, pid $(cat "$DEVNET_DIR/pqchaind.pid"))"
  fi
fi

# --- 7: deploy contracts ----------------------------------------------------
if [ "$SKIP_DEPLOY" = 1 ]; then
  stage "7/10 deploy-contracts.sh — skipped"
else
  stage "7/10 deploy-contracts.sh"
  "$SCRIPTS_DIR/deploy-contracts.sh"
fi

# --- 8: store + vote the wasm code -----------------------------------------
if [ "$SKIP_STORE_VOTE" = 1 ]; then
  stage "8/10 store-and-vote-wasm-code.sh — skipped"
else
  stage "8/10 store-and-vote-wasm-code.sh"
  "$SCRIPTS_DIR/store-and-vote-wasm-code.sh"
fi

# --- 9: create the 08-wasm light client -------------------------------
if [ "$SKIP_LIGHT_CLIENT" = 1 ]; then
  stage "9/10 create-light-client.sh — skipped"
else
  stage "9/10 create-light-client.sh"
  client_id="$("$SCRIPTS_DIR/create-light-client.sh")"
  ok "light client $client_id created and Active"
fi

# --- done ----------------------------------------------------------------
stage "10/10 devnet is up"
ok "bring-up-devnet.sh complete"
