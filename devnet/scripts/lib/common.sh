#!/usr/bin/env bash
# Shared helpers for devnet/scripts/*.sh. Sourced, never executed directly.
#
# A sourcing script is expected to have already set:
#   SCRIPT_DIR    = directory containing the sourcing script
# This file derives from that:
#   SCRIPTS_DIR   = devnet/scripts (this directory)
#   DEVNET_ROOT   = devnet/ (one level up)
#   REPO_ROOT     = the pqc-migration checkout (one level up from devnet/)

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVNET_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEVNET_ROOT/.." && pwd)"

# --- logging -----------------------------------------------------------
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- basic checks --------------------------------------------------------
have_cmd() { command -v "$1" >/dev/null 2>&1; }

require_cmd() {
  have_cmd "$1" || die "'$1' not found on PATH. ${2:-Run devnet/scripts/setup-toolchain.sh first.}"
}

require_file() {
  [ -f "$1" ] || die "expected file not found: $1${2:+ ($2)}"
}

require_dir() {
  [ -d "$1" ] || die "expected directory not found: $1${2:+ ($2)}"
}

# version_ge A B -> success (0) if dotted-numeric version A >= B.
# Non-numeric suffixes (e.g. "-stable") are stripped before comparing.
version_ge() {
  local a b
  a="$(sed -E 's/^v//; s/[^0-9.].*$//' <<<"$1")"
  b="$(sed -E 's/^v//; s/[^0-9.].*$//' <<<"$2")"
  [ "$a" = "$b" ] && return 0
  [ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)" = "$b" ]
}

# Detect the dpkg/GitHub-release architecture suffix for this host.
host_arch() {
  case "$(uname -m)" in
    x86_64) echo amd64 ;;
    aarch64|arm64) echo arm64 ;;
    *) die "unsupported architecture: $(uname -m)" ;;
  esac
}

# Add a directory to PATH for the rest of this script AND persist it in
# ~/.bashrc (idempotent: skips if the exact line is already there), so
# tools installed by setup-toolchain.sh are usable in future shells too.
persist_path() {
  local dir="$1"
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) export PATH="$dir:$PATH" ;;
  esac
  local line="export PATH=\"$dir:\$PATH\""
  local rc="$HOME/.bashrc"
  if [ -f "$rc" ] && grep -qF "$line" "$rc"; then
    return 0
  fi
  printf '\n# added by devnet/scripts/setup-toolchain.sh\n%s\n' "$line" >> "$rc"
  log "added $dir to PATH in $rc (open a new shell, or re-source it, to pick this up outside this script)"
}

# --- getting-started.md version table -----------------------------------
# Best-effort parse of the pipe-table rows in docs/getting-started.md. Falls
# back to the hardcoded default if the row can't be found (heading changed,
# file moved, etc.) so this script degrades gracefully rather than failing.
GETTING_STARTED_MD="$REPO_ROOT/docs/getting-started.md"

table_version() {
  local label="$1" fallback="$2"
  local v=""
  if [ -f "$GETTING_STARTED_MD" ]; then
    v="$(grep -F "| $label" "$GETTING_STARTED_MD" 2>/dev/null | head -n1 \
      | awk -F'|' '{print $3}' \
      | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/\*\*//g; s/`//g')"
  fi
  if [ -z "$v" ]; then
    warn "could not parse '$label' from $GETTING_STARTED_MD; using hardcoded default $fallback"
    v="$fallback"
  fi
  echo "$v"
}

# --- devnet.env resolution ------------------------------------------------
# Bash-only, minimal resolution of DEVNET_DIR (env > devnet.env > example
# default), just enough to know where to mkdir before lib/config.js's full
# precedence chain (env > devnet.env > devnet.env.example > generated files)
# can be consulted for everything else via devnet_cfg() below.
resolve_devnet_dir() {
  if [ -n "${DEVNET_DIR:-}" ]; then
    printf '%s\n' "$DEVNET_DIR"
    return
  fi
  local f v
  for f in "$DEVNET_ROOT/devnet.env" "$DEVNET_ROOT/devnet.env.example"; do
    [ -f "$f" ] || continue
    v="$(grep -E '^DEVNET_DIR=' "$f" | tail -n1 | cut -d= -f2-)"
    if [ -n "$v" ]; then
      v="${v/#\$HOME/$HOME}"
      v="${v/#\~/$HOME}"
      printf '%s\n' "$v"
      return
    fi
  done
  printf '%s\n' "$HOME/devnet-workdir"
}

# Resolve one or more config keys EXACTLY as devnet/lib/config.js would
# (process env > devnet.env > devnet.env.example > DEVNET_DIR's generated
# ports.env/cosmos.env/deploy.env). Requires DEVNET_DIR to already exist.
# Prints `KEY="value"` lines suitable for `eval`.
#
#   eval "$(devnet_cfg CHAIN_HOME CHAIN_ID GETH_RPC)"
devnet_cfg() {
  require_cmd node "devnet/lib/config.js needs node (see setup-toolchain.sh)."
  local out
  if ! out="$(node -e '
    const cfg = require(process.argv[1] + "/lib/config").load();
    for (const k of process.argv.slice(2)) {
      const v = cfg[k] == null ? "" : String(cfg[k]);
      console.log(k + "=" + JSON.stringify(v));
    }
  ' "$DEVNET_ROOT" "$@" 2>&1)"; then
    die "failed to resolve devnet config (${*}):
$out"
  fi
  printf '%s\n' "$out"
}

# Fetch the tag_name of a GitHub repo's latest release via the API (no
# guessed version strings). Honors GITHUB_TOKEN if set, to avoid the
# unauthenticated rate limit.
gh_latest_release_tag() {
  local repo="$1"
  local auth=()
  [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  curl -fsSL "${auth[@]}" "https://api.github.com/repos/$repo/releases/latest" \
    | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{try{console.log(JSON.parse(d).tag_name)}catch(e){process.exit(1)}})'
}
