# 0008. Code is published to GitHub; images wait

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** —

## Context

Commits can be pushed to GitHub, and container images can be published to GHCR for servers
to pull. Images on the `stable` channel are picked up automatically by Watchtower.

## Decision

Finished work is pushed to GitHub. Images are not published to GHCR until the operator
says so, which will be when the server deployment is ready.

## Consequences

- Nothing reaches a running server by accident before it has been prepared.
- When publishing starts, images are built from the GitHub `main` that has already been
  reviewed.
