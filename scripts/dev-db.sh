#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PG_BIN=${PG_BIN:-$ROOT/.local/postgresql-17/bin}
DATA_DIR="$ROOT/.local/postgresql"
SOCKET_DIR="$ROOT/.local/postgres-socket"
PORT=55439
DB_NAME=${CRM_DB_NAME:-aluola_crm_dev}
DB_USER=${CRM_DB_USER:-crm_dev}

case "${1:-}" in
  start)
    mkdir -p "$ROOT/.local" "$SOCKET_DIR"
    chmod 700 "$SOCKET_DIR"
    if [ ! -s "$DATA_DIR/PG_VERSION" ]; then
      TZ=GMT0 "$PG_BIN/initdb" -D "$DATA_DIR" --username="$DB_USER" --auth-local=trust --auth-host=reject --no-locale --encoding=UTF8
    fi
    if ! "$PG_BIN/pg_ctl" -D "$DATA_DIR" status >/dev/null 2>&1; then
      "$PG_BIN/pg_ctl" -D "$DATA_DIR" \
        -l "$ROOT/.local/postgresql.log" \
        -o "-c listen_addresses='' -c unix_socket_directories='$SOCKET_DIR' -p $PORT" start
    fi
    database_exists=$("$PG_BIN/psql" -h "$SOCKET_DIR" -p "$PORT" -U "$DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")
    if [ "$database_exists" != "1" ]; then
      "$PG_BIN/createdb" -h "$SOCKET_DIR" -p "$PORT" -U "$DB_USER" "$DB_NAME"
    fi
    printf 'Local PostgreSQL is ready: %s (socket %s, port %s; no TCP listener)\n' "$DB_NAME" "$SOCKET_DIR" "$PORT"
    ;;
  stop)
    "$PG_BIN/pg_ctl" -D "$DATA_DIR" -m fast stop
    ;;
  status)
    "$PG_BIN/pg_ctl" -D "$DATA_DIR" status
    ;;
  *)
    printf 'Usage: %s {start|stop|status}\n' "$0" >&2
    exit 2
    ;;
esac
