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
#
# **Archived from inside a container when the host cannot read it** (`B-31`).
# The raw store is written by services running as uid 1001 (`B-16`), so on a
# real deployment the files are not readable by whoever is running this script
# — and `tar` reports each one, exits non-zero, and leaves a 15KB archive
# beside a 113MB dump. That is precisely the "catalogue, not a corpus" this
# file's own header warns about, produced by the tool meant to prevent it.
#
# Same reasoning as `pg_dump` running in the container: the thing that can read
# the data should be the thing that reads it.
if [ ! -d "$RAW_ROOT" ]; then
  echo "meridian: WARNING — no raw store at $RAW_ROOT." >&2
  echo "  The dump will restore, and every raw_file_path in it will dangle." >&2
  : > "$OUT/raw.tar.gz"
elif [ -n "$(find "$RAW_ROOT" ! -readable -print -quit 2>/dev/null)" ]; then
  echo "meridian: the raw store is not readable from here; archiving in a container."
  # shellcheck disable=SC2086
  $COMPOSE run --rm --no-deps -T worker \
    tar -czf - -C "$(dirname /data/raw)" "$(basename /data/raw)" > "$OUT/raw.tar.gz"
else
  tar -czf "$OUT/raw.tar.gz" -C "$(dirname "$RAW_ROOT")" "$(basename "$RAW_ROOT")"
fi

# --- is the archive plausible? -----------------------------------------------
# `B-31`. The failure above was silent in the direction that matters: an empty
# archive is a successful-looking snapshot whose every citation dangles. So the
# file count is compared against what the database expects, and a shortfall is
# reported here rather than discovered on another machine months later.
expected="$(meridian_psql -c "SELECT count(*) FROM sources WHERE raw_file_path IS NOT NULL")"
archived="$(tar -tzf "$OUT/raw.tar.gz" 2>/dev/null | grep -vc '/$' || true)"
if [ "${expected:-0}" -gt 0 ] && [ "${archived:-0}" -lt "$(( expected / 2 ))" ]; then
  echo "meridian: WARNING — raw.tar.gz holds $archived files; the database" >&2
  echo "  expects about $expected. This snapshot is a catalogue, not a corpus." >&2
else
  echo "meridian: raw archive holds $archived files (database expects ~$expected)."
fi

# --- does this corpus fit in one archive? ------------------------------------
# `P1-45`. A corpus written partly natively and partly by a container spans two
# raw stores, and this script tars one. Without the check the snapshot succeeds,
# is quietly missing files, and only `restore_corpus.sh`'s sampling notices —
# on another machine, later, when it is too late to go back for them.
# Distinct roots, not roots that differ from this one (`B-31`). The store is a
# bind mount, so the services record the path they see — `/data/raw` — while
# this script is given the host's name for the same directory. Comparing the
# two strings reported every containerised snapshot as incomplete, which is a
# warning that is wrong every time it appears and therefore never read. What
# `P1-45` is actually about is a corpus written into *two* stores, and that is
# what more than one distinct root means.
roots="$(meridian_psql -c "
  SELECT DISTINCT raw_root FROM sources
  WHERE raw_file_path IS NOT NULL AND raw_root IS NOT NULL")"
if [ "$(echo "$roots" | grep -c .)" -gt 1 ]; then
  echo "meridian: WARNING — this corpus was written into more than one raw store:" >&2
  echo "$roots" | sed 's/^/    /' >&2
  echo "  This snapshot archives one of them. Snapshot the others too, or the" >&2
  echo "  restored corpus will have citations that cannot open." >&2
fi

unrooted="$(meridian_psql -c "
  SELECT count(*) FROM sources WHERE raw_file_path IS NOT NULL AND raw_root IS NULL")"
if [ "${unrooted:-0}" -gt 0 ]; then
  echo "meridian: note — $unrooted sources predate P1-45 and record no raw root." >&2
  echo "  If any of their files are missing from this archive, nothing can say so." >&2
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
