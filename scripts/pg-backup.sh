#!/usr/bin/env bash
# Back up the Nightjar database, with a manifest that says what is inside.
#
#   ./scripts/pg-backup.sh [--database-url URL] [--out DIR]
#
# The dump is PostgreSQL's custom format: one file, restorable table by table,
# already compressed. Next to it is a JSON manifest recording the SHA-256 of the
# dump, the git revision, the Alembic revision the database was at, and the row
# counts. pg_dump's custom format carries no checksum of its own, so the manifest
# is what lets a restore prove it is holding the bytes that were written.
#
# Nothing is deleted and no existing backup is overwritten: a second run in the
# same second is a failure, not a silent replacement.

set -euo pipefail

# shellcheck source=pg-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pg-common.sh"

usage() {
  cat <<'USAGE'
usage: pg-backup.sh [--database-url URL] [--out DIR]

  --database-url URL  SQLAlchemy URL to back up (default: $NIGHTJAR_DATABASE_URL)
  --out DIR           directory for the dump and manifest (default: ./backups)

Exit status is 0 only when the dump, its manifest, and the row counts were all
written. The dump path and manifest path are printed on stdout.
USAGE
}

DATABASE_URL="$DEFAULT_DATABASE_URL"
OUT="$ROOT/backups"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --database-url) DATABASE_URL="${2:?--database-url needs a value}"; shift 2 ;;
    --out) OUT="${2:?--out needs a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

require_tools

command -v pg_dump >/dev/null 2>&1 || die "pg_dump not found on PATH"
DSN="$(_parse_url dsn "$DATABASE_URL")"
REDACTED="$(_parse_url redact "$DATABASE_URL")"

if [[ "$(sql "$DSN" "select to_regclass('public.incidents') is not null")" != "t" ]]; then
  die "$REDACTED has no Nightjar schema; start it with: alembic upgrade head"
fi

mkdir -p "$OUT"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="nightjar-$stamp"
dump="$OUT/$base.dump"
manifest="$OUT/$base.json"
[[ -e "$dump" ]] && die "$dump already exists; wait a second and retry"

incidents="$(sql "$DSN" "select count(*) from incidents")"
timeline_events="$(sql "$DSN" "select count(*) from timeline_events")"
evidence_items="$(sql "$DSN" "select count(*) from evidence")"
schema_revision="$(sql_optional "$DSN" "select version_num from alembic_version")"
server_version="$(sql "$DSN" "show server_version")"

note "dumping $REDACTED ($incidents incidents) to $dump"
pg_dump \
  --dbname="$DSN" \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-privileges \
  --file="$dump" || die "pg_dump failed"

digest="$(sha256_of "$dump")"
bytes="$(wc -c <"$dump" | tr -d ' ')"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
git_sha="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]]; then
  git_dirty="true"
else
  git_dirty="false"
fi
head="$(alembic_head)"
pg_dump_version="$(pg_dump --version)"

MANIFEST_FORMAT=1 \
MANIFEST_CREATED_AT="$created_at" \
MANIFEST_DATABASE="$REDACTED" \
MANIFEST_DUMP="$(basename "$dump")" \
MANIFEST_SHA256="$digest" \
MANIFEST_BYTES="$bytes" \
MANIFEST_GIT_SHA="$git_sha" \
MANIFEST_GIT_DIRTY="$git_dirty" \
MANIFEST_ALEMBIC_HEAD="$head" \
MANIFEST_SCHEMA_REVISION="$schema_revision" \
MANIFEST_PG_DUMP_VERSION="$pg_dump_version" \
MANIFEST_SERVER_VERSION="$server_version" \
MANIFEST_INCIDENTS="$incidents" \
MANIFEST_TIMELINE="$timeline_events" \
MANIFEST_EVIDENCE="$evidence_items" \
"$PY" - <<'PY' >"$manifest"
import json
import os

manifest = {
    "format": int(os.environ["MANIFEST_FORMAT"]),
    "created_at": os.environ["MANIFEST_CREATED_AT"],
    "database": os.environ["MANIFEST_DATABASE"],
    "dump": os.environ["MANIFEST_DUMP"],
    "sha256": os.environ["MANIFEST_SHA256"],
    "bytes": int(os.environ["MANIFEST_BYTES"]),
    "git_sha": os.environ["MANIFEST_GIT_SHA"],
    "git_dirty": os.environ["MANIFEST_GIT_DIRTY"] == "true",
    "alembic_head": os.environ["MANIFEST_ALEMBIC_HEAD"],
    "schema_revision": os.environ["MANIFEST_SCHEMA_REVISION"],
    "pg_dump_version": os.environ["MANIFEST_PG_DUMP_VERSION"],
    "server_version": os.environ["MANIFEST_SERVER_VERSION"],
    "counts": {
        "incidents": int(os.environ["MANIFEST_INCIDENTS"]),
        "timeline_events": int(os.environ["MANIFEST_TIMELINE"]),
        "evidence_items": int(os.environ["MANIFEST_EVIDENCE"]),
    },
}
print(json.dumps(manifest, indent=2, sort_keys=True))
PY

note "manifest: $manifest ($digest)"
printf '%s\n' "$dump"
printf '%s\n' "$manifest"
