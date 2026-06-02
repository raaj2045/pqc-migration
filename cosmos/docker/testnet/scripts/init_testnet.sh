#!/usr/bin/env bash
# Initialise an N-validator testnet on the host, then emit a docker-compose.yml
# that starts one container per validator.
#
# What this does, step by step:
#  1. Calls `simd testnet init-files --validator-count N` inside a one-shot
#     builder container to produce testnet-data/node0..node(N-1) with genesis,
#     private validator keys, and persistent_peers already wired up.
#  2. Patches each node's config.toml to enable Prometheus (:26660), set an
#     external_address matching its static IP, and set a stable moniker.
#  3. Emits docker-compose.yml with one service per validator on a custom
#     bridge network (172.28.0.0/24), assigning unique host port offsets so
#     all validators can coexist on one host.
#
# Only N ∈ {4, 7, 16, 32} is supported so the ports stay in a predictable
# range and we can eyeball logs without paginating dozens of services.

set -euo pipefail

SUPPORTED_VALIDATORS=(4 7 16 32)
SUPPORTED_KEY_TYPES=(secp256k1 mldsa44)
CHAIN_ID="testnet"
KEY_TYPE="secp256k1"
COMMIT_TIMEOUT=""  # empty = use simd testnet init-files default (5s)
# Pre-signed sender address files. validator_scaling_v2 generates these once
# via tools/presigner_bin emit-addresses (deterministic). The senders are
# added to genesis FIRST so their account_numbers are 0..7 (secp) and 8..15
# (mldsa44), which is what the pre-signed tx pool bakes into each tx.
SECP_ADDRS_FILE=""
MLDSA_ADDRS_FILE=""
SUBNET="172.28.0.0/24"
# Validator IPs start at .10 to leave room at the low end for a future seed
# or gateway container without renumbering.
IP_BASE="172.28.0."
IP_START=10
BASE_P2P=26656
BASE_RPC=26657
BASE_PROM=26660
BASE_API=1317
BASE_GRPC=9090
# Per-validator offset: 10 ports per validator is enough to cover p2p (26656),
# rpc (26657), prometheus (26660) without collision between adjacent slots.
PORT_STRIDE=10

usage() {
  cat <<EOF
Usage: $0 --validators N [--chain-id ID] [--key-type TYPE]

Required:
  --validators N        Number of validators. Must be one of: ${SUPPORTED_VALIDATORS[*]}

Optional:
  --chain-id ID         Chain ID (default: ${CHAIN_ID})
  --key-type TYPE       Validator signing key type: ${SUPPORTED_KEY_TYPES[*]} (default: ${KEY_TYPE})
  --commit-timeout DUR  Block commit timeout (e.g. 1s, 500ms). Used by cold-sync
                        experiments that want to pack more blocks per minute.
  --secp-addrs-file F   JSONL of secp256k1 pre-signed sender addresses (presigner
                        emit-addresses output). Required.
  --mldsa-addrs-file F  JSONL of mldsa44 pre-signed sender addresses (presigner
                        emit-addresses output). Required.

Environment overrides:
  IMAGE_TAG             Docker image tag to build/use (default: cosmos-testnet:local)
  FORCE_REBUILD=1       Delete testnet-data/ before init even if it exists
EOF
  exit 1
}

N=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --validators) N="$2"; shift 2 ;;
    --chain-id)   CHAIN_ID="$2"; shift 2 ;;
    --key-type)   KEY_TYPE="$2"; shift 2 ;;
    --commit-timeout) COMMIT_TIMEOUT="$2"; shift 2 ;;
    --secp-addrs-file)  SECP_ADDRS_FILE="$2"; shift 2 ;;
    --mldsa-addrs-file) MLDSA_ADDRS_FILE="$2"; shift 2 ;;
    -h|--help)    usage ;;
    *) echo "unknown flag: $1" >&2; usage ;;
  esac
done

if [[ -z "$N" ]]; then usage; fi
if [[ -z "$SECP_ADDRS_FILE" || -z "$MLDSA_ADDRS_FILE" ]]; then
  echo "init_testnet: --secp-addrs-file and --mldsa-addrs-file are required" >&2
  exit 1
fi
if [[ ! -f "$SECP_ADDRS_FILE" ]]; then
  echo "init_testnet: secp addrs file not found: $SECP_ADDRS_FILE" >&2
  exit 1
fi
if [[ ! -f "$MLDSA_ADDRS_FILE" ]]; then
  echo "init_testnet: mldsa addrs file not found: $MLDSA_ADDRS_FILE" >&2
  exit 1
fi

ok=0
for s in "${SUPPORTED_VALIDATORS[@]}"; do
  if [[ "$s" == "$N" ]]; then ok=1; break; fi
done
if [[ "$ok" != "1" ]]; then
  echo "init_testnet: --validators must be one of ${SUPPORTED_VALIDATORS[*]} (got $N)" >&2
  exit 1
fi

ok=0
for s in "${SUPPORTED_KEY_TYPES[@]}"; do
  if [[ "$s" == "$KEY_TYPE" ]]; then ok=1; break; fi
done
if [[ "$ok" != "1" ]]; then
  echo "init_testnet: --key-type must be one of ${SUPPORTED_KEY_TYPES[*]} (got $KEY_TYPE)" >&2
  exit 1
fi

IMAGE_TAG="${IMAGE_TAG:-cosmos-testnet:local}"

# Resolve repo root: this script lives at docker/testnet/scripts/.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TESTNET_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TESTNET_DIR}/../.." && pwd)"

DATA_DIR="${TESTNET_DIR}/testnet-data"
COMPOSE_FILE="${TESTNET_DIR}/docker-compose.yml"

echo "init_testnet: N=$N chain_id=$CHAIN_ID key_type=$KEY_TYPE image=$IMAGE_TAG"
echo "init_testnet: repo root = $REPO_ROOT"
echo "init_testnet: data dir  = $DATA_DIR"

if [[ -d "$DATA_DIR" ]]; then
  if [[ "${FORCE_REBUILD:-0}" == "1" ]]; then
    echo "init_testnet: FORCE_REBUILD=1 — removing existing $DATA_DIR"
    rm -rf "$DATA_DIR"
  else
    echo "init_testnet: $DATA_DIR already exists. Run 'make testnet-down' or set FORCE_REBUILD=1." >&2
    exit 1
  fi
fi

mkdir -p "$DATA_DIR"

# Build the image (idempotent; Docker layer cache does the heavy lifting).
echo "init_testnet: building image $IMAGE_TAG"
docker build \
  -t "$IMAGE_TAG" \
  -f "${TESTNET_DIR}/Dockerfile" \
  "$REPO_ROOT"

# Run init-files inside the just-built image so the simd version used for init
# is exactly the one the validators will run.
echo "init_testnet: running 'simd testnet init-files' for $N validators"
init_args=(
  --validator-count "$N"
  --output-dir /out
  --starting-ip-address "${IP_BASE}${IP_START}"
  --chain-id "$CHAIN_ID"
  --key-type "$KEY_TYPE"
  --keyring-backend test
  --node-dir-prefix node
  --node-daemon-home simd
)
if [[ -n "$COMMIT_TIMEOUT" ]]; then
  init_args+=(--commit-timeout "$COMMIT_TIMEOUT")
fi
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  --entrypoint simd \
  -v "${DATA_DIR}:/out" \
  "$IMAGE_TAG" \
  testnet init-files "${init_args[@]}"

# Per-validator patches to config.toml.
for ((i=0; i<N; i++)); do
  node_home="${DATA_DIR}/node${i}/simd"
  cfg="${node_home}/config/config.toml"
  app="${node_home}/config/app.toml"
  ip="${IP_BASE}$((IP_START + i))"

  if [[ ! -f "$cfg" ]]; then
    echo "init_testnet: missing $cfg — init-files did not produce expected layout" >&2
    exit 1
  fi

  # Prometheus on :26660 for every node. init-files leaves this off by default.
  # external_address must match the static IP so peers can dial this node.
  # moniker is already set by init-files; we normalise to node<i> for clarity.
  sed -i \
    -e 's|^prometheus = false|prometheus = true|' \
    -e 's|^prometheus_listen_addr = ".*"|prometheus_listen_addr = ":26660"|' \
    -e "s|^external_address = \".*\"|external_address = \"${ip}:${BASE_P2P}\"|" \
    -e "s|^moniker = \".*\"|moniker = \"node${i}\"|" \
    "$cfg"

  # app.toml: enable API + gRPC on all interfaces so the host port-forwards work.
  # Also set minimum-gas-prices = "" so the validator-scaling experiment can
  # send zero-fee txs — this makes per-tx cost a function of signature cost
  # only, with no fee-accounting noise.
  if [[ -f "$app" ]]; then
    sed -i \
      -e '/^\[api\]/,/^\[/{s|^enable = false|enable = true|;}' \
      -e '/^\[api\]/,/^\[/{s|^address = "tcp://localhost:1317"|address = "tcp://0.0.0.0:1317"|;}' \
      -e 's|^minimum-gas-prices = ".*"|minimum-gas-prices = "0stake"|' \
      "$app"
  fi
done

# -----------------------------------------------------------------------------
# validator_scaling_v2 dual-arm rebuild.
#
# `simd testnet init-files --key-type mldsa44` creates both the validator
# consensus key (priv_validator_key.json) AND the node<i> delegator account in
# the keyring as mldsa44. v2 needs only the consensus key to vary; user
# accounts must be deterministic so a pre-signed tx pool can be replayed.
#
# Strategy: keep priv_validator_key.json untouched (that is the consensus key,
# which stays ed25519 in this fork regardless of --key-type). Delete every
# user-account keyring + gentx produced by init-files. Rebuild each node<i>
# delegator as secp256k1 inside the keyring (gentx still signs from there).
#
# For loadgen senders, the presigner tool owns the keys deterministically;
# this script only needs to add-genesis-account the addresses it provides.
# Adding senders FIRST gives them stable account_numbers 0..7 (secp) and
# 8..15 (mldsa44) regardless of N — which is what the pre-signed pool bakes
# into each tx.
#
# Also patch block.max_gas so the per-block tx ceiling stays off the
# critical path.
# -----------------------------------------------------------------------------
LOADGEN_SENDERS=8
VALIDATOR_ACCOUNT_BALANCE="1000000000000stake"
LOADGEN_ACCOUNT_BALANCE="1000000000000000stake"
VALIDATOR_SELF_STAKE="1000000000stake"
BLOCK_MAX_GAS="100000000"

echo "init_testnet: rebuilding user accounts (validator consensus stays ${KEY_TYPE})"
echo "init_testnet: pre-funding ${LOADGEN_SENDERS} secp256k1 + ${LOADGEN_SENDERS} mldsa44 senders"
echo "init_testnet: patching block.max_gas=${BLOCK_MAX_GAS}"

# Stage the per-arm address files into DATA_DIR so the docker bash block
# inside the container can read them via /out. These are JSONL files
# emitted by `tools/presigner_bin emit-addresses` on the host.
cp "$SECP_ADDRS_FILE"  "${DATA_DIR}/.secp_addrs.jsonl"
cp "$MLDSA_ADDRS_FILE" "${DATA_DIR}/.mldsa_addrs.jsonl"

# Single docker run to avoid per-command container-startup overhead (~1s each
# vs ~50ms inside one shell). All file writes land in $DATA_DIR (mounted /out)
# and are owned by the invoking user because we pass --user.
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  --entrypoint bash \
  -v "${DATA_DIR}:/out" \
  "$IMAGE_TAG" \
  -c '
set -euo pipefail
N='"$N"'
M='"$LOADGEN_SENDERS"'
CHAIN_ID='"'$CHAIN_ID'"'
VB='"'$VALIDATOR_ACCOUNT_BALANCE'"'
LB='"'$LOADGEN_ACCOUNT_BALANCE'"'
VS='"'$VALIDATOR_SELF_STAKE'"'
MAX_GAS='"'$BLOCK_MAX_GAS'"'

NODE0=/out/node0/simd
SECP_ADDRS=/out/.secp_addrs.jsonl
MLDSA_ADDRS=/out/.mldsa_addrs.jsonl

# 1. Reset accounts / balances / supply / gentxs in node0 genesis; patch
#    block.max_gas. `bank.supply` must be cleared alongside `bank.balances`
#    otherwise `genesis gentx` rejects the genesis with "supply is incorrect"
#    because the module checks supply == sum(balances). We rebuild supply
#    from the fresh balances in step 4b below (after add-genesis-account).
jq --arg mg "${MAX_GAS}" "
  .app_state.auth.accounts = []
  | .app_state.bank.balances = []
  | .app_state.bank.supply = []
  | .app_state.genutil.gen_txs = []
  | .consensus.params.block.max_gas = \$mg
" "${NODE0}/config/genesis.json" > "${NODE0}/config/genesis.json.tmp"
mv "${NODE0}/config/genesis.json.tmp" "${NODE0}/config/genesis.json"

# 2. For each node: wipe the keyring + gentx dir, recreate the node<i>
#    delegator key as secp256k1. priv_validator_key.json is NOT touched.
for ((i=0; i<N; i++)); do
  NH=/out/node${i}/simd
  rm -rf "${NH}/keyring-test"
  rm -rf "${NH}/config/gentx"
  mkdir -p "${NH}/config/gentx"
  simd keys add "node${i}" --algo secp256k1 \
    --keyring-backend test --home "${NH}" >/dev/null
done

# 3. (intentionally empty) — loadgen sender keys live entirely in the
#    presigner tool. We only need their addresses to fund them, which the
#    presigner emit-addresses output gave us in $SECP_ADDRS / $MLDSA_ADDRS.

# 4. add-genesis-account in the order that fixes account_numbers:
#    a. 8 secp256k1 senders   → account_numbers 0..7
#    b. 8 mldsa44 senders     → account_numbers 8..15
#    c. N validator delegators → account_numbers 16..(15+N)
#    The pre-signed pool was built assuming 0..7 / 8..15 — keep this order.

# 4a. secp loadgen senders.
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  addr=$(echo "$line" | jq -r .address)
  simd genesis add-genesis-account "${addr}" "${LB}" --home "${NODE0}" >/dev/null
done < "${SECP_ADDRS}"

# 4b. mldsa44 loadgen senders.
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  addr=$(echo "$line" | jq -r .address)
  simd genesis add-genesis-account "${addr}" "${LB}" --home "${NODE0}" >/dev/null
done < "${MLDSA_ADDRS}"

# 4c. validator delegators.
for ((i=0; i<N; i++)); do
  NH=/out/node${i}/simd
  addr=$(simd keys show "node${i}" -a --keyring-backend test --home "${NH}")
  simd genesis add-genesis-account "${addr}" "${VB}" --home "${NODE0}" >/dev/null
done

# 4d. Recompute bank.supply from the freshly-added balances. `add-genesis-account`
#     only updates balances, not supply; gentx validates supply == sum(balances)
#     and fails hard otherwise. We sum per-denom across all balance entries.
jq "
  .app_state.bank.supply = (
    [.app_state.bank.balances[].coins[]]
    | group_by(.denom)
    | map({denom: .[0].denom,
           amount: (map(.amount | tonumber) | add | tostring)})
  )
" "${NODE0}/config/genesis.json" > "${NODE0}/config/genesis.json.tmp"
mv "${NODE0}/config/genesis.json.tmp" "${NODE0}/config/genesis.json"

# 5. Distribute the funded genesis.json to every node before running gentx —
#    gentx checks the local genesis to confirm the delegator has enough to
#    self-stake.
for ((i=1; i<N; i++)); do
  cp "${NODE0}/config/genesis.json" "/out/node${i}/simd/config/genesis.json"
done

# 6. Generate gentxs per node. Each gentx is signed by the node<i> secp256k1
#    delegator and self-delegates to the validator consensus pubkey already
#    sitting in priv_validator_key.json (unchanged, scheme-varying).
for ((i=0; i<N; i++)); do
  NH=/out/node${i}/simd
  simd genesis gentx "node${i}" "${VS}" \
    --chain-id "${CHAIN_ID}" \
    --keyring-backend test \
    --commission-rate 0.1 \
    --commission-max-rate 0.2 \
    --commission-max-change-rate 0.01 \
    --min-self-delegation 1 \
    --home "${NH}" 2>&1 | tail -20
  if [[ "$i" != "0" ]]; then
    cp "${NH}/config/gentx/"*.json "${NODE0}/config/gentx/"
  fi
done

# 7. Collect all gentxs into node0 genesis.
simd genesis collect-gentxs --home "${NODE0}" 2>&1 | tail -20

# 7b. Set the lockandmint bridge authority to the node0 address. node0 is the key
#     the relayer signs MsgMint with, so it must be the gov-settable bridge
#     authority for mints to pass the authority check. Gov can rotate this later
#     via MsgUpdateParams without a chain upgrade.
NODE0_ADDR=$(simd keys show node0 -a --keyring-backend test --home "${NODE0}")
jq --arg ba "${NODE0_ADDR}" "
  .app_state.lockandmint.params = {bridge_authority: \$ba}
" "${NODE0}/config/genesis.json" > "${NODE0}/config/genesis.json.tmp"
mv "${NODE0}/config/genesis.json.tmp" "${NODE0}/config/genesis.json"

# 8. Validate assembled genesis.
simd genesis validate --home "${NODE0}" >/dev/null

# 9. Distribute the final, collected genesis to every node.
for ((i=1; i<N; i++)); do
  cp "${NODE0}/config/genesis.json" "/out/node${i}/simd/config/genesis.json"
done

# 10. Emit a unified .loadgen_senders.json that records the (scheme, idx,
#     address, account_num) for every funded sender. The presigner already
#     has the corresponding private keys; the loadgen does not need them.
loadgen_file=/out/.loadgen_senders.json
{
  echo "{"
  echo "  \"secp256k1\": ["
  i=0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    addr=$(echo "$line" | jq -r .address)
    name=$(echo "$line" | jq -r .name)
    sep=","; if [[ "$i" == "$((M-1))" ]]; then sep=""; fi
    printf "    {\"name\":\"%s\",\"index\":%d,\"address\":\"%s\",\"account_num\":%d}%s\n" \
      "${name}" "${i}" "${addr}" "${i}" "${sep}"
    i=$((i+1))
  done < "${SECP_ADDRS}"
  echo "  ],"
  echo "  \"mldsa44\": ["
  i=0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    addr=$(echo "$line" | jq -r .address)
    name=$(echo "$line" | jq -r .name)
    sep=","; if [[ "$i" == "$((M-1))" ]]; then sep=""; fi
    acct=$((i + M))
    printf "    {\"name\":\"%s\",\"index\":%d,\"address\":\"%s\",\"account_num\":%d}%s\n" \
      "${name}" "${i}" "${addr}" "${acct}" "${sep}"
    i=$((i+1))
  done < "${MLDSA_ADDRS}"
  echo "  ]"
  echo "}"
} > "${loadgen_file}"

# 11. Dump validator-account pubkey types for the verification harness.
#     keyring_pubkey_type should read /cosmos.crypto.secp256k1.PubKey for
#     all node<i> delegators (validator consensus stays ed25519, separate).
accts_file=/out/.validator_accounts.json
echo "[" > "${accts_file}"
for ((i=0; i<N; i++)); do
  NH=/out/node${i}/simd
  addr=$(simd keys show "node${i}" -a --keyring-backend test --home "${NH}")
  pkt=$(simd keys show "node${i}" --keyring-backend test --home "${NH}" \
    --output json | jq -r ".pubkey | fromjson | .[\"@type\"]")
  sep=","; if [[ "$i" == "$((N-1))" ]]; then sep=""; fi
  printf "  {\"name\":\"node%d\",\"address\":\"%s\",\"keyring_pubkey_type\":\"%s\"}%s\n" \
    "${i}" "${addr}" "${pkt}" "${sep}" >> "${accts_file}"
done
echo "]" >> "${accts_file}"
'

echo "init_testnet: user accounts rebuilt; final genesis distributed"
echo "init_testnet: loadgen senders      → ${DATA_DIR}/.loadgen_senders.json"
echo "init_testnet: validator accounts   → ${DATA_DIR}/.validator_accounts.json"

# CPU/memory caps per validator — computed OUTSIDE the compose redirection
# below so diagnostic echos don't leak into the generated YAML.
# Reserving 4 cores for relayer/loadgen/OS leaves HOST_CPUS for the N
# containers, so per-container CPU = min(1.0, HOST_CPUS / (N+4)). This keeps
# N=32 from drowning a 12-core laptop in scheduler overhead while leaving
# small-N runs uncapped in practice.
HOST_CPUS="$(nproc 2>/dev/null || echo 4)"
CPU_LIMIT="$(awk -v c="$HOST_CPUS" -v n="$N" 'BEGIN{v=c/(n+4); if (v>1.0) v=1.0; printf "%.2f", v}')"
MEM_LIMIT="1536M"
echo "init_testnet: per-validator resource caps: cpus=${CPU_LIMIT} mem=${MEM_LIMIT} (host cpus=${HOST_CPUS}, N=${N})"

# Emit docker-compose.yml. We build it by string concatenation rather than
# Helm/Jinja because the shape is small and predictable, and this keeps the
# script zero-dependency.
echo "init_testnet: writing $COMPOSE_FILE"

{
  cat <<EOF
# Generated by docker/testnet/scripts/init_testnet.sh — do not edit by hand.
# Regenerate with: make -C docker/testnet up N=$N
#
# Each validator runs the image built from docker/testnet/Dockerfile and
# mounts its own node home directory from testnet-data/node<i>/simd.
services:
EOF

  for ((i=0; i<N; i++)); do
    ip="${IP_BASE}$((IP_START + i))"
    p2p_host=$((BASE_P2P + i * PORT_STRIDE))
    rpc_host=$((BASE_RPC + i * PORT_STRIDE))
    prom_host=$((BASE_PROM + i * PORT_STRIDE))
    api_host=$((BASE_API + i))
    grpc_host=$((BASE_GRPC + i))
    cat <<EOF
  node${i}:
    image: ${IMAGE_TAG}
    container_name: cosmos-testnet-node${i}
    hostname: node${i}
    user: "$(id -u):$(id -g)"
    restart: unless-stopped
    volumes:
      - ./testnet-data/node${i}/simd:/cosmos
    ports:
      - "${p2p_host}:26656"
      - "${rpc_host}:26657"
      - "${prom_host}:26660"
      - "${api_host}:1317"
      - "${grpc_host}:9090"
    networks:
      testnet:
        ipv4_address: ${ip}
    deploy:
      resources:
        limits:
          cpus: "${CPU_LIMIT}"
          memory: ${MEM_LIMIT}
    command: ["start", "--log_level", "info"]

EOF
  done

  cat <<EOF
networks:
  testnet:
    driver: bridge
    ipam:
      config:
        - subnet: ${SUBNET}
EOF
} > "$COMPOSE_FILE"

# Stash a small metadata file so downstream tooling (experiment runner,
# aggregator) doesn't have to guess which scheme this testnet was init'd with.
cat > "${TESTNET_DIR}/testnet-data/.meta.json" <<EOF
{
  "validators": ${N},
  "chain_id": "${CHAIN_ID}",
  "key_type": "${KEY_TYPE}",
  "commit_timeout": "${COMMIT_TIMEOUT}",
  "image_tag": "${IMAGE_TAG}",
  "base_rpc_port": ${BASE_RPC},
  "base_p2p_port": ${BASE_P2P},
  "port_stride": ${PORT_STRIDE},
  "ip_base": "${IP_BASE}",
  "ip_start": ${IP_START},
  "loadgen_senders_per_arm": ${LOADGEN_SENDERS},
  "loadgen_arms": ["secp256k1", "mldsa44"],
  "secp_account_num_base": 0,
  "mldsa_account_num_base": ${LOADGEN_SENDERS},
  "block_max_gas": ${BLOCK_MAX_GAS},
  "validator_delegator_algo": "secp256k1",
  "validator_self_stake": "${VALIDATOR_SELF_STAKE}",
  "secp_addrs_file_used": "$(basename "$SECP_ADDRS_FILE")",
  "mldsa_addrs_file_used": "$(basename "$MLDSA_ADDRS_FILE")"
}
EOF

echo "init_testnet: done. Next: docker compose -f $COMPOSE_FILE up -d"
