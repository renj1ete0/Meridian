# 0001. Production runs on a server, with an optional GPU

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-129`, `B-130`, `B-131`, `B-132`

## Context

The stack was designed for single-board computers: two ARM boards, one of them given over to
the embedding model (`docs/guides/deploy-sbc.md`). Many defaults were sized for that layout: the
Postgres buffer pool, the embedder's memory limit, a one-passage embedding batch on CPU,
and a crawl that pauses while too many passages wait for a vector.

## Decision

Production is Docker on a single server. A GPU may be added for embedding and for a local
language model. The board layout remains supported but is no longer the reference.

## Consequences

- Sizing moves from compose into `.env`, with defaults that still suit a board
  (`B-132` for Postgres).
- GPU embedding is an override file, not the default, because a host without the NVIDIA
  runtime refuses a container that reserves a device (`deploy/gpu/gpu-embedder.yml`,
  `B-131`). The batch is sized from the card's memory (`B-129`). Batches larger than one
  request are split (`B-130`).
- Limits that existed because the CPU embedder was slow (the backlog ceiling, the triage
  of long documents) should be re-measured on the GPU before they are changed.
- Images stay multi-arch, so the board layout keeps working.

## Alternatives considered

- **Keep the two-board layout as the reference.** Rejected: the embedder was the
  bottleneck on every loop run, and a GPU removes it.
- **Make the GPU the default.** Rejected: it would make a stack without a card fail to
  start.
