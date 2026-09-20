#!/usr/bin/env bash
# One command from a fresh clone to a running, seeded Meridian. Task B-06.
#
# The path this replaces is in README.md and scaffold §6: install `uv`, install
# `npm`, bring up infra, export the right variables, migrate, seed, then start
# three services in three terminals. That is the right workflow for *writing*
# code and the wrong one for finding out whether you want to.
#
#   make quickstart            # bring it all up, migrate, seed, print the URL
#   make quickstart ARGS=--rebuild
#
# It drives `docker-compose.local.yml` (`B-05`), so everything is built from
# source and nothing needs registry access.
#
# **Idempotent by construction.** Every step is safe to repeat: compose
# converges rather than recreating, `alembic upgrade head` is a no-op at head,
# and `seed.py` skips rows it has already written (scaffold §1.6 — config seeds
# at first boot only, and the database is authoritative afterwards). So the
# answer to "it failed halfway" is to run it again, which is the only answer
# anybody is going to try anyway.

set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.local.yml"
URL="http://localhost:21116"
REBUILD=0
SKIP_PREFLIGHT=0

for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "meridian: unknown option $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==>\033[0m %s\n' "$1"; }
[ -t 1 ] && [ -z "${NO_COLOR:-}" ] || step() { printf '\n==> %s\n' "$1"; }

# --- 1. is this machine going to work at all? --------------------------------
if [ "$SKIP_PREFLIGHT" = 0 ]; then
  step "Checking this machine"
  # Exits non-zero only for a missing Docker daemon or Compose v1 — the two
  # things that mean nothing can start. Being under the recommended memory is a
  # warning there and must not stop us here (`B-08`).
  ./scripts/preflight.sh
fi

# --- 2. build and start ------------------------------------------------------
step "Building and starting the stack"
echo "  The first run builds five images and pulls a browser pool. Ten to twenty"
echo "  minutes is normal; after that it is cached and startup is seconds."
echo "  The 2.3 GB of model weights come later and separately — they are not in"
echo "  any image, which is what step 4 is for."

if [ "$REBUILD" = 1 ]; then
  # shellcheck disable=SC2086
  $COMPOSE build --no-cache
fi

# Postgres first and alone. There is no point starting a worker that will
# immediately fail to find its tables, and a failed migration is far easier to
# read when it is not interleaved with six other services' startup logs.
# shellcheck disable=SC2086
$COMPOSE up -d --build postgres

step "Waiting for Postgres"
for i in $(seq 1 60); do
  # shellcheck disable=SC2086
  if $COMPOSE exec -T postgres pg_isready -U meridian -d meridian >/dev/null 2>&1; then
    echo "  ready after ${i}s"
    break
  fi
  if [ "$i" = 60 ]; then
    echo "meridian: Postgres did not become ready in 60s." >&2
    echo "  $COMPOSE logs postgres" >&2
    exit 1
  fi
  sleep 1
done

# --- 3. schema and config ----------------------------------------------------
# Both run in the `tools` image, not on the host and not in a service image.
# The host may have no `uv` — not needing one is the premise of this script —
# and no service image carries Alembic, because it lives in the root project's
# `dev` group and every application image syncs with `--no-dev`. That is the
# right split; the tooling gets its own image rather than being smuggled into
# one that does not want it (deploy/tools/Dockerfile).
#
# `run --rm` rather than `exec`: this has to work before anything is up, and
# leaves nothing behind.
step "Applying migrations"
# shellcheck disable=SC2086
$COMPOSE run --rm --build tools alembic upgrade head

step "Seeding configuration"
echo "  Topics, fetch policy and the gazetteer from config/*.yaml. First boot"
echo "  only — the database is authoritative afterwards (scaffold §1.6)."
# shellcheck disable=SC2086
$COMPOSE run --rm tools python scripts/seed.py

# --- 4. somewhere to put things ----------------------------------------------
# Docker creates a missing bind-mount source as root and every service runs
# unprivileged, so without this the crawl fetches pages it cannot store and the
# weights land nowhere (`B-16`). One `chown` of three directories; a no-op once
# they are right.
step "Preparing the data directories"
# shellcheck disable=SC2086
$COMPOSE run --rm datadirs

# --- 5. the weights ----------------------------------------------------------
# Before the embedder starts, and from a container that has egress, because the
# embedder does not (`B-14`). Without this the sidecar waits for ever on a
# download it cannot make, and search is lexical-only with no error anywhere
# that says why. Idempotent — a populated cache downloads nothing.
step "Fetching the embedding model"
echo "  2.3 GB, once. The sidecar sits on an isolated network and cannot fetch"
echo "  this itself; the volume it mounts keeps it across recreates."
# shellcheck disable=SC2086
$COMPOSE run --rm --build modelfetch

# --- 6. everything else ------------------------------------------------------
step "Starting the rest of the stack"
# shellcheck disable=SC2086
$COMPOSE up -d --build

step "Waiting for the API"
for i in $(seq 1 90); do
  if curl -fsS "http://localhost:21114/health" >/dev/null 2>&1; then
    echo "  answering after ${i}s"
    break
  fi
  if [ "$i" = 90 ]; then
    echo "meridian: the API did not answer in 90s." >&2
    echo "  $COMPOSE logs api" >&2
    exit 1
  fi
  sleep 1
done

# --- 7. where to go ----------------------------------------------------------
cat <<EOF

  Meridian is running.

    ${URL}          the interface
    http://localhost:21114/health   the API's own view of itself

  It has no documents yet — production starts empty by design (scaffold §1.7),
  and the crawl has just begun from the seeds in config/. Give it an hour before
  judging what search returns.

    $COMPOSE logs -f worker     watch it crawl
    $COMPOSE ps                 what is running
    $COMPOSE down               stop it (add -v to delete the data too)

EOF
