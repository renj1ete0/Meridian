#!/usr/bin/env bash
# Run the integration suite once on a fresh database and print what it left behind.
# See docs/reference/commands.md#leak-check.
set -euo pipefail

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

snapshot() {
  docker exec -i meridian-clockshift-postgres-1 psql -U meridian -d meridian -At -v ON_ERROR_STOP=1 \
    < "$HERE/snapshot.sql"
}

docker compose -f "$HERE/compose.yml" down -v >/dev/null 2>&1 || true
FAKETIME=+0d docker compose -f "$HERE/compose.yml" up -d --build --wait >/dev/null 2>&1
uv run alembic upgrade head >/dev/null 2>&1
uv run python scripts/seed.py >/dev/null 2>&1

snapshot > "$OUT/before"
uv run pytest -q -p no:cacheprovider tests/integration 2>&1 | tail -1
snapshot > "$OUT/after"
docker compose -f "$HERE/compose.yml" down -v >/dev/null 2>&1

if ! python3 "$HERE/snapdiff.py" "$OUT/before" "$OUT/after" > "$OUT/diff"; then
  echo "The integration suite left the database changed:"
  cat "$OUT/diff"
  exit 1
fi
echo "The integration suite left the database as it found it."
