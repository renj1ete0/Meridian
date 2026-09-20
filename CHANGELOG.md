# Changelog

All notable changes to Meridian. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is `MAJOR.MINOR.PATCH` as defined in [AGENTS.md](AGENTS.md#versioning).

The version in [`VERSION`](VERSION) is the single source of truth. Documentation and
design-only changes do not require a version bump, but may be listed under Unreleased.

## [Unreleased]

- Docs: `docs/setup.md` — one document from a bare machine to a queryable
  corpus, with a status column per section. Some of the path works and some is
  not built, and instructions for something that does not exist are worse than
  no instructions, so the split is stated rather than implied

- Docs: `docs/deployment.md` — the runbook for putting the stack on the server,
  the bounded smoke run, and `P1-16`. Names four Makefile targets that call
  scripts which do not exist, one of which (`make snapshot-corpus`) is `P1-16`'s
  stated deliverable. New tasks `P1-36`, `P1-37`
- Docs: `docs/connectors.md` — four levels of adding a source, cheapest first,
  ending at the sidecar-container pattern that `crawl4ai` and `searxng` already
  are. Records which of the compose topology rules are enforced generically and
  which are asserted for `crawl4ai` by name, because a new sidecar inherits only
  the first
- Spec: `docs/spec/external-acquisition.md` — consigning URLs the crawler cannot
  fetch to an external actor (a container, or a person with a browser), and
  admitting what comes back through the ordinary pipeline with permanent
  provenance that it was not fetched here. Eligibility is a narrow allowlist;
  `robots_denied` and `unsafe_target` are never eligible, and the spec says why
  the line is absolute. New tasks `P1-38`–`P1-42`
- Spec: `docs/spec/shared-read-access.md` — read-only MCP access for other
  people's models, and the grant model for giving access to people who are not
  the operator. Cloudflare Access answers who is at the door; Meridian answers
  what they may read. New tasks `P3-06`–`P3-11`

- Docs: `TASKS.md` and `docs/handover.md` brought up to date with `v0.21.0`–
  `v0.24.0`. The handover's "what does not exist yet" claimed there were no
  embeddings, and its build-state and test counts were four releases stale
- Docs: `README.md` status block and version badge refreshed. The FAQ still said
  "nothing crawls yet", which stopped being true five releases ago
- Lint: `ruff check .` is green again. Six errors had accumulated in files
  nobody was touching — a long f-string in `fetch.py`, a `try`/`except`/`pass`
  in `topicmatch.py`, and four long lines in tests. No behaviour change; the
  point is that the next real error is visible rather than sixth in a list
- New task `P1-34`: nothing handles `query` rows. SearXNG runs in compose with
  its JSON API enabled and the cold-start seeds ship query seeds, but no handler
  claims them — so when the frontier empties the crawl idles rather than
  searching for more


## [0.76.3] — 2026-09-20

### Fixed

- `B-12` `docker-compose.yml` set `MERIDIAN_EMBEDDER_CACHE`; the code reads
  `MERIDIAN_EMBED_CACHE`. The `/models` volume was therefore never used and
  2.3 GB of weights re-downloaded on every recreate

### A misspelt variable cannot fail loudly

- `os.environ.get(name, default)` exists in order not to raise, so there is no
  runtime mechanism that could have caught this — the embedder simply used the
  library's default cache, on a layer nobody mounted, and worked
- So the test is the mechanism: every variable name any compose file sets must
  appear somewhere in the Python sources, with a short exemption list for the
  ones read by the Postgres entrypoint and the upstream images
- And a second test that every exemption is still set by some compose file, so
  the list cannot become a museum of variables that no longer exist — which is
  how the next real typo would hide

## [0.76.4] — 2026-09-20

### Fixed

- `B-13` Three undeclared imports, each of which dies at container startup and
  never in development: `meridian_core.embedder` imports `httpx`, declared on
  `meridian-worker`; `services/api` imports `jwt`, which had been arriving
  transitively through `mcp`; and `worker.embedserver` imports `fastapi` from
  the `embed` extra the worker image did not install
- The worker image now syncs `--extra embed`. `P2-17`'s sidecar is the worker
  image under a different command, precisely so there is one copy of the model
  — and without the extra it died at import, which it did, because the sidecar
  had never once been started from a container

### The rule, third time

- `P1-30` wrote it down: a package declares what it imports, because
  `uv sync --package X` installs X's closure and nothing the root happens to
  also pull in. The handover predicted the recurrence. Predicting was not enough
- Now asserted per workspace member, with both sides derived — imports from the
  AST, dependencies from the `pyproject.toml` files, siblings followed
  transitively because that much really is legitimate

## [0.76.5] — 2026-09-20

### Fixed

- `B-14` The embedding sidecar could never obtain its weights. It sits on
  `internal`, which has no DNS and no route out, and the weights are not in the
  image — so on a fresh stack it answered `loaded: false` for ever and timed out
  every embed request. Search stayed lexical-only, in production too
- The compose comment asserted the weights *were* in the image, two lines above
  the mount that exists because they are downloaded at runtime
- The worker now shares the same cache. `P2-19`'s backfill falls back to loading
  the model in-process when the sidecar does not answer, and without a mount that
  fallback pulled 2.3 GB into a layer that dies with the container, every run

### Added

- `python -m worker.fetchmodel` — a profile-gated one-shot on `egress` that
  writes into the volume the sidecar reads, and exits. It loads and encodes
  rather than only downloading: a cache missing one file downloads
  "successfully" and fails at the first real batch of a four-hour backfill
- It refuses a model whose width is not the schema's, for the same reason

### The isolation is kept on purpose

- Giving the sidecar egress would have been one line. It runs corpus text —
  derived from pages the crawler fetched — through a model, and a route out from
  there is a route out for anything that ever gets in
- Baking the weights into the image was the other option: 2.3 GB on every layer
  push, twice for a multi-arch build, for files that do not change between
  releases

## [0.76.6] — 2026-09-20

### Fixed

- `B-16` Every page the first containerised crawl fetched was thrown away.
  Docker creates a missing bind-mount source on the host as **root**; every
  application image runs as `meridian`, uid 1001, with `cap_drop: ALL`. So the
  worker fetched a handful of real pages, hundreds of kilobytes each, and could not create
  `/data/raw/<domain>/`, and settled every task `"outcome": "success",
  "stored": null` — because the fetch had succeeded
- The same would have happened on the server. The deploy docs chown
  `/srv/meridian/app` because that is the checkout, and say nothing about `raw`,
  `figures` or `models`

### Added

- A `chown` one-shot in both compose files, on `network_mode: none`, run by
  `make quickstart` and belonging in the runbook before `up`
- Drift tests tying the uid to the Dockerfiles that create it — three copies of
  1001 is three chances to move one — and asserting nothing else runs as root

### Two details that were nearly the other way

- The three top-level directories are chowned, not `-R`: what is created beneath
  them inherits the owner, and a recursive chown over a 100 GB raw store is its
  own outage
- `network_mode: none` rather than omitting `networks:`, which silently puts a
  container on compose's default bridge — that bridge has egress, and this is
  the only container in the stack running as root

## [0.77.0] — 2026-09-20

**The whole stack, locally, from a fresh clone.**

### Added

- `B-05` `docker-compose.local.yml` — every service built from source, so a
  fresh clone needs no registry access. Credentials that are not secrets and are
  not pretending to be, data under the working tree, loopback only
- `web/Dockerfile` and `web/nginx.conf`. `docker-compose.yml` had referenced
  `build: { context: ./web }` since it was written and the file was not there,
  so the production stack could not be brought up whole. Node builds, nginx
  serves, and the runtime carries no Node at all
- `deploy/tools/Dockerfile` for Alembic and the config seed. No service image
  carries either: `alembic` is in the root project's `dev` group and every
  application image syncs `--no-dev`
- `make local-up`, `make local-down`, `make local-logs`

### The network split is kept, deliberately

- One bridge would have been easier and wrong. `internal` versus `egress` is
  `P1-22`'s boundary, `crawl4ai` drives a real browser against hostile pages,
  and a local stack that flattened it would let someone develop against a
  topology production does not have
- One difference, stated rather than implied: `api` and `web` sit on a third
  `frontdoor` network. **A container on an `internal: true` network cannot
  publish a port at all** — Docker installs no gateway, so the `ports:` line is
  silently inert rather than an error. Production does not need it because
  `cloudflared` proxies inward

### Found by running it

- `api` was never given `MERIDIAN_EMBEDDER_URL`, so every search reported "this
  deployment has no embedder" a few hundred bytes from a running sidecar. A test
  now asserts the pairing: a stack that runs the sidecar has to tell its clients
  where it is
- `.localdata/` was not gitignored

## [0.78.0] — 2026-09-20

**One command from a fresh clone to a running, seeded Meridian.**

### Added

- `B-06` `make quickstart`. Checks the machine, builds every image from source,
  brings up Postgres alone, migrates, seeds, chowns the data directories,
  fetches the embedding weights, starts the rest, waits for the API and prints
  the URL
- A "Just run it" section in the README, above the development path, and port
  `21116` named in the port table

### Idempotent by construction

- Because "run it again" is the only thing anybody is going to try. Compose
  converges rather than recreating, `alembic upgrade head` is a no-op at head,
  `seed.py` skips what it has already written (scaffold §1.6), the chown is a
  chown, and the weights resolve from cache
- Postgres starts alone and first, so a failed migration is readable rather than
  interleaved with six services' startup logs
- `--skip-preflight` and `--rebuild` for the two cases where the default is
  wrong

## [0.91.0] — 2026-09-20

**The graph store is in the image.**

### Added

- `P4-01` `deploy/postgres/Dockerfile` — PostgreSQL 17 with pgvector *and*
  Apache AGE 1.7.0, compiled from the Apache source release and checksum-
  verified. §3 chose one store; no published image carries both
- A migration creating the extension and the `graph` graph, with the grants and
  ownership `meridian_rw` needs to write to it
- All three compose files use it, so `make quickstart` starts a database that
  already has both and nobody installs an extension by hand

### Verified on the database holding the crawl

- The image swap preserved every row — 447 sources, 2459 chunks, unchanged
- A Cypher `CREATE` and traversal, run as `meridian_rw`: the role the
  application actually connects as, not the owner

### Four failures on the way in, each naming something nobody wrote

- `shared_preload_libraries` in `postgresql.conf.sample` is read **only by
  `initdb`** — useless on an existing data directory, silently. The compose
  files pass the flag
- A graph named after the project collides with the role of the same name, so
  `"$user"` resolves to it and the graph becomes the default schema. It is
  called `graph`
- `create_graph` needs `ag_catalog` on the search path, or fails on
  `graphid_ops`. `SET LOCAL`, scoped to the migration's transaction
- AGE attaches label tables with `ALTER TABLE ... INHERIT`, which needs
  **ownership**, not `GRANT ALL`

### Licence

- Apache AGE is **Apache-2.0**, read from the `LICENSE` in the source tarball
  and shipped in the image so it can be re-checked from the container.
  `docs/licences.md` no longer lists it as unverified

## [0.90.0] — 2026-09-20

**Age-aware ranking, by document kind rather than globally.**

### Added

- `P2-20` `meridian_core/ageing.py`: a half-life per source tier, overridable
  per topic, applied as a decay on the fused score rather than as a filter
- `SearchFilters.age_aware` and `half_life_overrides`; hits carry `age_days`,
  `decay` and `score_before_decay`

### The failure this avoids

- **`peer_reviewed` does not decay at all.** A single "newer is better"
  multiplier buries the foundational paper, and for a corpus with an academic
  spine that is the failure that matters
- Press and informal rot in months; a government policy page supersedes rather
  than ages, so it sits well above press without being exempt

### Three properties

- **An undated document is neither old nor new.** A third of crawled pages have
  no date, and whichever default you pick is wrong for the other kind
- **The adjustment is shown.** A result silently demoted is one the reader
  cannot audit, which is the opposite of what this corpus is for
- **Floored at 0.25**, so decay reorders rather than deletes — without a floor
  a ten-year-old article scores within rounding of zero and leaves the result
  set entirely, which is a filter wearing a decay's clothes

### Off by default

- It changes what search returns, and `P2-04`'s benchmark and `P2-09`'s
  go/no-go are measured against the current baseline

## [0.89.0] — 2026-09-20

**What has this person's model been reading.**

### Added

- `P3-11` A `grant_audit` table and `record_call`, `within_rate_limit`,
  `calls_by_grant`

### Indexed by grant, throttled by token

- **Audited by grant**, because the question is about a person and a per-token
  log cannot answer it once they hold three clients
- **Rate-limited per token**, because what is being throttled is a client in a
  retry loop — a property of one client. Limiting the grant would let one
  misbehaving laptop silence the same person's phone

### Three smaller decisions

- **Arguments kept, results not.** What somebody searched for is the audit;
  what came back is the corpus, and copying it here would be a second store of
  the same content with none of §5.4's retention rules
- **Refusals recorded.** A log of successful calls answers half the question,
  and a grant repeatedly refused a tool is the more interesting half. Refused
  calls do not count against the rate limit, or one misconfiguration becomes
  two
- **An audit write never raises.** Monitoring that takes the read surface down
  is the outage it exists to detect

## [0.88.0] — 2026-09-20

**A guest cannot widen their own grant.**

### Added

- `P3-10` `filters_for`, `tiers_allowed` and `may_read_raw` — topic, source
  tier and raw-file scoping applied where a guest's request is turned into a
  query

### The property that matters

- **Filters intersect rather than replace.** A guest may narrow their own
  search further; nothing they send can widen it
- A guest asking only for topics they do not hold gets **nothing**, not
  everything they do hold — answering a question they did not ask would be the
  friendlier bug
- An **unrecognised** `max_source_tier` admits nothing. Treating a typo as "no
  ceiling" is a mistake failing in the direction that widens access

### §5's two defaults, both off

- **Raw files.** Serving the raw store to somebody else is redistribution of
  third-party material, a different act from sharing what was extracted
- **The operator's annotations.** §12.5 predicts they become the highest-quality
  layer precisely because they are the operator's own thinking, which makes
  them the most personal thing in the system. Sharing them is the `operator`
  profile, not a checkbox beside a colleague's email address
- And a guest's search is always `cleared_only` (`P4-14`): this is content
  going to somebody else's model

## [0.87.0] — 2026-09-20

**The unit of sharing is a person, not a credential.**

### Added

- `P3-06` A `grants` table and `agent_tokens.grant_id`. Somebody given access
  holds several credentials, and revoking their access has to revoke all of
  them at once — a per-token model leaves you chasing them
- `resolve_grant` and `revoke_grant`, and profiles as named sets of tools

### Three decisions

- **Tokens are revoked, not deleted.** An audit entry points at a token row;
  deleting it leaves the history unable to say whose credential made a call
- **A person grant must have an expiry**, enforced by a CHECK. §3: an access
  grant with no end is one nobody revisits, and a code path that forgot would
  create one. A service grant may be open-ended
- **No profile carries a write tool**, including `operator`, and an unknown
  profile grants nothing rather than everything — a profile added by a later
  migration must fail closed

## [0.86.0] — 2026-09-20

**An empty corpus now shows something true.**

### Decided

- `B-09` **Make the first hour legible rather than ship a demo corpus.** A
  snapshot of a real crawl is third-party content, and whether it may be
  redistributed is the question §14.2 keeps separate from everything else —
  the same reasoning that keeps `MERIDIAN_SERVE_RAW` off by default. Shipping
  one in the repository would have answered that question the other way
  without saying so
- Synthetic fixtures were ruled out by the task itself: they do not resemble
  real extraction output, so the first impression would be of a system that
  works better than it does

### Added

- `GET /api/explore/progress` — the queue by status, both halves of the last
  hour's fetch rate, and the domains most recently fetched
- A `FirstHour` view for Explore. A queue draining is a system working, and
  that is the honest thing an empty corpus has to show

### The two failures that both look like "no results"

- A crawl with **nowhere to begin** says so and points at Admin
- A crawl where **every fetch failed** says that, rather than reporting twenty
  attempts as though they were progress
- Queue statuses are listed rather than summed, for §12.5's reason: 4,000
  pending and 4,000 failed are the same depth and opposite situations

## [0.85.0] — 2026-09-20

**Cold-start seeds are editable from the interface.**

### Added

- `B-07` A Seeds section in Admin: what is still pending, what has been
  reached, and the ability to add or drop seeds. `GET /api/admin/first-run`,
  `POST /api/admin/seeds`, `DELETE /api/admin/seeds/{id}`
- Admin opens on Seeds when nothing has been crawled yet — the default section
  on a fresh machine is otherwise an empty gazetteer queue with no hint that
  the thing worth doing is elsewhere

### Why it is not a wizard

- §16 calls cold-start seed quality "worth spending an evening on", and that
  evening had to be spent editing `config/seed_sources.yaml` *before* first
  boot, because the file is read once and never again (§13.1)
- By the time anyone opens this, the crawl has started. A screen implying
  otherwise would invite removing a seed that has already been fetched. So both
  halves are shown, and "already reached" is not styled as an error

### Two refusals

- A typed URL is validated exactly as a model's is. A private address is no
  safer for having been typed by the operator — §11.8's attack path does not
  care who asked
- A seed cannot be removed once it has been claimed, which is **two**
  conditions: claiming is a lease (`P1-01`), so a seed being fetched right now
  is still `pending`, and checking only the status would delete the row out
  from under a worker mid-fetch

## [0.84.0] — 2026-09-20

**A domain earns its seeding allowance.**

### Added

- `P4-12` `fetch_policy.seed_allowed`, `first_seen_via` and `novel_fetches`.
  Whether new URLs on a domain may be *queued* is a third question, beside
  whether to fetch what is already queued (`status`) and whether a model may
  read what came back (`trust_state`)
- A frontier-discovered domain approves itself after three novel documents; an
  operator's own seed is approved immediately
- `awaiting_seed_approval` — the queue an admin screen shows, the same shape as
  the gazetteer's

### NULL is undecided, and undecided is not permission

- A boolean defaulting to false would have said the same thing worse.
  "Somebody declined this" and "nobody has looked yet" lead to different
  actions, so they get different states and different messages
- **A model-proposed domain never approves itself**, however much evidence
  accrues. Evidence gathered after the proposal is evidence the proposal
  caused, which is exactly how a model talks a crawl into a domain
- Novel documents, not fetches: a site serving one page under a thousand URLs
  would otherwise approve itself on volume alone

### Where it is recorded

- Inside `enqueue`, not at its four call sites. A fifth call site added later
  that forgot would leave a domain with no provenance — and a domain with no
  provenance can never auto-approve

## [0.83.0] — 2026-09-20

**Somebody has now checked the licences.**

### Added

- `B-11` `docs/licences.md` — every runtime dependency, its licence, and a
  verdict. Read off the installed artefact rather than recalled: distribution
  metadata, image labels, the model card on disk
- `tests/unit/test_licences.py`, an allowlist gate. A new licence fails until
  somebody reads it and adds it, which is the point rather than the friction

### The verdict

- **Nothing blocks commercial use.** All 116 Python distributions are
  permissive — no GPL, no AGPL, no non-commercial terms
- The model weights the task called the likeliest problem are clean:
  `BAAI/bge-m3` declares `license: mit`, read from the snapshot on disk

### Two that needed a judgement

- **SearXNG is AGPL-3.0-or-later**, confirmed from its image label. Run
  unmodified in its own container, reached over HTTP, never published — that is
  aggregation, and the document states exactly which changes would turn §13 on
- **`tld` is tri-licensed** MPL-1.1 / GPL-2.0-only / LGPL-2.1+. We take
  MPL-1.1; the choice lives in code, and a test fails if the package stops
  offering it

### Fixed

- Meridian's own three packages declared no licence. That is the worst case
  rather than a neutral one — no licence is no grant of rights, whatever the
  author intended

## [0.82.0] — 2026-09-20

**A flag from the injection pre-screen now does something.**

### Added

- `P4-14` `sources.trust_state` and a domain verdict cached on `fetch_policy`,
  plus `meridian_core/trust.py`. `P1-23` flagged and blocked nothing, on
  purpose — a screen that quarantines before its false-positive rate is known
  quarantines the corpus. It has since run clean on every real page crawled
- A domain clears on sight if it is in the curated tier map, or after five
  consecutive unflagged fetches; any flag resets the streak
- `SearchFilters.cleared_only`, set by the MCP surface and nothing else

### Three decisions

- **Screening is paid once per domain.** A site with four thousand pages must
  not be judged four thousand times, and a cleared domain must not have page
  3,001 quarantined for quoting an instruction
- **The filter admits `cleared` rather than excluding `quarantined`.** A page
  nothing has examined is not a page that has been checked, and "not known to
  be bad" is not the claim screening is for
- **Quarantined content stays stored, and stays visible to the operator.** §2.5
  keeps it; holding it back is reversible and deleting it is not. The person
  reading their own corpus sees it, or a false positive is invisible and the
  screen is unaccountable

### Outstanding

- The queue that hands a quarantined domain to a frontier model to judge needs
  phase 4. Until then a quarantine is lifted by a person — a worse experience
  and the correct failure, since the alternative is admitting unscreened
  content because nothing was available to screen it

## [0.81.1] — 2026-09-20

### Added

- `P4-13` `check_can_start_run` — the refusal §16 describes and nothing
  enforced. Three checks in the order somebody would fix them: no budget row,
  a budget with caps missing, and a month already at its ceiling
- It returns the budget it approved, so a run enforces the numbers it was
  checked against rather than re-reading caps that may have moved

### Two choices worth stating

- **One missing cap refuses, even with the others set.** A run capped on seeds
  and uncapped on tokens is an uncapped run — the expensive half is the one
  nobody limited
- **The ceiling is checked before a run, not during it.** A run cannot know
  what it will spend, and killing one halfway leaves a half-written graph to
  reconcile. The month's *next* run is the one refused, which is why §11.9 asks
  for trend alerting as well as a ceiling

## [0.81.0] — 2026-09-20

**Caps, so the first autonomous run cannot be the first invoice.**

### Added

- `P4-10` `meridian_core/budget.py` and the `budget_config` table: per-run token
  and seed caps, cost accumulation, and a monthly ceiling
- `reserve_tokens`, the same shape as `reserve_seeds` — all-or-nothing, locked
  with `FOR UPDATE` so two concurrent tool calls cannot both fit under one cap,
  and returning the remainder so a caller can stop before it is refused
- `GET`/`PUT /api/admin/budget`

### Absent is refused, never unlimited

- Every cap is nullable and null means *unconfigured*. A default of infinity is
  the shape in which forgetting to configure something becomes a bill, and the
  loop §11.9 describes is unattended — the first signal would be the invoice
- Nothing seeds a default budget. `config/*.yaml` seeds topics and fetch policy
  because a sensible default beats an empty table; a sensible default *cap*
  would satisfy §16's ordering requirement by accident

### Three decisions worth stating

- **One row, enforced by a CHECK.** A settings table that can hold two rows
  eventually does, and then "the budget" is whichever the query ordered first
- **Cost accumulates rather than being assigned.** A run makes many calls; a
  setter would record whichever wrote last, and the per-run figure §11.9
  compares week on week would mean different things in different runs
- **The calendar month, in UTC, measured on `started_at`.** A ceiling set from a
  monthly invoice should reset when the invoice does, and a run spanning the 1st
  must not be invisible to it while it keeps spending

## [0.80.0] — 2026-09-20

**The scheduler can be supervised.**

### Added

- `B-19` The scheduler loop writes `P5-08`'s heartbeat, so `worker.liveness` is
  a real probe for it and the compose healthcheck is a real healthcheck
- A rule that a long-running service overriding its image's command must
  declare a healthcheck or disable one explicitly

### Where the beat goes is the whole design

- **After each claim, not before it.** `worker.main` beats before its work,
  because a lane wedged inside a fetch should stop beating within the
  iteration. Here the database round trip that claims a job is the thing that
  hangs, so a beat in front of it would be refreshed by a scheduler that never
  gets an answer
- **Throughout a running job.** A backfill legitimately runs for half an hour;
  a probe that failed during normal work would restart the scheduler in the
  middle of the job it was reporting on — monitoring causing the outage it
  exists to detect
- That second beat proves less, and the docstring says so: a job is in flight
  and has not yet hit `--timeout-seconds`. The timeout is what stops it
  covering for a permanently hung child

### Fixed

- The healthcheck was `disable: true` for one release, which was honest but
  unsupervised. Omitting it entirely — the state before that — inherited the
  image's probe, which checks `worker.main` and poppler and would have called
  a wedged scheduler healthy for ever

## [0.79.1] — 2026-09-20

### Fixed

- `B-20` The embedding sidecar took 144.6s to answer its first request, on a
  cache that already held the weights. `embedder` has no route out by design,
  and `huggingface_hub` did not know — so every cold start issued HEAD requests
  for the optional config files the cache does not hold, got `Temporary failure
  in name resolution`, and retried five times with backoff per file before
  loading from cache anyway
- `HF_HUB_OFFLINE=1` takes the same load to **4.9s**, measured both ways on the
  same cache, and removes a wall of WARNING lines that look exactly like a
  sidecar that cannot find its weights at all

### Why it is also the more honest failure

- Offline, a genuinely missing *required* file raises "not in cache"
  immediately. Online, it times out against a host that was never reachable
  from there, which says nothing about what is actually wrong

### Asserted both ways

- A service that reads the model cache from a network with no route out must
  set it
- `modelfetch`, whose entire job is the download, must never have it

## [0.79.0] — 2026-09-20

**The timetable now has something reading it.**

### Added

- `B-15` A `scheduler` service in both compose files. `P5-06` built
  `worker.scheduler` and nothing had ever started it, so `seed.py`'s five
  `scheduled_jobs` rows — embed and novelty hourly, digest, sweep and harvest
  daily, all `enabled`, all already due — were a timetable nobody read
- In practice that meant a stack left alone fetched, extracted, chunked and
  stopped. Nothing embedded, deduplicated, swept or harvested, ever
- The worker image under a different command, like `embedder`: the jobs it
  spawns are `python -m` entry points from the same tree and inherit the
  container's environment, so it carries what *they* need — both networks, the
  raw store, the model cache

### A boundary deliberately widened

- `scheduler` is the second service on `internal` *and* `egress`, which
  `P1-22` previously allowed only `worker` and `orchestrator`. One of its five
  jobs (`worker.digest`) reaches Telegram; the other four want nothing outside
  `internal`
- The cost is that `worker.harvest` parses crawler-fetched text in a container
  with a route out. `worker` already makes that trade in the same image, which
  is why it is tolerable here and would not be for a service with no such need
- The reasoning lives in the test that holds the allowlist, so the next person
  can argue with it rather than discover it

### Known gap, and a trap found inside it

- The scheduler's healthcheck is `disable: true` — switched off, not omitted.
  Omitting it does not give a container none: it inherits the image's, and the
  worker image probes `worker.main` and poppler, which passes for as long as
  the package tree is intact
- So a wedged scheduler would have reported `healthy` for ever *and* suppressed
  the restart that no probe would have left to `restart: unless-stopped`
- `P5-08`'s real heartbeat is written by `worker.main`'s loop. Giving this loop
  one of its own is `B-19`

## [0.78.2] — 2026-09-20

### Fixed

- `B-18` `docker compose up -d` could not bring up the production stack.
  `orchestrator` is phase 4 and its Dockerfile does not exist, and **compose
  does not skip a service it cannot build** — it fails the whole command with
  `lstat ...: no such file or directory`. Profile-gated until the image exists
- The same shape as `web`, which had no Dockerfile either until `B-05`. That
  one was written; this one has nothing to write yet
- `api` no longer publishes `127.0.0.1:21114:8000`. A container attached only to
  an `internal: true` network has no gateway for the host to forward to, so the
  line published nothing and was not an error — under a comment calling it
  "loopback only", which is worse than no line at all

### Generalised rather than patched

- Every non-profiled service must build from a Dockerfile that exists. This is
  the second time a missing image has stopped the stack coming up
- Nothing may publish a port when every network it is on is internal
- Both checked by reintroducing the defect and watching them fail

## [0.78.1] — 2026-09-20

### Fixed

- `B-17` The first two commands in the deploy runbook could not work. Both
  `docs/setup.md` and `docs/deployment.md` said
  `docker compose run --rm worker alembic upgrade head`, and the worker image
  has no `alembic` — it is in the root project's `dev` group and every
  application image syncs `--no-dev` — and never copies `scripts/`, so the seed
  line failed too
- These are the first commands an operator runs on a new server, at the step
  where the database is created, and nobody had run them there

### Changed

- `deploy/tools/Dockerfile` is promoted to `docker-compose.yml`, profile-gated,
  and the docs name it. The thing worth preventing was a stack that migrates
  *itself* on boot with nobody watching, and the profile is what prevents that
  — not the image being absent
- `make migrate` is not the answer on the server: docs/setup.md §2 never
  installs `uv`, and the database URLs name `postgres`, which resolves only
  inside the compose network
- The release script builds the tools image too, because the server does not
  build

### Added

- A test that walks every `docker compose run` in the two deploy documents,
  resolves the service to the Dockerfile that builds it, and checks the invoked
  thing is actually in there. It cannot tell you the command succeeds — only
  that it is not missing, which is what was wrong

## [0.76.2] — 2026-09-20

### Added

- `B-08` `scripts/preflight.sh`. Checks Docker, Compose v2, cores, memory and
  free disk against the README's stated minimums, and names the consequence of
  each shortfall rather than only the number
- A drift test tying the script's hardcoded minimums to the README table, which
  is where a user actually reads them. Verified to fail when they disagree

### Warnings are not failures

- Only two things exit non-zero: no Docker daemon, and no Compose v2. Those mean
  nothing can start at all
- Being under the stated minimum is a loud warning and not a refusal. The
  README's figures are sized for a 50k-document corpus, and somebody trying this
  on 500 documents is not wrong — a preflight that refused would be substituting
  its judgement for theirs. `make quickstart` can therefore gate on the exit
  status without the gate being an opinion about somebody's laptop

### Details that were the other way first

- **`MemTotal`, not `MemAvailable`.** Available is what is free right now, which
  on a machine that has been up a while is mostly page cache and says nothing
  about whether the stack fits
- **Free space is measured at `DATA_ROOT`**, walking up to the nearest existing
  parent — raw files are what grow, and they land wherever that points, commonly
  a different disk from the checkout
- **Colour only on a TTY**, honouring `NO_COLOR`. Escape codes are noise in
  exactly the output somebody pastes into a bug report

## [0.76.1] — 2026-09-20

### Added

- `P1-37` `scripts/build_and_push.sh`. `make build-push` has called a script
  that did not exist since the target was declared; it now cross-builds one
  multi-arch manifest per application image and pushes it to GHCR, tagged by
  commit SHA (scaffold §5)
- Three tests on the script beyond "it exists and parses": it never tags
  `latest`, it refuses a dirty tree, and its image list is checked against the
  services `docker-compose.yml` actually builds — a service added there and not
  here is one that never gets built for arm64, which surfaces days later as a
  container that will not start on the Pi

### Two refusals, both about what the SHA tag is for

- **It will not push from a dirty working tree.** Scaffold §5 tags by SHA so a
  bad build does not roll out on the next restart and rollback is a one-line
  edit. A tag naming a commit whose code is not what was built makes rollback a
  guess, and the guess is discovered to be wrong while rolling back
- **It never tags `latest`**, which would remove exactly the control the SHA was
  providing

### Honest about what does not exist

- `orchestrator` (phase 4) and `web` have no Dockerfile yet, so they are skipped
  *loudly* and named in the summary. A script that quietly shipped two of four
  images would be indistinguishable from one that shipped all four
- The buildx preflight names the fix for the `docker` driver, whose own error
  ("docker exporter does not currently support exporting manifest lists") names
  neither the cause nor the remedy

## [0.76.0] — 2026-09-20

**Server-side write validation, before anything can write.**

### Added

- `P4-05` `meridian_core/validation.py`. §11.8's mitigations, built as guards
  rather than as checks inside a write tool: `check_nodes_exist`,
  `check_chunks_resolve`, `check_not_self_edge`, `check_relation_type`,
  `check_seed_allowed`, `reserve_seeds`, `check_provenance`,
  `check_tier_not_downgraded`, and `check_edge` composing them
- 32 tests, almost all rejections. That valid input is accepted is the weaker
  half; what matters is that a model which has read a hostile page is actually
  refused

### Why a module and not checks in the tools

- §11.1b: three callers reach the same writes — the orchestrator's local
  functions, an external agent over MCP, and an agent CLI with a scoped write
  token — and "none gets privileged access". A guard inside one caller's path is
  a guard the other two do not have
- Every function refuses by raising; none returns a boolean. A boolean gets
  assigned and not checked, and this module's failure mode is silence — a guard
  that never ran looks exactly like one that passed
- `ValidationError.rule` is the machine-readable half, so refusals can be
  counted and alerted on by rule rather than by message text

### The decisions inside it

- **`cap=None` refuses.** "Nobody configured a cap" must never read as
  "unlimited" (§16, `P4-13`). A default of infinity is the shape in which a
  missing config becomes a bill
- **Seed reservation locks the run row.** Two tool calls reading
  `seeds_emitted` at 9 against a cap of 10 would both pass and both write
- **All or nothing.** A partially admitted batch makes the cap depend on the
  order the model listed its seeds in
- **Seeds are refused at seed time, not just fetch time.** A rejected seed that
  reached `queue` would sit there as `pending` and be retried with backoff,
  which turns a rejected injection into a scheduled one
- **Hostnames are not resolved here.** DNS at seed time is a second answer that
  can disagree with the one `netguard` gets at fetch time, and the fetch-time
  one is what `P1-24` pinned. Literal private addresses are still refused, since
  there is nothing to look up
- **The relation vocabulary stays open.** §5.4 closes the node-type ontology and
  deliberately does not close relations, so `check_relation_type` refuses a
  shape — prose in a column a traversal groups by — rather than a value
- **An agent cannot write as `human`.** `P6-05` reserves it for the reader's own
  notes, and that layer is only distinguishable while nothing else can claim it

## [0.75.2] — 2026-09-20

### Fixed

- `B-10` `figures.linked_entity_ids` was the only `json` column in the schema,
  and the only list of ids not stored as `ARRAY(BigInteger)` — `entities.
  merged_from` and the four `supporting_chunk_ids` columns are the same shape
  and the same use. `json` keeps the literal document text, so `'[1, 2]'` and
  `'[1,2]'` were unequal values, nothing could be indexed, and reading one back
  meant parsing JSON to recover integers Postgres could return directly
- Two completeness probes derived from the live schema, so a column added next
  year is covered without anyone remembering: every `*_ids` column is an array
  of bigint, and nothing uses `json` where `jsonb` was meant

### The migration is four statements, not one

- Autogenerate emits a bare `ALTER COLUMN ... TYPE`, which has no cast from
  `json` to `bigint[]`. Supplying one needs `jsonb_array_elements_text`
  aggregated — a subquery, which Postgres rejects in `USING`. Add, `UPDATE`,
  drop, rename instead, which also puts the values through a real JSON parse
  rather than string surgery
- Caught by migrating rows put there on purpose. `figures` is empty in
  development, which is precisely the condition that lets a migration that
  would fail on the server pass locally

## [0.75.1] — 2026-09-20

### Fixed

- `P5-09` The alert suppression test expired on a calendar date.
  `record_alert` takes `created_at` from the database clock while
  `tests/integration/test_alerts.py` pins `NOW` to a literal instant, so "48
  hours after the alert" meant 48 hours after a date that kept receding. It
  went red five days after it was written, with nothing changed — the worst
  shape a failure can take, because the blame lands on whatever was committed
  that morning
- Suppression tests now set the row's age explicitly, the way `attempts()`
  always set `attempted_at`. Added the cooldown's *holding* half: asserting
  only that suppression expires passes equally well against a function that
  never suppresses anything, which is the failure §13.3 exists to avoid

## [0.75.0] — 2026-09-20

**Annotation as first-class nodes: the one layer nothing else can write.**

### Added

- `P6-05` A note is an `entities` row with `node_type='annotation'`, attached to
  what it is about by ordinary `annotates` edges. Reads on
  `GET /api/explore/annotations`, writes under `/api/admin/annotations`, a
  Markdown export at `/api/explore/export/annotations`, and the composer on both
  reading surfaces — the source page, where the passages are, and the node panel,
  which §12.5 ends with "own annotations"
- `entities.supporting_chunk_ids`, the one column this needed. §2 principle 3
  names edges, tags and attributes, not nodes, and that is right for a derived
  entity: what justifies it is the edges and attribute values that cite it. A
  node a *person* wrote has none of those — the note is the claim — so the
  passages they were reading have nowhere else to live

### Not a side table

- The graph's traversal, path mode and canvas filters all read `entities` and
  `edges`. A notes table would need every one of them taught about it, and the
  layer §12.5 calls the highest-quality one in the system would be the only
  layer the graph cannot see

### Authorship is assigned by the server, never accepted

- `AnnotationCreate` has no `produced_by` and forbids extra keys, so nothing can
  claim to be the reader's own thinking — including, later, a model holding a
  write tool (`P4-04`). The layer is worth having because a reader can tell
  their notes from the corpus's, and a note that merely *says* a human wrote it,
  on a surface where anything could say that, is not a distinguishable layer
- `scripts/seed.py` refuses to register an agent under the reserved `human` id,
  because an agent that could would write rows nothing downstream could tell
  apart from the reader's own
- `quality_tier` and `model` stay null. §11.12's tier is an ordinal over models;
  a note carrying one would be ranked against model output on an axis it is not
  on, and "quality tier only moves up" would become a rule about a person

### Built before the graph, on purpose

- A note may be about nothing. The thought that has not found its node yet is
  the one the corpus cannot re-derive, and with phase 4 unbuilt it is *every*
  note — so a composer that required a target would make the affordance
  unavailable exactly when §12.5 wants the habit forming
- A citation is refused if it resolves to nothing. The annotation layer is the
  part a reader trusts without re-checking, so a dangling chunk id here is one
  nobody will ever click to discover

### Fixed

- The client types for `EntityRead` and `NodeDetailRead` had fallen behind the
  DTOs. The drift test in `web/tests/api.test.ts` caught both, which is what it
  is for

## [0.74.0] — 2026-09-15

**Saved views: a filter set, named and re-openable.**

### Added

- `P6-09` `saved_views`, `GET /api/explore/views`, the writes under
  `/api/admin/views`, and a *Save this view* control that appears while looking
  at results
- A table rather than `localStorage`. A saved view is a piece of research
  method — the slice somebody decided was worth returning to — so it survives a
  cleared cache, reaches a second device, and travels in the database snapshot
  that is supposed to be the whole system. `P6-11`'s last-visit stamp stays in
  `localStorage` for the opposite reason: it is per-reader, per-device, and
  worthless to anybody else

### Reads on Explore, writes on Admin

- Which looks inconsistent for something a reader creates while reading, and is
  the right split for this table. §12.6 divides the prefixes by **mutation**, and
  the consequence here is exactly what is wanted: saved views are shared state
  with no per-viewer scoping, so a guest on a shared instance (`P3-06`) can open
  the owner's views and cannot add to them
- Listing views does not count as opening one. Listing is not returning — and a
  read that wrote would put `/api/explore` on the wrong side of the boundary

### Filters are validated on the way in

- Free-form in the column, because they mirror `SearchFilters` and §12.3's canvas
  filters will add to it — but checked against that model before storing. **A
  view that silently drops a filter when reopened is worse than one that refuses
  to save**: the reader gets a result set they believe is narrowed and nothing
  says otherwise

### Also

- A view never opened sorts last rather than being hidden. Somebody saved it and
  did not come back; disappearing it would be the system deciding that was a
  mistake
- `focus_entity_id` is `ON DELETE SET NULL`: a merged or deleted entity costs the
  view its focus, not the view
- The suggested name carries the topics that narrowed the search, because two
  views of the same words are otherwise indistinguishable in a list — which is
  the one thing a name has to prevent
- The only `DELETE` on the admin surface, and it is right: a view holds no
  evidence and cites nothing, so a tombstone would clutter the list it exists to
  be read from

## [0.73.0] — 2026-09-15

**The node detail panel, built before there are nodes.**

### Added

- `P6-04` `GET /api/explore/nodes/{id}` and the panel: description, attribute
  tags with confidence, the supporting chunks with source and tier, and a count
  of contested edges (§12.5). Nodes get a URL — `/nodes/{id}`
- Built ahead of the graph deliberately. The hard parts of this panel are about
  *how a claim is presented*, and those do not get easier by waiting for rows

### Tags are grouped by what they can be compared against

- §7.1 splits attributes into ones that apply across the corpus and ones that
  only mean something inside a topic. A flat row of tags asserts they are the
  same kind of claim — and comparing a topic-local dimension across topics is a
  comparison nobody made
- The groups are labelled in words, not by the enum value. `topic_local` is a
  column; "only meaningful inside its topic" is the thing a reader needs

### Confidence is on the tag, and it is a number

- §7 makes confidence first-class. A tag whose confidence a reader must hover for
  is a claim rendered as a fact
- A number rather than "high" / "low": rounding throws away the difference
  between 0.61 and 0.94, which is most of what a reader weighing two
  contradictory tags has to go on
- A tag with no supporting chunk is marked. The schema requires the array, so an
  empty one is a defect — and §2 principle 3 makes an unsupported tag an
  assertion

### Superseded chunks appear here and nowhere else

- `P1-32` excludes them everywhere in the read surface, because a superseded
  chunk is text the page no longer carries. Here it is the text the attribute was
  **derived from**, and §2.4 re-derives from source chunks — so a tag whose chunk
  was replaced by a re-crawl must still resolve, or the citation goes nowhere
- The panel says so: "as they read when they were read. A page may have changed
  since."

### Also

- Attributes sort by confidence. Insertion order puts whatever was tagged first
  at the top, which is a fact about the crawl rather than about the node
- Overflow uses `<details>`, so the folded tags stay in the document and reach by
  keyboard. A "+7 more" that drops the tags is truncation wearing a disclosure's
  clothes
- One request rather than four. Four is four chances at a partly-rendered panel
  that looks like a node with no attributes
- The first tests in this repository to write `entities`, `attribute_values` and
  `edges` rows, so they exercise those tables' provenance constraints too

## [0.72.0] — 2026-09-15

**Explore can narrow to a topic, and says what narrowing hides.**

### Added

- `P6-24` a topic filter in Explore, completing what `P2-14` started: the labels
  were on every hit and the filter was on the API, with no way to choose one
- `CorpusStats.topics`, from `topic_config` rather than from the labels present
  on sources. The second needs `DISTINCT unnest(topic_labels)` over the whole
  corpus, which no GIN index answers, and it would make the landing page's cost
  grow with the crawl. A topic with no sources filters to nothing, which is true
- On `/stats` rather than a route of its own, because Explore needs it at the
  same moment it needs the counts

### The caveat is the feature

- A topic filter excludes sources nothing has examined — correctly, since nothing
  has established they belong to the topic — and **silently**. A reader who
  narrows and sees three results has no way to know the corpus holds three hundred
  documents nobody looked at
- So `CorpusStats.sources_without_topics` exists, and the control says so: once,
  and only while a filter is active. On every render it is noise; never, it is an
  omission the reader cannot see
- Filtering re-runs the search rather than filtering the results in place. Fusion
  ranks a candidate pool, so a filter applied afterwards shows the top 20 of an
  unfiltered ranking with most of them removed — which looks like a topic with
  almost nothing in it

### Also

- Chips rather than a dropdown: the set is small (it is §10's weight vector), and
  a dropdown hides how much is on offer behind a click
- The control renders nothing when no topics are configured. A control with no
  options is furniture, and an empty row of chips reads as a failed load
- Selection uses the graph accent and nothing else. A topic is not better or
  worse than another one, so the only thing colour says is "this is on"

## [0.71.0] — 2026-09-15

**Per-domain fetch policy, and the guards that are deliberately not on it.**

### Added

- `P6-22` `/api/admin/fetch-policy` and Admin → Domains: delay, concurrency,
  timeouts, render mode and status, per domain

### Only some keys are editable, and the list is short

- `ResolvedPolicy` carries the SSRF guards — `block_private_addresses`,
  `block_cloud_metadata`, `allowed_schemes`, `require_https_final`,
  `block_mixed_dns`, `revalidate_each_redirect` — and none belongs behind a form
  field. **A browser form that could switch off private-address blocking is the
  single worst change available in this system**, and it would sit one click from
  controls about politeness
- `respect_robots` and `user_agent` are excluded for a different reason: a
  crawler that can stop honouring robots.txt, or change who it says it is, from a
  web form is a crawler whose operator did not decide that
- The refusal names where they belong, because "not allowed" alone sends
  somebody looking for a permission they do not have

### Editing the global row needs a confirmation the server checks

- It is the only edit here whose blast radius is the entire crawl, and a
  client-side dialog is a promise rather than a check

### Three layers, kept visibly separate

- What is set on the row, what it **resolves** to once the global row and file
  defaults merge underneath, and what the crawl **learned** (`P1-27`). A value a
  reader cannot find anywhere to change is the failure this screen is most prone
  to, so a learned render mode is explained in words with its count, and gets a
  *Check again now* button rather than a setting — clearing an observation and
  setting a policy are different acts
- Values set on the row are marked; inherited ones are not. "Everything is slow"
  and "this site is slow" call for different actions

### Also

- Settings merge rather than replace. A full-replacement PATCH from a form that
  rendered only some keys is how a delay somebody tuned disappears
- Every edit is validated by building a `ResolvedPolicy` from the merged result,
  so the bounds that already exist are the ones enforced (§2.6)
- Unblocking clears the failure count too. Either alone is a trap: the status
  without the counter leaves the domain one failure from being blocked again,
  and the counter without the status leaves it blocked with nothing explaining
  why
- Blocked domains sort first. §6.4 auto-blocks, so one appears without anybody
  choosing it — producing no sources and no errors

## [0.70.0] — 2026-09-15

**A domain that always needs the browser stops being asked twice.**

### Added

- `P1-27` `fetch_policy.render_js_escalations` and `render_js_learned_at`.
  `render_js: auto` fetched statically and re-fetched through the browser when
  the HTML turned out to be a shell — right for a corpus of mostly-static pages,
  and with no memory, so a JS-only domain paid both requests on every page
  forever
- The cost is not bandwidth. Both requests queue in the same per-domain
  rate-limit slot the pages do, so it is crawl throughput, on exactly the domains
  that are already slowest

### Three rules, and the third is what makes it safe unattended

- **Consecutive, not cumulative.** One static fetch that turned out to be enough
  puts the domain back to zero — the same shape as `consecutive_failures`, for
  the same reason: a domain that changes behaviour should stop being treated as
  though it had not
- **Only ever `auto` → `always`.** Learning gives `auto` a memory; it cannot
  overrule an operator who turned the browser off for a domain or demanded it for
  one. Keyed off the merged value rather than the row's own keys, because the
  global row ships `render_js: auto` as the default and treating that as a
  decision about every domain would make the feature dead on arrival
- **The conclusion expires** after a week. This is the trap the obvious version
  falls into, and it is silent: a domain skipping the static fetch produces no
  evidence about itself, so the first correct conclusion becomes permanent and a
  redesign can never be noticed — the crawl keeps working, on the expensive path,
  forever. Three double-fetches per domain per week is nothing against a crawl,
  and it buys the property that this cannot be permanently wrong

### Also

- Only `auto` fetches are recorded as evidence. A domain already going straight
  to the browser renders every time by construction, and counting that would be
  the conclusion feeding itself
- A domain with no `fetch_policy` row is not given one. Otherwise the table fills
  with a row per domain the frontier ever touched — configuration nobody wrote,
  in the screen where an operator looks for the configuration they did
- Recorded in the same transaction as the attempt row, so a log saying a domain
  escalated five times cannot sit beside a policy row that counted none
- Both columns are on `FetchPolicyRead`: a learned value that looked like a
  setting would be one somebody tried to change and could not find

## [0.69.0] — 2026-09-15

**One copy of the model, not two.**

### Fixed

- `P2-19` the backfill embeds through the sidecar `P2-17` already runs, instead
  of constructing a `BGEEmbedder` of its own. A stack running both held two
  copies of 2.3GB of weights on a machine chosen for being small
- `worker/vectors.py` is the seam: `LocalEmbedder` drives the in-process model
  off the event loop, `PreferRemote` asks the sidecar and falls back

### Falling back is right; falling back silently is not

- A backfill can afford to wait for a model to load. What it cannot afford is to
  *appear* to be using the sidecar while quietly loading a second copy — the
  symptom is memory pressure with nothing in the log to explain it. The switch is
  logged once with its reason, and the reason is asserted in a test
- One-way within a process. Once the local model is loaded the memory is spent,
  so returning to the sidecar mid-pass buys nothing and costs a reload's worth of
  uncertainty about which produced what. The next pass asks again
- The batch in flight is not lost: it is retried locally. The cursor has already
  moved past those rows

### A sidecar serving a different model is refused

- The one failure here that cannot be detected afterwards. `<=>` accepts any two
  vectors of the right width and returns a number, so a column holding two
  models' vectors ranks confident nonsense and nothing downstream — not the
  search, not the novelty gate, not a reader — can tell
- `RemoteEmbedder` takes `expect_model` (from `MERIDIAN_EMBED_MODEL`, the same
  variable the worker builds its own model from) and raises `EmbedderMismatch` on
  a response naming another. Checked on **every** response, not once at startup:
  a sidecar can be restarted with a different model under a running client
- A subclass of `EmbeddingUnavailable`, because every caller's correct response is
  the same — degrade, or fall back. The backfill falls back, since the local
  model is the right one, and reports it as a mismatch rather than an outage:
  one comes back on its own and the other needs somebody to change a URL
- `describe()` replaces the boolean `healthy()` internally. "Up" is not the
  question that matters — a sidecar running the wrong model is up

## [0.68.0] — 2026-09-15

**Sources record their topics, so search can filter by one.**

### Added

- `P2-14` `sources.topic_labels`, written at keep time, filterable through
  `/api/explore/search?topic=`, the MCP `search_chunks` tool, and shown on every
  hit
- The one dimension the whole system is organised around — steering weights
  topics, coverage scores topics, seeds are drawn per topic — was the one thing
  a reader could not filter by. The crawl already knew: the topic is on the queue
  row that produced the fetch, and `upsert_source` dropped it
- An array, named to match `entities.topic_labels` and `gazetteer.topic_labels`,
  which already carry exactly this. A source genuinely belongs to more than one

### Two kinds of evidence, unioned at keep time

- The claim's topic is **provenance**: why this URL was fetched at all. That is
  the stronger of the two and the reason this happens during the crawl — after
  the fact the only route back is a join on the URL, and a URL can be enqueued
  repeatedly under different topics while a redirect means the fetched URL is
  frequently not the queued one
- The path match is evidence from the URL itself, taken from the **final** URL: a
  redirect to `/transport/walking/…` says something about the document, and the
  address that was asked for says only what was guessed
- Labels **accumulate, never replace**. A source reached under two topics belongs
  to both, and overwriting would make the label depend on which crawl ran last —
  the same corpus filtering differently depending on fetch order

### NULL and `{}` are different facts

- NULL means nothing has examined the source; `{}` means something has, and it
  matched nothing. Only the first is a backfill queue, and without the
  distinction the backfill either re-reads the corpus every run or silently
  claims everything it could not match has no topic
- A topic filter excludes both: neither has been established as belonging to the
  topic, and claiming one would assert something no pass checked. Without a topic
  filter both are still searchable, so a corpus crawled before this still works
- Overlap (`&&`), not equality. Naming two topics means *either* — an AND across
  topics returns almost nothing, since a document rarely sits squarely in two

### `python -m worker.retopic`

- Backfills already-crawled sources by matching their URLs. Reports by default,
  writes with `--apply`
- **Deliberately records less than the live path.** It cannot recover the
  crawl's own topic, and the join that would is the one this task rejected — a
  label attached by a wrong join is indistinguishable from one the crawl
  established, which makes it worse than no label
- Refuses to run with an empty vocabulary: it would stamp `{}` on every source
  in the corpus, recording "examined, matched nothing" about a question it never
  asked, and it is not repeatable afterwards because the NULLs are gone

## [0.67.0] — 2026-09-15

**Chunks are superseded, not deleted, so a citation keeps resolving.**

### Fixed

- `P1-32` a re-crawl of a changed page deleted the source's chunks and wrote new
  ones. `edges.supporting_chunk_ids` is an array of ids with no foreign key
  behind it — Postgres cannot enforce one on array elements — so every edge
  citing a deleted chunk was left pointing at nothing
- **The failure was silent in the worst available way.** §2.3 makes provenance
  mandatory on every edge, and an orphaned edge still *has* provenance: it
  carries a list of ids, passes every check, and only following the citation
  reveals there is nothing there. Nothing in the system follows
- Now the old rows are stamped `superseded_at` and stay. Citations keep
  resolving; an edge keeps the text it was actually **derived from**, which
  matters because §2.4 re-derives from source chunks and the page has since
  changed; and the sweep can reclaim what nothing cites, as a decision a person
  makes rather than one a crawl makes at write time

### Every query that serves the corpus filters on it

- Search, both arms — via `_conditions`, which is the single filter source, so
  the two cannot drift. Not a filter a caller may turn off: `include_duplicates`
  exists because a near-duplicate is a *verdict* worth re-examining, and a
  superseded chunk is not a verdict, it is text the page no longer has
- The source page, `/stats`, the MCP walk, the embedding queue and the novelty
  gate. The gate mattered more than it looks: without the filter, every chunk
  from the new crawl of a changed page would be marked a duplicate of the
  generation it just replaced — true, and exactly backwards, since the copy
  being pointed at is the one that is gone

### Schema

- The unique constraint on `(source_id, chunk_index)` becomes a **partial**
  unique index over the live set. The old chunks keep their indices, so a plain
  constraint would refuse exactly the replacement this exists to allow — at
  write time, on a re-crawl, at whatever hour the page changed
- `superseded_at` is exposed on `ChunkRead`, so a caller holding an id from an
  edge's provenance can tell "the text this was derived from" from "what the
  page says now"
- `purge_superseded` reclaims retired chunks nothing cites, reported by
  `worker.sweep` and applied only with `--apply` — the same shape raw files
  already use, for the same reason

### Testing

- That a **cited** superseded chunk is not reclaimable, which is the half that
  matters: if it were wrong the sweep would delete exactly the chunks an edge
  depends on, which is the orphaning this task exists to prevent arriving
  through the mechanism meant to prevent it
- That a second re-crawl does not re-stamp the first generation — the timestamp
  is the one thing the column is for
- That two *live* chunks still cannot share an index, so the partial index is
  permissive only where intended
- Search exclusion tested per arm, plus the converse that the replacement is
  found in its place — without which both would pass against a search that had
  stopped returning anything

## [0.66.0] — 2026-09-15

**The robots cache survives a restart.**

### Added

- `P1-29` a `robots_cache` table, and a `RobotsCache` that reads and writes it.
  The cache was in-process, so a restart re-fetched `/robots.txt` for every
  origin the crawl touched — and each of those queues in the same per-domain slot
  the pages do, so the first minutes back were spent asking permission
- **The raw file is stored, not the parsed rules.** Re-parsing on load is cheap,
  the compiled matchers are not serialisable in any form worth versioning, and a
  parser fix then reaches everything already cached rather than only what is
  fetched afterwards. The file is also the evidence for "why was this URL
  refused"
- A per-origin lock. At the start of a crawl a lane claims many URLs from one
  domain at once, and without it every one of them missed the empty cache and
  fetched the same file — a thundering herd aimed at the one file that asked to
  be treated gently

### Two layers, two clocks

- In memory, entries still expire on `time.monotonic()`, which is right there: it
  cannot be moved by NTP stepping the wall clock, so a correction mid-run cannot
  extend or void an entry
- Persisted, they expire on wall clock, because monotonic counts from an
  arbitrary origin — usually boot — and a stored monotonic deadline would be
  compared against a different clock after exactly the restart the row exists to
  survive

### `missing` and `unreachable` are different columns' worth of meaning

- Both store no body and they mean opposite things (§2.3.1.3): a 404 permits the
  whole origin, an unreachable server refuses it until the file can be read. The
  outcome is a column rather than inferred from `body IS NULL`, because
  collapsing them would turn every origin that was down at restart into one that
  had granted permission
- A refusal keeps its short TTL across the round trip. Caching "refuse
  everything" for a day because of one blip takes a domain out of the crawl for
  a day

### The cache cannot stop the crawl

- Every store call is wrapped: a failed load is a miss, a failed save is a fetch
  that happens again. An optimisation that can take the crawl down is worse than
  no optimisation — the failure guarded against is a database hiccup becoming
  "this worker refuses every origin"
- Without a store it is the in-process cache it always was, so every caller
  without a database keeps the previous behaviour

### Testing

- A *new* `RobotsCache` answering without fetching — a different object, since an
  in-memory double would make the claim trivially true
- Both halves of the outcome distinction after a restart, the TTL boundary in
  both directions, and eight concurrent requests for one origin producing one
  fetch
- The migration/model drift test caught a missing `created_at` index that
  `TimestampMixin` declares. Worth recording: the index is not decoration, and
  the check is the only thing that reports it

## [0.65.0] — 2026-09-15

**Steering: the weight vector is editable, and the floor is a guarantee.**

### Added

- `P6-12` `meridian_core/steering.py`, `/api/admin/topics`, `/api/admin/steering-log`
  and the Topics screen. §10's model — "attention is a weight vector over topics;
  seeds are drawn proportionally" — with the three places the obvious
  implementation is wrong

### Normalising is not dividing by the total

- Every active topic has a floor (§10's "5–10% minimum so nothing fully stalls")
  and a ceiling. Proportional scaling violates both the moment one topic
  dominates, and **a violated floor is the guarantee not existing**: the topic
  stalls, which is exactly what the floor was written to prevent, and a vector
  that sums to 1.0 looks correct from every angle
- Clamp-and-redistribute instead: each pass scales the still-free topics into
  what the clamped ones left, and anything landing outside its bounds is pinned
  there. Settles in at most one pass per topic
- Setting a weight holds that topic at exactly the value asked for and
  redistributes the rest. Scaling it along with everything else would show the
  person a different number from the one they typed

### Bounds relax on read and are refused on write

- Found by a test: pausing every topic but one leaves a single topic with a 0.6
  ceiling, and the seeds still have to come from somewhere. A ceiling guards
  against one topic crowding out the others; with no others it constrains
  nothing, so it yields
- Floors scale down together when they sum past 1.0 rather than raising on a
  read — a stored configuration that has become infeasible must not take down the
  screen that would let somebody fix it. The write paths still refuse to create
  one, because the moment to report a mistake is while it is being made

### A boost removes itself

- Applied at read time from `boost_factor` × `weight` while the expiry is in the
  future. §10 wants "steer back later without needing to remember", and the way
  that promise breaks is a boost written into the stored weight and a cleanup job
  that does not run
- An expired boost is left on the row on purpose. It is the only trace a
  temporary intervention leaves after it ends; it simply stops counting
- A factor with no expiry is refused. That is a permanent multiplier wearing a
  temporary one's clothes

### Nothing deletes

- Archiving and pausing drop a topic out of the pool and change nothing else.
  §10.2: nodes, edges and tags stay untouched, so returning is a status change
  rather than a rebuild — and the copy says so, because a reader who thinks
  pausing discards the weight will not pause
- `maintenance` is out of the draw too, per §10.2: it finishes its queue and
  starts nothing new

### The log explains weights nobody touched

- §10.1 is explicit that `steering_log` is not optional: "with two writers, the
  alternative is opening the UI in a month and not knowing why a weight is where
  it is." So every consequent change is logged, not only the requested one — the
  question is almost always about a topic somebody did *not* steer
- `reason` is optional in the request and never null in the log. Requiring a
  person to type one before moving a slider produces a column full of the word
  "update", so the server writes what was actually done

### Interface

- Both numbers per row: `share` is what gets drawn, `weight` is what is stored,
  and a note appears **only** when they disagree, naming the mechanism that did
  it. A note on every row is noise, and then the rows that need one stop standing
  out
- The share slider commits on release. Each change renormalises the vector and
  writes an audit row per topic that moved, so a drag would write hundreds
- Admin gained a sub-nav; Gazetteer and Topics are its two screens

### Testing

- Floors hold as a property across the whole result, not on one topic: clamping
  one can push the next under, and a spot check would not see it
- Both infeasible directions, the exactly-satisfiable boundary, all-zero weights,
  and a negative weight — which scaled proportionally would take share *away*
  from the pool
- Integration fixtures **restore rather than delete**: the dev database holds a
  real steered vector and the app commits on its own connection, so a test that
  left weights where it put them would re-steer a live crawl
- Cross-language drift on the four topic statuses, between the CHECK constraint
  and the dropdown

## [0.64.1] — 2026-09-15

**Fix: the lockfile had been stale for a dozen commits, so the images would not build.**

### Fixed

- `P1-37` `uv.lock` records all four workspace versions, and every release bumps
  them. Both Dockerfiles build with `uv sync --frozen`, which **refuses** when the
  lock disagrees with the `pyproject.toml` files — so every version bump
  committed without re-locking made the image unbuildable
- Silent in the worst way: nothing in the suite touched it, `uv run` re-locks in
  place so local work was unaffected, and the failure surfaces at
  `make build-push`. Which is to say at deploy time, from a commit that passed
  everything

### Added

- `tests/unit/test_lockfile.py` — the workspace members are read from the
  `members` glob rather than listed, so a service added and forgotten here is not
  the one whose version goes stale
- It checks version correspondence rather than shelling out to `uv lock --check`.
  The full check is the stronger assertion and also the slow one that fails for
  reasons unrelated to this repo; version drift is the failure this project
  actually produces, and catching it costs a file read
- Plus a test that both Dockerfiles still pass `--frozen`, since that premise is
  the only reason the others are urgent
- Verified by breaking it: reverting one locked version fails
  `test_the_lockfile_agrees_with_the_pyprojects` and nothing else

## [0.64.0] — 2026-09-15

**Admin exists, and it can tell you when an approved term matches nothing.**

### Added

- `P6-13` the first half of Admin: `/api/admin/*`, the gazetteer approval queue,
  and the nav the shell deliberately did not carry while Admin was unbuilt
- §5.6 ends with "approve in the UI — a two-minute weekly task". The two minutes
  are the design constraint: arrive with a list a regex proposed, leave with each
  term decided
- Every row reports **whether the matcher will actually load it**, computed
  against the whole approved set. An approved term whose wording another row
  already claims is withheld, so it reads approved and matches nothing in any
  document — extraction runs, the row looks right, and the term is simply absent.
  Nothing else in the system says so
- A collision names the other rows. "This term is not used" alone is a dead end;
  a curator cannot resolve a collision without being told what it collided with

### A rejected term stays rejected

- `gazetteer.rejected_at`. `approved` is a boolean and the queue has three
  answers: waiting, yes, and no. Without the third, rejecting can only mean
  deleting the row — and the harvest reads the same documents on every pass, so
  the queue refills with exactly what somebody already turned down
- The harvest reads the tombstone: a rejected term is not re-created, not
  corroborated, and not pushed over the auto-approval threshold by a later
  document agreeing with the one that was wrong
- Reversible. Putting a term back returns it **undecided** rather than approved —
  a second look should start from the question, not from the answer being
  reconsidered

### Admin fails closed

- `/api/admin/*` is the only part of the API that changes anything, and it
  refuses every request with 503 unless Cloudflare Access is configured or
  `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` says this instance is not exposed
- Open-unless-configured fails silently and in the wrong direction: the symptom
  is nothing at all until somebody finds the hostname. The same opt-out shape the
  MCP surface uses, for the same reason
- The 503 names both variables, because "service unavailable" would send whoever
  deployed it looking for an outage that is not happening

### Interface

- The header carries `Explore | Admin`. A source page marks Explore — marking
  neither while a reader is two clicks into the corpus would say the header does
  not know where they are
- Approve and turn-down are rendered from one shared class, not merely both
  uncoloured. §2 has no green and no red: any difference in weight reads as one
  being the safe option, and here neither is
- `/admin` matches as a prefix, since §12.6 has several more screens under it

### Testing

- A behavioural completeness probe that **every** admin route refuses when
  nobody is identified, rather than a spot check on one
- That no route under `/api/explore` accepts a write — §12.6's role boundary only
  holds if nothing crosses it, and a writable session now exists in this service
- The route walk it depends on asserts it found something first. It did not, at
  first: FastAPI 0.141 wraps included routers in `_IncludedRouter`, which exposes
  neither `path` nor `routes`, so the obvious traversal returned nothing and the
  assertion passed for the wrong reason
- PATCH semantics both ways: an omitted key is left alone, an explicit null
  clears. Without the distinction, sending one field would silently null every
  other column on the row a curator was about to approve
- Cross-language drift on the five entity types, between the CHECK constraint and
  the dropdown, and on three new DTOs through the existing `*_FIELDS` chain

### Fixed

- `SourceRead` gained `acronyms_harvested_at` in `0.63.0` and the TypeScript
  client did not. The cross-language drift test caught it; it had not been run
  in that commit

## [0.63.1] — 2026-09-15

**Fix: an ambiguous term was contributing nothing at all.**

### Fixed

- `P5-02` a row flagged `ambiguous` now keeps its canonical and loses only its
  aliases. It was withholding every surface form, so the one seeded term carrying
  the flag produced no patterns — the full official name, which is the most
  reliable string in the table, never matched anything
- The flag means "the short ways of saying this are not decidable", not "this
  thing cannot be named". An acronym or an anaphoric alias is a mention whose
  context may be insufficient; the full form a curator wrote down to identify the
  term is not one, and withholding it buys nothing
- Where a canonical genuinely does collide, the collision rule already catches it
  — from two rows claiming the same string, which is evidence, rather than from a
  flag somebody remembered to set
- Same fix makes the harvest's own output more useful: two documents disagreeing
  about an acronym now withholds the acronym while the expansion each was found
  beside still loads, which is what those documents actually established

### How it was found

- By building `P6-13`'s approval screen, which reports per row whether the
  matcher will actually load it. The first page showed a term marked approved and
  loading nothing. Nothing else in the system says that: extraction runs, the row
  reads approved, and the term is simply absent from every document

## [0.63.0] — 2026-09-15

**The gazetteer grows itself, and is not allowed to decide anything.**

### Added

- `P5-02` the gazetteer compiled into spaCy `EntityRuler` patterns, and §5.6's
  acronym auto-harvest as a pass — `python -m worker.harvest`
- §5.6 is blunt about where this table comes from: **"do not hand-write it —
  bootstrap it."** Fifty terms seeded by a person, then the observation that
  government and academic documents define their acronyms on first use. One
  regex over text that has already been extracted
- `sources.acronyms_harvested_at` plus a partial index — NULL is the whole queue,
  the same shape the novelty gate uses on `chunks`. A timestamp rather than a
  boolean, because the harvest's rules will change and a re-harvest then needs to
  be targetable at everything read before a date
- A document is rejoined from its chunks before being read. A definition split
  across a chunk boundary is invisible to both halves, so a chunk-wise harvest
  would lose a fixed fraction of every long document, silently

### The ruler overrides the model, so three rules about what does not load

- **Unapproved rows do not load.** That is where harvested and model-proposed
  terms wait. Loading them would make the approval queue decorative and let a
  regex's mistake take the model's say away on every document mentioning the term
- **Rows flagged ambiguous do not load.** Where context is insufficient a mention
  must be left *unresolved* rather than guessed: a wrong resolution corrupts the
  graph invisibly, an unresolved one stays visible and fixable. A high-precedence
  pattern is exactly a guess made without context
- **Surface forms that collide are withheld even when nothing is flagged.** The
  flag is hand-maintained and will drift; a collision is the same fact observed
  rather than declared. Without this the ruler keeps whichever pattern it saw
  first, and the choice between two readings is made by row order

### The case rule

- A short all-caps form is matched **case-sensitively**; everything else on
  `LOWER`. "ODD" matched case-insensitively fires on the ordinary English word,
  and every hit becomes a curated, high-precedence entity in a research corpus.
  The mirror failure is much cheaper: it misses a long form in lower-cased prose,
  and nobody writes a sentence that accidentally spells out a three-word agency
- A single newline is **not** a sentence boundary. Extracted PDF text breaks
  lines mid-sentence constantly, so treating every line break as a sentence end
  would reject most real definitions in exactly the documents this pattern is
  high-yield in. A blank line is a paragraph break and does count

### What the harvest may and may not do

- Nothing it finds is approved. A term needs a person, or `APPROVAL_THRESHOLD`
  separate documents defining it the same way
- Corroboration is counted in **documents, not occurrences**. A report that
  defines a term in its glossary and again in each of forty sections has said one
  thing forty times, and counting hits would auto-approve on one author's typo
- It never edits an approved row beyond its count. An approved row is already
  loaded into the ruler, so an alias appended by a regex would take effect with
  nobody having agreed to it — the approval queue bypassed by the one mechanism
  it exists to hold back
- Two expansions for one acronym is the finding, not the failure: both rows are
  flagged ambiguous, which keeps both out of the ruler and hands the mention to
  the resolver, which can see the rest of the document

### spaCy is an optional extra

- `meridian-worker[ner]`, not a dependency. The fast loop does not run NER yet —
  `P5-01` is the task that introduces it — and thinc, blis and a model file in
  the image that fetches web pages is disk and build time the Pi spends on
  something nothing calls. `MERIDIAN_SPACY_MODEL=blank` runs the curated terms
  with no statistical model at all
- The patterns are built in `meridian_core`, which needs none of it, so what
  loads and what is withheld is testable without spaCy installed

### Testing

- Every bracket that matches `Full Name Here (ACRONYM)` on shape alone and must
  be refused: `(PDF)`, `(see Figure 3)`, `(USD)`, `(ii)`, `(2026)`, `(Q3)`.
  Admitting one is not a transient error — it is a permanent row in the table
  entity resolution consults
- That a term is not approved one document short of the threshold, that one
  document repeating itself counts once, and that a curated `approved=false` row
  is never auto-approved by corroboration
- That an ambiguous term is withheld from the matcher *even once approved* — the
  two mechanisms answer different questions and have to compose
- Against real spaCy where the extra is installed: the ruler lands in front of
  `ner`, a reload replaces rather than stacks, a match carries its row id, and an
  acronym does not match its lower-case homograph
- New drift tests over `config/schedule.yaml`: every module named there is
  importable and has an entry point, intervals are positive, `args` is a list of
  strings, and the sweep still has no `--apply`. Those names are strings in a
  YAML file and nothing else in the toolchain looks at them

## [0.62.0] — 2026-09-15

**A wedged worker can be told from a busy one.**

### Added

- `P5-08` a liveness heartbeat, a container healthcheck for the worker, and the
  systemd units for off-device backups
- **`restart: unless-stopped` only covers a worker that exits.** It does nothing
  for one still running and no longer working — a wedged fetch, a pool that
  never recovers, a lane stuck on a lock. From outside, that is
  indistinguishable from a healthy worker that happens to be busy, which is why
  the restart that would fix it never fires
- The loop touches a file each iteration, **before** the work rather than after:
  a lane that wedges *inside* a fetch should stop beating, and a beat at the end
  of the loop would only stop once the wedge cleared
- A file rather than a port. The worker serves no HTTP and should not start to
  be observable, and the check needs no credentials. It lives in the container's
  tmpfs, so it cannot survive a restart and be mistaken for a fresh one
- A **missing** heartbeat is not alive. `start_period` covers startup;
  afterwards it means the loop never reached its first iteration, which is
  exactly the state worth restarting

### The backup is a systemd timer, not a scheduled job

- It looks like an inconsistency with `P5-06` and is not. `backup.sh` needs
  `docker compose exec postgres pg_dump`, so it needs the Docker socket — and
  giving the worker container that socket would hand the process that fetches
  hostile web pages control of the host's container runtime. The timetable runs
  what belongs in the container; the host runs what belongs on the host
- `Persistent=true`, so a backup missed because the machine was off runs on the
  next boot. That is the backup you wanted
- `RandomizedDelaySec=1h`, because several machines backing up at exactly 00:00
  is a thundering herd against whatever holds the volume

### Testing

- That a stale heartbeat is *not* alive. A liveness probe that passes on a dead
  process is worse than no probe, because it also suppresses the restart
- That an unwritable path does not take the crawl down — monitoring that causes
  the outage it exists to detect
- That the healthcheck reports by exit code, since Docker reads that and not the
  output: a check that printed a problem and exited 0 always passes
- Compose drift tests for the worker's healthcheck and for every long-running
  service being probeable

## [0.61.0] — 2026-09-15

**What arrived while you were away.**

### Added

- `P6-11` the since-last-visit delta. `GET /api/explore/stats?since=` returns
  `new_sources` and `new_chunks`, and the landing state shows them. §12.5 asks
  for it because "what is new *to me*" is the question somebody opens this with,
  and a total answers a different one
- **Three states, not two.** `null` means this reader has never been here, so
  there is no "since" to speak of and claiming one would be inventing history.
  `0` means they have and nothing arrived — worth stating, because a silent
  panel reads as one that failed to load. Anything else is the delta
- **When the stamp advances is the whole design.** It is read once per session
  and written immediately, so the delta means "since you were last here" rather
  than "since a second ago". Writing it on render would make the number vanish
  as you looked at it; writing it later means the second render reads a stamp
  the first one just wrote, and the delta is permanently zero — which looks
  exactly like a corpus where nothing happened
- Per viewer, per browser, and that is correct: two people looking at the same
  Meridian have genuinely different answers to "what is new to me". It belongs
  in `localStorage`, not in a table

### Testing

- Every `localStorage` access is guarded and tested: a stored value that is not
  a timestamp (it would otherwise reach the API as a query parameter and come
  back a 422 the reader cannot act on), storage that throws on read, and storage
  that refuses to be written
- That the stamp is read and advanced in a *single* call, because doing it in
  two places is precisely how it ends up advanced before it was read
- That one source is not called "sources"

## [0.60.0] — 2026-09-15

**What happened while nobody was looking.**

### Added

- `P6-08` the notifications panel: `GET /api/explore/notifications`, a typed
  client method and a `NotificationsPanel` component
- The in-app counterpart to `P5-07`'s digest, reading the same rows. An alert is
  recorded *before* it is delivered, so a deployment with no bot token still has
  somewhere to see what would have been sent
- **Filterable by type, not by read state.** The model already said why and it
  is right: the useful question is "what finished" or "what needs a decision",
  not "what have I glanced at". A read/unread split turns a panel of findings
  into an inbox, and an inbox gets cleared without being read
- Counts cover **every** type, not the filtered set. A panel reading
  "alerts (0)" while three seed proposals wait is the filter hiding the thing
  the reader came for
- An empty panel says *why* it is empty: alerts fire on sustained conditions, so
  silence is a claim rather than an absence of data
- Alerts carry a heavier border as well as the attention hue — §2's "state in
  form, not only colour", so the distinction survives a colour-blind reader and
  print

### Testing

- That the timestamp is not parsed into a `Date`: `new Date('2026-09-15')` is
  UTC midnight and renders as the 14th in any negative offset, which is the same
  trap the API client documents for publication dates
- Ruff caught a duplicate test name — a second `test_an_oversized_limit_is_refused`
  would have silently shadowed the first, so one of them would never have run

## [0.59.0] — 2026-09-15

**The timetable lives in the database.**

### Added

- `P5-06` `scheduled_jobs`, `meridian_core/schedule.py` and
  `python -m worker.scheduler`. §13.1's corollary, implemented: *"no cron files.
  The scheduler reads its timetable from the DB so schedule changes are a UI
  action."* A crontab on the box is configuration nobody can see from the
  interface, cannot change without SSH, and does not travel with a snapshot — a
  corpus restored elsewhere arrives with no idea what was meant to be running
- **The queue's shape, reused.** `FOR UPDATE SKIP LOCKED` picks a due job and a
  *lease* holds it, so two schedulers running by accident — a systemd timer and
  a container, a deploy overlapping a restart — get different jobs rather than
  both running the same backup. A scheduler that dies mid-job leaves the claim
  to expire; a status flip would leave it "running" forever
- **Missed runs are run once, not caught up.** A machine off for a day leaves a
  daily job overdue by 24 hours; rescheduling from *now* rather than from the
  old `next_run_at` means one run and then the normal cadence. The alternative
  is a burst of catch-up runs the moment the machine returns
- **An interval, not a cron expression.** §13.2 wants these editable from a UI,
  where "every 6 hours" is a number and `0 */6 * * *` is a support question —
  and parsing one is a dependency
- **`python -m <module>`, never a shell.** The module comes from a row a UI can
  edit, and a row that could name a shell command would make the timetable a
  remote execution surface for anyone who could write to that table
- A repeatedly failing job backs off rather than being disabled: disabling needs
  a person to notice and re-enable, backing off recovers on its own
- `config/schedule.yaml` seeds four jobs at first boot — digest, embed, novelty
  and sweep. **Sweep without `--apply`**: it is the only pass that destroys
  something a re-crawl cannot reproduce, and it should not do that on a timer
  without somebody reading the report

### Fixed

- `extra={"module": ...}` **raises**. `logging` refuses an `extra` key that
  shadows a `LogRecord` attribute, and it fails only on the line that logs it —
  so the scheduler started, claimed a job, and died as it tried to say which
  module it was about to run. Recorded in the handover with the full reserved
  list

### Testing

- Thirteen tests, against a real Postgres because the claim is `SKIP LOCKED` and
  its whole point is what two concurrent schedulers see
- That a held job is not claimed twice, that an expired claim is taken over, and
  that shutdown hands claims back rather than making every job wait out its lease
- That the next run is measured from **now** and not from the missed slot
- That one success clears a backoff, so a transient failure does not become a
  permanent slow cadence nobody remembers to reset
- The drift test caught the new enum having no DTO alias, unprompted

## [0.58.0] — 2026-09-15

**Sustained conditions, not events.**

### Added

- `P5-07` the digest and alert pass: `python -m worker.digest`. §12.5's health
  line sent rather than logged, and §13.3's alerts underneath it
- §13.3 names the failure mode and it shaped everything here: *"Single-event
  alerting teaches me to ignore the channel, which is the real failure mode."*
  Every condition is measured over a window, and every alert is suppressed for a
  cooldown after firing
- **Suppression lives in the database.** The digest runs on a timer and exits;
  anything remembered in memory would be forgotten before the next run and the
  same alert would arrive every time the timer fired — single-event alerting
  wearing a different hat. `notifications` already existed for this, so the
  in-app panel (`P6-08`) will show exactly what was sent
- Four conditions, all derived from rows rather than counters: fetch success
  below threshold over a window, nothing fetched for hours (a worker that died
  has no failures to lower a rate with — the silence is the signal), disk above
  80%, and **the frontier drained**. That last one is the failure that cost
  `v0.26.0`: a crawl that empties its queue and idles logs exactly what a
  healthy one logs, because there are no errors, there is simply no work
- Findings are recorded *before* they are sent, so a send that fails loses the
  delivery and not the evidence. A deployment with no bot token is not a
  deployment with no monitoring
- No Markdown parse mode. A digest interpolates URLs, titles and error strings
  from crawled pages, and an unbalanced asterisk in somebody else's page title
  would make Telegram reject the whole message — losing an alert to a formatting
  character

### Fixed

- **The digest reported zeros for everything.** `fetch.total` and
  `novelty.duplicate_rate` are neither of them real attributes, and
  `getattr(..., default)` turned both into permanent, plausible zeros: it said
  "nothing attempted, 0% duplicate" on a corpus with 28 judged chunks and 2
  duplicates. Every field is now read directly, so a renamed attribute raises
  instead of lying. A defaulted lookup in a monitoring tool makes the thing
  whose job is reporting problems report none

### Testing

- Mostly about *not* alerting: too few attempts to judge (0% over three is
  noise, over three hundred is an outage), a healthy rate, failures that fall
  outside the window, and a condition already reported
- That an alert names *what* went wrong and not just the rate — §12.5's point
  that 40% `robots_denied` and 40% `timeout` need different people looking
- That suppression expires, and is per condition, so one noisy condition cannot
  silence a real one
- The alert fixture clears by **condition key** rather than by title, because a
  stale row *suppresses* the next alert rather than failing visibly — which made
  the file pass alone and fail in a full run for a reason that looked nothing
  like its cause

## [0.57.0] — 2026-09-15

**A source reads as a document.**

### Added

- `P6-21` the source detail page: `/sources/{id}`, pulling together the header,
  the passages in document order, the figures panel (`P6-14`) and the exports
  (`P6-15`). The first screen where the corpus reads like documents rather than
  results — and the place several endpoints built today finally have to live
- **Provenance is the page, not a footnote on it.** Tier, date, DOI and
  `extractor` are in the header, because "what is this and how do I know" is the
  question a reader arrives with — and `P1-44`'s extractor is the difference
  between a document that had no text and one whose extractor fell over
- A source with no text is presented as a finding rather than a failure. §6.5
  makes metadata-only a valid resting state: a scan, or a paywall. It is still
  citable and still counts toward coverage, and the page says so
- Results now carry two destinations, kept distinct: the title goes to the live
  page, "in this corpus" goes to what Meridian actually holds. They answer
  different questions, and a reader chasing a citation usually wants the second

### A router, deliberately hand-rolled

- Forty lines, no dependency. There are two routes, and a dependency for two
  routes is an upgrade path inherited for the life of the project
- It does the one thing that matters here: **URLs are real.** A corpus that
  insists everything be checkable cannot make its own documents unaddressable
- It will stop being the right answer — phase 6 has fifteen more screens, and
  when nested layouts or route-level loading arrive this should be replaced
  rather than grown. `P6-20`
- A modified click is left alone. A reader holding ⌘ is asking for a new tab,
  and swallowing that is the most irritating thing a hand-rolled router can do

### Testing

- That what the UI writes into an `href`, the router reads back — the property
  that keeps a citation shareable
- That a non-numeric id does not coerce: `/sources/../../etc/passwd` parses as
  explore, not as a request for `/sources/NaN`
- Component tests with a mocked client, covering the two states that are not
  success: an error that names its cause (§4), and a textless source that reads
  as a finding

## [0.56.0] — 2026-09-15

**Figures become openable.**

### Added

- `P6-14` the figures panel: `GET /api/explore/sources/{id}/figures`, a raw-file
  route, a typed client method and a `FiguresPanel` component
- §12.5 asks for "thumbnails linked to the node". **There are no thumbnails** —
  nothing downloads figure images (`P1-10`), so a placeholder grid would be a
  promise the corpus cannot keep. What there is, is what §6.6 says carries most
  of the value: the caption, *"often the most information-dense sentence about
  the figure"*
- Two links per figure, and the difference is the point. `image_url` is the
  picture where the publisher has it — live, and liable to move. `raw_url` is
  this corpus's own copy at the figure's page, which is what §5.4 keeps raw
  files *for*: link rot is the binding reason, and `#page=N` is the whole of
  "page-accurate"
- **Raw files are not served unless `MERIDIAN_SERVE_RAW` says so.** The raw
  store holds third-party material kept as a research archive (§14.2); serving
  it is redistribution rather than reading, and the default has to be the safe
  one. `P3-10`'s `grants.raw_files` is the per-person version beneath it
- When raw serving is off the link is **absent with a line saying why**, not
  broken. A caption with a dead link is worse than a caption alone — the reader
  spends a click finding out

### Security

- **The raw path comes from the database, never the request.** The caller
  supplies an integer and `raw_file_path` is read off the row. That is not a
  hardened traversal check; it is the absence of anything to traverse, which is
  a stronger property than validating a user-supplied path would be. A
  containment check sits behind it anyway, for a corpus restored from elsewhere

### Testing

- That a path fragment cannot be a `source_id` — 404 or 422, never a file
- That the link is absent rather than broken when raw serving is off, and that
  a figure with no links at all still renders its caption: a PDF caption has no
  addressable image, which is the ordinary case for the PDF path
- Both new DTOs added to the cross-language drift pairs, so the contract is
  protected rather than merely written

## [0.55.0] — 2026-09-15

**Material can leave.**

### Added

- `P6-15` Export — BibTeX and Markdown, in `meridian_core/export.py` and behind
  `/api/explore/export/{bibtex,markdown}`. §12.5: *"avoid trapping material in a
  bespoke store"*
- The requirement is about leaving, and it is not a courtesy. A corpus readable
  only through its own interface is a bet that the interface outlives the
  research, and that bet is usually lost
- **Nothing is generated.** A field the document did not carry is omitted rather
  than guessed: a fabricated author or year is wrong in a file somebody pastes
  into a paper, where nothing will check it against the source again
- TeX escaping, because an unescaped `&` in a title does not fail here — it
  fails weeks later in the document it was pasted into, as an error nobody
  traces back
- Citation keys are stable across exports (a bibliography is re-exported and
  diffed, and a moving key rewrites every citation referencing it) and carry the
  source id (two reports from one body in one year is the ordinary case, and
  colliding keys drop entries with no error in any tool involved)
- **The entry type is a format decision, not a verdict.** §8 extracts structure
  and scores nothing, and a bibliography is exactly where an implied ranking
  would do damage — `@article` versus `@misc` reads as a judgement if allowed
  to. The tier rides verbatim in `note` instead
- Exports name their sources explicitly rather than taking a query. A
  bibliography is what a person kept after reading; exporting a result set
  produces a file whose contents depend on a ranking that moves as the corpus
  grows

### Testing

- Twenty-one tests, mostly about what is *not* in the output, because both
  failures this can have are invisible from here and surface in someone else's
  tool
- A completeness probe that every `SOURCE_TIER` value maps to an entry type — a
  new tier would otherwise fall to the default silently and be mis-formatted in
  every bibliography until somebody noticed
- Verified against the real corpus: valid BibTeX with `%` and `_` escaped inside
  URLs, and Markdown with page/offset labelled correctly per `P2-18`

## [0.54.0] — 2026-09-15

**The escape hatch, behind the narrowest role there is.**

### Added

- `P3-04` `run_readonly_query` — §12.4's escape hatch, as an MCP tool. One
  SELECT, on a `meridian_guest` connection, bounded by a statement timeout and a
  row cap
- **The enforcement is the role, not the module.** `meridian_guest` (`P3-07`)
  has SELECT on the corpus and the graph and nothing else — no `agent_tokens`,
  whose `token_hash` is the one secret in the schema, and no `fetch_policy`.
  Arbitrary SQL cannot talk its way past a privilege it does not hold, and the
  textual checks are a courtesy that makes a refusal legible rather than the
  thing keeping it safe
- **The timeout is what makes it exposable.** The role stops a query reading
  what it must not; it does nothing about one that reads what it may, forever —
  a cartesian join across the corpus is a perfectly legal SELECT. `SET LOCAL`,
  so it dies with the transaction rather than riding a pooled connection to its
  next borrower
- Every query is logged, succeeded or not, because §12.4 asks for exactly that:
  *"watch which queries the agent writes there — those are the next curated
  tools."* A query that timed out says as much about a missing tool as one that
  worked
- The tool is registered **only when a guest connection exists**. A tool that is
  always going to fail is worse than an absent one: the tool list is a model's
  entire view of what it can do, and it will spend a turn discovering otherwise
- Truncation is reported rather than implied. Returning exactly the limit is
  indistinguishable from "that was all of it", and an agent would report a
  partial count as a finding

### Fixed

- A statement timeout surfaces as `DBAPIError`, which is the **parent** of the
  `DatabaseError` this caught — so the one failure the design creates on purpose
  escaped as a stack trace. Found by testing the timeout rather than reasoning
  about it

### Testing

- 23 tests against a real Postgres, because the enforcement *is* Postgres. Tests
  that only exercised the regexes would be testing the courtesy while the thing
  that keeps this safe went unverified
- That `agent_tokens` and the operational tables are unreachable — refused by a
  privilege, not a pattern
- That a slow query is cancelled, that the timeout does not leak onto the next
  query, that the cap truncates *and says so*, and that a result which fits is
  not marked truncated — a `truncated` flag that is always true says nothing

## [0.53.0] — 2026-09-15

**Search stops being lexical-only.**

### Added

- `P2-17` the embedding sidecar. `python -m worker.embedserver` serves the same
  model the corpus was embedded with, and `meridian_core.embedder.RemoteEmbedder`
  is the client. `P2-07`'s `embed_query()` seam is filled
- **Why a sidecar rather than either obvious alternative.** Depending on
  `sentence-transformers` in the API puts 2.3GB of weights and a cold start in
  an HTTP request path. Accepting a vector from the caller is worse than it
  sounds: not mainly a security problem — `<=>` takes a vector, not SQL — but
  that a vector from a *different model is meaningless against this corpus*.
  Two models' embeddings of the same phrase are points in unrelated spaces, and
  comparing them computes without erroring. The result is plausible, ranked,
  confident nonsense
- It runs from the **worker's image with a different command**, because that is
  the one image here already carrying the model. A separate service would mean
  a second download and a second resident copy on a machine that has one of
  each. On `internal`, no credentials, no `env_file` — the reasoning that keeps
  `crawl4ai` from holding database passwords
- Every response names the model, so a caller can tell it is talking to the one
  its corpus was built with

### Fixed

- **An absent embedder and a broken one reported identically.** Found by killing
  the sidecar and reading what the API said: "This API has no embedder", when it
  had one that was down. Those are different facts and a reader acts on them
  differently — one is a choice somebody made, the other is an outage somebody
  should fix — and reporting an outage as a deployment choice is how a broken
  dependency goes unnoticed for a week. It is also the same mistake
  `degraded_reason` exists to prevent one level up, where an empty result set is
  not allowed to look like an empty corpus

### Testing

- The alignment check earns most of the client's tests: a sidecar returning
  fewer vectors than texts would pair each with the wrong one, and every value
  involved is a valid float of the right length. Nothing downstream can detect
  it — a chunk would be searchable under someone else's meaning, permanently,
  with no symptom but bad results
- That an unavailable sidecar *raises* rather than returning nothing, so the
  caller decides whether to degrade. A client that silently returned no vectors
  would make an outage indistinguishable from a deployment that never had one
- Verified end to end against the real corpus: both arms ran with
  `degraded: false`, then the sidecar was killed and the same search still
  answered on one arm — now saying so correctly

## [0.52.0] — 2026-09-15

**A hosted client can find out how to authenticate.**

### Added

- `P3-09` server half. The MCP surface advertises
  `/.well-known/oauth-protected-resource/mcp` when authentication is on. A
  hosted assistant is handed a URL and nothing else — no config file, nowhere
  to put a header — so it has to *discover* the authorization server, and
  without this a phone given the URL can only fail
- Anonymous mode advertises nothing, which is not cosmetic: a server offering an
  authorization endpoint it does not enforce sends a client through an OAuth
  flow for no reason

### Fixed

- **Tokens issued for a different resource were accepted.** The SDK leaves
  `validate_token_resource` off and defaults it on in 3.0; off, this surface
  would take an otherwise-valid token minted for another service on the same
  issuer. That is the same mistake as verifying an Access assertion without
  checking its audience, and it was sitting in a deprecation warning. The
  verifier now stamps the resource — truthfully, since Meridian's credentials
  are rows in this database issued for this server and no other — and the
  setting is on

### Docs

- `docs/setup.md` §8 is now a runbook rather than a list of blockers: point a
  tunnel at the API, put Access in front, tell Meridian, **verify before
  trusting it**, connect the assistant. The verification step includes sending a
  forged `Cf-Access-Authenticated-User-Email` and confirming it is not believed
- It warns that a startup line reading *not configured* means every request is
  unauthenticated, because that is the failure someone would otherwise discover
  by being asked why their corpus is public

## [0.51.0] — 2026-09-15

**Figures become findable, without a model touching them.**

### Added

- `P1-10` `extract/figures.py`, `meridian_core/figures.py`, and the write in the
  keep transaction. The `figures` table has existed since `P0-06` and had no
  writer at all; it now fills at ingestion
- §6.6 is explicit about where to start — **"Start with captions, not vision.
  Figure captions are text, usually extractable, and often the most
  information-dense sentence about the figure"** — so this extracts captions
  and alt text and nothing else. No image bytes, no bounding boxes, no model.
  That is not a stub: a caption plus a page is already a citable claim about
  what a figure shows
- Two inputs, two genuinely different problems. **HTML has semantics**:
  `<figure>`/`<figcaption>` says outright what is a figure, and `alt` is a
  description written for the purpose. **A PDF has none**: a caption is
  distinguishable only by convention — a line beginning "Figure 3:" — and that
  convention is near-universal in what this corpus collects
- The PDF path carries a page and no bbox. The page is known exactly, the
  position on it is not known at all, and a fabricated bbox would put false
  precision on a citation someone follows
- New column `figures.image_url`. `file_path` is a *local* path and nothing
  downloads figure images, so without this a row describes a picture nobody
  could ever look at and `P7-07`'s enrichment would have nothing to fetch

### Testing

- Nineteen extraction tests, and the rejections carry the weight: an image with
  neither caption nor alt text is not a figure, `Figure 4.` alone is a label
  without a caption, and "Figure out the cost" is prose. A figures table full of
  logos and spacers makes every count larger and nothing findable
- That when the per-document cap truncates, marked-up `<figure>` elements
  survive and bare `alt` attributes are dropped — the cap must shed the weaker
  evidence, not whatever came last
- Six persistence tests: replacement rather than accumulation, the empty case
  that a replacement which only inserts would get wrong, the cascade, and that
  one source's figures are not another's
- End-to-end through the worker to a **committed row**, per the rule `P1-28`
  bought

## [0.50.0] — 2026-09-15

**The Access header is never trusted.**

### Added

- `P3-08` `services/api/api/access.py` — Cloudflare Access assertions verified
  cryptographically against the team's published keys, with the audience and
  issuer checked, on every request
- **This is the whole task.** A header is a string, and an application that
  reads an identity out of one is a single misconfiguration from letting anyone
  assert any identity by typing it — a direct port publish, a second ingress, a
  reverse proxy added later for an unrelated reason. None of those looks like a
  security change when it is made, which is why verification is unconditional
  rather than a belt-and-braces extra
- There is deliberately **no path where an unverifiable assertion is treated as
  anonymous-but-allowed**. That path is the bug
- A JWKS endpoint that cannot be reached refuses rather than bypasses. Letting
  requests through when the keys are unfetchable converts a dependency outage
  into an authentication bypass, at exactly the moment nobody is watching
- Both `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` are required. A team domain
  alone verifies that *some* Access application signed the token, including one
  belonging to a different service on the same team
- `/health` bypasses, because a watchdog cannot complete an SSO flow — and it
  reports liveness and a database connection and nothing about the corpus, so
  exempting it discloses nothing. The exempt set is explicit paths, not a
  prefix: a prefix exempts every route added under it later, and nobody
  revisits an exemption when adding a route
- Not configured means not installed, with a startup line saying so. This is for
  a service behind a tunnel, and demanding it locally would push everyone into
  disabling it — `P3-03`'s token check does not depend on deployment shape and
  fails closed on its own

### Testing

- Twenty tests against a locally-generated RSA key, so every branch is
  reachable without a Cloudflare account
- The attacks, individually: an assertion signed by a different valid key, an
  `alg: none` forgery, one issued for another Access application, one from
  another team, an expired one, and one with no expiry at all — a token that
  never expires is a credential nobody can withdraw
- **That the identity comes from the verified claims and not from the
  headers.** Cloudflare also sends `Cf-Access-Authenticated-User-Email`
  unsigned; a request carrying a genuine assertion for one address and that
  header claiming another must resolve to the signed one

## [0.49.0] — 2026-09-15

**A citation can say what its number counts.**

### Fixed

- `P2-18`, all four things writing the frontend client found awkward in
  `P2-07`'s surface
- **`page_or_offset` can now be interpreted.** §5.3 defines it as a page number
  for paginated documents and a character offset otherwise — a rule every
  consumer had to know and apply for itself, from a media type the hit did not
  carry. `page_unit` is derived once, in core, and the UI labels the number
  instead of printing "page/offset". None when the media type was never
  recorded: guessing "offset" mislabels every PDF and guessing "page" mislabels
  every web page, and a wrong label on a citation someone will open is worse
  than an honest hedge
- `arms` is `list[SearchArm]` rather than `list[str]`, so the value set crosses
  the boundary the way `SourceTier` does. A frontend inventing its own union was
  the one type the cross-language drift test could not protect
- `detail` is always a string. FastAPI returns one from `HTTPException` and a
  list of objects from validation, so every client normalises both shapes or
  renders `[object Object]` at the moment someone needs to read the message. The
  structured form is kept alongside under `errors`, so a client highlighting the
  offending field does not have to parse prose to find it
- `/stats` carries `as_of`. These are the numbers someone quotes as "the corpus
  has N documents" months later, and a client could not tell a cached figure
  from a fresh one

### Testing

- The cross-language drift test caught both changed DTOs without being asked,
  and TypeScript caught the test fixture that had not been updated. That is the
  two-link chain working exactly as `P2-13` designed it
- That the UI hedges *only* when the media type is genuinely unknown. The
  fallback has to stay and has to stay rare — hedging on every source teaches
  readers the label carries no information

## [0.48.0] — 2026-09-15

**The MCP surface refuses by default.**

### Added

- `P3-03` enforcement. `services/api/api/auth.py` bridges a bearer token on the
  wire to a refusal inside a tool: a `TokenVerifier` resolving against
  `agent_tokens`, and a `require_tool` guard on every tool
- **Anonymous access is an explicit opt-out, not a default.** The surface
  refuses unauthenticated callers unless `MERIDIAN_MCP_ALLOW_ANONYMOUS` is set.
  The alternative — open unless configured — is one forgotten environment
  variable away from publishing the corpus, and the person who forgets is
  deploying rather than reading the source
- **Two checks, not one.** The transport verifies the token; each tool then
  checks that *this* token was scoped to *it*. §11.4's point is that an
  interactive session holds read tools while only the orchestrator carries write
  ones, and that distinction cannot live at the transport — once a request is
  authenticated, which tool it asked for is the only thing separating them
- A refusal names the tool and never the credential. A caller with a valid token
  is entitled to know which capability it lacks; it is not entitled to anything
  about the token
- `AuthSettings` is built only when authentication is on, because it needs
  issuer and resource URLs a loopback machine has no answer for — demanding them
  there would push everyone toward turning auth off to get started

### Testing

- Fourteen tests, refusal-first. A caller wrongly refused reports it; a caller
  wrongly served does not
- That a half-set opt-out does not read as permission: `""`, `"false"`, `"no"`,
  `"0"` and `"maybe"` all keep the door shut. A template's empty value and
  someone's attempt to turn it *off* must not turn it on
- A completeness probe over every registered tool, because a tool added later
  without the guard is reachable by anyone who reaches the transport and its
  absence looks like nothing
- The probe also turned up an ordering worth knowing: argument validation runs
  *before* the tool body, so an unauthenticated call with bad arguments fails as
  validation. Not a leak — a configured verifier rejects at the transport first,
  and in anonymous mode the schema is public via `list_tools` anyway

## [0.47.0] — 2026-09-15

**Scoped credentials, before anything is exposed.**

### Added

- `P3-03` `meridian_core/tokens.py` — §11.4's model made enforceable: issue,
  resolve, revoke, with tool scope, expiry and a revocation switch
- **Secrets are never compared in code.** The presented token is hashed and the
  hash is looked up, so no branch's duration depends on how much of a token was
  right and a log line that accidentally included the row would include a hash
- SHA-256 rather than bcrypt, deliberately. Password hashes are slow to defend
  low-entropy human choices against offline guessing; these are 256 bits of
  `secrets.token_urlsafe`, where an attacker holding the hash cannot guess the
  preimage at any cost and a slow hash would only add latency to every request.
  The scheme is named in the stored value so changing it is a migration rather
  than archaeology
- **An unscoped token grants nothing.** `allowed_tools` is nullable and NULL
  means *no tools*. Same argument as `P3-07`'s refusal of default privileges:
  the nullable column's empty state is what a row created without thinking will
  hold, so it must be the safe one
- Every rejection returns None — unknown, revoked and expired are not
  distinguished to the caller. Telling an unauthenticated client which applies
  confirms that a token exists or once existed, and "revoked" in particular
  confirms a real credential was guessed. The operator gets the reason in the log
- Revocation rather than deletion, so "which agent read this, and when was its
  access withdrawn" outlives the credential

### Testing

- Twelve tests, mostly rejections: unknown, empty, revoked, expired, and the
  expiry boundary — a token expiring *at* an instant is expired, because the
  other rounding leaves a credential valid for the moment it was meant to stop
  being valid, which nobody notices until an audit
- That the secret does not survive issuing, that the resolved scope does not
  carry the row containing the hash, and that all three rejection paths are
  indistinguishable from outside

### Not closed

- `P3-03` stays `[~]`. This is the mechanism; wiring it into the MCP surface as
  an enforced verifier lands with `P3-05`, and until then `/mcp` authenticates
  nobody. That is survivable only because it is not exposed — `api` sits on
  `internal` and is published to loopback — and it must not reach a tunnel first

## [0.46.0] — 2026-09-15

**The frontend shows the corpus.**

### Added

- `P2-08` The Explore page, wired. `SearchField`, `CorpusCounts`, `EntryPoints`
  and `WhereYouWere` were already built as components (`P2-16`); this is the
  page, the results list, and the client between them. The `Lockup` goes in the
  header, so the mark finally appears in the application it belongs to
- Every result shows its source, tier, date and page/offset. A results list that
  showed text without provenance would make this a RAG interface over a pile of
  documents, which the README is explicit it is not

### The degraded search is rendered, not swallowed

- `P2-07` runs the lexical arm only, and the reason is shown whenever it is set.
  When the result set is **empty** it escalates — bordered, at attention weight,
  with an added sentence saying that passages using different wording were not
  searched and that an empty result is not evidence the corpus lacks the subject
- The test that gives this meaning is its converse: with `degraded: false` and
  no hits, that sentence must **not** appear. A caveat shown on every empty
  result says nothing about degradation, and readers learn to skip it

## [0.45.0] — 2026-09-15

**A typed client, checked against the server it talks to.**

### Added

- `P2-13` `web/src/lib/api.ts` — a typed client over `/api/explore/*`. Every
  call takes `RequestInit`, so an `AbortSignal` flows through and a superseded
  search is cancelled rather than left to land out of order
- No base URL and no environment read, enforced rather than intended: a test
  greps the source for `http(s)://`, `import.meta.env` and `process.env` and
  fails on any of them

### Testing

- **The cross-language contract is tested in two links, and both were
  mutation-checked.** TypeScript types are erased, so nothing can compare an
  `interface` to a pydantic model directly. `tsc` ties each interface to a
  runtime field list via a type-level equality; a vitest drift test ties each
  list to the pydantic class parsed out of `meridian_core/schemas/`
- The second link is the one that matters. Removing a field from *both* the
  interface and its list leaves `tsc` green — the compiler happily asserting a
  shape the server contradicts — and only the drift test catches it
- The parser strips docstrings before scanning, because a prose line can begin
  with four spaces and `word:`. A flake there is how a drift test gets deleted
- Checked against the running server as well, which the tests cannot do without
  Postgres: all six field lists compared to live response keys, exact match

## [0.44.0] — 2026-09-15

**An external agent can pull evidence, with citations.**

### Added

- `P3-01`/`P3-02` The MCP read surface, mounted on the API at `/mcp`. §11.1's
  agent-initiated direction: an external model connects inward and pulls
  evidence; Meridian holds the corpus and the provenance and generates nothing
- Four tools — `search_chunks`, `get_source_metadata`, `list_new_since`,
  `corpus_overview`. `list_new_since` is §11.1a's entry point and needs no query
  and no embedder at all: a synthesis session *walks* what is new rather than
  searching for it
- **The instructions are load-bearing, not documentation.** They are the only
  thing a model reads before deciding how to treat these results, and three of
  the mistakes it would otherwise make are ones this system exists to prevent:
  concluding the corpus lacks a topic when the search was word-matching,
  reading `source_tier` as a credibility score §8 explicitly refuses to compute,
  and reporting a claim without its citation
- The retrieval mode rides on **every search result**, not only on the server
  instructions — a client reads instructions once at connect and then summarises
  individual calls, so a warning attached anywhere else does not survive the
  summary. When degraded, the wording tells the model *what to do*: retry with
  alternative wordings. A client told only that a flag is true will not think to
  try synonyms, and that retry is what makes lexical-only retrieval usable while
  `P2-17` does not exist
- Mounted on the same app as `/api/explore/*` deliberately. Same corpus, same
  read-only role, same provenance — §11.1b is explicit that all three
  integration directions hit one validation layer with none privileged
- DNS-rebinding protection on the transport, configurable by environment and
  defaulting to loopback. It is off by default in the SDK and matters the moment
  this is behind a tunnel: without it a page on any origin can point a hostname
  at loopback and drive this surface through the reader's own browser

### Testing

- 15 tests, and most are about what the tools *say* rather than what they
  return. The consumer is a language model with no other source of truth about
  this corpus: it acts on the wording it is given, summarises away what it was
  not told to care about, and reports to someone who cannot check
- That no write tool is registered — absent, not disabled. §11.6 splits read
  from write and §11.4 puts write scope on the orchestrator alone; a write tool
  present and guarded is one refactor from present and unguarded
- That an **empty** search still carries the caveat, which is the case that
  matters most and the one an implementation naturally skips
- That the mark walks forward exactly once: no overlap, no gap, and an exhausted
  mark does not rewind — a caller polling for new work must not be sent back to
  the beginning, which is an infinite loop that looks like activity
- Verified against a real MCP client over streamable HTTP: initialize,
  `list_tools`, all four tools, resumption across two pages, and a missing
  source answered rather than raised

### Fixed

- `list_new_since` read ORM attributes after its session closed. Found by
  driving it from a real client — `DetachedInstanceError` surfaces as "error
  executing tool" with nothing to say it was a lifetime problem

## [0.43.0] — 2026-09-15

**The corpus becomes reachable over HTTP.**

### Added

- `P2-07` `services/api/` — the service, from zero. FastAPI over
  `meridian_core.search`, with five `/api/explore/*` routes: search, corpus
  stats, a source, a source's chunks in document order, and a chunk. Plus
  `/health`, which says whether the process and the database are up and nothing
  about the corpus
- **Explore runs on the read-only role**, and that is tested twice on purpose.
  Once through the session, which issues `SET TRANSACTION READ ONLY` so a
  mistake is an error at the statement rather than a surprise at commit; and
  once against the catalogue, asserting `meridian_ro` holds SELECT and not
  INSERT, UPDATE or DELETE. The second exists because the transaction setting
  *masks* the role — a test that only ever saw `ReadOnlySQLTransactionError`
  would keep passing if the grants were widened
- DTOs went into `meridian_core.schemas.search` and the corpus counts into
  `meridian_core/stats.py`, not into the API. Core owns anything touching the
  database; a service that defined its own would be the second definition of
  the same rows
- `services/api/Dockerfile` — 293MB, unprivileged, mirroring the worker's shape

### Decisions worth reviewing

- **No embedder, so search is lexical-only and every response says so.**
  Depending on `sentence-transformers` puts gigabytes of weights and a cold
  start in an HTTP request path; accepting a client-supplied vector puts a
  1024-float array from an unauthenticated caller straight into a pgvector
  distance operator. Neither is acceptable, so `embed_query()` is the seam and
  returns `None` — the same shape as `Crawl4aiClient.from_env()`, where an
  absent dependency degrades the service rather than stopping it. `degraded`
  and a plain-language reason ride on every response
- **Paging past the candidate pool is a 422, not an empty page.** RRF can only
  order what the arms handed it, so an offset beyond `candidates` was never a
  candidate. An empty page there is indistinguishable from "end of results", and
  a client would stop paging believing it had seen everything
- **No CORS middleware.** Same-origin in dev (the Vite proxy) and in production
  (cloudflared). Permissive CORS on a service whose only auth is Cloudflare
  Access would hand away exactly what Access protects

### Fixed

- Uvicorn's plain-text logs violated the one-JSON-object-per-line contract
  `meridian_core/logging.py` is built on — the access log during operation, and
  the startup banner before lifespan can intervene. `--no-access-log` plus a
  `log_config.json`, with the app's own middleware logging each request
  structurally under a per-request `run_id` and returning `x-request-id`

### Testing

- 37 new tests. Dataclass↔DTO drift in both directions, filters narrowing the
  query (mutation-checked — the filter was broken to confirm the test fails),
  pages that neither overlap nor skip, an empty query returning 200, an
  oversized limit refused, a tier the database would reject refused at the
  boundary, and `embedding` absent from chunk payloads
- Verified running against the real dev corpus: `/health`, `/stats`, and a
  search returning real government-tier hits with full provenance. Structured
  logging confirmed at 8 lines, 0 non-JSON

## [0.42.0] — 2026-09-15

**A backup script, before the run makes one necessary.**

### Added

- `P1-37` `scripts/backup.sh`, behind `make backup`. A different job from
  `snapshot_corpus.sh`: a snapshot is a deliberate artefact somebody names and
  restores on purpose, a backup runs unattended on a timer and nobody looks at
  it until the day they need it — so it asks no questions and fails loudly
- What it protects is not the database. Postgres rebuilds from migrations and
  the config re-seeds; the crawl does not. A page fetched in March is not
  re-fetchable, only re-visitable
- It warns when the backup root is on the same filesystem as the data root. A
  backup on the disk it protects survives an accidental delete and nothing else
  — not the disk failing, not the filesystem corrupting, not the machine being
  lost. Scaffold §5 says off-device, and that is the kind of instruction
  followed on day one and quietly undone the first time someone is short of
  space
- The dump is checked for being non-empty, because `pipefail` does not reach
  across a redirect: a `pg_dump` that died after printing a header leaves a
  small, plausible file and a zero exit from the shell that wrote it
- Rotation runs *after* the new backup is written and checksummed. Pruning first
  means a failed backup costs the oldest good one too
- `P1-45` carries through: it warns when sources reference a raw store the
  backup does not include

### Testing

- `tests/unit/test_scripts.py`'s stale-exemption test did its job — it failed
  the moment `backup.sh` existed while still being listed as unwritten, which is
  exactly the state where the next real omission would hide
- Verified against the real dev data: dump, raw archive, checksums, and the
  same-filesystem warning firing

## [0.41.0] — 2026-09-15

**The Explore landing, as components rather than as a page.**

### Added

- `P2-16` §8's Explore default state, built as components taking typed props:
  the search field with its `hybrid` marker and the "filters apply before the
  vector search" note, the four counts in mono numerals, the three entry points
  from §12.5 as parallel cards, and the "where you were" list
- Deliberately **not** a page. `/api/explore/*` does not exist yet, so a page
  would have to display fabricated numbers — and §12.5's whole first state is
  about making absence visible, which a screen of invented figures inverts.
  Components with props are the honest form of this work until there is
  something to wire them to
- `CorpusCounts` takes `counts: CorpusFigures | null` and renders em dashes for
  null. Absence and zero are different findings — "nothing crawled yet" and "no
  contested nodes" are not the same sentence

### Testing

- The counts component's substance is its absent state, and the test that makes
  it mean anything is the converse: a real `{documents: 0}` must render `0` and
  no em dash. Without that, the dash could simply be how this component draws
  zero and the distinction would not exist
- **A voice-guide drift test.** §4 ends with "Words this system does not use:
  …". The test parses that list out of `design-system.md`, renders every Explore
  component, strips tags, and asserts none appear — with a guard test on the
  parse, because a regex that silently matched nothing is the usual way a rule
  like this stops being enforced
- `EntryPoints` takes an `unavailable` map rather than a boolean. A disabled
  card showing its normal description tells the reader nothing about why it will
  not open, and §4 asks an error to name the cause

## [0.40.0] — 2026-09-15

**The mark exists in the application, not only in the README.**

### Added

- `P6-16` `Mark` and `Lockup` — the globe, meridian, bearing edge and four nodes
  from §1's coordinate table, plus the compact variant that drops the bearing
  edge and thickens every stroke below 32px. The app had no mark at all; the
  README uses PNGs
- Both lockup orientations. §1 specifies horizontal as primary and stacked with
  the mono descriptor, and the descriptor renders on stacked only — on
  horizontal it competes with the wordmark it qualifies

### Testing

- **The bounding box is a drift test, not an opinion.** §1 publishes both a
  coordinate table and a box (76 × 86.2), and the box is derivable from the
  coordinates — so the test computes it from the exported geometry and compares.
  A mistyped radius or centre moves the box, and nothing else in the drawing
  would show it
- "Single ink" written as the test §1 says it is: strip `class` and `fill`, and
  every element must survive. The same shape as §6's test for the contested
  dagger, and for the same reason — the mark is a stroke drawing, not a colour
  composition
- That the meridian stroke stays heavier than the globe (§1's stated principle;
  equalise them and it becomes a globe with a line on it), that compact *drops*
  the bearing edge rather than thinning it, and that the minimum sizes are
  ordered so no band of sizes lacks a legal variant

### Open

- `P6-19`: three values §1 does not publish had to be inferred — the lockup's
  cap-height ratio, the mark-to-wordmark size ratio, and whether the published
  bounding box applies to the compact variant as well as the full mark

## [0.39.0] — 2026-09-15

**The worker has no route to the LAN, and the host is what says so.**

### Added

- `P1-25` `deploy/egress-restrict.nft` — the fetching process gets no route to
  RFC1918. `netguard` refuses private addresses and `P1-24` pins the validated
  one; both are correct, and both are code inside the process that is
  deliberately fetching attacker-chosen URLs. This is the defence that survives
  a mistake in either, because it is not in the application, not in the
  container, and not reachable from either
- `internal: true` was never this. It stops a container reaching the internet
  and does nothing about the worker, which must have a default route, reaching
  `192.168.0.0/16`
- Compose subnets are pinned. Docker allocates bridge subnets from a pool and
  reallocates them when networks are recreated, so a firewall rule written
  against last week's subnet matches nothing, protects nothing, and is
  indistinguishable from one that works
- `docs/deployment.md` §4b: install, persist, and four verification commands
  that must each behave as stated — LAN blocked, metadata endpoint blocked, open
  web reachable, Postgres reachable. Three passing and one wrong is the
  configuration that looks fine and is not

### Testing

- That every compose network pins a subnet. This is not a detail of the compose
  file; it is the thing the firewall rules rest on
- Cross-file drift between `docker-compose.yml` and the rules: every pinned
  subnet must fall inside the supernet the rules treat as Meridian's own. A
  network moved outside it would be blocked by its own firewall — loud, and
  fine — but one moved outside it *and* exempted would be silently unprotected

### Not closed

- `P1-25` stays `[~]`. It names an egress proxy as the alternative and it is not
  a drop-in one: a forward proxy resolves the hostname itself, which takes DNS
  away from the worker and undoes `P1-24`'s address pinning. Adopting it means
  deciding the proxy's destination ACL replaces pinning — a design decision, not
  a deployment one

## [0.38.0] — 2026-09-15

**"Missing" and "under a different root" stop being the same observation.**

### Added

- `P1-45` `sources.raw_root` — which raw store `raw_file_path` is relative to.
  Found by `P1-31`'s first real run: three sources dangled against
  `.devdata/raw` and their files were in `.devdata/containerraw`, put there by
  `P1-30`'s containerised verification writing to its own bind mount. Nothing
  was lost, and no amount of care could have shown that
- Provenance, not a lookup. `raw_file_path` stays relative and
  `MERIDIAN_RAW_ROOT` still resolves it — an absolute path in the table would
  bake in a container's mount point and break the moment the store moved. The
  column answers "which store was this written into", which is a different
  question and the one nothing could previously answer
- The sweep now separates `elsewhere` from `dangling`. A row naming a different
  store is not missing its file; it is a file that sweep is not looking at.
  Folded together, a corpus written partly natively and partly in a container
  produces a dangling list long enough that a real loss inside it would never be
  noticed
- `make snapshot-corpus` warns when sources reference a store it is not
  archiving. It tars one root, so without the check a snapshot of a multi-root
  corpus succeeds, is quietly incomplete, and is only caught by
  `restore_corpus.sh`'s sampling — on another machine, later, too late to go
  back for the files
- Nullable with no backfill. Rows written before this genuinely do not record
  their root, and guessing one would turn "unknown" into a confident wrong
  answer for exactly the rows the column exists to explain. The sweep says so
  rather than classifying them

### Testing

- That a row under another root is `elsewhere`, that a row missing from its
  *own* root is still `dangling` — the excuse must not generalise — and that a
  row with no recorded root stays dangling rather than being assumed either way
- That nothing in `elsewhere` is ever deleted, since it sits between two lists
  that are
- That `store()` records the root while the path stays relative, and that a
  source keeping no file records no root — naming one would claim a file exists
  somewhere

## [0.37.0] — 2026-09-15

**Which failures may be handed to someone else.**

### Added

- `P1-38` `meridian_core/consignment.py` — §3 of
  `docs/spec/external-acquisition.md` and deliberately nothing else: no table,
  no lease, no API. It is the part of that design with a security argument
  behind it, it needs none of the rest to be correct, and it is worth having
  tested before anything can call it
- The question is not "did this fail". `queue_disposition` abandons eight
  outcomes and they are not interchangeable. What the consignable ones share is
  that the content exists and *this* fetcher cannot have it — a bot wall on a
  public page, a login the operator legitimately holds, a format or size this
  crawler refuses. Everything else abandoned is a correct answer about a URL,
  and asking a third party to try harder produces nothing
- Two outcomes are never eligible, checked before every other branch so no
  argument can reach past them. `robots_denied`: routing a refused request
  through a third party is the same crawl with the conduct removed, and §14.2's
  commitment to honouring robots would be decorative if this path could launder
  one. `unsafe_target`: publishing an address `netguard` rejected asks an
  external service to fetch the operator's own network and post the result back
- Unknown outcomes are refused — the opposite of `queue_disposition`, which
  retries what it cannot classify. There the forgiving default is bounded by
  `max_retries`; here it publishes a URL to somebody outside this system

### Testing

- Fifty tests, mostly rejections, because the failure that matters is not a URL
  that should have been consigned and was not. It is one published to a third
  party that should never have left, and nothing downstream would notice
- The absolute refusals are checked under every combination of every argument.
  "Absolute" means there is no way to configure past it, and the only way to
  show that is to try
- A drift test over the database's `FETCH_OUTCOME` enum: every outcome must get
  a verdict. A new one will be added for a reason, and "is this publishable to a
  third party" is not a question whose answer should be inherited from whichever
  branch happens to catch it
- That the one opt-in widens exactly one outcome and nothing else. A single
  boolean that grows into "consign more, generally" is how the absolute refusals
  would eventually become reachable
- That nothing eligible is outside `TASK_ABANDON`. A URL still being retried has
  not finished failing, and consigning one would race the retry that was going
  to succeed

## [0.36.0] — 2026-09-15

**A read role that cannot read the token table.**

### Added

- `P3-07` `meridian_guest` — SELECT on the corpus and the graph, and nothing
  else. `meridian_ro` can SELECT every table in the schema, `agent_tokens`
  included, and that table's `token_hash` column is the only secret the database
  holds. It is the right role for the operator's own read path and the wrong one
  to put behind `P3-04`'s SQL escape hatch or any shared read surface
- Eight readable tables: sources, chunks, figures, entities, edges, attribute
  definitions and values, observations. The excluded ones are not all secrets —
  `queue` and `fetch_policy` describe what this crawler is about to look at and
  how it behaves, which is the operator's research direction rather than the
  corpus
- Grants live in a migration and the credential does not. Tables must exist
  before they can be granted on, which makes this schema-scoped work; the
  password is deployment state and stays in `init-roles.sh` (§11.11). The
  migration creates the role NOLOGIN if absent, so an existing database gets
  correct privileges without a password being invented for it, and a deployment
  that shares nothing ends up with a role that simply cannot connect
- **No default privileges, deliberately.** Every other role here has
  `ALTER DEFAULT PRIVILEGES` so migrations' new tables are covered
  automatically; this one does not, so a table added later is invisible to
  guests until granted explicitly. The asymmetry is the argument: forgetting to
  grant means a guest cannot read something they should, which gets reported,
  and forgetting to revoke means a guest reads a table nobody considered, which
  does not

### Testing

- Twelve tests against a real Postgres, because privileges are a property of no
  code. `P0-18` is the precedent — a role bootstrap that never ran and default
  privileges that silently denied reads were invisible to everything else — and
  the same holds in the other direction: a grant nobody intended looks exactly
  like one nobody made
- The privilege matrix is checked over `pg_tables` rather than over a list in
  the test, so a table added to neither set fails. "Should a guest read this" is
  a decision, and a new table silently inheriting either answer is how the wrong
  one gets made. The expected list is loaded from the migration itself; a copy
  would drift, and the copy is what the test would then be checking
- Rejection tests run under `SET ROLE` rather than asserting the catalogue,
  because `has_table_privilege` and the planner are meant to agree and checking
  only the first would pass if they ever did not. Plus the converse — the role
  can read the corpus — since a role that can read nothing is trivially safe,
  useless, and passes every other test here
- A probe that creates a table and asserts a guest cannot read it, which is the
  fail-closed property stated as a test rather than as a comment

## [0.35.0] — 2026-09-15

**The retention sweep, and what measuring first changed about it.**

### Added

- `P1-31` `meridian_core/retention.py` and `python -m worker.sweep`. §5.4 splits
  raw retention three ways and there was no sweep; `P2-03` supplied the verdict
  it was waiting on, and this spends it
- Measuring the dev corpus before writing it changed what it is. There was
  nothing to reclaim and no orphans — and **three sources whose `raw_file_path`
  pointed at nothing**. So the reclaim arm is correct, necessary and currently a
  no-op *by construction*: `P1-11` never writes the files §5.4 says to drop, and
  `retention_for` only ever moves a tier up, so a file that exists was written
  under a tier that keeps files and cannot since have fallen below it. The sweep
  prints that rather than reporting as though it did work
- Three verdicts, and only one deletes. `droppable` and `orphaned` go with
  `--apply`; `dangling` is reported and never acted on, because the row is the
  only record that the fetch happened and the text extracted from it is still in
  the corpus — deleting it to tidy the report destroys more than the missing
  file did
- Primary is refused twice: when the plan is built, and again when it is
  applied. A plan is data and can be constructed or replayed by a caller that
  did not build it, and §5.4 makes link rot the binding reason for raw retention
  — a primary file is frequently the only remaining copy of what a citation
  points at
- §5.4's "background keeps a snapshot *if cited*" is a graph question, so
  `cited_source_ids()` asks it. Empty until edges exist, and written now rather
  than deferred because the day the first edges land is the day a sweep without
  it starts deleting the evidence beneath them — and nobody would connect that
  to a retention pass
- Dry run is the default. This is the only operation in the system that destroys
  something a re-crawl cannot reproduce: the web moves on, so a page fetched
  last month is not re-fetchable, only re-visitable
- A separate pass rather than housekeeping inside the fetch loop. An hourly
  sweep would eventually run at the same moment as the mistake that made
  something droppable, and the window between "wrongly demoted" and "file gone"
  would be an hour rather than however long it takes someone to read a report

### Found

- New task `P1-45`: `raw_file_path` is stored relative to a root that nothing
  records. The three dangling sources are not lost — their files are in
  `.devdata/containerraw`, written by `P1-30`'s containerised verification
  against its own bind mount. "The file is gone" and "you are looking in the
  wrong place" are therefore the same observation. It also means
  `make snapshot-corpus` tars one root and would silently omit them;
  `restore_corpus.sh`'s sampling check is currently the only thing that notices

### Testing

- Nine tests against a real Postgres and a real temporary directory, because the
  subject is the relationship between the two — a double for either half could
  only confirm the agreement this code exists to check
- Mostly rejection tests, deliberately: that a primary file is never droppable,
  that `apply_sweep` refuses a primary candidate it is handed and refuses it
  *before* deleting anything else, that a dry run deletes nothing, that a
  dangling row survives, and that citing one source does not protect another —
  a protection rule that is too broad silently turns the sweep off and looks
  exactly like one that works

## [0.34.0] — 2026-09-15

**The 48h run's output has somewhere to go.**

### Added

- `P1-36` `scripts/snapshot_corpus.sh` and `scripts/restore_corpus.sh`, behind
  the `make snapshot-corpus` / `make restore-corpus` targets that have declared
  them since `P0-01` and called nothing. `make snapshot-corpus` is `P1-16`'s
  stated deliverable, so until now the 48h run's output had nowhere to go
- The database and the raw store travel together or not at all. `sources` rows
  carry `raw_file_path`, so a dump without the files it points at is a
  catalogue rather than a corpus — every provenance link resolves to nothing,
  and the failure surfaces much later as "why does no citation open"
- `pg_dump` and `pg_restore` run *inside* the Postgres container rather than on
  the host. Client and server versions have to match, and a host with an older
  client fails with a version mismatch after you have waited for the dump
- A manifest recording the Alembic revision, row counts and both checksums.
  The revision matters because restoring a corpus dumped under a newer schema
  into older code fails in ways that never mention the schema — a missing
  column surfaces as an ORM attribute error three layers up
- The restore verifies checksums **before** touching anything. A truncated dump
  discovered halfway through has already dropped the tables it was replacing
- It refuses a non-empty target outright unless `--replace`, and then asks for
  the source count to be typed back. Refusing on non-empty rather than on "is
  this production" is deliberate: environment detection is a guess, while "does
  this database already hold a corpus" is a fact — and the thing worth
  preventing is destroying crawl output that cannot be re-fetched, wherever it
  lives. A re-crawl returns today's web, not the web the snapshot recorded
- After restoring, it samples `raw_file_path` values and checks the files are
  there. A restore where the rows landed and the files did not looks entirely
  successful until someone follows a citation

### Fixed

- The scripts distinguish "Postgres is not running" from "the Docker daemon is
  unreachable". They are identical from the caller's side and have nothing to
  do with each other — one sends you to `make dev-up`, the other to
  `DOCKER_HOST` — and the dev/production compose probe asks Docker a question,
  so an unreachable daemon silently answered "not dev" and the error named the
  production compose file on a dev machine
- `.gitignore` now excludes `fixtures/` rather than only `fixtures/corpus_*.dump`.
  A snapshot is a directory of a dump, a tarball and a manifest, so the old
  pattern would have committed the raw store

### Testing

- `tests/unit/test_scripts.py` — every `./scripts/*.sh` the Makefile invokes
  must exist, be executable, and parse. Nothing catches that class of bug by
  reading code: the Makefile is valid and the target is declared, and the
  failure only appears when someone runs it — which for these targets is once,
  under time pressure, after a two-day crawl
- The two still-unwritten scripts are listed with the task that writes them,
  and a second test fails if one gets written while its exemption stays. A
  stale exemption is where the next real omission hides
- Verified against the real dev corpus: snapshot, both refusal paths with the
  corpus intact afterwards, and a restore into a throwaway database that came
  back with every source, chunk and vector

## [0.33.0] — 2026-09-15

**The primitives every later screen is made of.**

### Added

- `P6-16` Shared UI primitives from the design system: the icon set on §7's
  grid, source-tier and data chips, and the contested mark in its three forms.
  No API dependency — these are the atoms `P2-08` and everything in phase 6 are
  assembled from
- `Icon` owns every shared attribute — the 24-unit grid, the two stroke weights,
  the round terminals — rather than repeating them per icon. An icon carrying
  its own could quietly stop matching the others, and a set whose strokes
  disagree reads as amateurish long before anyone can say why
- The optical-size rule from §7: interior detail is dropped below 20px and the
  silhouette carries alone. At glyph size a 1.35-unit stroke on a 24-unit grid
  is under half a pixel — it renders as a smudge that makes the silhouette look
  blurry rather than as detail
- Tier chips take no variant, tone or colour prop, because there is nowhere for
  a verdict to go. §2 is explicit that the palette has no green and no red and
  that colour must not imply a verdict, so every tier is drawn identically and
  differs only in its text. A chip that coloured peer-reviewed differently from
  informal would be the interface asserting a credibility judgement §8
  explicitly refuses to make

### Testing

- §6 states the contested mark's rule and its test in the same breath — "strip
  the colour and the reading must survive — that is the test" — so it is written
  as one, across all three forms. It matters beyond appearance: §9 makes
  contested nodes the highest-value ones in the graph, and a reader who cannot
  see which those are loses the finding, not the decoration
- A cross-language drift test: the frontend's tier list is compared against
  `SOURCE_TIER` parsed out of the Python model. A tier added to Postgres and not
  to the UI renders as a raw enum value, underscore and all, and nothing catches
  that until it is in front of someone
- That all tier chips render identical markup, which is the enforceable form of
  "colour must not imply a verdict", paired with its converse — that their text
  still differs, or the chip says nothing at all
- Accessibility as a rule rather than a nicety: a titleless icon must be hidden
  from screen readers, because an icon repeating adjacent text announced as
  "graphic" beside every control trains people to ignore the announcements that
  matter
- 50 frontend tests, 1262 backend

## [0.32.0] — 2026-09-15

**The theme follows the machine until someone says otherwise.**

### Added

- `P6-17` Theme switching with three states, not two: `system` (the default),
  `light` and `dark`. "No explicit choice" now means *the system preference*
  rather than *dark* — a reader whose machine is in light mode and who has never
  touched the control gets light
- `system` is expressed by the **absence** of `data-theme`, which is what lets
  the `prefers-color-scheme` block apply. A `data-theme="system"` sentinel would
  match neither the light block nor the dark one and would silently leave every
  such reader on the flagship palette
- The `:root:not([data-theme='dark'])` guard on the media query is what lets a
  reader on a light-mode machine still choose dark. Without it the toggle
  appears broken in one direction only, which is the kind of bug reported as
  "the theme button doesn't work sometimes"
- `color-scheme` moved into `tokens.css` beside each theme's role mapping. Split
  across two files they drift, and the failure is a dark palette with light
  scrollbars, date pickers and autofill — which reads as a rendering bug rather
  than a missing declaration

### Changed

- `tokens.css` now separates **palettes** from **roles**. Each published colour
  is written exactly once as `--dark-*` or `--light-*`; components use roles
  (`--surface`, `--text`), and each theme state is nothing but a mapping between
  them. Repeating hexes per theme meant a palette change was an edit in several
  places with no way to notice when one was missed — a test now asserts no hex
  appears twice in the file
- Every `localStorage` access is guarded. It does not merely return null in a
  private window or with site data blocked — reading the property throws, and
  that happens during the first render. A theme preference must never be able to
  stop the app loading

### Testing

- The structure makes a second class of drift testable, and three tests now
  cover it: system-light and explicit-light must remap the same role set, or
  two kinds of reader see different colours; explicit dark must be able to undo
  everything light remapped, or switching back leaves one role stranded on the
  light value; and the four roles light deliberately does not remap are pinned,
  so one quietly dropped fails rather than inheriting
- A completeness probe that every role is reachable as a Tailwind utility. A
  role that exists in CSS and not in `@theme` cannot be used, and the component
  that wants it reaches for a literal
- Rejection tests for a stored value that is not a theme, storage that throws on
  read, and storage that refuses to be written
- 19 frontend tests, 1262 backend

## [0.31.0] — 2026-09-15

**Both extraction paths now filter to the same standard.**

### Fixed

- `P1-43` The browser path did no boilerplate removal of its own. It took
  Crawl4AI's `fit_markdown` as-is, on the stated reasoning that
  `PruningContentFilter` had seen a rendered DOM this process never had. The
  premise was wrong: the rendered HTML comes back in the same response and is
  already what the extractor receives as its content, so trafilatura can see
  everything the filter saw
- What the premise cost was an asymmetry nobody chose. `PruningContentFilter`
  is far more permissive than trafilatura at `favor_precision`, so whether a
  page kept its navigation depended on whether the fetcher happened to escalate
  it to a browser — a decision made on how much visible text the *static* fetch
  found, which has nothing to do with how much boilerplate the page carries
- Expensive twice over: boilerplate becomes entities and entities become edges
  (§2.3), and it inflates the novelty gate's duplicate count with text that was
  never content, because every page on a site repeats the same chrome. Visible
  in the dev corpus as chunks that were repeated link lists, a promo banner and
  a footer block
- Now trafilatura extracts the text from the rendered HTML, and the payload
  contributes what it is genuinely better at: metadata read from the rendered
  DOM, and links including the ones JavaScript inserted. `fit_markdown` stays
  as the fallback for pages trafilatura finds nothing in — a real case on
  JS-assembled pages with no semantic structure to detect, which is why the
  browser was escalated to in the first place — and only above `TEXT_FLOOR`,
  because a sub-floor fallback is a cookie banner
- The tradeoff, recorded rather than discovered later: a DOI written in a
  region precision filtering strips is no longer seen. A DOI that is a *link*
  still is, since links come from the rendered DOM rather than the text, and a
  page's own identifier is read straight from its meta tags either way

### Added

- `P1-44` `sources.extractor` — which tool produced this source's text. §6.6
  routes each format to a different tool and HTML to two of them, so "how was
  this read" is a per-row fact that cannot be derived from the media type.
  Without it, telling a browser-extracted page from a locally-extracted one
  meant looking for markdown link syntax in the text, which is how `P1-43` was
  found and is not a diagnostic anyone should have to invent twice
- Deliberately not a constrained value set. These names grow whenever an
  extractor or a failure mode is added, and a CHECK would recreate `P1-28`'s
  trap exactly — a literal used in code and missing from the enum raises at the
  insert, after the fetch, the parse and the log line have all reported
  success. A diagnostic that can fail a write is worse than no diagnostic
- Nullable with no backfill. The sources already in a corpus were extracted
  before anything recorded it, and inventing a value for them would assert
  something nobody knows

### Testing

- The five tests that encoded "the browser's markdown is preferred" now encode
  the opposite, and three were added: that site chrome does not survive the
  browser path, that the markdown fallback still works when local extraction
  finds nothing, and that a sub-floor fallback is refused
- `P1-44`'s end-to-end test reads the **committed row** back rather than
  trusting the extractor's return value, per the rule `P1-28` bought: for any
  handler that ends in a write, the parser passing tells you nothing
- 1262 backend tests, 5 frontend

## [0.30.0] — 2026-09-15

**The corpus becomes searchable.**

### Added

- `P2-06` `meridian_core/search.py` — hybrid retrieval over the chunk corpus.
  Two arms, `tsvector` and pgvector, fused by reciprocal rank. This is the
  thing phase 2 exists to judge, and the first time anything in this system
  reads the corpus back
- `P2-04` the HNSW index, `vector_cosine_ops` to match the operator everything
  here already uses — vectors are normalised at embedding time and the novelty
  gate compares with `cosine_distance`, so an index built for another operator
  class would not be a slower index, it would be an unused one. Built now
  rather than after the long run: maintained incrementally it costs nothing per
  insert, where building one over a finished corpus is a single operation
  wanting more `maintenance_work_mem` than the target has. The *measurement*
  half of `P2-04` still waits for a real corpus, and the task says so
- Filters are predicates inside both arm queries, which is the whole of §12.5's
  "filters before vector search". The anti-pattern — take the top k by
  distance, then drop what fails the filter — returns a truncated set with no
  indication it was truncated, and an empty page then reads as a thin corpus
  rather than as a query built the wrong way round
- Reciprocal rank rather than score fusion, because the two arms produce
  numbers that are not comparable: `ts_rank_cd` is unbounded and length
  dependent, cosine distance is bounded. RRF needs only the ordering, which is
  the part both arms agree is meaningful
- A missing arm is reported rather than hidden. Retrieval with no query vector
  is lexical-only, which is legitimate — the embedder is a separate pass and a
  chunk exists before it has a vector — but a caller that believes it ran a
  hybrid search and ran half of one will conclude the wrong thing about the
  corpus. `SearchResult.arms` and `.degraded` say which ran
- Near-duplicates are excluded by default and available on request, which is
  what `P2-03`'s mark-don't-delete was for. `duplicate_of` rides on every hit
  so a surface can say why something is missing
- Every hit carries its citation — url, title, tier, date, page or offset.
  A retrieval surface that returns text and leaves the caller to find its
  source is a RAG endpoint, and §2 principle 3 is the opposite of that
- New task `P2-14`: a source records no topic, so search cannot filter by one
  even though §12.5 and §12.3 both list it

- `scripts/benchmark_search.py` and `make bench-search` — `P2-04`'s measurement
  half. Index recall against exact search across an `ef_search` sweep, latency
  per method, and how often the two arms agree. Query vectors are sampled from
  the corpus rather than generated: a random 1024-dimension vector is
  near-orthogonal to everything, so every candidate is equidistant and the
  measurement reduces to a scan-rate test
- The benchmark refuses to report a number it cannot support. Below a few
  thousand vectors the planner prefers a sequential scan, which is exact, so
  recall is 1.0 by construction and says nothing about the index — it reports
  whether the index was used and says so. The same problem appears again in arm
  agreement: with a corpus smaller than the candidate pool the vector arm
  returns everything, so agreement is 100% by arithmetic. Both caveats print
  loudly rather than being left for the reader to notice
- Retrieval *quality* is explicitly not measured. That needs `P0-15`'s held-out
  questions, written before the results are visible, and `--questions` is the
  hook for when they exist
- New tasks `P1-43` and `P1-44` from reading the dev corpus: the browser
  extraction path does no boilerplate removal of its own, and a source does not
  record which extractor produced its text. `P1-10` gains the current state —
  the `figures` table has no writer at all, so figures are not extracted
  anywhere today

### Testing

- `tests/unit/test_rrf.py` — fusion arithmetic with no database, including the
  property that justifies using RRF at all: a chunk both arms found at rank 2
  must outrank one a single arm found at rank 1, or the second query is wasted
  work
- `tests/integration/test_search.py` — 13 tests against a real Postgres. The
  load-bearing one puts the two nearest neighbours outside the filter and sets
  the candidate pool to exactly two, so post-filtering would return nothing and
  a predicate inside the query reaches past them
- Both arms are checked to narrow identically. Two copies of a filter
  eventually disagree, and the arm that drifted is the one quietly returning
  material the caller excluded — which RRF then rewards
- Scoping is deliberate: a vector search has no WHERE clause hiding the rest of
  the corpus, and the dev database holds a real crawl, so every test filters to
  its own fixtures and the lexical assertions use a term that cannot occur in
  crawled text
- Verified live against the real dev corpus. Both arms ran, and fusion
  reordered rather than rubber-stamping either: the top result placed fourth in
  both arms, ahead of the lexical first place that the vector arm ranked
  twelfth

## [0.29.0] — 2026-09-15

**The lexical half of search, and a frontend that has tokens before it has screens.**

### Added

- `P2-05` `chunks.search_vector` — a STORED generated `tsvector` over
  `chunks.text`, plus the GIN index behind it. The lexical half of §12.5's
  hybrid retrieval; the vector half already exists and nothing yet fuses them
  (`P2-06`)
- A generated column rather than the trigger the task named. Postgres 12 made
  the trigger unnecessary, and a generated column cannot be bypassed by a write
  path that forgot to fire it, cannot drift from `text` after a bulk UPDATE, and
  needs no ordering agreement with other BEFORE triggers. The failure a trigger
  has here is silent: a chunk that is present, embedded and novelty-judged, and
  lexically unfindable, which is indistinguishable from a corpus that does not
  contain the term
- The regconfig is named rather than defaulted. `to_tsvector(text)` resolves
  through `default_text_search_config`, a session GUC, so it is not IMMUTABLE
  and Postgres refuses it in a generated column — and naming it also pins the
  stemming, so the same text cannot index differently depending on who connected
- `P2-11` `web/` scaffold: Vite, React, TypeScript and Tailwind v4, with the dev
  proxy sending `/api` onward so no environment-specific base URL exists in the
  source. The proxy target is a property of the machine, not the app, and reads
  `VITE_API_PROXY`
- `P2-12` Design tokens in code — both palettes, the type scale and §5's surface
  rules as CSS custom properties, wired into the Tailwind theme so the token
  file generates the utilities rather than being mirrored into a second config
- `P6-18`, a new task: four light-theme roles are inferred rather than decided.
  The design system publishes nine light tokens against thirteen dark ones, and
  the four it omits are all canvas roles — its graph-canvas table has no light
  column

### Fixed

- Nothing. Both tasks are new surface

### Testing

- `tests/integration/test_search_index.py` — seven tests against a real
  Postgres. `alembic check` explicitly declines to compare computed defaults
  ("Computed default on chunks.search_vector cannot be modified"), so the one
  thing autogenerate normally guards is exactly what it does not guard here: a
  drift test compares the model's `Computed` expression against the database's
  own record of it instead
- The suite covers the failures this column can actually have: a plain column
  that never populates, a vector that does not follow an UPDATE to the text, an
  application write that should be refused, a configuration that quietly became
  `simple` (caught through stemming, the only observable difference), a missing
  index, and an index the planner cannot use for `@@`
- `web/tests/tokens.test.ts` — five tests parsing the palette out of
  `docs/design/design-system.md` and comparing it against `tokens.css` in both
  directions: every published colour must be defined at its published value, and
  no source file outside the token file may contain a colour literal at all.
  Neither hardcodes a palette, so a legitimate change is a one-file edit
- 1234 backend tests pass with a real Postgres, 5 frontend


## [0.28.0] — 2026-09-08

**Two more places to look, and the bug that finding them exposed.**

### Added

- `P1-14` Europe PMC and Semantic Scholar, after §6.5's four rather than in
  place of them. Both need no credential and both hold copies the aggregators
  above them miss — Europe PMC because it mirrors full text rather than pointing
  at it, Semantic Scholar because it indexes the repository PDF where Unpaywall
  frequently has only the repository's landing page. §6.5's order is a ranking
  by how likely a provider is to be right, not a closed list
- Europe PMC's result list always carries a `doi` entry pointing back at the
  publisher, marked "Subscription required". Only `availabilityCode: OA`
  locations are followed; the rest lead to the paywall this chain routes around
- `SEMANTIC_SCHOLAR_API_KEY`, optional. Worth having: the anonymous quota
  throttles hard enough to matter

### Fixed

- **A rate-limited provider was indistinguishable from one that had no copy**,
  and the difference decides whether a paper is ever looked for again. A 429 was
  folded in with connection errors and skipped silently, so the chain reported
  "no open-access copy", the task settled `done`, and that was the end of it.
  Now a throttled provider makes the resolution *incomplete*: if nothing was
  found, the task retries instead of being written off
- 403 counts as throttling too — several of these APIs answer 403 rather than
  429 for "over the anonymous quota", and a flat refusal reading would silently
  drop every paper for the rest of the window
- Per-provider pacing (`PROVIDER_MIN_INTERVAL_S`), so a citation-heavy page
  cannot burst through a provider's quota on its own. Around the call rather
  than inside it, so a provider skipped for a missing credential costs nothing

### Notes

- Found by measuring, not by reading. Resolving 75 real DOIs back to back
  returned an open-access copy from Semantic Scholar for **none** of them; the
  same DOIs asked one per second returned a PDF for **every one** of the twelve
  sampled. The first result looked like "these two providers add nothing", which
  is exactly what a silent 429 is supposed to look like
- The pacing is a floor, not a solution to a hostile quota. The penalty outlasts
  the burst by a long way — after those runs the same provider returned nothing
  even at one request per three seconds, and answered normally twelve seconds
  apart — so a paced re-measurement taken immediately after an unpaced one
  reproduces the unpaced result and reads as confirmation. Under load the chain
  still gets throttled, and that is now visible as a retry rather than a false
  negative, which is the behaviour that matters. The queue's own backoff is the
  right timescale for waiting out a quota
- Europe PMC found nothing across a transport-research sample, which is expected
  rather than disappointing: it is a biomedical index, and it is in the chain for
  the health- and environment-adjacent work this corpus also touches


## [0.27.0] — 2026-09-08

**A paywalled landing page stops being a dead end.** `P1-14`'s resolution chain,
and the citation seeding that gives it something to resolve.

### Added

- `P1-14` `worker/resolve_doi.py` — §6.5's chain: Unpaywall → OpenAlex → CORE →
  preprint, stopping at the first **legally available** copy. That word is the
  design constraint: a corpus whose promise is checkable citations cannot be
  built on copies its readers cannot legally follow
- `P1-14` The `doi` task handler. A DOI is not fetchable, so what the handler
  produces is one ordinary `url` row pointing at the open-access copy — which
  then goes through the whole fetch stack, robots and `netguard` included. That
  is what makes it safe for a hostile page to put any DOI it likes in its
  reference list: nothing here fetches the answer, it only queues it
- `P1-14` Citation seeding. §6.1 lists citations beside outbound links as
  frontier expansion and §6.4 says the citation graph alone sustains a full
  queue for weeks; until now they were extracted, written to `sources.extra`
  and never queued. Capped at 30 per page — a review article cites hundreds,
  and letting one page put hundreds of rows in ahead of everything waiting is
  how a crawl goes depth-first through a single literature
- `seed_source` gains `citation` and `doi`: a work another paper cited, and the
  copy found by resolving it, are two more answers to §5.2's provenance question
- `MERIDIAN_CONTACT_EMAIL` and `CORE_API_KEY`, both from the environment
  (§11.11). A provider with no credential is **skipped, not failed** — a
  deployment with neither still gets OpenAlex and the preprint rule

### Notes

- Three outcomes, three dispositions, the same distinction the search backend
  needs: one provider erroring is routine and the chain continues; every
  provider answering "no copy" is an *answer*, so the task is `done` rather than
  retried against a paywall that will still be there tomorrow; no provider
  answering at all is transient, so it retries. A malformed DOI is abandoned —
  re-parsing the same string gives the same error
- An arXiv DOI is resolved from the DOI itself, before any request. It deviates
  from §6.5's literal order and lands on §6.5's answer, since Unpaywall would
  send us to arXiv anyway
- Verified live against real Unpaywall and OpenAlex, across all four outcomes: a
  paywalled publisher paper resolved to an institutional repository copy, an
  arXiv DOI resolved with no request at all, two open-access papers resolved to
  direct PDFs, and one genuinely closed paper returned no copy — settling `done`
  rather than retrying
- `_claimable_task_types` now covers both conditional types through one table,
  and the loop's drift tests read the dispatch out of the source rather than
  listing the types, so the next conditional type cannot be added to the claim
  without a branch to receive it


## [0.26.0] — 2026-09-08

**The frontier can widen again — and a discovery channel that never worked
starts working.** `P1-34`'s query handler, plus the `P1-28` bug found while
building it.

### Fixed

- **`P1-28`'s sitemap handler had never enqueued a single URL.** It passes
  `seed_source="sitemap"` to `enqueue()` and the value was not in the
  `seed_source` enum, so every sitemap that fetched and parsed cleanly then
  raised at the insert and queued nothing. Fetch, parse and settle all
  succeeded, so the logs, `fetch_attempts` and every existing test agreed the
  feature worked
- The reason it shipped: `test_sitemaps.py` covers the parser thoroughly and the
  parser was never the problem — nothing drove a sitemap through the loop to a
  committed row. Four tests in `test_worker_run.py` now do, and all four fail
  against the old enum
- This is `P0-21`'s trap for the third time, in its worst form: not a value
  widened in the model and missed in the migration, but a value that existed
  only in the code that used it. See the migration's docstring

### Added

- `P1-34` `worker/search.py` — a SearXNG client. §6.4's failure model is the
  whole design: an unresponsive engine is routine and the other engines' results
  are the answer; every engine returning nothing is an *answer*, so the query is
  `done` rather than retried forever; SearXNG itself being unreachable is
  transient, so the query retries with the ordinary backoff. Collapsing any two
  of those means either a query stuck in a retry loop or a good seed abandoned
  over a five-minute restart
- `P1-34` The `query` handler in the loop. Results go through the prefilter —
  §6.4 warns SearXNG returns content-farm and SEO junk, and a search result is
  the least trustworthy way a URL can reach the queue, since no page pointed at
  it and no site listed it — and are enqueued at tier priority carrying the
  query's topic. Unlike a sitemap entry, a query was written *for* a topic by a
  person, so its results answer that question
- `P1-34` `query` is claimable only by a worker that has a backend. A worker
  without one leaves the row for a worker that has one, rather than failing a
  task that is not broken and burning its retries while SearXNG is down
- `P1-34` `search: configured | unreachable | absent` on the §12.5 health line,
  and a startup warning when `SEARXNG_URL` is unset. Louder than the browser's
  equivalent because the consequence is worse: without a browser the crawl
  extracts JS-heavy pages badly, and without search it drains its frontier and
  idles — and an idle crawler looks exactly like a finished one
- `seed_source` gains `sitemap` and `search`. §5.2's seed provenance asks how a
  URL got here, and a link someone placed, a site's own index of itself and a
  search ranking are three different answers

### Fixed (smaller)

- `stats.queued` now counts sitemap and search rows, not only frontier links. A
  discovery channel missing from the run summary understates exactly the thing
  the run was for

### Notes

- Verified live against a real SearXNG, not only a mock transport: a seed query
  that had been sitting `pending` since `make seed` was claimed, answered with
  47 real results, prefiltered to 44, and queued at tier priority — with one
  upstream engine unresponsive throughout, which is the routine case §6.4
  describes and which changed nothing
- A search does **not** go through `Crawler.fetch`, and must not. The crawler
  pins every request to a validated public address and `netguard` refuses
  RFC1918 — correct for the open web and exactly wrong for an internal service
  on the compose network. It writes no `fetch_attempts` row either: that log is
  keyed by domain and answers "is this host refusing us", and a search asks one
  internal service about many hosts


## [0.25.0] — 2026-09-08

**The corpus stops keeping every copy of everything.** §6.1's one-line novelty
gate, built as a pass rather than a stage — and, deliberately, as a mark rather
than a delete.

### Added

- `P2-03` `meridian_core/novelty.py` — the gate. For each embedded chunk it
  finds the nearest chunk written *before* it and, above `0.95` cosine, records
  which one it duplicates. Compared naively, two identical chunks are each
  other's nearest neighbour, both clear the threshold, and the corpus loses the
  text entirely rather than deduplicating it; `chunk_id <` is what makes the
  first copy the survivor, and makes the answer independent of the order a
  batch happened to be read in
- `P2-03` Three columns on `chunks` rather than a `DELETE`. §6.1 says "drop" and
  §5.4 says a near-duplicate loses its raw file, but a gate that deleted could
  report no pass rate, could not be re-tuned against the corpus it collected,
  and would leave nothing to audit. `novelty_checked_at` is the queue,
  `nearest_similarity` is the score whether or not it cleared the bar, and
  `duplicate_of` is the verdict. The retention sweep (`P1-31`) is what spends it
- `P2-03` `worker/novelty.py` — the pass (`python -m worker.novelty`). Its own
  process, not a stage of the fetch loop (the vector arrives a pass later) and
  not a stage of the embedding backfill either: the gate is Postgres and
  arithmetic, so binding it to the one process carrying 2.3GB of weights would
  mean a corpus could only be deduplicated on a machine that could embed it.
  Resumable by predicate, one batch per transaction, `--once` and
  `--max-batches` for a bounded first run
- `P2-03` Source-level demotion (§5.4). A source whose chunks are ≥90% duplicates
  and fully judged moves `background` → `junk`. **Never `primary`**: §5.4 keeps
  the raw file for government documents and papers precisely because link rot
  makes them unrecoverable, and a mirror crawled second is still the citable
  copy of a real document
- `P2-03` §12.5's novelty pass rate on the health line. The comment saying it
  belonged to the orchestrator was true until the gate became a worker pass. A
  rate that collapses means the crawl has found a mirror, a paginated view of
  one document, or a site serving the same boilerplate under every URL — all of
  which read as a healthy crawl in every other number on the line

### Notes

- `duplicate_of` is self-referential and `ON DELETE SET NULL`, not CASCADE. A
  re-crawl replaces a source's chunks, so a survivor can vanish under a verdict
  naming it — and the duplicate is then the only copy of that text left
- A duplicate never points at another duplicate: marked chunks are excluded from
  the candidate set, and a verdict landing on one marked in the same batch is
  followed through to the survivor. So `duplicate_of` is "the surviving copy",
  not the head of a chain every consumer has to walk
- Verified against a real crawled corpus, not only against tests: the pass found
  the boilerplate blocks a site repeats under every URL, at similarity 1.0, and
  demoted no source — the pages carrying them are otherwise distinct. Unrelated
  chunks from real bge-m3 sit far below the threshold, so `0.95` is not close to
  the noise floor
- The `chunk_id <` filter is applied after the vector scan, so `P2-04`'s HNSW
  index will make this faster without making it exact. Acceptable: a missed
  near-duplicate is a chunk that stays, not one wrongly dropped


## [0.24.0] — 2026-09-07

**The stack gets a topology, and the browser stops being invisible.** Closes the
two items standing between a worker image that runs and a stack that does.

### Added

- `P1-22` Two networks instead of one. `internal` keeps `internal: true` and now
  contains only what must never reach the open web; `egress` is an ordinary
  bridge for everything that must. `worker` is the only service on both, because
  it is the only one that fetches hostile content *and* writes it to the
  database. The comment admitting this was unresolved has been in the compose
  file since `P0-01`
- `P1-26` `deploy/crawl4ai/` — a Dockerfile and a server-side config. Thin on
  purpose: the upstream image is a 6GB browser pool, so this pins the version
  §6.4 requires pinned and supplies the configuration, rather than rebuilding it
- `P1-26` A health check the worker trusts. `Crawl4aiClient.healthy()` probes
  `/health`, and the §12.5 health line now carries `browser:
  configured | unreachable | absent`. Three states because they need different
  responses — `absent` is a deployment that never intended to render, while
  `unreachable` is the silent failure this exists for: the fetcher degrades to
  static and keeps working, so nothing else ever notices that JS-dependent pages
  stopped being rendered. `unreachable` logs at WARNING
- A compose healthcheck on the browser, with a 60s `start_period` because a
  browser pool is slow to warm, so `worker` can `depends_on` it meaningfully
- `tests/unit/test_compose_topology.py` — drift tests over the compose file.
  The topology is a security boundary edited by hand, and its failure mode is
  silent: a service keeps working perfectly while sitting on the wrong network

### Fixed

- **The browser held every credential in `.env`.** `x-common` carried
  `env_file: .env`, so `crawl4ai` — the one process whose job is rendering
  hostile pages — inherited the database passwords and the tunnel token. It now
  receives exactly two variables, both named explicitly, and `x-common` carries
  no `env_file` or `networks` at all, since a shared default for either is how
  this happened
- **`orchestrator` set both `network_mode` and `networks`.** Compose rejects a
  file that does, so the production stack would have failed to start

### Notes

- Verified rather than assumed: the compose file resolves, the image builds and
  starts, `/health` answers 200 in about ten seconds, and the worker's own probe
  returns True against it and False against a dead address
- `/health` is unauthenticated on 0.9.2 — a wrong token still returns 200, while
  `/schema` and `/crawl` refuse. The probe sends the token regardless
- `internal: true` is **not** `P1-25`. It stops a container reaching the
  internet; it does not stop `worker`, which must have a default route, from
  reaching the LAN. The egress restriction is still the defence that survives an
  application bug


## [0.23.0] — 2026-09-07

**A challenge interstitial gets one bounded chance to clear itself.**

### Added

- `is_challenge()` and a re-fetch through the browser when a static fetch hits a
  bot-challenge interstitial. The common non-interactive kind runs a few seconds
  of JavaScript and then serves the real page, so waiting it out is not evasion
  — it is what an ordinary browser does, and §6.4's decision to skip stealth
  mode, undetected browsers and proxy escalation is untouched
- `challenge_wait_s` on the fetch policy, default 15s, `0` to disable per
  domain. Bounded and tried **once**: the interactive kind never clears however
  long it is given, so a domain that serves one should not pay for a browser
  launch on every URL
- `Crawl4aiClient.crawl(settle_s=...)` holds the page open after load and waits
  on `networkidle` rather than `domcontentloaded` — a challenge fires its own
  requests, and returning at DOM-ready reads the interstitial instead of
  whatever replaces it. The HTTP timeout grows by the settle, or the wait would
  be spent and then discarded by a client-side timeout

### Fixed

- `Fetcher.fetch` returned any failed static result immediately, so a challenge
  — the one refusal a browser can sometimes turn into a success — never reached
  the browser at all
- A refused static fetch now carries its response headers. Detection reads them
  from the result, long after the response context has closed, and `cf-mitigated`
  is what separates a challenge from an ordinary refusal

### Notes

- Detection is deliberately narrow, because the expensive mistake is the false
  positive: a detector that fires on ordinary 403s spends a browser launch on
  every permission-denied URL in the corpus. It requires a challenge status
  *and* either the mitigation header or one of three machine-generated body
  markers, scanned only in the first 8KB — so an article discussing bot
  challenges does not trip it, the same distinction `P1-23` turns on


## [0.22.0] — 2026-09-07

**Sitemaps become frontier, and a URL's topic stops being inherited.** Measured
against the real seed list: most of the seed domains advertise a sitemap, the
largest runs to several thousand URLs, and every one of them is served as
`text/xml`.

### Added

- `P1-28` `worker/sitemaps.py` — sitemap and sitemap-index parsing.
  `RobotsRules.sitemaps` has been parsed since `P1-04` and thrown away ever
  since; this is what finally reads it. `FetchResult.sitemaps` carries what
  robots.txt advertised, the loop enqueues it, and `HANDLED_TASK_TYPES` grows a
  `sitemap` handler so those rows are not claimed by nothing forever
- **Two independent defences against XML entity expansion.** lxml expands
  internal entities by default — measured, not assumed: four levels of nesting
  turn ten bytes into ten thousand, and each further level multiplies by ten.
  A byte pre-scan refuses a DTD *before* parsing, which is the defence that
  matters because the allocation is the attack; `resolve_entities=False` is the
  second, tested directly against the parser configuration
- **A sitemap may not write to another site's frontier.** sitemaps.org permits
  cross-submission when robots.txt authorises it; we refuse it, because a
  hostile robots.txt would otherwise inject unbounded URLs at whatever priority
  the target domain's tier grants. Same-site is suffix-matched in both
  directions, since `registrable_domain` keeps subdomains and equality would
  discard a legitimate sitemap's entire contents
- Nesting is handled by the queue, not by recursion: a `<sitemapindex>` becomes
  further `sitemap` tasks rather than being followed inline, so one task cannot
  fetch an unbounded tree while holding a lease
- `P1-28` `worker/topicmatch.py` — a URL's topic from its path, mechanically.
  Frontier expansion inherits the linking page's topic and that is a fair guess
  for a link; for a site's whole index it is wrong about nearly every row, and
  wrong in a way that *spreads*, since every crawled page passes its topic to
  the links it discovers. Matching is against the gazetteer, which already
  carries `topic_labels`, **plus the topic names themselves** — without the latter a
  newly added topic could never be assigned, so nothing would be crawled for it,
  so nothing would be harvested to populate it: a loop with no way in
- **Ambiguous gazetteer terms are excluded**, which is what the flag exists
  for. A short acronym routinely expands to two unrelated things; §5.5 resolves
  that from document context, and a URL path has none. Matching is on whole
  tokens, so `bus` does not match `business`
- **An unmatched sitemap URL is deprioritised, not dropped.** Priority `-10`,
  below `informal`'s 5, so it is crawled when the frontier has nothing better —
  which is exactly when incidental discovery is worth paying for. Dropping it
  would assume the path is evidence of irrelevance, and §7.4 warns against a
  corpus that only ever confirms its own vocabulary
- `Crawler.fetch(policy_overrides=...)`, following the seam `RobotsCache`
  already uses for robots.txt. Sitemaps need it because `text/xml` is not in
  `allowed_content_types` — every real sitemap checked is served as exactly
  that, so without the override the feature would refuse the majority of its own
  input while looking like a network problem
- Three further topics seeded, with weights renormalised to 1.0 across the set,
  and one existing topic renamed across config and the four tables that
  referenced it

### Fixed

- `P1-19` A 4xx is no longer recorded as a bare `HTTP 403`. `describe_http_error`
  reads the headers that say *why* — `cf-mitigated`, `retry-after`, `server` —
  and names a Cloudflare bot challenge in words. The case that motivated it was
  repeated attempts against one seed domain, all recorded identically, with
  nothing in the database saying the cause was a challenge that will never
  succeed however often it is retried
- `lxml` declared explicitly on `meridian-worker`. It arrives transitively
  through trafilatura, and `P1-30` already paid for that lesson once with
  `meridian_core`'s undeclared `pyyaml` dying on `ModuleNotFoundError` in a
  container built with `uv sync --package`

### Notes

- **Undetected browser mode was investigated and does not work**, tested rather
  than assumed. Crawl4AI 0.9.2 forbids `proxy_config`, `magic`, `simulate_user`
  and `override_navigator` from remote request bodies, but that turned out not
  to be the obstacle: running `UndetectedAdapter` + `enable_stealth` + all three
  forbidden flags *natively inside the container*, with waits of 28s and 41s
  across `networkidle` and `load`, still returns 403 with `cf-chl` and
  `turnstile` in the body. It is an interactive Turnstile challenge, which
  undetected browsing does not solve — only a CAPTCHA-solving service would.
  §6.4's "skip stealth mode" stands, now on evidence rather than on principle
- One seed domain is consequently unfetchable, including its robots.txt and
  root. A 4xx on robots.txt maps to `ALLOW_ALL` per RFC 9309, so nothing else
  about the domain is confused by it — it simply fails, three attempts at a
  time, and `domain_signal` correctly declines to mark it blocked because a 403
  means the server answered


## [0.21.0] — 2026-09-08

**Phase 2 opens: the corpus becomes searchable by meaning.** Verified against
the real crawl — a natural-language query returns the relevant agency page and
its annual report, neither of which shares a word with the query.

### Added

- `P2-01` `worker/embeddings.py` — bge-m3 through sentence-transformers. §4
  chose a multilingual model because serious literature for the comparison set is
  substantially non-English, and an English-only model would bias the corpus
  toward Western sources while every measurement of it looked fine
- **Nothing loads until something asks for a vector.** `worker.main` imports the
  package tree and never embeds; a 2.3GB load at import would sit in the
  crawler's memory budget for work it does not do. The runtime is imported
  inside the loader too, so a worker built without the `embed` extra starts,
  crawls, and reports the absence rather than dying at startup
- **The dimension is checked at load, not discovered at insert.** A model
  returning 768 would otherwise surface as a pgvector error a thousand chunks
  later, pointing at the write rather than at the misconfiguration
- `worker/embed.py` — the backfill. §6.1 draws embedding inside the fast loop
  and this runs it as a separate pass: the fetch path never touches the model,
  the worker image stays at 729MB, and a slow encode cannot stall a fetch that
  had nothing to do with it. The schema was already built for the split, since
  `chunks.embedding` is nullable and `P2-02` writes vectors as NULL by design
- **Resumable by construction.** The queue is `embedding IS NULL` — there is no
  cursor to corrupt and no state outside the table — and one batch is one
  transaction, so an interrupted four-hour backfill keeps everything up to its
  last commit
- `chunks_without_embeddings()`, `store_embeddings()` and `embedding_backlog()`
  in `meridian_core/chunks.py`. Paging is by id rather than OFFSET: a backfill
  that pages by offset re-scans what it has read on every page and shifts under
  its own feet as the crawl writes new chunks in the middle of the run
- `FakeEmbedder` — deterministic unit vectors from a hash, not a mock. Identical
  text embeds identically and cosine behaves, which is what lets `P2-03`'s
  novelty gate and `P2-06`'s search be built and tested without a 2.3GB download
  in CI

### Fixed

- `httpx`, `huggingface_hub`, `transformers` and friends quieted to WARNING.
  Loading bge-m3 emitted around forty INFO lines of Hub HEAD requests into a
  JSON log stream before saying anything useful

### Notes

- `sentence-transformers` is an **optional extra** (`meridian-worker[embed]`),
  so the worker image does not carry torch. Whatever runs the backfill installs
  it; the crawler does not need to
- Measured on this x86 machine at ~1.1 chunks/second on CPU with a batch of 8.
  The arm64 figure is what `P1-16` will actually reveal, and the batch size is
  deliberately small — §3's node is a 16GB board shared with Postgres, and a
  batch that swaps is far slower than two that do not

## [0.20.0] — 2026-09-08

**The worker becomes deployable.** It has been production-shaped for five
releases — read-only, unprivileged, graceful on `SIGTERM` — and there was still
no way to run it anywhere but a laptop, because the `services/worker/Dockerfile`
that `docker-compose.yml` has referenced since `P0-01` did not exist.

### Added

- `P1-30` `services/worker/Dockerfile`. Multi-stage uv build on
  `python:3.12-slim-bookworm`, pinned to the floor every `pyproject` declares so
  a wheel that exists only for a newer interpreter fails at build time rather
  than on the Pi at 3am. Built from the repository root, because the services
  share a uv workspace and a context narrowed to `services/worker` cannot see
  `meridian_core`
- **`poppler-utils` in the runtime image.** Not optional: `extract_pdf` raises
  rather than degrading, so an image without it is one where every PDF fails
  loudly on the first crawl
- `deploy/meridian.service`. The scaffold is specific that compose's
  `restart: unless-stopped` **or** systemd supervises, not both — two
  supervisors racing to restart one container is how a crash loop becomes
  invisible — so the unit is `oneshot` and owns only the stack. `TimeoutStopSec`
  is 300 because `P1-15` drains rather than drops on `SIGTERM`, and killing it
  early throws the work away and strands the leases for their full expiry
- `make build-worker`, a `.dockerignore`, and a `HEALTHCHECK` that fails while
  poppler is missing or the package tree is broken — the two faults that would
  otherwise surface only as a corpus that quietly stopped growing

### Fixed

- **`meridian_core` never declared its dependency on `pyyaml`.** `policy.py`
  imports it to read `config/fetch_policy.yaml` as the floor under every fetch
  policy, and it was declared on the *root* project instead. Development never
  noticed because the root install provides it; a container built with
  `--package meridian-worker` gets `meridian_core` and nothing the root happens
  to also depend on, and died on `ModuleNotFoundError: No module named 'yaml'`
  at import. Found by running the image rather than by building it
- `HOME=/tmp` in the image. The filesystem is read-only apart from the tmpfs,
  and onnxruntime — pulled in by MarkItDown's file sniffing — prints a
  plain-text warning to stderr when it cannot persist a telemetry id. That
  landed in the middle of a JSON log stream a health check greps by severity

### Notes

- Verified by running the built image against the real database with
  `--read-only --cap-drop ALL` as an unprivileged user: three real government
  pages fetched, stored, extracted, chunked, and 175 links queued, to a
  bind-mounted raw store. Building it proves nothing; §13.4 is about what
  happens when nobody is watching
- This does **not** close phase 1's checkpoint. `P1-22` (the compose file still
  admits in a comment that `internal: true` blocks the outbound access the
  worker needs) and `P1-26` (Crawl4AI has no image and no health check the
  worker trusts) are what stand between a worker that runs and a stack that does

## [0.19.0] — 2026-09-08

**The last format gap closes.** §6.6 routes Office documents to MarkItDown, and
government and consultancy sources arrive as them far more often than expected.

### Added

- `P1-08` `services/worker/worker/extract/document.py` — `.docx`, `.xlsx`,
  `.pptx` and CSV, each verified against a real file built in its tests
- **`convert_stream()` on already-fetched bytes only.** Never `convert()` on a
  URL, never `convert_local()` — §6.6 is explicit that MarkItDown's `convert()`
  "is intentionally permissive across local files, remote URIs and byte
  streams", and it performs that I/O with the calling process's privileges
- **An explicit converter allowlist**, which turned out to matter more than the
  invariant above. MarkItDown ignores the declared media type as a gate: it
  sniffs the bytes with magika and tries *every* converter that accepts any
  guess, including a final pass where converters see no type at all. Its default
  registry contains converters that fetch URLs (YouTube, Wikipedia, Bing), shell
  out to `exiftool` on untrusted bytes, and a `ZipConverter` that extracts an
  archive to a temp directory and re-dispatches its members by extension — and a
  `.docx` *is* a zip, so hostile input reaches that path through sniffing
  whatever the `Content-Type` said. `enable_builtins=False` plus four registered
  converters closes it, and two tests fail if the registry is widened
- Metadata from the OOXML `docProps/core.xml`, because MarkItDown returns only
  markdown and a title that is always `None` for these formats — every Office
  source in the corpus would otherwise have a NULL title and cite as a bare URL.
  Read with entity resolution and network access off, and size-capped: verified
  against a 200MB decompression bomb, refused both with an honest header and
  with a forged one
- The injection pre-screen now runs its **text half on every format**, not just
  HTML. A tool directive in a spreadsheet reaches a model exactly as well as one
  in a web page; the DOM half stays HTML-only because a `.docx` hides text by
  other means and deserves its own screen rather than a pretend one

### Notes

- No wall-clock timeout on conversion, deliberately. `asyncio.to_thread` cannot
  cancel a running thread, so a `wait_for` would free the lane while leaking a
  worker from a bounded pool — a handful of pathological documents would then
  stall the loop permanently. The fetcher's `max_page_bytes` is the enforceable
  bound
- `.doc`, `.xls`, EPub and ZIP are outside the allowlist on purpose. Converting
  an EPub as a zip of HTML is exactly §6.6's "silent degradation"; they are
  stored and left metadata-only, and the raw file is what lets §11.12 recover
  them if a converter appears
- `markitdown` needs an explicit `onnxruntime>=1.29` on Python 3.14. It pins
  `magika~=0.6.1`, which uv resolves to an `onnxruntime` with no cp314 wheel, and
  the failure names Python ABI tags without mentioning markitdown at all

## [0.18.0] — 2026-09-08

**A tripwire for prompt injection, before anything reaches a model.** The slow
loop will hand a frontier model chunks pulled straight out of pages this crawler
found by following links off other pages, and that model holds write tools —
`add_edge`, `tag_entity`, `enqueue_seed`. Since `P1-06` the frontier follows
links at volume, so the pages reaching extraction stopped being a curated seed
list and this stopped being theoretical.

Verified against the 11 real government pages already crawled: 9 clean, 2 noted,
**0 flagged**. The false-positive rate is the number that decides whether anyone
ever reads this flag.

### Added

- `P1-23` `services/worker/worker/extract/injection.py`. Mechanical only — regex
  and DOM work, no model. §2.1's fast-loop invariant is that ingestion keeps
  working with every reasoning model offline, and screening for injection *with*
  a model would put the guard behind the thing it guards
- **Hidden is the signal; imperative is not.** This is the distinction the module
  turns on. A research corpus about AI will legitimately quote "ignore all
  previous instructions" — in an article about prompt injection, exactly the sort
  of source this system should be reading. That is recorded as
  `visible_instructions` and is *not* suspicious. Text hidden from a reader that
  still reaches extraction has no honest purpose, and that is
  `hidden_instructions`
- Seven ways of hiding text, each named in the finding so the flag says *how*:
  `display:none`, `visibility:hidden`, zero opacity, zero font size, positioned
  offscreen, clipped to nothing, zero size — plus the `hidden` and `aria-hidden`
  attributes, and white-on-white including the ordinary form where the white
  background is inherited rather than set on the same tag
- Instructions in HTML comments, which reach no reader by construction and
  survive naive extractors. Screened on the raw source, and still screened when
  the page will not parse — serving broken HTML would otherwise be a way past
  the DOM checks
- `tool_directive`: imperatives aimed at a tool-holding agent ("add an edge",
  "send the contents to"), suspicious **even when visible**, because visible
  injection works and such a page has no ordinary-prose version of itself
- The screen runs on the raw HTML *and* the extracted text, because they answer
  different halves: hiddenness is a DOM property extraction has already
  discarded, and what survived extraction is what a model would actually read

### Notes

- **Flag, do not delete.** §2.5's rule that steering adjusts rather than destroys
  applies here: a flagged page is stored, extracted and chunked exactly as
  normal, and the finding sits beside it in `sources.extra["injection"]`. Nothing
  is blocked — excluding quarantined content from the slow loop's batch is
  `P4-06`, which needs the frontier model that judges the domain to exist first
- The `WARNING` log line is therefore the whole mechanism until `P4-06` lands,
  which is why it is a warning rather than an info
- One false positive is accepted deliberately: an article *quoting* a full
  payload trips `tool_directive`. That is the right side to err on — the flag
  blocks nothing, so the cost is a source somebody glances at, against the cost
  of a visible injection read as prose by a model holding `add_edge`
- HTML only. A PDF has no DOM to hide text in the same way, and its text-layer
  equivalents need a different screen than this one — worth having, and not
  worth pretending this is it

## [0.17.0] — 2026-09-08

**PDFs become readable, and scans become findable.** Since `P1-06` the frontier
had been queueing PDFs faster than anything could read them, and on a government
corpus that is a large share of the substance rather than an edge case.

Verified live against real government documents: a short PDF extracted across
2 pages, a long transport master plan extracted with its own title read out of
the PDF's Info dictionary, and every chunk carrying the page number a citation
resolves to.

### Added

- `P1-09` `services/worker/worker/extract/pdf.py`. `pdftotext` over stdin — the
  bytes are already in memory, and `-` means nothing this crawler downloaded
  ever lands on disk under a name another process could reach. Page boundaries
  come from the form feed poppler already writes, so they are exact rather than
  reconstructed, which §6.6 warns is the first thing these pipelines flatten
- **Scan detection**, §6.6's fork. Below ~100 characters per page a document is
  a scan, and this is the check that stops a scanned planning report entering
  the corpus as "extracted, nothing found" — the failure mode where nobody ever
  looks again. Its stray text layer (a page number, a running header) is dropped
  rather than kept, or the novelty gate would be left telling two scans apart by
  their headers
- `P1-13` `worker/ocr_queue.py`. OCR never runs inline — it would stall the
  23-hour loop for one document — so a scan becomes a source record plus one
  `enrichment_queue` row. Idempotent, because the pending count is a number an
  operator makes a spending decision from and a re-crawl must not inflate it.
  `ocr_applied` and `ocr_tier` are written explicitly so a skipped document is
  findable rather than inferred from an absence
- `chunk_pages()` — chunks carrying page numbers instead of character offsets
  (§5.3). **A chunk never spans a page break**: one covering pages 4 and 5 has
  to be cited as one of them and would send a reader to the wrong page for half
  its content. Short pages therefore make short chunks, which is the honest
  trade
- `worker/extract/base.py` — the document shape every extractor returns. §6.6
  routes each format to a different tool and the pipeline behind them is
  identical, so the answer's shape has to be identical too, or the loop grows a
  branch per format and each one is where a field gets forgotten
- Title, author and creation date from `pdfinfo`. A PDF with no title in
  `sources` is a citation that renders as a URL, and government reports set the
  field far more often than they are given credit for

### Notes

- A missing poppler **raises**, and is logged as the deployment fault it is.
  §6.6's own lesson about `markitdown-ocr` silently skipping applies exactly: a
  worker that has quietly lost poppler would store every PDF and extract none of
  them, and the only symptom would be a corpus that stopped growing
- Everything else about an unreadable document is returned rather than raised —
  an encrypted file, a truncated download, a HTML error page served as
  `application/pdf`. Those are documents this crawler cannot read, and §6.5
  already has a resting state for them. The raw file is kept either way, so
  §11.12 recovers them the day a better extractor lands
- The PDF tests build **real PDFs** with ghostscript rather than checking in
  fixtures. A byte string starting with `%PDF-` exercises the error path and
  nothing else, and both things worth testing — surviving page boundaries, and
  recognising a scan — only exist inside a file a real extractor can read

## [0.16.0] — 2026-09-07

**The crawler starts crawling.** Since `P1-15` the worker had drained the same
13 seeded rows on every run and then idled forever, which is a fetcher.
`ExtractedDocument.links` had been populated and dropped on the floor since
`P1-07`. This connects the two, through the gate that makes connecting them
safe.

Verified live: 13 seeds in, **334 pending out**, with the crawl claiming
frontier-discovered pages within the same run. Tier priority sorts them without
anyone curating a list — `.edu.sg` at 60, `.gov.sg` at 50, blogs below — and no
blocked domain or asset URL reached the queue.

### Added

- `P1-06` `services/worker/worker/prefilter.py`. Four gates, cheapest first, and
  the order is the design: normalise, then shape (scheme, host, extension), then
  blocklists, then one batched query each against `queue` and `sources`. A page
  with 500 links reaches the database as a batch of 40, because §6.4's reason
  for the prefilter is that *not fetching* a duplicate is cheaper than fetching
  it and discovering it later
- **Conservative URL normalisation.** Fragment, tracking parameters, credentials
  and default ports go; host is lowercased. Trailing slashes, path case and
  query order stay — anything that changes *which resource is requested* trades
  a duplicate for a page that silently never enters the corpus, and the two
  errors are not symmetric
- Frontier expansion in the loop: a fetched page's links become queue rows in
  the same transaction as its source row and chunks. In the fetch pass, not a
  later sweep, because the link list lives only in memory — and deliberately not
  stored on the source row, where 500 URLs would be ~40KB of JSONB and ~2GB
  across a 50k corpus
- `enqueue()` and `already_queued()` in `queueing.py`. `enqueue` deliberately
  does no filtering: whether a URL is worth fetching needs blocklists, the
  already-seen check and the domain's policy, none of which belong in a function
  that inserts a row, and all of which Admin would have to work around when
  injecting a seed by hand (§13.2)
- A seeded `frontier.blocked_domains` list in `config/fetch_policy.yaml` —
  social platforms, link shorteners, search engines. Not a judgement: these are
  places a research corpus cannot cite, they appear on nearly every government
  page, and without the list the frontier spends its first hour discovering that
  Facebook exists. Matched by suffix, so one entry covers `m.` and `www.`
- Tier-derived queue priority is finally wired. `priority_for_domain()` has
  existed since `P1-17` with no caller; a government link now outranks a blog
  with nobody curating a seed list (§5.2)

### Notes

- Any queue status counts as seen. A URL that failed is not worth retrying under
  a new task id — `next_attempt_at` is for that — and one that is `done` is not
  worth re-fetching because another page links to it. Re-crawl scheduling is a
  separate decision, and conflating them would have every page's link list
  resurrect the whole corpus
- Frontier expansion is off when the `Worker` is built without a prefilter,
  which is distinct from a prefilter that drops everything. A one-shot refetch
  should not silently start crawling
- A link hub with no extractable text still contributes its links. An index page
  whose only content is a list of links is exactly the page most worth following
  out of, and gating expansion on `has_text` would skip it

## [0.15.0] — 2026-09-07

**Extracted text stops being discarded.** `P1-07` produced text and dropped it,
because `chunks` is the only home the schema has for it and chunking sat in
phase 2. That was survivable for a primary source — its raw file is kept, so
§11.12 can re-derive — and permanent loss for a `background` one, which keeps no
file at all. `P2-02` is pulled forward for that reason: the loss was live, and
`P1-16`'s 48-hour unattended run would have spent two days making it worse.

Verified live across two passes. First: seven sites crawled, six chunked, and
every stored offset round-trips — re-extracting the raw file from disk puts each
chunk back exactly where its `page_or_offset` says it is. Second: five real
`304`s, one `200` whose bytes were byte-identical, and `chunks: 0` for all of
them.

### Added

- `P2-02` `worker/extract/chunk.py`. **A chunk is a verbatim slice**:
  `source[offset:offset + len(text)] == text`, exactly. §5.3 requires the offset
  at extraction time because reconstructing it later is "painful and often
  impossible", and an offset that does not locate its passage is worse than none
  — it makes a citation look checkable when it is not. Offsets are tracked
  through every split rather than recovered by searching for the text
  afterwards, which would resolve a repeated boilerplate paragraph to its first
  occurrence and cite the wrong copy
- Structure-aware splitting: whole paragraphs packed to a target, sentences when
  a paragraph exceeds the cap, a hard cut only when a single sentence does. Each
  fallback reached only when the one above it cannot help. Undersized chunks are
  merged into a neighbour — backwards normally, forwards for a leading one,
  since a document opening with a `# Title` line is the common case
- **No overlap, deliberately.** Overlap compensates for blind splitting cutting
  through an idea; splitting on paragraph boundaries fixes the same problem
  directly, and paying for both duplicates text in the table, in the embedding
  index, and in every batch the slow loop reads
- `meridian_core/chunks.py` — `replace_chunks()`, `delete_chunks()`,
  `chunk_count()`, and an `as_writes()` seam so the core package can describe its
  own table without depending on a service's dataclasses
- Chunking runs in the same transaction as the source row. A source whose
  checksum says one thing and whose chunks were cut from another is a corpus
  citing text it does not hold

### Notes

- A re-crawl of unchanged content leaves the chunks alone. §6.3's high-water mark
  is a `chunk_id`, so rewriting identical chunks would hand the slow loop a day
  of "new" material it has already read — the most expensive possible no-op,
  since reading it is the part that costs tokens
- Changed content *replaces* them, and the new ids are what make the slow loop
  re-read the page. Nothing has to notice the change or schedule the re-read;
  the mark simply falls behind
- Replacement is not yet safe for provenance. `edges.supporting_chunk_ids` is an
  array with no foreign key behind it, so a deleted chunk leaves an edge pointing
  at nothing. No edges exist today, so nothing is orphaned — recorded as `P1-32`,
  which needs the graph to exist before it can be answered

## [0.14.0] — 2026-09-07

**The bytes become text.** §6.6's routing table sends HTML to Crawl4AI and
§6.4's first operational constraint says not to render every page — both right,
and together they mean extraction has two inputs, not one. Crawl4AI's
`PruningContentFilter` output is used where the browser actually ran; everything
else, which is most of the corpus, is extracted locally.

Verified live: seven seeded sites crawled and six extracted, with sae.org
correctly landing metadata-only because it is a 62-visible-character JS shell.
A real arXiv abstract page yields its title, its 2024-01-05 publication date,
its abstract, and its own DataCite DOI — with no self-citation in its reference
list.

### Added

- `P1-07` `services/worker/worker/extract/html.py`. `trafilatura` for the static
  path, configured `favor_precision`: a research corpus would rather lose a
  sentence of body text than gain a navigation menu, because boilerplate becomes
  entities, entities become edges, and a graph full of "Skip to main content" is
  expensive to unpick. Crawl4AI's `fit_markdown` wins where a browser payload
  exists — re-extracting from the HTML it returned would throw away a filter that
  ran on a rendered DOM this process never had
- Bibliographic metadata into `sources`: title, author, publisher,
  `publication_date`, language, `doi`, `text_available`. Never guessed — a
  partial date is discarded rather than completed, because `publication_date` is
  a DATE that citations are built from and a fabricated day is worse than a
  missing one
- Mechanical citation extraction: DOIs, arXiv identifiers (both the 2007 and the
  pre-2007 schemes), PubMed IDs and handles, from the text *and* the links. Both,
  because a reference list writes DOIs as text while a "view on arXiv" button
  carries the identifier only in an `href` — taking one and not the other misses
  a predictable population of papers rather than a random sample. Feeds `P1-14`
- The page's own DOI is kept apart from the ones it cites. `sources.doi` is what
  makes a source resolvable; a cited DOI is a pointer to something else to fetch.
  arXiv publishes no `citation_doi` tag at all, so its DOI is derived from the
  identifier already in the URL — its own mechanical mapping, not a guess — and
  its own identifiers are excluded from its citation list
- `text_available` now means something. §6.5 makes metadata-only an explicit
  resting state, and it is a *threshold*, not `text != ""` — a page whose only
  extractable content is a cookie banner has text in the strict sense and nothing
  a graph can be built from

### Notes

- New dependency: `trafilatura` (Apache-2.0), in the worker only. It wins the
  extraction benchmarks by a wide margin, and boilerplate removal is the one part
  of this pipeline where the quality difference compounds downstream
- Links are anchors, not `iterlinks()`. That yields every URL in the document —
  favicons, stylesheets, apple-touch-icons — and on a real government home page
  the assets outnumber the documents several times over. A frontier fed from that
  list spends its budget fetching 180-byte PNGs
- Extraction failing does **not** retry the fetch, unlike a storage failure. The
  bytes are already stored and re-extractable (§11.12); going back to the network
  would spend a request to solve a local problem
- A format with no extractor yet — PDFs (`P1-09`), Office documents (`P1-08`) —
  is stored and left metadata-only rather than failed. The raw file is what makes
  that recoverable the day its extractor lands

## [0.13.0] — 2026-09-07

**The crawl stops throwing away what it fetched.** Until now the loop got the
bytes, recorded that it got them, and dropped them — no source record, no local
copy, and a `conditional_requests` setting that was on, correct, tested and
unreachable because nothing had ever stored an ETag for it to send.

Verified live, and the second crawl is where it shows: a first pass fetched five
seeded sites and kept four (the fifth is a blog, and §5.4 says background sources
keep their text and not their bytes). The second pass over the same five returned
`304 Not Modified` four times and zero bytes, and the one server that answered
`200` returned content whose checksum was unchanged — so both halves of "has this
page changed" now work, the cheap one and the common one.

### Added

- `P1-11` `services/worker/worker/rawstore.py` — the raw store. Path is
  `<domain>/<hex shard>/<sha256(url)><ext>`: derived from the URL so a re-fetch
  lands on the file the last fetch wrote, domain-first because "everything from
  this site" is what a takedown and a retention sweep both need, sharded so one
  busy domain is not one directory with a hundred thousand entries. The URL is
  attacker-influenced, so a host that cannot be a directory name is refused
  rather than sanitised into something plausible, and the extension comes from
  an allowlist rather than from anything the server claimed its file was called
- Atomic writes. Content goes to a temporary name in the destination's own
  directory, is fsynced, and is then `os.replace`d into place. A crash halfway
  through a 20MB PDF must not leave a truncated file that the checksum beside it
  swears is complete — that is a corruption you discover years later, when the
  citation is the thing you needed
- **Retention tiers actually split** (§5.4). Government, peer-reviewed and
  institutional sources keep the file; press and informal ones keep their
  checksum and metadata and nothing else. A Pi's NVMe cannot hold the HTML of
  every page the frontier wanders into, and `raw_file_path IS NULL` now means
  *deliberately not kept* rather than *missing*. `junk` is unreachable from
  here on purpose: it is the novelty gate's verdict on a near-duplicate, and
  nothing at fetch time has seen enough of the corpus to make it
- `meridian_core/sources.py` — `upsert_source()`, `touch_source()`,
  `get_source()`. Every field is optional and `None` means "nothing new", never
  "clear it": a response that came back without an ETag must not erase the one
  from last week, because the next request would silently stop being
  conditional and nothing would notice except the bandwidth graph. Returns
  whether the checksum changed, which is what lets a re-crawl skip extraction
- `resolve_source_tier()` / `source_tier_map()` in `policy.py`. The domain → tier
  mapping has been seeded into the global `fetch_policy` row since `P1-12` and
  read by nothing; this reads it back. Mechanical and deterministic (§5.2),
  never a model judgement
- The loop persists before it settles, and a fetch it could not keep is retried
  rather than advanced. Advancing anyway would lose the URL permanently — the
  queue saying `fetched`, no source row, and nothing left that would ever ask
  for it again. A full disk now looks like a backoff and then a `failed` row
  carrying `storage_error:`, which is a problem somebody can see
- Checksums are stored as `sha256:<hex>`. A bare hex string is a checksum nobody
  can verify in five years without first working out what produced it, and being
  checkable then is the entire point

### Notes

- Source and retention tier both refuse to move *down* automatically, the same
  rule §11.12 states for quality tier. Tiering is deterministic so the mechanical
  answer and the stored one normally agree; they stop agreeing the moment someone
  corrects a domain by hand in Admin, and a correction the next crawl reverts is
  worse than no correction at all
- `sources.raw_file_path` is relative to the store root. An absolute path would
  bake in `/data/raw` — the container's mount point, not the host's — and a store
  moved to a bigger disk would invalidate every row that recorded one

## [0.12.0] — 2026-09-07

**Nothing ran unattended until now.** Every piece of the fetch path has existed
and been tested since `0.11.0`, and none of it had a caller: `claim_next` handed
out tasks nobody claimed, `Crawler.fetch` fetched URLs nobody asked for, and
`prune_attempts()` bounded a table nothing was filling. This release is the loop
that runs them — which is the whole difference between a crawler and a library
that could crawl.

Verified against the real web, not only against doubles: seven government and
standards sites fetched under their own robots.txt and delays, a
403 abandoned rather than retried three times, the health line printed with
§12.5's queue depth and fetch success rate, and `SIGTERM` finishing the two
requests in flight before stopping.

### Added

- `P1-15` `services/worker/worker/main.py` — the worker loop. Concurrency is N
  independent claim-fetch-settle lanes over one shared `Crawler`, with no
  dispatcher and no in-process queue: the database is the queue and
  `FOR UPDATE SKIP LOCKED` is the dispatcher, so four lanes in one process and
  two processes of two behave identically and scaling out needs no coordination
  invented for it. Politeness stays where it already was — `DomainLimiter` is
  what stops four lanes becoming four simultaneous requests to one host
- `queue_disposition()` in `queueing.py` — what a fetch outcome means for the
  *task*, which is a different question from what `domain_signal()` asks about
  the domain, and the two disagree in both directions. A 404 is a healthy domain
  and a dead URL; a decompression bomb counts against the domain and is never
  requested again. Refusals that are deterministic in the retry window — robots,
  the block list, a rejected media type, an address `netguard` refused — are
  abandoned on the first attempt instead of spending three requests to be told
  the same thing
- `abandon()` in `queueing.py` — fail a task now, with no retry, while still
  counting the attempt that happened
- `release_worker_claims()` — a clean shutdown hands its leases back rather than
  leaving them to expire, so a restart is not fifteen minutes of a queue that
  looks busy and is doing nothing
- `queue_depth()` — counts by status, not one number: 4,000 pending and 4,000
  failed are the same depth and opposite situations, and the health line exists
  to tell them apart
- `task_types` filter on `claim_next()`. The queue holds `query`, `doi` and
  `sitemap` rows whose handlers are still unwritten (`P1-14`, `P1-28`), and a
  loop that claims one has only two ways out — fail a task that is not broken,
  or hand it back and claim it again forever
- A housekeeping tick inside the loop: `prune_attempts()` finally has somewhere
  to run, and §12.5's health line — queue depth, fetch attempts, success rate,
  breakdown by outcome — is logged hourly. Nothing else in the system is awake
  often enough to bound a table that gains a row per request
- `MERIDIAN_WORKER_*` environment settings (id, concurrency, idle sleep, lease,
  topics, housekeeping interval, max tasks), validated at startup so a
  misconfigured worker fails loudly instead of quietly running one lane

### Notes

- The loop catches everything except cancellation. A lane that hits an
  unexpected exception logs it, hands the task back with a backoff and goes on;
  a database that has gone away backs the lane off along a capped schedule
  rather than ending the run. Cancellation is deliberately not caught — that is
  the shutdown path, and swallowing it turns `SIGTERM` into a process that has
  to be killed
- `attempt_number` is `task.attempts + 1`. The counter on the row is how many
  attempts have *finished*, so passing it straight through would file every
  retry in the attempt log as a first try — and telling a URL that failed once
  from one that has been failing all week is the single question that log exists
  to answer

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
