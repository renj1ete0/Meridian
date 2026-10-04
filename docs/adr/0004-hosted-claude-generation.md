# 0004. Hosted Claude rows use the current generation at high effort

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-134`

## Context

The seeded hosted rows named the previous Opus and Sonnet. The current Opus costs less
per token. It decides its own reasoning depth, with a default one level lower than its
predecessor's. Graph-building is reasoning-heavy, and a missed or wrong relation costs
more than the tokens saved.

## Decision

The hosted rows use `claude-opus-5-5` (graph-building) and `claude-sonnet-5-5` (tagging).
Requests set effort to `high` explicitly, so that depth does not silently drop with the
model's default.

## Consequences

- A fresh install is seeded with the new models. An existing database is updated through
  its registry rows, which is where the model is authoritative once seeded.
- No edge has been written by a hosted model yet, so nothing needs to be re-derived.
- Effort becomes a setting that can be changed if cost or quality says it should move.

## Alternatives considered

- **The model's default effort (`medium`).** Cheaper per run; rejected for the
  reasoning-heavy stages.
- **Keep the previous generation.** It costs more, and gains nothing.
