#!/usr/bin/env bash
# Operational backup — task P1-37, scaffold §5, spec §13.4.
#
# Not the same job as `snapshot_corpus.sh`, and the difference is worth keeping
# straight. A snapshot is a deliberate artefact: somebody takes one, names it,
# and restores it somewhere on purpose. A backup runs unattended on a timer and
# nobody looks at it until the day they need it, which means everything about it
# has to fail loudly and nothing about it may ask a question.
#
# What it protects is not the database. Postgres can be rebuilt from migrations
# and the config can be re-seeded; what cannot be reproduced is the crawl. The
# web moves on, so a page fetched in March is not re-fetchable, only
# re-visitable — and §5.4 keeps raw files precisely because government URLs
# reorganise and a local copy is what keeps a citation checkable years later.
#
# Install as a timer:
#   sudo systemctl edit --force --full meridian-backup.timer
#   (OnCalendar=daily, with a service unit running this script)

set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=scripts/_compose.sh
. scripts/_compose.sh
COMPOSE="$(meridian_compose)"
meridian_require_postgres

DATA_ROOT="${DATA_ROOT:-/srv/meridian}"
RAW_ROOT="${MERIDIAN_RAW_ROOT:-${DATA_ROOT}/raw}"
BACKUP_ROOT="${MERIDIAN_BACKUP_ROOT:-${DATA_ROOT}/backups}"
KEEP_DAYS="${MERIDIAN_BACKUP_KEEP_DAYS:-14}"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_ROOT}/${STAMP}"

# --- is this actually off-device? --------------------------------------------
# The check that makes this a backup rather than a copy. A backup on the disk it
# is protecting survives `rm -rf` and nothing else: not the disk failing, not the
# filesystem corrupting, not the machine being lost. Scaffold §5 says off-device
# and it is the kind of instruction that gets followed on day one and quietly
# undone the first time someone is short of space.
if [ -d "$DATA_ROOT" ] && [ -e "$BACKUP_ROOT" ] || mkdir -p "$BACKUP_ROOT"; then
  data_dev="$(stat -c %d "$DATA_ROOT" 2>/dev/null || echo 0)"
  backup_dev="$(stat -c %d "$BACKUP_ROOT" 2>/dev/null || echo 1)"
  if [ "$data_dev" = "$backup_dev" ]; then
    echo "meridian: WARNING — $BACKUP_ROOT is on the same filesystem as $DATA_ROOT." >&2
    echo "  This survives an accidental delete and nothing else. Set" >&2
    echo "  MERIDIAN_BACKUP_ROOT to a different device, or sync it off afterwards." >&2
  fi
fi

mkdir -p "$OUT"
echo "meridian: backing up to $OUT"

# --- the database ------------------------------------------------------------
# shellcheck disable=SC2086
$COMPOSE exec -T postgres pg_dump \
  --username "${PG_USER:-meridian}" --dbname meridian \
  --format=custom --no-owner --no-privileges \
  > "$OUT/meridian.dump"

# Non-empty, because `set -o pipefail` does not help across a redirect: a
# pg_dump that died after printing a header leaves a small, valid-looking file
# and a zero exit from the shell that wrote it.
if [ ! -s "$OUT/meridian.dump" ]; then
  echo "meridian: the dump is empty. Refusing to call this a backup." >&2
  exit 1
fi

# --- the raw store -----------------------------------------------------------
if [ -d "$RAW_ROOT" ]; then
  tar -czf "$OUT/raw.tar.gz" -C "$(dirname "$RAW_ROOT")" "$(basename "$RAW_ROOT")"
else
  echo "meridian: WARNING — no raw store at $RAW_ROOT; backing up the database alone." >&2
fi

# `P1-45`: sources may reference a store this backup does not include.
others="$(meridian_psql -c "
  SELECT DISTINCT raw_root FROM sources
  WHERE raw_file_path IS NOT NULL AND raw_root IS NOT NULL AND raw_root <> '${RAW_ROOT}'" || true)"
if [ -n "$others" ]; then
  echo "meridian: WARNING — sources reference raw stores not in this backup:" >&2
  echo "$others" | sed 's/^/    /' >&2
fi

sha256sum "$OUT"/* > "$OUT/SHA256SUMS"

# --- rotation ----------------------------------------------------------------
# After the new one is written and checksummed, never before. Pruning first
# means a failed backup costs the oldest good one too.
if [ "${KEEP_DAYS}" -gt 0 ]; then
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+${KEEP_DAYS}" \
    -exec rm -rf {} + 2>/dev/null || true
fi

echo "meridian: backup complete"
du -sh "$OUT" | sed 's/^/  /'
ls -1 "$BACKUP_ROOT" | wc -l | sed 's/^/  backups retained: /'
