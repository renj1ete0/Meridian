# Changelog

All notable changes to Meridian. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is `MAJOR.MINOR.PATCH` as defined in [AGENTS.md](AGENTS.md#versioning).

The version in [`VERSION`](VERSION) is the single source of truth. Documentation and
design-only changes do not require a version bump, but may be listed under Unreleased.

## [Unreleased]

Nothing yet.

## [0.5.1] — 2026-09-06

**Phase 0 closed.** From an empty database, `make migrate && make seed` applies
six migrations and yields 21 tables with configuration populated and every
content table empty — the checkpoint the roadmap set. 116 tests pass.

`P0-15`, the held-out question set, is deferred by decision: §14.1 uses it to
measure whether the graph improves month to month, and there is nothing to
measure until a corpus exists. It is needed before the phase 2 go/no-go, not
before phase 1.

### Added

- `P0-16` Cold-start seeds — 8 authority roots and 5 query seeds
- `P0-17` Gazetteer at 64 terms, jurisdiction-scoped, 20 ambiguous surface forms
  flagged

## [0.3.0] — 2026-09-06

The §14.3 design exercise, and the schema changes it forced. Ten questions
traced against the schema before any content exists — five were unanswerable.
Full write-up in [docs/design-questions.md](docs/design-questions.md).

### Added

- `P0-14` The ten questions, each traced against the schema with its verdict
- `P0-20` `observations` — a measured quantity attached to an entity: metric,
  value, unit, denominator, geography, period, method, and a JSONB `qualifiers`
  column (GIN-indexed) for open-ended breakdowns like user segment or time of
  day. Keyed to one subject so a time series accumulates against a stable node
  rather than spawning near-identical entities for resolution to mis-merge
- `edges.valid_from` / `valid_to` — when the fact held, distinct from when it
  was recorded, derived, or published
- `edges.similarity_dimension` / `disanalogy`, with a CHECK rejecting any
  `comparable_to` edge missing either. §7.2 requires both; enforcing it in
  Postgres rather than in a prompt follows §2 principle 6
- Host ports moved to a distinctive `211xx` block, with the ingress/egress
  split documented — production publishes exactly one port, on loopback

### Fixed

- `P0-21` Alembic autogenerate does not detect a `CheckConstraint` added to an
  existing table, and `alembic check` shares that blind spot. Two edge
  constraints existed only in the model. Hand-written into the migration, and a
  drift test now compares `Base.metadata` against `pg_constraint`
- That drift test initially passed while the constraint it guarded was absent:
  the module imported `Base` but not the models, so it compared against an empty
  metadata. It now imports the models and asserts the expected set is non-empty
  before comparing — a guard that cannot fail is worse than no guard
- Local-model config no longer reserves a port. Meridian is the client there,
  not the server; the tier stays optional and disabled
- Stale README claims corrected — the core is built and tested, not unwritten

## [0.2.0] — 2026-09-06

**Phase 0 complete.** `make migrate && make seed` yields a clean, empty database
ready to crawl, with 84 tests passing against a real Postgres.

### Added

- `P0-04` `meridian_core` database layer: lazy async engines per role, session
  scopes, declarative `Base` with a constraint naming convention
- `P0-05`–`P0-09` SQLAlchemy models — 18 tables covering the queue, sources,
  chunks, figures, entities, edges, attributes, gazetteer, topic config, fetch
  policy, agent registry, runs, enrichment, reports and notifications
- `P0-10` Pydantic DTOs, 60 exports, with enum values derived from the models'
  own CHECK constraints so the two cannot drift
- `P0-11` Alembic, wired to run as the database owner
- `P0-12` `scripts/seed.py` — idempotent, configuration only, never overwrites
- `P0-13` Structured JSON logging with contextvar-scoped `run_id`
- `P0-19` 84 tests: drift, rejection and completeness, against real Postgres
- Testing conventions in AGENTS.md; frontend chosen as React + TypeScript + Tailwind

### Fixed

- `P0-18` Role bootstrap never ran — psql variable syntax in a `.sql` file the
  entrypoint cannot bind. Now a shell script; scaffold doc §4 corrected
- Default privileges declared for both writers, so the read-only role keeps
  SELECT regardless of which role created a table
- `constrained()` omitted `create_constraint`, so every status column was an
  unchecked VARCHAR accepting any string
- 35 NOT NULL columns had Python-side defaults only, failing any non-ORM insert
- pgvector enabled at first boot; dev Postgres moved to a named volume

## [0.1.1] — 2026-09-06

### Added

- `P0-04` `meridian_core` package with the database layer: lazily-created async
  engines for both roles, session scopes, `check_connection`, and a declarative
  `Base` carrying a constraint naming convention so Alembic autogenerate produces
  deterministic migration names
- uv workspace at the repository root; services join `members` as they gain a
  `pyproject.toml`
- Backlog `B-05`–`B-09`: making Meridian runnable locally by someone who isn't
  developing it

### Fixed

- `P0-18` Role bootstrap never ran. `scripts/init-roles.sql` used psql variable
  syntax (`:'rw_password'`), but the Postgres entrypoint executes `.sql` files
  with no variable bindings — the script errored and the container died during
  init. Replaced with `scripts/init-roles.sh`, taking passwords from the
  environment. The same broken snippet is corrected in scaffold doc §4
- Default privileges are now declared for both writers. They only cover objects
  created by the role that declared them, so a table created by `meridian_rw`
  left `meridian_ro` without `SELECT` — caught by testing the roles against a
  real database rather than assuming
- `pgvector` is enabled at first boot
- Dev Postgres uses a named volume instead of a bind mount; the previous bind
  mount left a root-owned `pgdata` in the working tree that couldn't be removed
  without a container

### Changed

- `.env.example` gained `PG_RW_PASSWORD`, `PG_RO_PASSWORD`, `PG_MIGRATION_URL`,
  and commented pooling knobs

## [0.1.0] — 2026-09-06

First tagged state. Design and scaffold complete; no application code yet.

### Added

- Architecture specification and project scaffold document (`docs/spec/`)
- Repository scaffold: directory layout, `docker-compose.yml` (production) and
  `docker-compose.dev.yml` (infra-only development), `Makefile`, `.env.example`
- First-boot configuration seeds in `config/` — topics and weights, attribute schema,
  fetch policy, source-tier map, agent registry, gazetteer, SearXNG settings
- Database role bootstrap (`scripts/init-roles.sql`): `meridian_rw` / `meridian_ro`
- Brand and design system: logo mark, colour tokens for both themes, typography
  (Archivo + IBM Plex Mono), voice guide, contested-claim dagger glyph, and UI
  mockups for Explore, Admin, About, and the synthesis panel (`docs/design/`)
- Brand assets as PNG in four combinations — light and dark ink, with and without
  background — for lockup and icon (`docs/design/assets/`)
- `README.md`, `docs/roadmap.md`, `TASKS.md`, `CHANGELOG.md`, MIT `LICENSE`
- `AGENTS.md` conventions for anyone writing code in this repository

### Specification changes

- §10.2 — adding and archiving topics are now explicit permanent interventions;
  `topic_config.status` gained `archived`. Archiving stops seeding and leaves the
  weight pool but deletes nothing
- §11.13 — new section: user-triggered report generation (drafting jobs), scope-bound,
  with a coverage pre-flight that warns when evidence is thin or stale before spending
- §11.6, §13.2 — added `submit_report_job`, `add_topic`, `archive_topic`
- §12.5 — added report generation and a filterable notifications panel to required
  interface features

[Unreleased]: https://github.com/renj1ete0/meridian/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/renj1ete0/meridian/releases/tag/v0.1.0
