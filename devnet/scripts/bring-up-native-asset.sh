#!/usr/bin/env bash
# Second-stage orchestrator: brings a stake-flow devnet (bring-up-devnet.sh)
# up to a runnable step-native-send.js. Stages are individually skippable and
# safe to re-run, same pattern as bring-up-devnet.sh.
#
# Usage: bring-up-native-asset.sh [flags]
#   --skip-prereq-check   skip verifying the stake-flow devnet is already up
#   --skip-proof-api      skip regenerating/restarting proof-api
#   --skip-eth-client     skip create-eth-client.js (EVM client + Cosmos
#                         counterparty registration — one script, since the
#                         Cosmos side needs the EVM client's auto-assigned ID)
#   --skip-test-token     skip deploy-test-token.sh
#   --skip-wallet-check   skip verifying RECEIVER_ADDR/RECEIVER_PK/
#                         COSMOS_RECEIVER are set in devnet.env
#   --verifier=mock|real  which SP1 verifier the new EVM light client binds to
#                         AND, necessarily, which prover proof-api is configured
#                         with — the two are one setting, not two. mock binds
#                         SP1MockVerifier (near-instant, not a real proof) and
#                         proof-api's mock prover; real binds SP1VerifierGroth16
#                         (~10 min/proof) and proof-api's cpu prover. No default
#                         — always explicit, so it's unambiguous which mode
#                         produced a given run's results. Passed through to
#                         create-eth-client.js and into proof-api-config.json.
#
#                         These MUST agree. SP1MockVerifier's entire body is
#                         `assert(proofBytes.length == 0)`, so a mock-bound
#                         client handed a real Groth16 proof reverts with
#                         Panic(0x01) on every single relay — and a real-bound
#                         client handed an empty mock proof fails just as
#                         surely. Configuring one side without the other is the
#                         defect this flag exists to make impossible.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

SKIP_PREREQ=0 SKIP_PROOF_API=0 SKIP_ETH_CLIENT=0 SKIP_TEST_TOKEN=0 SKIP_WALLET_CHECK=0
VERIFIER=""
for arg in "$@"; do
  case "$arg" in
    --skip-prereq-check) SKIP_PREREQ=1 ;;
    --skip-proof-api) SKIP_PROOF_API=1 ;;
    --skip-eth-client) SKIP_ETH_CLIENT=1 ;;
    --skip-test-token) SKIP_TEST_TOKEN=1 ;;
    --skip-wallet-check) SKIP_WALLET_CHECK=1 ;;
    --verifier=mock|--verifier=real) VERIFIER="${arg#--verifier=}" ;;
    -h|--help) grep '^#' "$0" | sed 's/^#//'; exit 0 ;;
    *) die "unknown flag: $arg (--help for usage)" ;;
  esac
done
# Required by BOTH the proof-api stage (prover) and the eth-client stage
# (on-chain verifier) — either one running without it would pick a side
# blind, which is exactly how a mock verifier ends up paired with a real
# prover.
if { [ "$SKIP_ETH_CLIENT" != 1 ] || [ "$SKIP_PROOF_API" != 1 ]; } && [ -z "$VERIFIER" ]; then
  die "--verifier=mock|real is required (no default) — it selects BOTH the SP1 verifier the EVM light client binds to AND proof-api's prover, which must match. See --help."
fi

# The prover that pairs with each verifier. mock <-> mock is not an
# optimization, it is a correctness requirement (see --help).
case "$VERIFIER" in
  mock) PROVER_TYPE="mock" ;;
  real) PROVER_TYPE="cpu" ;;
  "")   PROVER_TYPE="" ;;   # only reachable with both stages skipped
esac

stage() { printf '\n\033[1;36m### %s\033[0m\n' "$*" >&2; }

DEVNET_DIR="$(resolve_devnet_dir)"
mkdir -p "$DEVNET_DIR"
require_cmd node
require_cmd cast "run devnet/scripts/setup-toolchain.sh first (Foundry)."
require_cmd jq

# --- 1: verify the stake-flow devnet is already up --------------------------
if [ "$SKIP_PREREQ" = 1 ]; then
  stage "1/5 verify stake-flow prerequisites — skipped"
else
  stage "1/5 verify stake-flow prerequisites"
  eval "$(devnet_cfg CHAIN_NODE GETH_RPC ICS26_ROUTER ICS20_TRANSFER COSMOS_CLIENT_ID PROOF_API_ADDR PQCHAIND_BIN CHAIN_HOME)"

  status_url="$(sed -E 's#^tcp://#http://#' <<<"${CHAIN_NODE:-}")/status"
  curl -fsS "$status_url" >/dev/null 2>&1 \
    || die "Cosmos chain not reachable at $CHAIN_NODE. Run bring-up-devnet.sh first."
  ok "Cosmos chain reachable at $CHAIN_NODE"

  rpc_url="$GETH_RPC"; case "$rpc_url" in http*) ;; *) rpc_url="http://$rpc_url" ;; esac
  cast chain-id --rpc-url "$rpc_url" >/dev/null 2>&1 \
    || die "Ethereum devnet not reachable at $GETH_RPC. Run bring-up-devnet.sh first (or write-ports-env.sh if it was just restarted)."
  ok "Ethereum devnet reachable at $GETH_RPC"

  for name_addr in "ICS26_ROUTER=$ICS26_ROUTER" "ICS20_TRANSFER=$ICS20_TRANSFER"; do
    name="${name_addr%%=*}" addr="${name_addr#*=}"
    [ -n "$addr" ] || die "$name not set — run bring-up-devnet.sh (deploy-contracts.sh) first."
    code="$(cast code "$addr" --rpc-url "$rpc_url" 2>/dev/null || echo 0x)"
    [ "$code" != "0x" ] || die "$name=$addr has no code on the current chain. The Ethereum devnet's execution " \
      "state does not survive an enclave restart (see devnet/README.md) — re-run bring-up-devnet.sh."
  done
  ok "ICS26Router and ICS20Transfer have live code"

  [ -n "${COSMOS_CLIENT_ID:-}" ] || die "COSMOS_CLIENT_ID not set in deploy.env or devnet.env. Run " \
    "devnet/scripts/create-light-client.sh first (bring-up-devnet.sh's stage 9)."
  cs_status="$("$PQCHAIND_BIN" query ibc client status "$COSMOS_CLIENT_ID" --home "$CHAIN_HOME" --node "$CHAIN_NODE" -o json 2>/dev/null | jq -r '.status')"
  [ "$cs_status" = "Active" ] || die "Cosmos client $COSMOS_CLIENT_ID status is '$cs_status', not Active. " \
    "Run devnet/scripts/create-light-client.sh to create a fresh one."
  ok "Cosmos light client $COSMOS_CLIENT_ID is Active"

  # proof-api itself is not checked here — bring-up-devnet.sh never starts it
  # either; stage 2 below brings it up or fixes a stale config.
  [ -n "${PROOF_API_ADDR:-}" ] || die "PROOF_API_ADDR not set; see devnet.env.example."
fi

# --- 2: verify-and-repoint the (single) cosmos_to_eth proof-api instance ----
# One instance serves both flows' SP1 legs (stake forward, native-asset ack).
# There is no eth_to_cosmos instance: that module has no SP1 mode and nothing
# in this project calls it — step-native-recv.js/step-ack.js prove Ethereum
# state directly instead. Runs before stage 3, which needs it for CreateClient.
if [ "$SKIP_PROOF_API" = 1 ]; then
  stage "2/5 verify-and-repoint proof-api — skipped"
else
  stage "2/5 verify-and-repoint proof-api"
  eval "$(devnet_cfg CHAIN_ID CHAIN_NODE ICS26_ROUTER GETH_RPC PROOF_API_ADDR)"
  SIBE_HOME_DIR="${SIBE_HOME:-$HOME/solidity-ibc-eureka}"
  IBC_SOLIDITY="$SIBE_HOME_DIR/ibc-solidity"
  ELF_DIR="$IBC_SOLIDITY/programs/sp1-programs/target/elf-compilation/riscv64im-succinct-zkvm-elf/release"

  missing_elf=0
  for prog in update-client membership uc-and-membership misbehaviour; do
    [ -f "$ELF_DIR/sp1-ics07-tendermint-$prog" ] || missing_elf=1
  done
  if [ "$missing_elf" = 1 ]; then
    log "SP1 program ELFs missing — building (just build-sp1-programs, one-time, a few minutes)"
    (cd "$SIBE_HOME_DIR" && just build-sp1-programs)
  fi
  ok "SP1 program ELFs present in $ELF_DIR"

  eth_chain_id="$(cast chain-id --rpc-url "http://${GETH_RPC#http://}" 2>/dev/null)"
  CONFIG_FILE="$DEVNET_DIR/proof-api-config.json"
  NEW_CONFIG="$(jq -n \
    --arg cosmos_chain "$CHAIN_ID" --arg eth_chain "$eth_chain_id" \
    --arg tm_rpc "$(sed -E 's#^tcp://#http://#' <<<"$CHAIN_NODE")" \
    --arg ics26 "$ICS26_ROUTER" --arg eth_rpc "http://${GETH_RPC#http://}" \
    --arg uc "$ELF_DIR/sp1-ics07-tendermint-update-client" \
    --arg mem "$ELF_DIR/sp1-ics07-tendermint-membership" \
    --arg ucm "$ELF_DIR/sp1-ics07-tendermint-uc-and-membership" \
    --arg mis "$ELF_DIR/sp1-ics07-tendermint-misbehaviour" \
    --arg addr "${PROOF_API_ADDR%%:*}" --argjson port "${PROOF_API_ADDR##*:}" \
    --arg prover "$PROVER_TYPE" \
    '{
      server: { address: $addr, port: $port },
      observability: { level: "info", use_otel: false, service_name: "pqc-proof-api", otel_endpoint: null },
      modules: [{
        name: "cosmos_to_eth", src_chain: $cosmos_chain, dst_chain: $eth_chain,
        config: {
          tm_rpc_url: $tm_rpc, ics26_address: $ics26, eth_rpc_url: $eth_rpc,
          mode: { sp1: { sp1_prover: { type: $prover },
            sp1_programs: { update_client: $uc, membership: $mem, update_client_and_membership: $ucm, misbehaviour: $mis }
          } }
        }
      }]
    }')"

  needs_restart=1
  if [ -f "$CONFIG_FILE" ] && diff -q <(echo "$NEW_CONFIG") "$CONFIG_FILE" >/dev/null 2>&1; then
    host="${PROOF_API_ADDR%%:*}" port="${PROOF_API_ADDR##*:}"
    if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
      exec 3<&- 3>&-
      needs_restart=0
      ok "proof-api already running with current config ($CONFIG_FILE) — nothing to do"
    fi
  fi

  if [ "$needs_restart" = 1 ]; then
    echo "$NEW_CONFIG" > "$CONFIG_FILE"
    ok "wrote $CONFIG_FILE (ics26=$ICS26_ROUTER eth_rpc=$GETH_RPC prover=$PROVER_TYPE, paired with --verifier=$VERIFIER)"
    host="${PROOF_API_ADDR%%:*}" port="${PROOF_API_ADDR##*:}"
    port_open() { (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null && { exec 3<&- 3>&-; return 0; }; return 1; }

    # Wait for the OLD process to release the port before rebinding. `pkill`
    # returns as soon as the signal is sent, and a fixed `sleep 1` raced it:
    # the new proof-api died with "Address already in use", the old one then
    # exited too, and the bind check below — which cannot tell old from new —
    # saw the still-listening old process and reported success. The result was
    # a devnet with NO proof-api and a script that said it was running.
    pkill -f "proof-api start --config $CONFIG_FILE" 2>/dev/null || true
    for _ in $(seq 1 30); do port_open || break; sleep 1; done
    if port_open; then
      die "something is still listening on $PROOF_API_ADDR after stopping the old proof-api; refusing to start a second one that would fail to bind."
    fi

    # Background the SUBSHELL and let it exec into proof-api, so `$!` is
    # proof-api's own pid. Writing it any other way gives the pid of a wrapper
    # instead: `( cmd & )` leaves `$!` unset in this shell, and
    # `( cd X && nohup cmd & )` applies `&` to the whole `cd && nohup` list, so
    # `$!` names the subshell that merely parents proof-api. The liveness
    # checks below are only meaningful against the real process.
    api_pid_file="$DEVNET_DIR/proof-api.pid"
    (
      cd "$DEVNET_DIR" || exit 1
      exec nohup proof-api start --config "$CONFIG_FILE" > proof-api.log 2>&1
    ) &
    api_pid=$!
    disown "$api_pid" 2>/dev/null || true
    echo "$api_pid" > "$api_pid_file"
    log "waiting for proof-api (pid $api_pid) to bind $PROOF_API_ADDR"
    for _ in $(seq 1 60); do
      port_open && break
      # A dead child will never bind — fail now with the log, rather than
      # burning the full timeout.
      kill -0 "$api_pid" 2>/dev/null \
        || die "proof-api (pid $api_pid) exited before binding $PROOF_API_ADDR; check $DEVNET_DIR/proof-api.log"
      sleep 2
    done
    port_open || die "proof-api did not bind $PROOF_API_ADDR within 120s; check $DEVNET_DIR/proof-api.log"
    kill -0 "$api_pid" 2>/dev/null \
      || die "$PROOF_API_ADDR is bound but the proof-api we started (pid $api_pid) is gone — a stale process is holding the port; check $DEVNET_DIR/proof-api.log"
    ok "proof-api (pid $api_pid) running with prover=$PROVER_TYPE, listening on $PROOF_API_ADDR"
  fi
fi

# --- 3: EVM-side SP1ICS07Tendermint client + Cosmos counterparty pairing ----
if [ "$SKIP_ETH_CLIENT" = 1 ]; then
  stage "3/5 create-eth-client.js — skipped"
else
  stage "3/5 create-eth-client.js (EVM client + Cosmos counterparty registration, --verifier=$VERIFIER)"
  (cd "$DEVNET_ROOT" && node create-eth-client.js "--verifier=$VERIFIER")
fi

# --- 4: deploy TestERC20 ----------------------------------------------------
if [ "$SKIP_TEST_TOKEN" = 1 ]; then
  stage "4/5 deploy-test-token.sh — skipped"
else
  stage "4/5 deploy-test-token.sh"
  "$SCRIPT_DIR/deploy-test-token.sh"
fi

# --- 5: verify the remaining hand-set devnet.env vars are present ----------
# RECEIVER_ADDR/RECEIVER_PK/COSMOS_RECEIVER are user-maintained (devnet.env,
# gitignored) — this only verifies and reports what's missing, it doesn't
# write into a file devnet.env.example documents as yours to edit.
if [ "$SKIP_WALLET_CHECK" = 1 ]; then
  stage "5/5 verify test-wallet vars — skipped"
else
  stage "5/5 verify test-wallet vars"
  eval "$(devnet_cfg RECEIVER_ADDR RECEIVER_PK COSMOS_RECEIVER GETH_RPC)"
  missing=()
  [ -n "${RECEIVER_ADDR:-}" ] && [ -n "${RECEIVER_PK:-}" ] || missing+=("RECEIVER_ADDR/RECEIVER_PK — generate with: cast wallet new")
  case "${COSMOS_RECEIVER:-}" in
    cosmos1*) ;;
    *) missing+=("COSMOS_RECEIVER — a Cosmos bech32 receiver address, e.g. an ML-DSA-65 test key: pqchaind keys show relayer -a --keyring-backend test") ;;
  esac
  if [ "${#missing[@]}" -gt 0 ]; then
    warn "the following must be set by hand in devnet.env before step-native-send.js can run:"
    for m in "${missing[@]}"; do printf '    - %s\n' "$m" >&2; done
    die "set the above in devnet.env, then re-run (--skip-wallet-check to bypass this check)"
  fi
  rpc_url="$GETH_RPC"; case "$rpc_url" in http*) ;; *) rpc_url="http://$rpc_url" ;; esac
  bal="$(cast balance "$RECEIVER_ADDR" --rpc-url "$rpc_url" 2>/dev/null || echo 0)"
  if [ "$bal" = "0" ]; then
    warn "RECEIVER_ADDR ($RECEIVER_ADDR) has a zero ETH balance on the current chain — funding it from the deployer"
    DEPLOYER_PK="$("$SCRIPT_DIR/get-deployer-key.sh")"
    cast send "$RECEIVER_ADDR" --value 10ether --private-key "$DEPLOYER_PK" --rpc-url "$rpc_url" >/dev/null
    ok "funded $RECEIVER_ADDR with 10 ETH"
  else
    ok "RECEIVER_ADDR funded ($(cast balance "$RECEIVER_ADDR" --rpc-url "$rpc_url" --ether) ETH)"
  fi
fi

stage "native-asset devnet is up"
ok "step-native-send.js can now run"
