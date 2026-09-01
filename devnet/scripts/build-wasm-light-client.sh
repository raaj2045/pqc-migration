#!/usr/bin/env bash
# Build cw-ics08-wasm-eth (the Cosmos-side Ethereum light client) via the
# CosmWasm optimizer, and copy the result to a location this repo controls.
#
# Deliberately does NOT call `just build-cw-ics08-wasm-eth`: that recipe's
# `cd ibc-solidity/programs/cw-ics08-wasm-eth && ...` persists for the rest
# of the recipe (just runs a whole recipe as one shell script), so its own
# `cp .../wasm` and `gzip` lines land one level too deep unless invoked from
# exactly the right directory. Running the same docker steps here, with
# explicit paths throughout, avoids that trap entirely.
#
# Also passes buildx --load explicitly rather than relying on a `docker`
# driver default builder: --load works with either the `docker` or
# `docker-container` buildx driver, so the image lands in `docker images`
# regardless of what builder this host happens to default to.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

SIBE_HOME_DIR="${SIBE_HOME:-$HOME/solidity-ibc-eureka}"
IBC_SOLIDITY="$SIBE_HOME_DIR/ibc-solidity"
BUILD_DIR="$IBC_SOLIDITY/programs/cw-ics08-wasm-eth"
OUT_DIR="$DEVNET_ROOT/artifacts"
OUT_FILE="$OUT_DIR/cw_ics08_wasm_eth.wasm.gz"

require_cmd docker
docker buildx version >/dev/null 2>&1 || die "docker buildx plugin not found; run devnet/scripts/setup-toolchain.sh first."
require_dir "$BUILD_DIR" "run devnet/scripts/setup-eureka-checkout.sh first."
require_file "$BUILD_DIR/Dockerfile"

log "removing any stale cosmwasm-builder:latest image"
docker image rm -f cosmwasm-builder:latest >/dev/null 2>&1 || true

log "building the optimizer image (docker buildx build --load)"
( cd "$BUILD_DIR" && docker buildx build --load --platform linux/amd64 -t cosmwasm-builder:latest . )

log "running the optimizer"
# The image's build script does `cd /code/ibc-solidity/programs/cw-ics08-wasm-eth`
# internally and writes output to /code/artifacts, so /code must be the
# solidity-ibc-eureka repo ROOT, not the cw-ics08-wasm-eth subdirectory.
#
# The container runs as root (no USER in the Dockerfile — /usr/local/cargo
# needs root to write its registry cache), so artifacts/ and target/ come
# back root-owned on the host. chown them back via a throwaway container
# (root inside can always chown, regardless of what the host user could do
# directly) both before and after, so a stale root-owned artifacts/ from an
# interrupted previous run never blocks `rm -rf` here or on a later re-run.
chown_output() {
  docker run --rm --entrypoint chown \
    -v "$SIBE_HOME_DIR":/code cosmwasm-builder:latest \
    -R "$(id -u):$(id -g)" /code/artifacts /code/target 2>/dev/null || true
}
chown_output
rm -rf "$SIBE_HOME_DIR/artifacts"
docker run --rm --platform=linux/amd64 -t \
    -v "$SIBE_HOME_DIR":/code \
    cosmwasm-builder:latest
chown_output

WASM="$SIBE_HOME_DIR/artifacts/cw_ics08_wasm_eth.wasm"
require_file "$WASM" "expected the optimizer to write this; check the docker run output above"

# Also drop the uncompressed+gzipped copy where solidity-ibc-eureka's own
# e2e tooling expects it (e2e/interchaintestv8/wasm), so anything in that
# checkout that reads it (e.g. its Go e2e suite) keeps working.
EUREKA_WASM_DIR="$SIBE_HOME_DIR/e2e/interchaintestv8/wasm"
if [ -d "$EUREKA_WASM_DIR" ]; then
  cp -f "$WASM" "$EUREKA_WASM_DIR/cw_ics08_wasm_eth.wasm"
  gzip -n -f "$EUREKA_WASM_DIR/cw_ics08_wasm_eth.wasm"
  ok "also updated $EUREKA_WASM_DIR/cw_ics08_wasm_eth.wasm.gz"
fi

mkdir -p "$OUT_DIR"
gzip -n -c "$WASM" > "$OUT_FILE.tmp"
mv "$OUT_FILE.tmp" "$OUT_FILE"

ok "built $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"
