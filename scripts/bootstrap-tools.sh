#!/usr/bin/env bash
# Nightjar isolated toolchain bootstrap.
#
# Installs every build/runtime dependency into .tools/ inside the repository.
# Nothing is written to /usr, /opt, ~/.local or any system package manager.
#
# Usage:
#   ./scripts/bootstrap-tools.sh
#   ./scripts/bootstrap-tools.sh --with-pgvector
#
# The script is idempotent: existing, verified artifacts are skipped.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/.tools"
BIN="$TOOLS/bin"
SRC="$TOOLS/src"
NODE_DIR="$TOOLS/node"
PG_DIR="$TOOLS/pg"

UV_VERSION="0.13.7"
NODE_VERSION="24.22.2"
TEMPORAL_VERSION="1.10.0"
OPA_VERSION="1.22.2"
PG_VERSION="18.6.1"
PGVECTOR_VERSION="0.8.7"

WITH_PGVECTOR=0
for arg in "$@"; do
  case "$arg" in
    --with-pgvector) WITH_PGVECTOR=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$BIN" "$SRC"

log()  { printf '\033[1;36m[bootstrap]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[bootstrap]\033[0m %s\n' "$*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || fail "required host tool missing: $1"
}

need curl
need tar
need sha256sum
need unzip

fetch() {
  local url="$1" out="$2"
  if [[ -s "$out" ]]; then
    log "cached $(basename "$out")"
    return 0
  fi
  log "download $(basename "$out")"
  curl -fL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$out.part" "$url"
  mv "$out.part" "$out"
}

verify_sha() {
  local file="$1" expected_file="$2" pattern="$3"
  local expected
  expected="$(grep -E "$pattern" "$expected_file" | awk '{print $1}' | head -1)"
  [[ -n "$expected" ]] || fail "no checksum found for $(basename "$file")"
  local actual
  actual="$(sha256sum "$file" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || fail "checksum mismatch for $(basename "$file")"
  log "verified $(basename "$file")"
}

# ---------------------------------------------------------------- uv ----------
if [[ ! -x "$BIN/uv" ]]; then
  UV_TGZ="uv-x86_64-unknown-linux-gnu.tar.gz"
  UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_TGZ}"
  fetch "$UV_URL" "$SRC/$UV_TGZ"
  fetch "$UV_URL.sha256" "$SRC/$UV_TGZ.sha256"
  verify_sha "$SRC/$UV_TGZ" "$SRC/$UV_TGZ.sha256" "$UV_TGZ"
  tar -xzf "$SRC/$UV_TGZ" -C "$SRC"
  install -m 0755 "$SRC/uv-x86_64-unknown-linux-gnu/uv" "$BIN/uv"
fi
log "uv $("$BIN/uv" --version | awk '{print $2}')"

# --------------------------------------------------------------- node ---------
if [[ ! -x "$NODE_DIR/bin/node" ]]; then
  NODE_NAME="node-v${NODE_VERSION}-linux-x64"
  NODE_TGZ="${NODE_NAME}.tar.xz"
  NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_TGZ}"
  fetch "$NODE_URL" "$SRC/$NODE_TGZ"
  fetch "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" "$SRC/node-SHASUMS256.txt"
  verify_sha "$SRC/$NODE_TGZ" "$SRC/node-SHASUMS256.txt" "$NODE_TGZ"
  rm -rf "$NODE_DIR"
  mkdir -p "$NODE_DIR"
  tar -xJf "$SRC/$NODE_TGZ" -C "$SRC"
  cp -a "$SRC/$NODE_NAME/." "$NODE_DIR/"
fi
log "node $("$NODE_DIR/bin/node" --version)"

# ------------------------------------------------------------ temporal --------
if [[ ! -x "$BIN/temporal" ]]; then
  TMP_NAME="temporal_cli_${TEMPORAL_VERSION}_linux_amd64.tar.gz"
  fetch "https://github.com/temporalio/cli/releases/download/v${TEMPORAL_VERSION}/${TMP_NAME}" "$SRC/$TMP_NAME"
  fetch "https://github.com/temporalio/cli/releases/download/v${TEMPORAL_VERSION}/checksums.txt" "$SRC/temporal-checksums.txt"
  verify_sha "$SRC/$TMP_NAME" "$SRC/temporal-checksums.txt" "$TMP_NAME"
  tar -xzf "$SRC/$TMP_NAME" -C "$SRC"
  install -m 0755 "$SRC/temporal" "$BIN/temporal"
fi
log "temporal $("$BIN/temporal" --version | head -1 | awk '{print $3}')"

# ----------------------------------------------------------------- opa --------
if [[ ! -x "$BIN/opa" ]]; then
  OPA_BIN="opa_linux_amd64_static"
  fetch "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/${OPA_BIN}" "$SRC/$OPA_BIN"
  fetch "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/${OPA_BIN}.sha256" "$SRC/$OPA_BIN.sha256"
  verify_sha "$SRC/$OPA_BIN" "$SRC/$OPA_BIN.sha256" "$OPA_BIN"
  install -m 0755 "$SRC/$OPA_BIN" "$BIN/opa"
fi
log "opa $("$BIN/opa" version | awk '{print $2}')"

# ----------------------------------------------------------- postgres ---------
if [[ ! -x "$PG_DIR/bin/postgres" ]]; then
  PG_NAME="postgresql-${PG_VERSION}-x86_64-unknown-linux-gnu"
  PG_TGZ="${PG_NAME}.tar.gz"
  fetch "https://github.com/theseus-rs/postgresql-binaries/releases/download/${PG_VERSION}/${PG_TGZ}" "$SRC/$PG_TGZ"
  fetch "https://github.com/theseus-rs/postgresql-binaries/releases/download/${PG_VERSION}/${PG_TGZ}.sha256" "$SRC/${PG_TGZ}.sha256"
  verify_sha "$SRC/$PG_TGZ" "$SRC/${PG_TGZ}.sha256" "$PG_TGZ"
  rm -rf "$PG_DIR"
  mkdir -p "$PG_DIR"
  tar -xzf "$SRC/$PG_TGZ" -C "$SRC"
  cp -a "$SRC/$PG_NAME/." "$PG_DIR/"
fi
log "postgres $("$PG_DIR/bin/postgres" --version | awk '{print $3}')"

# ------------------------------------------------------------ pgvector --------
if [[ "$WITH_PGVECTOR" == "1" ]]; then
  if [[ ! -f "$PG_DIR/share/postgresql/extension/vector.control" ]]; then
    command -v git >/dev/null 2>&1 || fail "git required to build pgvector"
    NEED_PG_CONFIG="$PG_DIR/bin/pg_config"
    [[ -x "$NEED_PG_CONFIG" ]] || fail "portable pg_config not found; cannot build pgvector"
    mkdir -p "$TOOLS/src"
    if [[ ! -d "$TOOLS/src/pgvector" ]]; then
      git clone --depth 1 --branch "v${PGVECTOR_VERSION}" https://github.com/pgvector/pgvector.git "$TOOLS/src/pgvector"
    fi
    # PGXS cannot survive the spaces in this repository path, so compile the
    # extension directly against the portable server headers. This is the same
    # source list and flags the upstream Makefile uses.
    PGV="$TOOLS/src/pgvector"
    gcc -shared -fPIC -O2 -march=native -ftree-vectorize -fassociative-math \
      -fno-signed-zeros -fno-trapping-math -ffp-contract=fast -D_GNU_SOURCE \
      -I "$("$NEED_PG_CONFIG" --includedir)" \
      -I "$("$NEED_PG_CONFIG" --includedir-server)" \
      "$PGV"/src/*.c -o "$("$NEED_PG_CONFIG" --pkglibdir)/vector.so" -lm
    cp "$PGV/vector.control" "$("$NEED_PG_CONFIG" --sharedir)/extension/"
    cp "$PGV/sql/vector.sql" "$("$NEED_PG_CONFIG" --sharedir)/extension/vector--${PGVECTOR_VERSION}.sql"
    for f in "$PGV"/sql/*--*--*.sql; do
      [[ -e "$f" ]] && cp "$f" "$("$NEED_PG_CONFIG" --sharedir)/extension/"
    done
    log "pgvector v${PGVECTOR_VERSION} installed into .tools/pg"
  else
    log "pgvector already installed"
  fi
fi

cat > "$BIN/env.sh" <<EOF
# shellcheck shell=bash
# Source this file to use the isolated Nightjar toolchain.
export NIGHTJAR_ROOT="$ROOT"
export PATH="$BIN:$NODE_DIR/bin:$PG_DIR/bin:\$PATH"
export PGDATA="\${PGDATA:-$TOOLS/pgdata}"
export PGHOST="\${PGHOST:-127.0.0.1}"
export PGPORT="\${PGPORT:-55432}"
export PGUSER="\${PGUSER:-nightjar}"
export NIGHTJAR_DATABASE_URL="\${NIGHTJAR_DATABASE_URL:-postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar}"
export UV_PYTHON_INSTALL_DIR="\${UV_PYTHON_INSTALL_DIR:-$TOOLS/python}"
EOF

log "done. source $BIN/env.sh"
