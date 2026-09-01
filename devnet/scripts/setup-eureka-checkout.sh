#!/usr/bin/env bash
# Prepare a solidity-ibc-eureka checkout for this devnet: clone, pin the
# commit, apply this repo's patches, copy the helper binaries, bun install,
# and build proof-api.
#
# Usage: setup-eureka-checkout.sh [target-dir]
# Target directory precedence: $1 > $SIBE_HOME > ~/solidity-ibc-eureka
#
# Idempotent: skips the clone/checkout/patch/helper-copy steps if already
# done; `bun install` and `just install-proof-api` are safe to re-run as-is.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

TARGET="${1:-${SIBE_HOME:-$HOME/solidity-ibc-eureka}}"
UPSTREAM_URL="https://github.com/srdtrk/solidity-ibc-eureka.git"
PATCH_README="$DEVNET_ROOT/patches/README.md"
PATCH_FILE="$DEVNET_ROOT/patches/solidity-ibc-eureka.patch"

PINNED_COMMIT="$(grep -oE '`[0-9a-f]{40}`' "$PATCH_README" 2>/dev/null | head -n1 | tr -d '`')"
if [ -z "$PINNED_COMMIT" ]; then
  PINNED_COMMIT="604476b11eb2ee5c677f773d2086a352b03bb0a5"
  warn "could not parse the pinned commit from $PATCH_README; using hardcoded default $PINNED_COMMIT"
fi

require_cmd git
require_cmd bun "run devnet/scripts/setup-toolchain.sh first."
require_cmd just "run devnet/scripts/setup-toolchain.sh first."
require_cmd cargo "run devnet/scripts/setup-toolchain.sh first."
require_file "$PATCH_FILE"

log "target checkout: $TARGET"
log "pinned commit: $PINNED_COMMIT"

# --- clone -------------------------------------------------------------
if [ -d "$TARGET/.git" ]; then
  ok "checkout already present at $TARGET"
else
  log "cloning $UPSTREAM_URL"
  git clone "$UPSTREAM_URL" "$TARGET"
fi

cd "$TARGET"

# --- pin the commit ------------------------------------------------------
current_commit="$(git rev-parse HEAD)"
if [ "$current_commit" = "$PINNED_COMMIT" ]; then
  ok "already at pinned commit $PINNED_COMMIT"
else
  if ! git cat-file -e "$PINNED_COMMIT" 2>/dev/null; then
    log "fetching (pinned commit not present locally)"
    git fetch origin
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "checkout at $TARGET has uncommitted changes and is not at the pinned commit; resolve manually (this script never discards local changes)."
  fi
  git checkout --quiet "$PINNED_COMMIT"
  ok "checked out $PINNED_COMMIT"
fi

# --- patch ---------------------------------------------------------------
if git apply --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
  ok "patch already applied"
elif git apply --check "$PATCH_FILE" >/dev/null 2>&1; then
  log "applying $PATCH_FILE"
  git apply "$PATCH_FILE"
  ok "patch applied"
else
  die "patch does not apply cleanly to $TARGET at $current_commit and is not already applied. Run 'git apply --check $PATCH_FILE' from $TARGET for details — the checkout may have local edits, or the patch may be stale for this commit."
fi

# --- helper binaries (devnet/patches/README.md, not part of the patch) ----
declare -A HELPERS=(
  ["bin/cost-estimator.rs"]="packages/sp1-ics07-tendermint-prover/src/bin/cost-estimator.rs"
  ["bin/sp1-account.rs"]="packages/sp1-ics07-tendermint-prover/src/bin/sp1-account.rs"
  ["bin/createclient.go.txt"]="e2e/interchaintestv8/cmd/createclient/main.go"
)
for src in "${!HELPERS[@]}"; do
  dest="${HELPERS[$src]}"
  src_path="$DEVNET_ROOT/patches/$src"
  dest_path="$TARGET/$dest"
  require_file "$src_path" "listed in devnet/patches/README.md"
  if [ -f "$dest_path" ] && cmp -s "$src_path" "$dest_path"; then
    ok "helper already in place: $dest"
  else
    mkdir -p "$(dirname "$dest_path")"
    cp "$src_path" "$dest_path"
    ok "copied $src -> $dest"
  fi
done

# --- bun install (NOT npm — see setup-toolchain.sh) -----------------------
require_dir "$TARGET/ibc-solidity"
log "bun install in ibc-solidity/"
(cd "$TARGET/ibc-solidity" && bun install)
ok "bun install complete"

# --- build proof-api -------------------------------------------------------
# Needs a C++ compiler + libstdc++.so dev symlink at link time (the 08-wasm
# BLS verifier), protoc >= 3.15, and CARGO_TARGET_DIR overridden to a
# directory literally named "target" (sp1-recursion-core's build.rs walks up
# from OUT_DIR looking for an ancestor with that exact name). See
# docs/getting-started.md#building-solidity-ibc-eureka.
CARGO_TARGET_DIR="${SIBE_CARGO_TARGET_DIR:-$HOME/.cache/sibe/target}"
[ "$(basename "$CARGO_TARGET_DIR")" = "target" ] || die "CARGO_TARGET_DIR/SIBE_CARGO_TARGET_DIR must be named literally 'target' (got: $CARGO_TARGET_DIR) — sp1-recursion-core's build.rs requires it."
mkdir -p "$CARGO_TARGET_DIR"

extra_path=""
extra_library_path=""
if ! (echo 'int main(){return 0;}' | g++ -x c++ -lstdc++ -o /dev/null - ) >/dev/null 2>&1; then
  if [ -d "$HOME/.local/gxx11" ]; then
    extra_path="$HOME/.local/gxx11/bin:"
    extra_library_path="$HOME/.local/gxx11/usr/lib/gcc/x86_64-linux-gnu/11"
  else
    die "no working C++ toolchain (g++ + libstdc++.so dev symlink) found, and no fallback at ~/.local/gxx11. Install g++/libstdc++-dev (root), or unpack one into ~/.local/gxx11 per docs/getting-started.md#cgo-libstdc-is-required, then re-run."
  fi
fi

PROTOC_BIN="$HOME/.local/protoc/bin/protoc"
if [ -x "$PROTOC_BIN" ]; then
  extra_path="${extra_path}$HOME/.local/protoc/bin:"
elif have_cmd protoc; then
  PROTOC_BIN="$(command -v protoc)"
else
  die "protoc not found (expected $PROTOC_BIN or on PATH). Run devnet/scripts/setup-toolchain.sh first."
fi

log "building proof-api (CARGO_TARGET_DIR=$CARGO_TARGET_DIR, PROTOC=$PROTOC_BIN)"
env \
  PATH="${extra_path}$PATH" \
  PROTOC="$PROTOC_BIN" \
  ${extra_library_path:+LIBRARY_PATH="$extra_library_path"} \
  CARGO_TARGET_DIR="$CARGO_TARGET_DIR" \
  just install-proof-api

require_cmd proof-api "cargo install should have put it on \$HOME/.cargo/bin, which should be on PATH."
proof-api --help >/dev/null || die "proof-api --help failed after install"
ok "proof-api installed and working: $(command -v proof-api)"
