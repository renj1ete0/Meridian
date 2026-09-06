# Meridian — build conventions

Read `docs/spec/autonomous-research-system-spec.md` and
`docs/spec/meridian-project-scaffold.md` before writing code. Section references
below (§n) point to the architecture spec unless marked "(scaffold)".

## Invariants (do not violate)

- The worker NEVER calls an LLM. Ingestion must run with every reasoning model offline (§2.1).
- All writes validate server-side. Never trust model output for structure (§2.6, §11.8).
- Every edge, tag and attribute carries provenance: source chunk, producing agent,
  model, quality_tier, schema_version (§2.3, §11.12).
- Re-derive from source chunks, never from prior model output (§2.4).
- Steering adjusts generation, never deletes (§2.5, §10).
- MarkItDown: use `convert_local()` or `convert_stream()` only. Never `convert()`
  on a URL (§6.6).
- Credentials come from environment variables, never from the database (§11.11).
- Quality tier only moves up automatically; a lower tier never silently overwrites
  a higher one (§11.12).

## Layout (scaffold §2)

- Anything touching the database lives in `packages/meridian_core`.
- Services import from `meridian_core`; they never define their own models.
- Single writer per concern: worker owns ingestion tables, orchestrator owns graph
  writes, API is read-only except through `/api/admin/*`.
- Explore routes (`/api/explore/*`) use the read-only DB role; admin routes
  (`/api/admin/*`) use read-write (scaffold §4, §12.6).

## Style

- Type hints throughout; pydantic for all boundaries.
- Alembic for every schema change — no manual DDL.
- Structured logging; every run logs `run_id`.
- No `platform:` keys in compose; images are multi-arch (scaffold §5).

## Testing

**Every new function ships with a test, frontend and backend.** Not as ceremony —
as the only thing that catches the failures this codebase actually produces.

**Write the test first when the spec already says what should happen.** The
architecture spec states its invariants precisely, so for anything it covers —
provenance is mandatory, quality tier only moves up, the read-only role cannot
write, steering never destroys — the test is a transcription of a spec section and
belongs before the implementation. Cite the section in the test docstring. For
exploratory or mechanical code the spec doesn't cover, test-after is fine; don't
perform TDD ritual where the design isn't known yet.

**What counts as a test here.** A single `assert result is True` does not. Prefer,
in rough order of value:

1. **Drift tests** — compare two sources of truth and fail when they disagree:
   models vs the migration, DTO enums vs database CHECK constraints, model columns
   vs schema fields. Never hardcode the expected value set; a test that needs
   editing whenever the schema legitimately grows will be edited into passing.
2. **Rejection tests** — assert invalid input is actually *refused*. That valid
   input works is the weaker half. Three Phase 0 bugs were "the constraint exists"
   assumptions that turned out to be false.
3. **Real dependencies** — integration tests run against a real Postgres, never
   SQLite or a mock. A role bootstrap that never ran, CHECK constraints that were
   never created, and default privileges that silently denied reads were all
   invisible to anything else.
4. **Completeness probes** — assert that a new column is exposed, a new enum has a
   DTO alias, a new artifact table carries full provenance. These catch the
   *absent*, which is what review misses.

**Run `make test` before committing.** A task is done when it runs, not when it
compiles (see [TASKS.md](TASKS.md)).

## Config

- `config/*.yaml` seeds the database at first boot only (`make seed`). The DB is
  authoritative thereafter — don't add code paths that re-read these files at
  runtime (spec §13.1, scaffold §1.6).
- Production always starts empty. Never add a fixture-loading path to the
  production seed script (scaffold §1.7, §6).

## Task tracking

[TASKS.md](TASKS.md) at the repo root is the source of truth for what to build next.

- Read it at the start of a session; update it at the end of one.
- Tick a task (`[x]`) only when it *runs*, not when it compiles.
- Reference task IDs (`P1-04`) in commit messages.
- Tasks marked **⚑ human** need a judgment call — don't complete them autonomously.
- Add new tasks freely; never renumber existing ones.

## Versioning

`MAJOR.MINOR.PATCH`, no zero-padding. [`VERSION`](VERSION) at the repo root is the
single source of truth; service `pyproject.toml` files and `web/package.json` mirror it.

| Bump | When |
|---|---|
| **MAJOR** | A breaking change: data model or migration that isn't backward compatible, a changed or removed MCP tool contract, a config schema that invalidates existing rows |
| **MINOR** | A new capability that hangs together as a feature — a new MCP tool, a new UI surface, a new pipeline stage, a new task type |
| **PATCH** | Bugfixes, and small standalone improvements that aren't part of a larger feature group |

Rules:

- Bump on every change to application code, in the same commit as the change.
- Add a [CHANGELOG.md](CHANGELOG.md) entry under the new version, with the task ID.
- Documentation, spec, and design-only changes don't need a bump — note them under
  `Unreleased` if they're worth recording.
- Tag releases `v0.1.0`. While `MAJOR` is `0`, breaking changes bump `MINOR`.
- Do **not** zero-pad (`0.01.00`): SemVer forbids leading zeros in numeric identifiers,
  npm rejects them outright, and Python packaging silently normalises them away — so a
  padded version would not survive contact with the tooling.

## When unsure

Check the architecture spec section referenced in the module docstring. Ask
rather than inventing schema.
