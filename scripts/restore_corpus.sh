#!/usr/bin/env bash
# Restore a corpus snapshot — task P1-36, scaffold §6, §7.
#
# The counterpart to `snapshot_corpus.sh`, and the more dangerous half: it
# replaces a database. Scaffold §7 says development corpora are "never loadable
# in production", and this is where that has to be enforced rather than
# documented — the guard is that a **non-empty** target is refused outright
# unless `--replace` is passed and confirmed.
#
# Refusing on non-empty rather than on "is this production" is deliberate.
# Environment detection is a guess: a `MERIDIAN_ENV` that nobody set, a hostname
# that changed, a compose file copied to the wrong machine. "Does this database
# already contain a corpus" is a fact, and it is the fact that actually matters
# — the thing worth preventing is destroying crawl output that cannot be
# re-fetched, wherever it happens to live.
#
# Usage:
#   scripts/restore_corpus.sh                  # newest snapshot
#   scripts/restore_corpus.sh fixtures/corpus_20260915-1200
#   scripts/restore_corpus.sh --replace        # overwrite a non-empty database

set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=scripts/_compose.sh
. scripts/_compose.sh

REPLACE=0
SNAPSHOT=""
for arg in "$@"; do
  case "$arg" in
    --replace) REPLACE=1 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) SNAPSHOT="$arg" ;;
  esac
done

if [ -z "$SNAPSHOT" ]; then
  SNAPSHOT="$(ls -d fixtures/corpus_* 2>/dev/null | sort | tail -1 || true)"
  [ -n "$SNAPSHOT" ] || { echo "meridian: no snapshot in fixtures/" >&2; exit 1; }
  echo "meridian: newest snapshot is $SNAPSHOT"
fi

MANIFEST="$SNAPSHOT/manifest.json"
[ -f "$MANIFEST" ] || { echo "meridian: $MANIFEST is missing" >&2; exit 1; }

COMPOSE="$(meridian_compose)"
meridian_require_postgres

field() { python3 -c "import json,sys;d=json.load(open('$MANIFEST'));print(eval('d'+sys.argv[1]))" "$1"; }

# --- the snapshot is intact --------------------------------------------------
# Before anything is touched. A truncated dump discovered halfway through a
# restore has already dropped the tables it was going to replace.
for artefact in corpus.dump raw.tar.gz; do
  expected="$(field "['checksums']['$artefact']")"
  actual="$(sha256sum "$SNAPSHOT/$artefact" | cut -d' ' -f1)"
  if [ "$expected" != "$actual" ]; then
    echo "meridian: $artefact does not match its manifest checksum. Refusing." >&2
    exit 1
  fi
done

# --- the schema agrees -------------------------------------------------------
snapshot_revision="$(field "['alembic_revision']")"
current_revision="$(meridian_psql -c 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]' || true)"
if [ -n "$current_revision" ] && [ "$snapshot_revision" != "$current_revision" ]; then
  echo "meridian: WARNING — snapshot is at revision $snapshot_revision, this database is at $current_revision." >&2
  echo "  The dump carries its own schema, so the restore will succeed and leave" >&2
  echo "  the database at the snapshot's revision. Run 'make migrate' afterwards." >&2
fi

# --- the target is safe to overwrite -----------------------------------------
existing="$(meridian_psql -c "SELECT count(*) FROM sources" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [ "${existing:-0}" -gt 0 ] && [ "$REPLACE" -eq 0 ]; then
  cat >&2 <<MSG
meridian: this database already holds $existing sources. Refusing.

  A corpus is crawl output. It took wall-clock time and somebody else's
  bandwidth to collect, and nothing here can re-fetch it — a re-crawl returns
  today's web, not the web this snapshot recorded.

  Pass --replace if you are certain. Take a snapshot first if you are not.
MSG
  exit 1
fi

if [ "${existing:-0}" -gt 0 ]; then
  echo "meridian: about to REPLACE a database holding $existing sources."
  printf "  Type the source count to confirm: "
  read -r answer
  [ "$answer" = "$existing" ] || { echo "meridian: not confirmed. Nothing changed."; exit 1; }
fi

# --- restore -----------------------------------------------------------------
echo "meridian: restoring $SNAPSHOT via '$COMPOSE'"
# shellcheck disable=SC2086
$COMPOSE exec -T postgres pg_restore \
  --username "${PG_USER:-meridian}" --dbname meridian \
  --clean --if-exists --no-owner --no-privileges \
  < "$SNAPSHOT/corpus.dump"

RAW_ROOT="${MERIDIAN_RAW_ROOT:-${DATA_ROOT:-.devdata}/raw}"
if [ -s "$SNAPSHOT/raw.tar.gz" ]; then
  mkdir -p "$(dirname "$RAW_ROOT")"
  tar -xzf "$SNAPSHOT/raw.tar.gz" -C "$(dirname "$RAW_ROOT")"
fi

# --- prove the two halves match ----------------------------------------------
# The check that makes this more than two unpack operations. A restore where the
# rows landed and the files did not looks entirely successful until someone
# follows a citation.
missing="$(meridian_psql -c "
  SELECT count(*) FROM sources WHERE raw_file_path IS NOT NULL" | tr -d '[:space:]')"
absent=0
if [ "${missing:-0}" -gt 0 ]; then
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ -f "$RAW_ROOT/$path" ] || absent=$((absent + 1))
  done < <(meridian_psql -c "SELECT raw_file_path FROM sources WHERE raw_file_path IS NOT NULL LIMIT 200")
fi

echo "meridian: restored"
meridian_psql -c "SELECT 'sources: '||count(*) FROM sources" | sed 's/^/  /'
if [ "$absent" -gt 0 ]; then
  echo "  WARNING: $absent of the first 200 raw_file_path values have no file." >&2
  echo "  The dump and the raw store are out of step — citations will dangle." >&2
else
  echo "  raw store: every sampled raw_file_path resolves to a file"
fi
