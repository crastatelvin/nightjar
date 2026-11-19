#!/usr/bin/env bash
# Shared helpers for pg-backup.sh and pg-restore.sh. Sourced, never executed.
#
# The scripts work with either the project toolchain (.tools/) or a system
# install: PostgreSQL client tools are taken from PATH, the Python runtime and
# Alembic from the project virtualenv when it exists. Nothing here touches a
# database until the caller asks it to.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/.tools/bin/env.sh" ]]; then
  # shellcheck source=../.tools/bin/env.sh
  source "$ROOT/.tools/bin/env.sh"
fi

PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
ALEMBIC="${ALEMBIC:-$ROOT/.venv/bin/alembic}"
if [[ ! -x "$ALEMBIC" ]]; then
  ALEMBIC="$(command -v alembic || true)"
fi

DEFAULT_DATABASE_URL="${NIGHTJAR_DATABASE_URL:-postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar}"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '  %s\n' "$*" >&2; }

require_tools() {
  [[ -n "$PY" ]] || die "no Python runtime found"
  [[ -n "$ALEMBIC" ]] || die "alembic not found; run: uv sync --extra dev"
  command -v pg_dump >/dev/null 2>&1 || die "pg_dump not found on PATH"
  command -v pg_restore >/dev/null 2>&1 || die "pg_restore not found on PATH"
  command -v psql >/dev/null 2>&1 || die "psql not found on PATH"
}

# _parse_url MODE URL — print one component of a SQLAlchemy URL.
#   dsn      libpq URL, password intact (for pg_dump/pg_restore)
#   redact   libpq URL, password hidden (for logs)
#   database database name
#   admin    same server, connected to the `postgres` maintenance database
_parse_url() {
  "$PY" - "$1" "$2" <<'PY'
import sys

from sqlalchemy.engine import make_url

mode, raw = sys.argv[1], sys.argv[2]
url = make_url(raw)
base = url.set(drivername="postgresql")
if mode == "dsn":
    print(base.render_as_string(hide_password=False))
elif mode == "redact":
    print(base.render_as_string(hide_password=True))
elif mode == "database":
    print(url.database or "")
elif mode == "admin":
    print(base.set(database="postgres").render_as_string(hide_password=False))
else:
    raise SystemExit(f"unknown mode: {mode}")
PY
}

# sql DSN QUERY — one value, no headers, abort on SQL error.
sql() {
  psql --no-psqlrc --quiet --tuples-only --no-align \
    --set ON_ERROR_STOP=1 --dbname="$1" --command="$2"
}

# sql_optional DSN QUERY — one value, or empty when the query fails (a table
# that does not exist yet is not an error worth stopping a backup for).
sql_optional() {
  sql "$1" "$2" 2>/dev/null || true
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# manifest_get MANIFEST KEY — print a string field, or nothing if absent.
manifest_get() {
  "$PY" - "$1" "$2" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(1)
value = data
for part in sys.argv[2].split("."):
    if not isinstance(value, dict) or part not in value:
        raise SystemExit(1)
    value = value[part]
print(value)
PY
}

alembic_head() {
  ( cd "$ROOT" && "$ALEMBIC" -c "$ROOT/alembic.ini" heads 2>/dev/null | awk '{print $1}' | paste -sd, - )
}
