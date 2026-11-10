#!/usr/bin/env bash
# Manage the project-local PostgreSQL instance.
#
#   ./scripts/db.sh init    # create the cluster (idempotent)
#   ./scripts/db.sh start
#   ./scripts/db.sh stop
#   ./scripts/db.sh status
#   ./scripts/db.sh psql
#   ./scripts/db.sh reset   # DESTROYS the local database
#
# Everything lives under .tools/ and .tools/pgdata/. No system service is used.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../.tools/bin/env.sh
source "$ROOT/.tools/bin/env.sh"

PORT="${PGPORT:-55432}"
SOCKDIR="$ROOT/.tools/run"
LOG="$ROOT/.tools/log/pg.log"

mkdir -p "$SOCKDIR" "$(dirname "$LOG")"

init() {
  if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
    initdb -D "$PGDATA" -U "$PGUSER" --auth=trust --encoding=UTF8 --no-instructions
  fi
  if ! pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    pg_ctl -D "$PGDATA" -l "$LOG" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories='$SOCKDIR'" start
  fi
  if ! psql -h 127.0.0.1 -p "$PORT" -U "$PGUSER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='nightjar'" | grep -q 1; then
    createdb -h 127.0.0.1 -p "$PORT" -U "$PGUSER" nightjar
  fi
  psql -h 127.0.0.1 -p "$PORT" -U "$PGUSER" -d nightjar -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null
  echo "postgres ready at 127.0.0.1:$PORT (database: nightjar)"
}

case "${1:-start}" in
  init) init ;;
  start)
    pg_ctl -D "$PGDATA" -l "$LOG" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories='$SOCKDIR'" start
    ;;
  stop)
    pg_ctl -D "$PGDATA" stop -m fast
    ;;
  status)
    pg_ctl -D "$PGDATA" status || true
    pg_isready -h 127.0.0.1 -p "$PORT" -U "$PGUSER" || true
    ;;
  psql)
    exec psql -h 127.0.0.1 -p "$PORT" -U "$PGUSER" -d nightjar
    ;;
  reset)
    pg_ctl -D "$PGDATA" stop -m immediate 2>/dev/null || true
    rm -rf "$PGDATA"
    init
    ;;
  *)
    echo "usage: $0 {init|start|stop|status|psql|reset}" >&2
    exit 2
    ;;
esac
