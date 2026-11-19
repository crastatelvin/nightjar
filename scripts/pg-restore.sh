#!/usr/bin/env bash
# Restore a Nightjar backup into a database, migrate it, and prove the ledger.
#
#   ./scripts/pg-restore.sh DUMP --database-url URL [--force] [--no-verify]
#
# The target database must not exist unless --force is given, because the most
# expensive mistake in this script is restoring over a live deployment. With
# --force the database is dropped and recreated first: a restore is a whole
# database, never a merge into one.
#
# After pg_restore the schema is moved to the current Alembic head and (unless
# --no-verify) `nightjarctl verify-ledger` recomputes every evidence and timeline
# hash chain in the restored database and compares the row counts with the
# manifest. Exit 0 means the restored ledger is byte-for-byte the one that was
# dumped. --no-verify exists for a partially built dump; it prints a warning and
# exits 0, so a caller cannot mistake it for a verified restore by accident.

set -euo pipefail

# shellcheck source=pg-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pg-common.sh"

usage() {
  cat <<'USAGE'
usage: pg-restore.sh DUMP --database-url URL [--manifest FILE] [--force] [--no-verify]

  DUMP                dump written by pg-backup.sh
  --database-url URL  SQLAlchemy URL of the database to create and restore into
  --manifest FILE     manifest to trust (default: DUMP with .json suffix)
  --force             drop the target database if it already exists
  --no-verify         skip checksum and chain verification (prints a warning)

Exit status is 0 only when the dump matched its manifest, pg_restore succeeded,
and every hash chain in the restored database verified.
USAGE
}

DUMP=""
TARGET=""
MANIFEST=""
FORCE=false
VERIFY=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --database-url) TARGET="${2:?--database-url needs a value}"; shift 2 ;;
    --manifest) MANIFEST="${2:?--manifest needs a value}"; shift 2 ;;
    --force) FORCE=true; shift ;;
    --no-verify) VERIFY=false; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown argument: $1 (try --help)" ;;
    *) [[ -z "$DUMP" ]] || die "only one dump can be restored at a time"; DUMP="$1"; shift ;;
  esac
done

[[ -n "$DUMP" ]] || { usage >&2; die "a dump path is required"; }
[[ -f "$DUMP" ]] || die "dump not found: $DUMP"
[[ -n "$TARGET" ]] || die "--database-url is required: this script never guesses a database to overwrite"

require_tools

[[ -n "$MANIFEST" ]] || MANIFEST="${DUMP%.dump}.json"
TARGET_DSN="$(_parse_url dsn "$TARGET")"
TARGET_REDACTED="$(_parse_url redact "$TARGET")"
DBNAME="$(_parse_url database "$TARGET")"
ADMIN_DSN="$(_parse_url admin "$TARGET")"

[[ "$DBNAME" =~ ^[A-Za-z0-9_]+$ ]] || die "refusing database name '$DBNAME': use letters, digits, underscores"

if [[ "$VERIFY" == true ]]; then
  [[ -f "$MANIFEST" ]] || die "manifest not found: $MANIFEST (pass --manifest or --no-verify)"
  expected_sha="$(manifest_get "$MANIFEST" sha256)" || die "manifest is not valid JSON: $MANIFEST"
  expected_bytes="$(manifest_get "$MANIFEST" bytes)" || die "manifest has no byte count: $MANIFEST"
  actual_sha="$(sha256_of "$DUMP")"
  actual_bytes="$(wc -c <"$DUMP" | tr -d ' ')"
  [[ "$actual_sha" == "$expected_sha" ]] || die "checksum mismatch: dump is $actual_sha, manifest says $expected_sha"
  [[ "$actual_bytes" == "$expected_bytes" ]] || die "size mismatch: dump is $actual_bytes bytes, manifest says $expected_bytes"
  note "checksum ok: $actual_sha"
else
  note "WARNING: --no-verify means the dump is not checked against its manifest and"
  note "         the restored hash chains are not verified. This restore is unproven."
fi

exists="$(sql "$ADMIN_DSN" "select 1 from pg_database where datname = '$DBNAME'")"
if [[ -n "$exists" ]]; then
  if [[ "$FORCE" != true ]]; then
    die "database '$DBNAME' already exists; pass --force to drop and recreate it"
  fi
  note "dropping existing database $DBNAME"
  sql "$ADMIN_DSN" "drop database \"$DBNAME\"" >/dev/null
fi
note "creating database $DBNAME"
sql "$ADMIN_DSN" "create database \"$DBNAME\"" >/dev/null

note "restoring $(basename "$DUMP") into $TARGET_REDACTED"
pg_restore \
  --dbname="$TARGET_DSN" \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  "$DUMP" || die "pg_restore failed"

note "migrating to head"
# Only the database URL is needed here. Forcing the development environment keeps
# a restore working on a machine that does not hold the deployment's production
# secrets, which are irrelevant to a schema migration.
( cd "$ROOT" && NIGHTJAR_ENVIRONMENT=development NIGHTJAR_DATABASE_URL="$TARGET" \
  "$ALEMBIC" -c "$ROOT/alembic.ini" upgrade head ) || die "alembic upgrade head failed"

revision="$(sql "$TARGET_DSN" "select version_num from alembic_version")"
head="$(alembic_head)"
[[ -n "$head" ]] || die "could not read the current Alembic head"
[[ "$revision" == "$head" ]] || die "restored schema is at $revision, expected head $head"

if [[ "$VERIFY" != true ]]; then
  printf 'restored %s into %s (UNVERIFIED)\n' "$(basename "$DUMP")" "$TARGET_REDACTED"
  exit 0
fi

note "verifying every evidence and timeline chain"
if ! report="$("$PY" -m nightjar.cli.main verify-ledger --database-url "$TARGET" --json)"; then
  printf '%s\n' "$report" >&2
  die "the restored ledger did not verify"
fi

REPORT_JSON="$report" "$PY" - "$MANIFEST" "$TARGET_REDACTED" <<'PY'
import json
import os
import sys

manifest_path, target = sys.argv[1], sys.argv[2]
report = json.loads(os.environ["REPORT_JSON"])
with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)

if not report["ok"]:
    for item in report["broken"]:
        print(
            f"broken chain: {item['incident_id']} {item['chain']} "
            f"at {item['broken_at']}: {item['reason']}",
            file=sys.stderr,
        )
    raise SystemExit("the restored ledger has broken hash chains")

mismatches = [
    f"{key}: restored {report[key]}, manifest {expected}"
    for key, expected in manifest["counts"].items()
    if report[key] != expected
]
if mismatches:
    for line in mismatches:
        print(f"count mismatch: {line}", file=sys.stderr)
    raise SystemExit("the restored database does not contain what was dumped")

print(
    f"verified: {report['incidents']} incidents, "
    f"{report['timeline_events']} timeline events, "
    f"{report['evidence_items']} evidence items, every chain intact"
)
print(f"restored {manifest['dump']} into {target}")
PY
