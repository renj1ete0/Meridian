#!/usr/bin/env bash
# Snapshot the corpus — task P1-36, scaffold §6.
#
# Phase 1's checkpoint is "runs 48h unattended; the result becomes the first dev
# corpus", and this is what turns the second half of that sentence into a file.
# Scaffold §7 is emphatic that development corpora are snapshots of real crawls
# and never synthetic fixtures, because entity messiness, extraction failures,
# tier distributions and edge density all differ in ways that get UI rebuilt
# later.
#
# **The database and the raw store travel together or not at all.** `sources`
# rows carry `raw_file_path`, so a dump without the files it points at is a
# catalogue, not a corpus: every provenance link in it resolves to nothing, and
# the failure appears much later as "why does no citation open".
#
# Output: fixtures/corpus_<stamp>/{corpus.dump,raw.tar.gz,manifest.json}

set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=scripts/_compose.sh
. scripts/_compose.sh
COMPOSE="$(meridian_compose)"
meridian_require_postgres

RAW_ROOT="${MERIDIAN_RAW_ROOT:-${DATA_ROOT:-.devdata}/raw}"
STAMP="$(date -u +%Y%m%d-%H%M)"
OUT="fixtures/corpus_${STAMP}"

mkdir -p "$OUT"
echo "meridian: snapshotting via '$COMPOSE' -> $OUT"

# --- the database ------------------------------------------------------------
# Custom format: compressed, and `pg_restore` can then be selective and parallel.
# shellcheck disable=SC2086
$COMPOSE exec -T postgres pg_dump \
  --username "${PG_USER:-meridian}" --dbname meridian \
  --format=custom --no-owner --no-privileges \
  > "$OUT/corpus.dump"

# --no-owner / --no-privileges so the dump restores into a database whose roles
# are named differently or do not exist yet. Roles are `init-roles.sh`'s job and
# are cluster state, not corpus state; carrying them here would make a corpus
# snapshot fail on any machine that bootstrapped its roles even slightly
# differently.

# --- the raw store -----------------------------------------------------------
if [ -d "$RAW_ROOT" ]; then
  tar -czf "$OUT/raw.tar.gz" -C "$(dirname "$RAW_ROOT")" "$(basename "$RAW_ROOT")"
else
  echo "meridian: WARNING — no raw store at $RAW_ROOT." >&2
  echo "  The dump will restore, and every raw_file_path in it will dangle." >&2
  : > "$OUT/raw.tar.gz"
fi

# --- what is in it -----------------------------------------------------------
revision="$(meridian_psql -c 'SELECT version_num FROM alembic_version' | tr -d '[:space:]')"
counts="$(meridian_psql -c "
  SELECT json_build_object(
    'sources',   (SELECT count(*) FROM sources),
    'chunks',    (SELECT count(*) FROM chunks),
    'embedded',  (SELECT count(*) FROM chunks WHERE embedding IS NOT NULL),
    'judged',    (SELECT count(*) FROM chunks WHERE novelty_checked_at IS NOT NULL),
    'duplicates',(SELECT count(*) FROM chunks WHERE duplicate_of IS NOT NULL),
    'queue',     (SELECT count(*) FROM queue),
    'attempts',  (SELECT count(*) FROM fetch_attempts)
  )")"

sha() { sha256sum "$1" | cut -d' ' -f1; }

cat > "$OUT/manifest.json" <<JSON
{
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "meridian_version": "$(cat VERSION)",
  "alembic_revision": "${revision}",
  "raw_root": "${RAW_ROOT}",
  "counts": ${counts},
  "checksums": {
    "corpus.dump": "$(sha "$OUT/corpus.dump")",
    "raw.tar.gz": "$(sha "$OUT/raw.tar.gz")"
  }
}
JSON

# The revision is recorded because restoring a corpus dumped under a newer
# schema into older code fails in ways that do not mention the schema — a
# missing column surfaces as an ORM attribute error three layers up.

echo "meridian: snapshot complete"
echo "  revision  ${revision}"
echo "  counts    ${counts}"
du -sh "$OUT"/* | sed 's/^/  /'
