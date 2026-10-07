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

## Code and comment standards

Code follows the recognised standard for its language. `make lint` enforces what a linter
can, and runs as the first step of `make test`.

**Python**

- **PEP 8** for layout and naming (ruff `E`, `W`, `N`; `ruff format`), with a line length
  of 100.
- **PEP 257** for docstrings (ruff `D2xx`–`D4xx`, convention `pep257`). The summary is one
  line, ends in `.` or `?`, and is followed by a blank line before any detail. A summary may
  be a noun phrase ("Whether a passage is in its sample.") or a command (`D401` is off for
  that reason).
- **PEP 484 / 604 / 695** type hints on every public function, `X | None` rather than
  `Optional`, and type parameters (`class Kept[T]:`) rather than `Generic[T]`.
- Exceptions are named for what happened (`NotConfigured`, `QueryRefused`), with no
  required `Error` suffix (`N818` is off).
- Tests and migrations are exempt from the docstring rules: test docstrings explain a
  scenario, and Alembic writes its own.

**TypeScript**

- `strict` TypeScript (`tsc -b`), React function components, no `any` without a comment
  saying why.
- **oxlint** (`web/.oxlintrc.json`) and **Prettier** (`web/.prettierrc.json`: no semicolons,
  single quotes, 100 columns), both in `make lint` (ADR 0012). `npm run format` rewrites;
  an `eslint-disable-next-line` needs a comment above it saying why.
- **TSDoc** (`/** … */`) on exported components, hooks and functions: one summary
  sentence, then `@param` / `@returns` where they add something the types do not.

**Dates and times** (ADR 0009)

- Store instants as `timestamptz` (UTC) and send them as ISO 8601 with an offset. Never store
  local time, and never create a naive `datetime` for an instant.
- A calendar fact with no time of day (a publication date) is a `date` and is never shifted.
- Format for people only through `meridian_core.timefmt` (server) and `web/src/lib/time.ts`
  (web), which use the deployment's display zone and label the offset.

**Comments and docstrings: documentation lives in `docs/`**

- A docstring says what a thing is or does, and anything a caller must know: units, side
  effects, what it refuses. A few lines, not an essay.
- A comment says *why* a line is written the way it is, when the code cannot say it
  itself. One to three lines.
- The rationale, the history, the measurements and the traps belong in the feature's doc
  (`docs/features/`), the decision record (`docs/adr/`), or the handover. The code links to
  them: `# See docs/features/embedding.md#tiers.`
- Task IDs (`B-127`) in a comment are fine as pointers; the story behind them goes in the
  docs.
- When you change a file that still carries essay comments, move the narrative into its
  feature doc in the same commit. `D-01` cleared the backlog; a pointer into `docs/` must resolve
  (`tests/unit/test_doc_pointers.py`).

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

## Documentation

Everything a person needs to understand, run or change the system lives in `docs/`. The
map is [docs/README.md](docs/README.md). It follows the Diátaxis split (tutorials, how-to
guides, reference, explanation) plus decision records:

| Folder | Answers | Write one when |
|---|---|---|
| `docs/features/` | What a feature does, how it works, how to configure, operate and debug it | You build or change a feature. **Every feature has one.** |
| `docs/guides/` | How to do a task, step by step (set up, deploy, connect an assistant) | A task has more than two steps that someone will repeat |
| `docs/reference/` | Exact facts: environment variables, commands, scheduled jobs, MCP tools, API | You add a variable, command, job, tool or route |
| `docs/adr/` | What was decided, why, and at what cost | The operator makes a decision. Rephrase it; never quote them |
| `docs/spec/` | The architecture as designed | The design itself changes |
| `docs/design/` | What the interface looks like (mocks) | A new reader surface is proposed, before it is built |

A feature doc uses the template in `docs/features/README.md`. Update it in the same commit
as the code it describes. Documentation-only commits need no version bump (see
[Versioning](#versioning)).

[docs/handover.md](docs/handover.md) is the companion to `TASKS.md`, and answers a different
question: not what to build, but how the built parts fit together, which traps have
already cost someone a session, and what has been verified against the real web
rather than only against tests. Read it too at the start of a session, and add to it
whenever you learn something the next person would rather not rediscover.

## Commits

**One task per commit, one version per commit.** A commit answers "what changed
and why" for exactly one unit of work, and its message is the only place that
answer survives — a diff shows what changed and never why.

- **Never** batch several tasks into one commit. `P1-43` and `P1-44` may ship
  together *only* because one exists to diagnose the other; two unrelated tasks
  in one commit means neither can be reverted, and the message has to hedge
  about both.
- One version bump per commit, in the same commit as the change it describes
  (see [Versioning](#versioning)). A commit that bumps `VERSION` without
  changing behaviour, or changes behaviour without bumping, breaks the
  correspondence the changelog depends on.
- Documentation-only work is its own commit, and needs no bump.
- **Title:** the task ID, a colon, and what changed in plain words — not a
  restatement of the ID. `P2-06: the corpus becomes searchable (v0.30.0)`.
  Multiple IDs only when they are genuinely one change.
- **Body:** why, not what. The reasoning that is not recoverable from the diff —
  what was considered and rejected, what premise turned out to be wrong, what
  trade-off was accepted and at what cost. If the body only restates the diff,
  it is not finished.
- If a change is hard to describe in one title, it is usually two commits.

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
