#!/usr/bin/env bash
# Point the `stable` channel at a commit that was already built and pushed (`P3-12`).
#
#   ./scripts/promote.sh <sha>             # every image
#   ./scripts/promote.sh <sha> worker api  # only these
#   ./scripts/promote.sh --dry-run <sha>
#
# `build_and_push.sh` tags by commit and never tags a moving name, so that a bad
# build cannot roll itself out. Watchtower needs a moving name to follow. This is
# where the two meet: promoting is the deliberate act, and it is the only thing
# that changes what the servers run. Rollback is promoting the previous SHA.
#
# No rebuild and no pull: `imagetools create` writes a new tag onto the existing
# multi-arch manifest in the registry, so `stable` is byte-for-byte what was built.

set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRY="${MERIDIAN_REGISTRY:-ghcr.io/renj1ete0}"
CHANNEL="${MERIDIAN_CHANNEL:-stable}"
ALL=(worker api orchestrator web tools postgres crawl4ai)

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; shift; fi
SHA="${1:?usage: promote.sh [--dry-run] <sha> [image …]}"
shift
WANTED=("$@")
[ ${#WANTED[@]} -eq 0 ] && WANTED=("${ALL[@]}")

promoted=()
missing=()
for name in "${WANTED[@]}"; do
  src="${REGISTRY}/meridian-${name}:${SHA}"
  dst="${REGISTRY}/meridian-${name}:${CHANNEL}"
  if [ "$DRY_RUN" = 1 ]; then
    echo "  docker buildx imagetools create -t ${dst} ${src}"
    continue
  fi
  # A missing source is a skip, not a failure: not every commit rebuilds every
  # image, and promoting the ones that exist is still a coherent release — the
  # others stay on whatever `stable` already names.
  if ! docker buildx imagetools inspect "$src" >/dev/null 2>&1; then
    missing+=("$name")
    continue
  fi
  docker buildx imagetools create -t "$dst" "$src"
  promoted+=("$name")
done

[ "$DRY_RUN" = 1 ] && exit 0
echo "meridian: ${CHANNEL} → ${SHA}"
[ ${#promoted[@]} -gt 0 ] && printf '  promoted  %s\n' "${promoted[@]}"
[ ${#missing[@]} -gt 0 ] && printf '  not built %s (left as they were)\n' "${missing[@]}"
echo "  Servers running Watchtower pick this up within WATCHTOWER_POLL_INTERVAL."
[ ${#promoted[@]} -gt 0 ]
