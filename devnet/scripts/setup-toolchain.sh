#!/usr/bin/env bash
# One-time host setup for building the chain and running the devnet.
#
# Idempotent: each tool is version-checked first and only installed if
# missing or too old. Safe to re-run after a partial failure — it picks up
# wherever it left off.
#
# Version pins are read from docs/getting-started.md's requirements table
# where possible (table_version(), in lib/common.sh); anything not in that
# table (bun, just, the JRE) is hardcoded here from the same doc's prose.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

GO_VERSION="$(table_version 'Go' '1.26.5')"
NODE_MAJOR="$(table_version 'Node.js' '20')"
PROTOC_VERSION="$(table_version 'protoc' '36.0')"
KURTOSIS_VERSION="$(table_version 'Kurtosis CLI' '1.15.2')"
FOUNDRY_VERSION="$(table_version 'Foundry' '1.0.0-stable' | sed -E 's/-stable$//')"
# Not in the pipe table (mentioned in prose, or not pinned at all):
JRE_MAJOR="21"
ARCH="$(host_arch)"

log "targets: Go $GO_VERSION, Node $NODE_MAJOR.x, protoc $PROTOC_VERSION, Kurtosis $KURTOSIS_VERSION, Foundry $FOUNDRY_VERSION, JRE $JRE_MAJOR ($ARCH)"

# --- Go --------------------------------------------------------------------
if have_cmd go && version_ge "$(go version | awk '{print $3}' | sed 's/^go//')" "$GO_VERSION"; then
  ok "go $(go version | awk '{print $3}') already satisfies >= $GO_VERSION"
else
  log "installing Go $GO_VERSION"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/go.tar.gz" "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz"
  rm -rf "$HOME/.local/go-${GO_VERSION}"
  mkdir -p "$HOME/.local/go-${GO_VERSION}"
  tar -C "$HOME/.local/go-${GO_VERSION}" --strip-components=1 -xzf "$tmp/go.tar.gz"
  rm -rf "$tmp"
  persist_path "$HOME/.local/go-${GO_VERSION}/bin"
  ok "installed go $(go version)"
fi

# --- Node.js -----------------------------------------------------------
if have_cmd node && version_ge "$(node --version | tr -d v)" "${NODE_MAJOR}.0.0"; then
  ok "node $(node --version) already satisfies >= ${NODE_MAJOR}.x"
else
  log "installing Node.js ${NODE_MAJOR}.x"
  # Pick the newest LTS release in the required major line from the
  # official index, rather than guessing a patch version.
  node_full="$(curl -fsSL https://nodejs.org/dist/index.json \
    | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{
        const want=process.argv[1];
        const list=JSON.parse(d).filter(r=>r.version.startsWith("v"+want+".") && r.lts);
        if(!list.length) process.exit(1);
        console.log(list[0].version);
      })' "$NODE_MAJOR" 2>/dev/null || true)"
  [ -n "$node_full" ] || die "could not resolve a Node ${NODE_MAJOR}.x LTS release from nodejs.org/dist/index.json"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/node.tar.xz" "https://nodejs.org/dist/${node_full}/node-${node_full}-linux-${ARCH}.tar.xz"
  rm -rf "$HOME/.local/node-${node_full}"
  mkdir -p "$HOME/.local/node-${node_full}"
  tar -C "$HOME/.local/node-${node_full}" --strip-components=1 -xJf "$tmp/node.tar.xz"
  rm -rf "$tmp"
  persist_path "$HOME/.local/node-${node_full}/bin"
  ok "installed node $(node --version)"
fi

# --- protoc --------------------------------------------------------------
PROTOC_HOME="$HOME/.local/protoc"
if [ -x "$PROTOC_HOME/bin/protoc" ] && version_ge "$("$PROTOC_HOME/bin/protoc" --version | awk '{print $2}')" "$PROTOC_VERSION"; then
  ok "protoc $("$PROTOC_HOME/bin/protoc" --version | awk '{print $2}') already at $PROTOC_HOME"
elif have_cmd protoc && version_ge "$(protoc --version | awk '{print $2}')" "$PROTOC_VERSION"; then
  ok "system protoc $(protoc --version | awk '{print $2}') already satisfies >= $PROTOC_VERSION"
else
  log "installing protoc $PROTOC_VERSION to $PROTOC_HOME"
  proto_arch="x86_64"; [ "$ARCH" = "arm64" ] && proto_arch="aarch_64"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/protoc.zip" \
    "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VERSION}/protoc-${PROTOC_VERSION}-linux-${proto_arch}.zip"
  rm -rf "$PROTOC_HOME"
  mkdir -p "$PROTOC_HOME"
  (cd "$PROTOC_HOME" && unzip -q "$tmp/protoc.zip")
  chmod +x "$PROTOC_HOME/bin/protoc"
  rm -rf "$tmp"
  persist_path "$PROTOC_HOME/bin"
  ok "installed protoc $("$PROTOC_HOME/bin/protoc" --version)"
fi

# --- Kurtosis CLI ----------------------------------------------------------
# No GitHub releases; apt.fury.io only (see docs/getting-started.md#kurtosis-version-pins).
if have_cmd kurtosis && [ "$(kurtosis version 2>&1 | awk '/CLI Version:/{print $3}')" = "$KURTOSIS_VERSION" ]; then
  ok "kurtosis $KURTOSIS_VERSION already installed"
else
  log "installing kurtosis $KURTOSIS_VERSION from apt.fury.io (no root required)"
  require_cmd dpkg-deb
  require_cmd curl
  pkgs="$(curl -fsSL https://apt.fury.io/kurtosis-tech/Packages)"
  fname="$(echo "$pkgs" | awk -v arch="$ARCH" -v ver="$KURTOSIS_VERSION" '
    BEGIN{RS=""; FS="\n"}
    {
      pkg=""; a=""; v=""; f="";
      for (i=1;i<=NF;i++) {
        if ($i ~ /^Package: /)      pkg=substr($i,10)
        if ($i ~ /^Architecture: /) a=substr($i,15)
        if ($i ~ /^Version: /)      v=substr($i,10)
        if ($i ~ /^Filename: /)     f=substr($i,11)
      }
      if (pkg=="kurtosis-cli" && a==arch && v==ver) print f
    }')"
  [ -n "$fname" ] || die "kurtosis-cli $KURTOSIS_VERSION ($ARCH) not found in apt.fury.io/kurtosis-tech/Packages"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/kurtosis.deb" "https://apt.fury.io/kurtosis-tech/$fname"
  rm -rf "$HOME/.local/kurtosis"
  mkdir -p "$HOME/.local/kurtosis/bin"
  dpkg-deb -x "$tmp/kurtosis.deb" "$HOME/.local/kurtosis/extract"
  bin="$(find "$HOME/.local/kurtosis/extract" -type f -name kurtosis | head -n1)"
  [ -n "$bin" ] || die "kurtosis binary not found inside $fname"
  cp "$bin" "$HOME/.local/kurtosis/bin/kurtosis"
  chmod +x "$HOME/.local/kurtosis/bin/kurtosis"
  rm -rf "$tmp"
  persist_path "$HOME/.local/kurtosis/bin"
  ok "installed kurtosis $(kurtosis version 2>&1 | awk '/CLI Version:/{print $3}')"
  if have_cmd kurtosis; then
    log "restarting kurtosis engine to match the CLI"
    kurtosis engine restart || warn "kurtosis engine restart failed; run it manually before using kurtosis"
  fi
fi

# --- Foundry (forge, cast) -------------------------------------------------
if have_cmd forge && version_ge "$(forge --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" "$FOUNDRY_VERSION"; then
  ok "forge $(forge --version | head -n1) already satisfies >= $FOUNDRY_VERSION"
else
  log "installing Foundry $FOUNDRY_VERSION"
  if ! have_cmd foundryup; then
    curl -fsSL https://foundry.paradigm.xyz | bash
    persist_path "$HOME/.foundry/bin"
  fi
  "$HOME/.foundry/bin/foundryup" --install "$FOUNDRY_VERSION"
  ok "installed $(forge --version | head -n1)"
fi

# --- Rust + SP1 toolchain (sp1up, cargo-prove) ------------------------------
if ! have_cmd rustc; then
  log "installing Rust (rustup)"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
  persist_path "$HOME/.cargo/bin"
else
  ok "rustc $(rustc --version | awk '{print $2}') already installed"
fi

if have_cmd sp1up && have_cmd cargo-prove; then
  ok "sp1up / cargo-prove already installed"
else
  log "installing SP1 toolchain (sp1up)"
  curl -fsSL https://sp1.succinct.xyz | bash
  persist_path "$HOME/.sp1/bin"
  "$HOME/.sp1/bin/sp1up"
  ok "installed cargo-prove: $(cargo prove --version 2>&1 | head -n1)"
fi

# --- bun (NOT npm) -----------------------------------------------------
# solidity-ibc-eureka's ibc-solidity/ pins github: dependencies (sp1-contracts)
# that have no package.json. npm's installer hard-requires one for every
# dependency type and there is no npm-native workaround; bun's git fetcher
# does not share that requirement. See ibc-solidity/bun.lock and its README.
if have_cmd bun || [ -x "$HOME/.bun/bin/bun" ]; then
  ok "bun $("$HOME/.bun/bin/bun" --version 2>/dev/null || bun --version) already installed"
else
  log "installing bun"
  curl -fsSL https://bun.sh/install | bash
  persist_path "$HOME/.bun/bin"
  ok "installed bun $("$HOME/.bun/bin/bun" --version)"
fi

# --- Docker + buildx plugin --------------------------------------------
if have_cmd docker && docker info >/dev/null 2>&1; then
  ok "docker $(docker --version | awk '{print $3}' | tr -d ,) reachable"
else
  die "docker is not installed or the daemon is not reachable. Docker engine install needs root and is not automated here — install it (e.g. https://get.docker.com) and ensure this user can run 'docker info', then re-run this script."
fi

if docker buildx version >/dev/null 2>&1; then
  ok "docker buildx $(docker buildx version | awk '{print $2}') already installed"
else
  log "installing docker buildx plugin (resolving latest release via GitHub API)"
  tag="$(gh_latest_release_tag docker/buildx)"
  [ -n "$tag" ] || die "could not resolve the latest docker/buildx release tag from the GitHub API"
  mkdir -p "$HOME/.docker/cli-plugins"
  curl -fsSL -o "$HOME/.docker/cli-plugins/docker-buildx" \
    "https://github.com/docker/buildx/releases/download/${tag}/buildx-${tag}.linux-${ARCH}"
  chmod +x "$HOME/.docker/cli-plugins/docker-buildx"
  ok "installed docker buildx $tag"
fi

# --- JRE for TLC/Apalache -----------------------------------------------
# TLC (tla2tools.jar) and Apalache are fetched separately, per
# docs/testing.md, when actually reproducing the formal verification. Only
# the JRE they run on is provisioned here.
java_major() { java -version 2>&1 | head -n1 | grep -oE '"[0-9]+' | tr -d '"'; }
if have_cmd java && [ "$(java_major)" -ge "$JRE_MAJOR" ] 2>/dev/null; then
  ok "java $(java -version 2>&1 | head -n1) already satisfies >= $JRE_MAJOR"
else
  log "installing a JRE $JRE_MAJOR"
  if command -v apt-get >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y "openjdk-${JRE_MAJOR}-jre-headless"
  else
    log "no passwordless sudo; downloading a portable JRE from Adoptium"
    jdk_arch="x64"; [ "$ARCH" = "arm64" ] && jdk_arch="aarch64"
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/jre.tar.gz" \
      "https://api.adoptium.net/v3/binary/latest/${JRE_MAJOR}/ga/linux/${jdk_arch}/jre/hotspot/normal/eclipse"
    rm -rf "$HOME/.local/jre-${JRE_MAJOR}"
    mkdir -p "$HOME/.local/jre-${JRE_MAJOR}"
    tar -C "$HOME/.local/jre-${JRE_MAJOR}" --strip-components=1 -xzf "$tmp/jre.tar.gz"
    rm -rf "$tmp"
    persist_path "$HOME/.local/jre-${JRE_MAJOR}/bin"
  fi
  ok "installed $(java -version 2>&1 | head -n1)"
fi

# --- just ------------------------------------------------------------------
if have_cmd just; then
  ok "just $(just --version | awk '{print $2}') already installed"
else
  log "installing just"
  mkdir -p "$HOME/.local/bin"
  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
    | bash -s -- --to "$HOME/.local/bin"
  persist_path "$HOME/.local/bin"
  ok "installed just $(just --version)"
fi

ok "toolchain setup complete"
