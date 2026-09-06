# Changelog

All notable changes to Meridian. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is `MAJOR.MINOR.PATCH` as defined in [AGENTS.md](AGENTS.md#versioning).

The version in [`VERSION`](VERSION) is the single source of truth. Documentation and
design-only changes do not require a version bump, but may be listed under Unreleased.

## [Unreleased]

## [0.11.0] — 2026-09-07

**The crawl now keeps a record of itself.** Every fetch has always returned a
valid `fetch_attempts.outcome` and nothing had ever written one, so the only
trace a crawl left was the most recent error on each queue row. §12.5 asks for a
daily health line for a specific reason — *"without this the Pi can crawl 404s
for a week unnoticed"* — and that line needs a log, not a counter. The same
outcome now also reaches the policy that governs the next request to the domain.

### Added

- `P1-19` `meridian_core/attempts.py` — the fetch attempt log. `record_attempt()`
  writes one row per fetch on every path including the refusals that never
  touched the network, `fetch_health()` derives §12.5's success rate and a
  breakdown by outcome from it, and `prune_attempts()` keeps a table with one row
  per request bounded, deleting in batches so a prune skipped for a month is
  still a series of statements rather than one that locks the table
- `FetchHealth` DTO. `success_rate` is `None` rather than `0.0` when nothing was
  attempted: a crawler that fetched nothing and a crawler where everything failed
  need different responses, and 0% reports the first as the second
- `P1-05` `domain_signal()` and `apply_fetch_outcome()` in `policy.py` — what a
  fetch outcome says about the *domain*, which is a different question from what
  it says about the URL. Three answers, not two: the domain answered (a 404, an
  oversized file, a rejected media type — evidence it is alive, so the counter
  resets), the domain is unreachable (timeouts, 5xx, 429, redirect loops, a
  decompression bomb, an address `netguard` refuses), or no request went out at
  all (robots denial, an already-blocked domain — no evidence either way).
  Counting a robots denial as a failure would auto-block every well-behaved site
  with a restrictive robots.txt

### Changed

- `Crawler.fetch` records the attempt and applies its consequence before
  returning, rather than leaving both to the worker loop. A log that depends on
  each caller remembering to write it has holes in exactly the paths nobody
  thought about
- A newly blocked domain is dropped from the rate limiter's state. A crawl that
  runs for weeks would otherwise keep a semaphore per domain it ever touched,
  including the ones it will never fetch again

### Documentation

- `docs/handover.md` — how the built parts fit together, the traps already
  discovered (Alembic and CHECK constraints, `urllib.robotparser`'s version
  dependence, httpx decoding a whole network read at once, TEST-NET addresses
  being non-global, Crawl4AI's loopback bind), and what has been verified against
  the real web rather than only against tests. Linked from AGENTS.md and README
- README's getting-started no longer claims `services/` is unimplemented

## [0.10.0] — 2026-09-06

**Politeness.** The fetcher could get bytes; now it knows whether it should, how
often, and what it already has. Verified live: arxiv.org's `Crawl-delay: 15` is
honoured with real 15-second gaps, and a second fetch of an unchanged page comes
back 304 with no body.

### Added

- `P1-04` `worker/robots.py`, `worker/ratelimit.py` and `worker/crawl.py` —
  robots.txt, per-domain concurrency and delay, and conditional requests, with
  `Crawler` composing them in cheapest-refusal-first order: the policy row (no
  network), then robots.txt (one cached request per origin per day), then the
  domain's delay, then the request itself
- `sources.etag` and `sources.last_modified`. `last_modified` is Text rather
  than a timestamp deliberately — the header is compared by the origin as an
  opaque string, and parsing it to a datetime and formatting it back would
  re-serialise the server's own wording, so a strict origin would quietly stop
  returning 304 and the cheapest request in the crawl would become the most
  expensive one

### Notes

- **robots.txt is parsed here rather than by `urllib.robotparser`.** The stdlib
  parser was rewritten for RFC 9309 in Python 3.13; before that it had no
  wildcard support and returned the first matching rule rather than the longest.
  On the two most ordinary patterns in a real robots.txt it gives *opposite*
  answers across the versions this project supports:

  | | 3.12 | 3.13+ |
  |---|---|---|
  | `Disallow: /*.pdf$` | fetch | refuse |
  | `Allow: /private/notice` over `Disallow: /private/` | refuse | fetch |

  §14.2 states respecting robots.txt as a commitment, and a commitment that
  depends on which interpreter the container shipped is not one. The parser
  implements RFC 9309 §2.2 directly: longest match wins, `Allow` breaks a tie,
  `*` and `$` are the only metacharacters, and a named group suppresses the `*`
  group entirely
- An unreadable robots.txt refuses the origin (RFC 9309 §2.3.1.3). A 4xx means
  the site has stated no exclusions and everything is allowed; a 5xx or timeout
  means permission could not be established, and assuming it would have been
  granted is how a crawler gets banned. The refusal is cached for ten minutes
  rather than a day, so one 503 does not take a domain out of the crawl
- Delay is measured between request *starts*, not completions — a server sees
  arrivals — and the limiter's gate is held across the sleep, because releasing
  it first would let every waiter wake at the same instant and start together,
  which is a burst wearing a delay's clothing

### Fixed

- The `policy`, `resolver` and transport fixtures moved from `tests/unit/` to
  `tests/conftest.py` so the integration suite can use them too

## [0.9.0] — 2026-09-06

**The fetcher.** A URL now becomes bytes, and does so without becoming a way
into the network it runs on. Live against the real internet and a real Crawl4AI
0.9.2, not only against tests.

### Added

- `P1-03` `worker/fetch.py` — `httpx` for static content, Crawl4AI's browser for
  JS-dependent pages, `render_js: auto` deciding per page from a mechanical
  heuristic (no model, per the §2.1 fast-loop invariant). `auto` short-circuits
  on any page that already has a paragraph of visible text, which is what keeps
  §6.4's "don't render every page" constraint from eroding
- `P1-24` **The SSRF TOCTOU gap is closed.** Requests are addressed to the IP
  literal `netguard` validated, with `Host` and TLS SNI set to the original
  hostname, so the certificate is still checked against the name while the
  socket cannot be steered elsewhere by a second DNS answer. Redirects are
  followed by hand — `follow_redirects` is off at the client level, not just per
  request — with every hop re-validated, re-resolved and re-pinned
- `P1-21` Content safeguards: content-type allowlist checked on the headers,
  streaming abort at `max_page_bytes`, and a decompression-ratio cap that
  actually bounds memory. The first implementation did not: letting `httpx`
  decode meant one 64KB network read arrived as a single 67MB object, so the cap
  was checked after the allocation it existed to prevent. The body is now read
  raw and pushed through `zlib` in 1MiB steps with both caps re-checked between
  them — a 200MB gzip bomb costs single-digit megabytes and is refused mid-inflation
- `fetch_attempts.outcome` gains `unsafe_target`, `content_type_rejected`,
  `decompression_bomb` and `too_many_redirects`. Kept distinct rather than
  folded into `blocked`: they diagnose different things, and the §12.5 health
  line is the only place an unattended crawler's problems become visible
- `netguard.check_https_final()` plus named refusal reasons, so the fetcher can
  tell a dead link (`connection_error`) from a hostile target (`unsafe_target`)
  without matching on prose. A drift test asserts the two modules still agree
- `services/worker` as a workspace package, with `httpx` scoped to it rather
  than added to `meridian_core` — an HTTP client has no business in the
  dependency closure the API and orchestrator import for database access

### Fixed

- `docker-compose.dev.yml` published port 21113 for Crawl4AI, which reached
  nothing: since 0.9.0 its entrypoint binds gunicorn to loopback *inside the
  container* unless `CRAWL4AI_API_TOKEN` is set. Now set, and every request
  carries the bearer token
- Migration `28fc51373a89` is hand-written for the reason `P0-21` recorded:
  autogenerate detected the VARCHAR widening and left the CHECK constraint
  alone, which would have produced a column accepting the new outcomes and a
  constraint rejecting all of them

## [0.8.0] — 2026-09-06

### Added

- `P1-20` **SSRF guard** — `meridian_core/netguard.py`. Classification happens
  after DNS resolution, on the addresses, never on the hostname text; every
  redirect hop is re-checked; a name answering with *any* private address is
  refused outright, which is the DNS-rebinding defence; and the integer and
  IPv4-mapped spellings of a loopback address are normalised before judgement.
  Allow-nothing-by-default — an address it cannot classify is refused

## [0.7.0] — 2026-09-06

### Added

- `P1-01` Queue claim/pop — `meridian_core/queueing.py`. `FOR UPDATE SKIP
  LOCKED` so N workers drain the queue without coordination, a lease rather than
  a status flip so a crashed worker's task expires instead of stranding, and
  exponential backoff with full jitter so a recovering domain does not get a
  thundering herd
- `P1-02` Policy resolution — `meridian_core/policy.py`. Per-domain row → global
  `'*'` row → file defaults, shallow merge, plus the consecutive-failure counter
  that blocks a dead domain before it eats weeks of crawl budget

## [0.6.0] — 2026-09-06

### Added

- `P1-12` Source tier assignment — `meridian_core/tiering.py`. Exact match, then
  longest matching suffix pattern, then default; mechanical and pure, never a
  model judgement (§5.2)
- `P1-17` Tier-derived queue priority, so a government search result is fetched
  before a blog without anyone curating a seed list
- `P1-18` Randomised per-domain delay. A fixed interval is both a recognisable
  fingerprint and a way to synchronise bursts across domains

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
