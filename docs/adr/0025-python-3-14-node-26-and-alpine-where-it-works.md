# 0025. Python 3.14, Node 26, and Alpine wherever the dependencies allow it

- **Status:** Accepted
- **Date:** 2026-11-01
- **Tasks:** `B-266`

## Context

The images were built on `python:3.12-slim-bookworm` and `node:22-slim`, and nginx was a 1.27
build whose Alpine base had not been refreshed in over a year. CI already ran the suite on
Python 3.14 and it passed, while the images ran 3.12, so the interpreter that was tested was not
the one that shipped. The operator asked for the dependencies to be brought current, for
Python 3.14, for Node 26 (the active LTS line since October 2026), and for Alpine in every image
whose dependencies install cleanly on musl, keeping Debian only where Alpine genuinely fails.

## Decision

- Every Python package declares `requires-python >= 3.14`; `.python-version`, ruff's target,
  CI and every Python image use 3.14.
- The web build stage is `node:26-alpine` and CI uses Node 26.
- An image is Alpine when every wheel it installs is published for musllinux on 3.14, so
  nothing compiles during the build. That holds for the API, the tools image, the web image
  (nginx) and the `datadirs` helper. It does not hold for the worker (torch and onnxruntime
  publish no musl wheels) or the orchestrator (onnxruntime, through markitdown's file
  sniffing, has neither a musl wheel nor a source distribution). Those two use
  `python:3.14-slim-trixie`, the current Debian stable.
- The database image stays on Debian bookworm, pinned explicitly, until a reindex is planned:
  moving Postgres to trixie changes the glibc collation version under existing text indexes.

The per-image table, with sizes and the reason for each base, is
[docs/reference/base-images.md](../reference/base-images.md).

## Consequences

- One interpreter from development to production.
- ruff's 3.14 target writes `except A, B:` without parentheses (PEP 758). That is the
  formatter's output, not a style anyone chose, and it parses only on 3.14 or later.
- The API and tools images are about a quarter smaller (most of what is left is numpy and
  the other wheels) and carry far fewer OS packages for a scanner to flag. musl's DNS
  resolver and allocator differ from glibc's; the API and tools images were started and
  exercised before this was accepted, and a problem that appears only there is a reason to
  move that image back, not to patch around musl.
- A dependency that drops its musl wheel, or a new dependency without one, fails the API or
  tools build. The fix is to move that image to `slim-trixie`, recorded in the table.

## Alternatives considered

- **Alpine for every image.** The worker and orchestrator would have to compile torch or
  onnxruntime from source, for hours, on every architecture, or could not build at all.
- **Debian everywhere.** Simpler, but the operator prefers Alpine, and for the API and tools
  there is no technical cost to it.
- **Python 3.15.** Released, but torch's CUDA wheels and triton are marked below 3.15.
