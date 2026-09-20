#!/usr/bin/env bash
# Cross-build the application images and push one multi-arch manifest each.
# Task P1-37, scaffold §5 ("Architecture: arm64 production, x86_64 development").
#
# Production is an arm64 Pi; development is an x86_64 box. Scaffold §5's rule is
# "build on x86, verify on arm64, never develop under emulation" — QEMU is for
# confirming an image builds and starts, and is far too slow to iterate in. So
# this runs on the Fedora box and produces manifests that `docker compose pull`
# resolves correctly on both machines with no tag juggling.
#
#   ./scripts/build_and_push.sh              # build and push every image
#   ./scripts/build_and_push.sh --dry-run    # print the commands, touch nothing
#   ./scripts/build_and_push.sh worker api   # only these
#
# Two things this deliberately refuses to do, both because of what the SHA tag
# is *for*. Scaffold §5: "Tag by commit SHA, not `latest`. Pinning the SHA in
# docker-compose.yml means a bad build does not roll out on the next restart,
# and rollback is a one-line edit."
#
#   1. It will not push from a dirty working tree. A tag naming a commit whose
#      code is not what was built is worse than no tag: rollback-by-SHA becomes
#      a guess, and the guess is only discovered to be wrong while rolling back,
#      which is the one moment nobody has time for it.
#   2. It never tags `latest`. An unattended system that pulls `latest` removes
#      exactly the control the SHA was giving.

set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRY="${MERIDIAN_REGISTRY:-ghcr.io/renj1ete0}"
PLATFORMS="${MERIDIAN_PLATFORMS:-linux/amd64,linux/arm64}"

# The four images scaffold §5 names, and where each one is built from. An image
# whose Dockerfile does not exist yet is *skipped loudly* rather than silently
# or fatally: `orchestrator` is phase 4 and `web` is served by the API for now,
# so a release today is a real release of the parts that exist — but a script
# that quietly shipped two of four would be indistinguishable from one that
# shipped all four.
#
# name|dockerfile|build context
IMAGES=(
  "worker|services/worker/Dockerfile|."
  "api|services/api/Dockerfile|."
  "orchestrator|services/orchestrator/Dockerfile|."
  "web|web/Dockerfile|web"
  # Migrations and the config seed (`B-17`). Not an application service, but it
  # is the first thing run on a new server and the server does not build — so a
  # tools image that only exists on somebody's laptop is a deploy that stops at
  # `alembic upgrade head`.
  "tools|deploy/tools/Dockerfile|."
)

DRY_RUN=0
WANTED=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "meridian: unknown option $arg" >&2; exit 2 ;;
    *) WANTED+=("$arg") ;;
  esac
done

run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '  %s\n' "$*"
  else
    "$@"
  fi
}

# --- the tag -----------------------------------------------------------------
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "meridian: not a git repository, so there is no commit to tag by." >&2
  exit 1
fi

TAG="$(git rev-parse --short HEAD)"

if [ -n "$(git status --porcelain)" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "meridian: NOTE — the working tree is dirty; a real run would refuse here." >&2
  else
    echo "meridian: refusing to push — the working tree has uncommitted changes." >&2
    echo "  Images are tagged by commit SHA so that rollback is a one-line edit to" >&2
    echo "  docker-compose.yml (scaffold §5). A tag naming $TAG while the build" >&2
    echo "  contains something else makes that tag a lie, and the lie is found" >&2
    echo "  while rolling back." >&2
    echo "  Commit, stash, or use --dry-run." >&2
    exit 1
  fi
fi

# Not fatal: pushing an image before its commit is worth a warning and not a
# refusal, because the commit can be pushed afterwards. But an image whose
# source was never published cannot be rebuilt by anyone else, which is the
# state this warns about while it is still cheap to fix.
if ! git branch --remotes --contains HEAD >/dev/null 2>&1 \
   || [ -z "$(git branch --remotes --contains HEAD 2>/dev/null)" ]; then
  echo "meridian: WARNING — $TAG is not on any remote branch." >&2
  echo "  The image will be pullable and its source will not. Push the commit." >&2
fi

# --- the tools ---------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
  echo "meridian: the Docker daemon is not reachable." >&2
  echo "  Check it is running, and that DOCKER_HOST points at its socket" >&2
  echo "  (rootless installs commonly need DOCKER_HOST=unix:///var/run/docker.sock)." >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "meridian: docker buildx is not available, and a multi-arch manifest needs it." >&2
  echo "  Install the buildx plugin, or build natively on each machine instead." >&2
  exit 1
fi

# A multi-platform build needs a builder that is not the default `docker` driver
# — that one can only produce images for the host's own architecture, and the
# error it gives when asked for two ("docker exporter does not currently support
# exporting manifest lists") names neither the cause nor the fix.
if [ "$DRY_RUN" = 0 ]; then
  driver="$(docker buildx inspect --bootstrap 2>/dev/null | awk '/^Driver:/ {print $2}')"
  if [ "$driver" = "docker" ]; then
    echo "meridian: the active buildx builder uses the 'docker' driver, which cannot" >&2
    echo "  produce a multi-arch manifest. Create one that can:" >&2
    echo "" >&2
    echo "    docker buildx create --name meridian --driver docker-container --use" >&2
    echo "    docker run --privileged --rm tonistiigi/binfmt --install arm64" >&2
    echo "" >&2
    echo "  The second line registers the QEMU handler that lets an x86 box emit" >&2
    echo "  arm64 layers (scaffold §5: build on x86, verify on arm64)." >&2
    exit 1
  fi
fi

# --- build -------------------------------------------------------------------
echo "meridian: building ${REGISTRY}/meridian-*:${TAG} for ${PLATFORMS}"
[ "$DRY_RUN" = 1 ] && echo "  (dry run — nothing will be built or pushed)"

built=()
skipped=()

for entry in "${IMAGES[@]}"; do
  IFS='|' read -r name dockerfile context <<<"$entry"

  if [ ${#WANTED[@]} -gt 0 ]; then
    case " ${WANTED[*]} " in *" $name "*) ;; *) continue ;; esac
  fi

  if [ ! -f "$dockerfile" ]; then
    skipped+=("$name ($dockerfile does not exist yet)")
    continue
  fi

  image="${REGISTRY}/meridian-${name}:${TAG}"
  echo "meridian: ${name} → ${image}"
  run docker buildx build \
    --platform "$PLATFORMS" \
    -f "$dockerfile" \
    -t "$image" \
    --push \
    "$context"
  built+=("$image")
done

# --- what happened -----------------------------------------------------------
echo ""
if [ ${#built[@]} -gt 0 ]; then
  [ "$DRY_RUN" = 1 ] && echo "meridian: would push" || echo "meridian: pushed"
  printf '  %s\n' "${built[@]}"
  echo ""
  echo "  Pin these in docker-compose.yml on the Pi, then:"
  echo "    docker compose pull && docker compose up -d"
fi

if [ ${#skipped[@]} -gt 0 ]; then
  echo "meridian: skipped (no Dockerfile yet)"
  printf '  %s\n' "${skipped[@]}"
fi

if [ ${#built[@]} -eq 0 ]; then
  echo "meridian: nothing was built." >&2
  exit 1
fi
