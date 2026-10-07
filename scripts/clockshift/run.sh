#!/usr/bin/env bash
# Run the tests twice, today and DAYS ahead on every clock, and print what fails only ahead.
# See docs/reference/commands.md#clock-check.
set -euo pipefail

DAYS="${1:-400}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/scripts/clockshift"
OUT="$(mktemp -d)"
cd "$ROOT"

set -a
. ./.env.dev
set +a
export PG_RW_URL=postgresql://meridian_rw:dev@localhost:21121/meridian
export PG_RO_URL=postgresql://meridian_ro:dev@localhost:21121/meridian
export PG_MIGRATION_URL=postgresql://meridian:dev@localhost:21121/meridian
export PG_GUEST_URL=postgresql://meridian_guest:dev@localhost:21121/meridian

database_at() {
  docker compose -f "$HERE/compose.yml" down -v >/dev/null 2>&1 || true
  FAKETIME="+${1}d" docker compose -f "$HERE/compose.yml" up -d --build --wait >/dev/null 2>&1
  for _ in $(seq 60); do
    docker exec meridian-clockshift-postgres-1 psql -U meridian -d meridian -Atc 'SELECT 1' \
      >/dev/null 2>&1 && break
    sleep 1
  done
  uv run alembic upgrade head >/dev/null 2>&1
  uv run python scripts/seed.py >/dev/null 2>&1
}

failures() {
  SHIFT_DAYS="$1" PYTHONPATH="$HERE" uv run --with time-machine \
    pytest -q -p shiftclock -p no:cacheprovider -m 'not server_timer' 2>&1 | grep -E '^(FAILED|ERROR)' | sort || true
  (cd web && SHIFT_DAYS="$1" NODE_OPTIONS="--import $HERE/shiftdate.mjs" \
    npx vitest run --reporter=dot 2>&1 | grep -E '^ *FAIL ' | sort -u || true)
}

database_at 0
failures 0 > "$OUT/today"
database_at "$DAYS"
failures "$DAYS" > "$OUT/ahead"
docker compose -f "$HERE/compose.yml" down -v >/dev/null 2>&1

# Liveness compares the clock with a file's real mtime, which no shift can move.
new="$(comm -13 "$OUT/today" "$OUT/ahead" | grep -v 'liveness' || true)"
if [ -n "$new" ]; then
  echo "Fails only ${DAYS} days ahead:"
  echo "$new"
  exit 1
fi
echo "Nothing fails ${DAYS} days ahead that does not fail today."
