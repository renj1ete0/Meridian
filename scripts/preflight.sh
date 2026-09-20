#!/usr/bin/env bash
# Check this machine against the stated minimums before anything is started.
# Task B-08, README "Minimum requirements".
#
# The failure this replaces: compose comes up, Postgres is OOM-killed somewhere
# in the middle of the first crawl, and the visible symptom is a restart loop in
# a container that is not the one with the problem. That is an hour of reading
# logs to discover a number that could have been printed in a second.
#
#   ./scripts/preflight.sh          # check, and say what is wrong
#   ./scripts/preflight.sh --quiet  # print only problems
#
# **Warnings are not failures.** Only two things here are actually fatal — no
# Docker daemon and no Compose v2 — because those mean nothing can start at all.
# Being under the recommended memory means the stack runs and crawls slowly,
# which is a legitimate choice on hardware somebody already owns, and a script
# that refuses to run on it would be substituting its judgement for theirs.
# Under the *minimum* is a loud warning and still not a refusal, for the same
# reason: the README's numbers are sized for a 50k-document corpus, and someone
# trying it on 500 documents is not wrong.
#
# Exit status is 1 only for the fatal cases, so `make quickstart` can gate on it
# without the gate being an opinion about somebody's laptop.

set -euo pipefail
cd "$(dirname "$0")/.."

QUIET=0
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "meridian: unknown option $arg" >&2; exit 2 ;;
  esac
done

# README "Minimum requirements", and the one place these numbers live in code.
MIN_CORES=4
MIN_MEM_GB=8
MIN_DISK_GB=100
MIN_COMPOSE_MAJOR=2

fatal=0
warned=0

# Colour only when something is watching. Piped into a file or a CI log, the
# escape codes are noise in exactly the output somebody is going to paste into a
# bug report.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_OK=''; C_WARN=''; C_BAD=''; C_OFF=''
fi

ok()    { [ "$QUIET" = 1 ] || printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$1"; }
warn()  { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$1" >&2; warned=$((warned + 1)); }
bad()   { printf '  %s✗%s %s\n' "$C_BAD" "$C_OFF" "$1" >&2; fatal=$((fatal + 1)); }
note()  { [ "$QUIET" = 1 ] || printf '      %s\n' "$1"; }

[ "$QUIET" = 1 ] || echo "meridian: checking this machine against the stated minimums"

# --- the two that are actually fatal -----------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  bad "Docker is not installed."
  note "https://docs.docker.com/engine/install/ — the Engine, not Desktop, on a server."
elif ! docker info >/dev/null 2>&1; then
  bad "Docker is installed but the daemon is not reachable."
  note "Check it is running, and that DOCKER_HOST points at its socket."
  note "Rootless installs commonly need DOCKER_HOST=unix:///var/run/docker.sock."
else
  ok "Docker daemon reachable ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'version unknown'))"
fi

# Compose v2 is a docker subcommand; v1 was a separate `docker-compose` binary
# with different behaviour around profiles, healthchecks and `depends_on`
# conditions — all of which this stack uses. Naming the version matters because
# a v1 install fails later with errors about keys it simply does not know.
if compose_version="$(docker compose version --short 2>/dev/null)"; then
  major="${compose_version%%.*}"
  if [ "${major:-0}" -lt "$MIN_COMPOSE_MAJOR" ] 2>/dev/null; then
    bad "Compose v${compose_version}; this stack needs v${MIN_COMPOSE_MAJOR} or newer."
    note "v1 does not understand the healthcheck-conditioned depends_on used here."
  else
    ok "Compose v${compose_version}"
  fi
else
  bad "Docker Compose v2 is not available ('docker compose version' failed)."
  note "The v1 'docker-compose' binary is not a substitute."
fi

# --- the numbers -------------------------------------------------------------
cores="$(nproc 2>/dev/null || echo 0)"
if [ "$cores" -ge "$MIN_CORES" ]; then
  ok "${cores} CPU cores"
else
  warn "${cores} CPU cores; the stated minimum is ${MIN_CORES}."
  note "Fetch concurrency and embedding both scale with this. It will work, slowly."
fi

# MemTotal, not MemAvailable. Available is what is free *now*, which on a
# machine that has been up for a while is mostly page cache and tells you
# nothing about whether the stack fits.
mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
mem_gb=$((mem_kb / 1024 / 1024))
if [ "$mem_gb" -ge "$MIN_MEM_GB" ]; then
  ok "${mem_gb} GB memory"
else
  warn "${mem_gb} GB memory; the stated minimum is ${MIN_MEM_GB} GB."
  note "Postgres is configured with shared_buffers=2GB and the embedder loads a"
  note "2.3 GB model. Under 8 GB, expect the OOM killer during the first crawl —"
  note "lower shared_buffers, or run the embedder on another machine."
fi

# The data root, not the repo: raw PDFs and HTML are what grow, and they land
# wherever DATA_ROOT points — commonly a different disk from the checkout.
DATA_ROOT="${DATA_ROOT:-./.devdata}"
probe="$DATA_ROOT"
while [ ! -d "$probe" ] && [ "$probe" != "/" ] && [ "$probe" != "." ]; do
  probe="$(dirname "$probe")"
done

if disk_gb="$(df -BG --output=avail "$probe" 2>/dev/null | tail -1 | tr -dc '0-9')" \
   && [ -n "$disk_gb" ]; then
  if [ "$disk_gb" -ge "$MIN_DISK_GB" ]; then
    ok "${disk_gb} GB free on $(readlink -f "$probe")"
  else
    warn "${disk_gb} GB free on $(readlink -f "$probe"); the stated minimum is ${MIN_DISK_GB} GB."
    note "Text, embeddings and the graph stay in single-digit GB, but retained raw"
    note "files reach 50–150 GB for a 50k-document corpus. Retention is tiered and"
    note "configurable — see config/retention.yaml — so a smaller disk is workable."
  fi
else
  warn "Could not determine free space for ${DATA_ROOT}."
fi

# --- native extras, needed for development rather than for running ------------
# Not fatal and not even a warning by default: the containers carry their own
# copies. It matters when running the worker natively via `uv`, which is what
# §6 of the scaffold says development looks like.
for tool in pdftoppm:poppler-utils gs:ghostscript; do
  bin="${tool%%:*}"; pkg="${tool##*:}"
  if command -v "$bin" >/dev/null 2>&1; then
    ok "${pkg}"
  else
    [ "$QUIET" = 1 ] || note "${pkg} absent — only needed to run the worker natively, not in Docker."
  fi
done

# --- verdict -----------------------------------------------------------------
echo ""
if [ "$fatal" -gt 0 ]; then
  echo "meridian: ${fatal} blocking problem(s). Nothing will start until these are fixed." >&2
  exit 1
fi

if [ "$warned" -gt 0 ]; then
  echo "meridian: ${warned} warning(s) — below the stated minimums, but nothing is stopping you."
  exit 0
fi

[ "$QUIET" = 1 ] || echo "meridian: this machine meets the stated minimums."
