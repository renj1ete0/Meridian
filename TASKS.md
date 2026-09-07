# Meridian — build tasks

**This file is the source of truth for what to build next.** Read it at the start of a
session, update it at the end of one. If work happened and this file didn't change,
something went wrong.

- Phases and their acceptance checkpoints: [docs/roadmap.md](docs/roadmap.md)
- Conventions and invariants before writing code: [AGENTS.md](AGENTS.md)

## How to use this file

- Task IDs are stable (`P1-04`). Reference them in commit messages and PR titles.
- Status: `[ ]` not started · `[~]` in progress · `[x]` done · `[-]` dropped (say why).
- Tick a task only when it runs, not when it compiles.
- A task should be one sitting of work. If it isn't, split it and give the parts new IDs.
- Tasks marked **⚑ human** need a judgment call and should not be delegated to an agent.
- Add new tasks freely; don't renumber existing ones.

**Current phase: 2 opening, phase 1 not yet closed.** The crawl runs unattended,
expands its own frontier, and reads HTML, PDFs and Office documents: `P1-01`–`P1-09`,
`P1-11`–`P1-13`, `P1-15`, `P1-17`–`P1-21`, `P1-23`, `P1-24`, `P1-30` and (pulled
forward) `P2-02` are done, at 964 tests. `P2-01` adds embeddings, so chunks now
carry vectors. Verified live — real government PDFs extracted with page-accurate chunks,
and the injection screen clean across every page crawled so far.

Phase 1's checkpoint (`P1-16`, the 48h run) is the gate on phase 2's go/no-go
(`P2-09`), because a search quality judgement over 17 sources and 28 chunks
measures nothing. The agreed sequence:

1. `P1-28` sitemaps — the cheapest frontier widener, and it decides what the
   long run is worth. `RobotsRules.sitemaps` is already parsed and unused
2. `P1-22` network topology, then `P1-26` Crawl4AI's image and health check —
   what stands between a worker image that runs and a *stack* that does
3. A short bounded run (`MERIDIAN_WORKER_MAX_TASKS`, not a timer) as a **stack**
   smoke test, deployed to the server rather than run from a checkout
4. `P2-03` novelty gate, then `P2-05`/`P2-04`/`P2-06` search, built against that
   real output. The novelty gate goes before the long run deliberately: nothing
   deletes from the raw store (`P1-31`), so an ungated 48h run keeps every
   near-duplicate it finds
5. `P1-16` the 48h run, then `P0-15` held-out questions — which must be written
   before `P2-06` is judged, not after — and `P2-09`, the call

`P1-10` (figures) and `P1-14` (DOI resolution) are the remaining phase-1
extraction work and neither blocks the checkpoint.

---

## Phase 0 · Foundations

*Checkpoint: `make migrate && make seed` yields a clean, empty database ready to crawl.*

- [x] `P0-01` Repo scaffold: layout, compose files, Makefile, `.env.example`, `.gitignore`
- [x] `P0-02` Brand and design system: mark, palette, typography, voice, UI mockups, assets
- [x] `P0-03` Licence, README, roadmap, task tracking, versioning policy
- [x] `P0-04` `meridian_core` package: `pyproject.toml`, `db.py` (engine, session, pooling)
- [x] `P0-05` SQLAlchemy models — queue
- [x] `P0-06` SQLAlchemy models — sources, chunks, figures
- [x] `P0-07` SQLAlchemy models — graph (entities, edges, attributes) with provenance columns
- [x] `P0-08` SQLAlchemy models — gazetteer, `topic_config`, `fetch_policy`, agent registry
- [x] `P0-09` SQLAlchemy models — `runs`, `steering_log`, `enrichment_queue`, `reports`
- [x] `P0-10` Pydantic DTOs in `meridian_core/schemas/` for every service boundary
- [x] `P0-19` Test suite: drift, rejection and completeness tests against real Postgres
- [x] `P0-20` Schema changes from the `P0-14` trace: `observations` table (with JSONB
      `qualifiers`), `edges.valid_from`/`valid_to`, `edges.similarity_dimension`/`disanalogy`
      with a CHECK enforcing §7.2
- [x] `P0-21` Fix: Alembic autogenerate does not detect CheckConstraints on existing
      tables, so two edge constraints existed only in the model. Hand-written into the
      migration; drift test added so it cannot recur
- [x] `P0-18` Fix role bootstrap: `.sql` → `.sh` (entrypoint has no psql var bindings),
      default privileges declared for both writers, pgvector enabled at init
- [x] `P0-11` Alembic setup + initial migration; run as `PG_MIGRATION_URL` (owner), not rw
- [x] `P0-12` `scripts/seed.py` — idempotent `config/*.yaml` → DB, config only, never content
- [x] `P0-13` Structured logging setup (`meridian_core/logging.py`), `run_id` on every record
- [x] `P0-14` ⚑ human — the ten questions, traced against the schema → `docs/design-questions.md`.
      Found five gaps; fixed in `P0-20`
- [-] `P0-15` ⚑ human — held-out question set, **deferred by decision**. Needed before
      the phase 2 go/no-go, not before phase 1: §14.1 uses it to measure whether the
      graph improves month to month, and there is nothing to measure until the corpus
      exists. Re-open when phase 2 starts
- [x] `P0-16` ⚑ human — cold-start seeds: 8 authority roots + 5 query seeds. Kept short
      deliberately; tier-upranking of search results does the discovery
- [x] `P0-17` ⚑ human — gazetteer at 64 terms, jurisdiction-scoped, with 20 ambiguous
      surface forms flagged so the resolver disambiguates from context rather than
      guessing

## Phase 1 · Ingestion

*Checkpoint: runs 48h unattended without failing; the result becomes the dev corpus.*

- [x] `P1-01` Queue claim/pop — `meridian_core/queueing.py`. FOR UPDATE SKIP LOCKED,
      a lease rather than a status flip, exponential backoff with full jitter
- [x] `P1-02` Policy resolution — `meridian_core/policy.py`. Per-domain → global → file,
      shallow merge, plus consecutive-failure blocking
- [x] `P1-03` `fetch.py` — httpx for static, Crawl4AI for JS-dependent, `render_js: auto`.
      `auto` short-circuits on any page that already has a paragraph of visible text,
      so the browser is reserved for genuine shells (§6.4 constraint 1)
- [x] `P1-24` **Pin the validated address (closes the SSRF TOCTOU gap).** The request
      goes to the IP literal `netguard` judged, with `Host` and TLS SNI set to the
      original hostname; `follow_redirects` is off at the client level, and each hop
      is re-validated, re-resolved and re-pinned by hand. Verified by a test that
      hands the resolver a different answer on its second call
- [x] `P1-04` Robots handling, per-domain concurrency and delay, conditional requests —
      `worker/robots.py`, `worker/ratelimit.py`, `worker/crawl.py`. robots.txt is
      parsed here rather than by `urllib.robotparser`, which only became RFC 9309
      compliant in Python 3.13 and gives opposite answers on 3.12 for both
      `Disallow: /*.pdf$` and a longer `Allow` — §14.2 makes this a commitment, and
      one that varies by interpreter is not one. Conditional requests needed
      `sources.etag`/`last_modified`
- [x] `P1-05` Blocked-domain marking after N consecutive failures. `domain_signal()`
      decides what each outcome is evidence *of*, which is the part that had to be
      right: three answers, not two — the domain answered (a 404 or a rejected media
      type resets the counter), the domain is unreachable (timeout, 5xx, 429,
      redirect loop, decompression bomb, refused address), or no request went out
      (robots denial, already-blocked domain, so no evidence either way). A newly
      blocked domain is dropped from the limiter
- [x] `P1-06` `prefilter.py` — four gates, cheapest first: normalise, shape
      (scheme/host/extension), blocklists, then one batched query each against
      `queue` and `sources`. Normalisation is conservative — fragment, tracking
      params, credentials and default ports go, trailing slashes and query order
      stay, because anything that changes *which* resource is requested trades a
      visible duplicate for an invisible missing page. Wired into the loop as
      frontier expansion, which is what turns the fetcher into a crawler, and it
      finally gives `priority_for_domain()` (`P1-17`) a caller
- [x] `P1-20` **SSRF guard** — `meridian_core/netguard.py`. Post-DNS address
      classification, per-hop redirect revalidation, scheme allowlist, DNS-rebinding
      rejection, integer-encoded host normalisation, https-final enforcement
- [x] `P1-21` Content safeguards: content-type allowlist checked on the headers,
      streaming abort at `max_page_bytes`, decompression-ratio cap, plaintext final
      response refused unless the domain overrides `require_https_final`. The
      decompression is driven by hand through `zlib` in bounded steps — letting
      httpx decode meant a 64KB read arrived as one 67MB object, so the cap was
      checked after the allocation it existed to prevent
- [x] `P1-23` **Injection pre-screen, mechanical (no LLM)** —
      `worker/extract/injection.py`. Hidden text (seven techniques, each named in
      the finding), imperative HTML comments, and instruction-like phrasing, on
      the raw HTML *and* the extracted text. The design turns on one distinction:
      **hidden is the signal, imperative is not** — an article *about* prompt
      injection quotes the phrases and must not be flagged, or the flag becomes
      one people ignore. Flags only; §2.5 keeps the page stored, extracted and
      chunked, and `P4-06` is what eventually quarantines. 0 false positives
      across the 11 real pages crawled so far
- [x] `P1-17` Tier-derived queue priority — `priority_for_domain()`. Wiring it into
      enqueue happens with the fetcher in `P1-03`
- [x] `P1-18` Randomised per-domain delay — `jittered_delay_ms()`. Wiring it into the
      fetch loop happened with `P1-04` — `Crawler` draws a fresh jittered delay per
      request and takes the larger of it and any `Crawl-delay` from robots.txt
- [x] `P1-19` Record every fetch attempt in `fetch_attempts`, success or failure, and
      derive the health line's fetch success rate from it. Add a retention prune.
      `meridian_core/attempts.py`: `record_attempt()`, `fetch_health()` (rate plus a
      breakdown by outcome — the rate says something is wrong and only the breakdown
      says what) and `prune_attempts()`. Written by `Crawler.fetch` itself rather
      than by its callers, in the same transaction as the policy consequence
- [x] `P1-07` `extract/html.py` — two inputs, not one. Crawl4AI's `fit_markdown`
      where the browser ran, `trafilatura` (`favor_precision`) for everything
      that took the static path, which is most of the corpus. Fills the §5.2
      bibliographic columns, never guessing — a partial date is discarded rather
      than completed. Citations are mechanical (DOI, arXiv both schemes, PMID,
      handle) and read from the text *and* the links; the page's own identifiers
      are kept out of them, with arXiv's DOI derived from its URL because it
      publishes no `citation_doi` tag
- [x] `P1-08` `extract/document.py` — MarkItDown, `convert_stream` on fetched
      bytes only. The load-bearing part turned out to be an **explicit converter
      allowlist**: MarkItDown sniffs bytes with magika and ignores the declared
      media type, and its default registry fetches URLs, shells out to
      `exiftool`, and re-dispatches zip members — which a `.docx` reaches by
      being a zip. `enable_builtins=False` plus four registered converters.
      OOXML `core.xml` supplies the metadata MarkItDown does not return
- [x] `P1-09` `extract/pdf.py` — `pdftotext` over stdin, page boundaries from the
      form feed poppler already writes (exact, not reconstructed). Scan detection
      at ~100 chars/page is §6.6's fork: below it the document is queued for OCR
      and stays metadata-only, and its stray text layer is dropped rather than
      admitted as content. A missing poppler raises loudly — a worker that had
      quietly lost it would store every PDF and extract none of them. Title and
      creation date come from `pdfinfo`
- [ ] `P1-10` `extract/figures.py` — figure extraction with captions
- [x] `P1-11` Raw store writer — `worker/rawstore.py` plus
      `meridian_core/sources.py`. Path is `<domain>/<hex shard>/<sha256(url)><ext>`,
      derived from the URL so a re-fetch overwrites rather than accumulating, and
      refused outright for a host that cannot safely be a directory name. Writes
      are atomic (temp file in the destination directory, fsync, `os.replace`).
      Retention actually splits per §5.4 — primary keeps the file, background
      keeps the checksum and metadata only. `upsert_source()` stores the
      validators that make `conditional_requests` real for the first time, and
      returns whether the checksum changed. A fetch the store could not keep is
      retried, never advanced
- [x] `P1-12` Source tier assignment — `meridian_core/tiering.py`, exact → longest
      pattern → default, seeded into the DB with the global fetch policy
- [x] `P1-13` `ocr_queue.py` — a scan becomes a source record plus one
      `enrichment_queue` row, idempotent so a re-crawl does not inflate the
      pending count an operator makes a spending decision from. `ocr_applied`
      and `ocr_tier` written explicitly so a skipped document is findable rather
      than inferred from an absence (§6.6)
- [ ] `P1-14` `resolve_doi.py` — Unpaywall → OpenAlex → CORE → preprint chain
- [x] `P1-15` Worker main loop, supervision, graceful restart —
      `services/worker/worker/main.py`. N claim-fetch-settle lanes over one
      shared `Crawler`; the database is the queue and `SKIP LOCKED` is the
      dispatcher, so the loop needs no scheduler of its own. `queue_disposition()`
      decides whether an outcome is a failure to retry or a refusal to abandon —
      a different question from `domain_signal()`'s, and they disagree in both
      directions. Everything is caught except cancellation, which is the
      shutdown path. `SIGTERM` finishes the fetches in flight and hands the
      leases back; a second signal cancels. Housekeeping gives `prune_attempts()`
      somewhere to run and logs §12.5's health line
- [ ] `P1-25` **Egress restriction — the defence that survives an application bug.**
      Everything in `netguard` is one mistake from failing open. Give the fetching
      process no route to RFC1918 at all: a network namespace without a LAN route, or
      an egress proxy that refuses private destinations
- [ ] `P1-22` **Network topology.** `internal: true` blocks outbound, but worker,
      crawl4ai and searxng all need it — the compose file admits this in a comment
      and never resolves it. Split into `internal` (postgres, api, web) and `egress`
      (worker, crawl4ai, searxng, orchestrator, cloudflared), with worker on both.
      crawl4ai drives a browser against hostile content and must hold no credentials
      and have no route to postgres
- [ ] `P1-16` 48h unattended acceptance run → `make snapshot-corpus`
- [ ] `P1-26` **Crawl4AI needs a Dockerfile and a health check the worker trusts.**
      `Crawl4aiClient.from_env()` returns None when `CRAWL4AI_URL` is unset and the
      fetcher degrades to static — correct, but silent. A worker that has quietly
      lost its browser for a week should say so on the health line (§12.5), not just
      extract worse
- [ ] `P1-28` **Sitemap discovery from robots.txt.** `RobotsRules.sitemaps` is parsed
      and returned already — Wikipedia's lists one, and §6.4 names `AsyncUrlSeeder` for
      sitemap-based discovery — but nothing enqueues them. Cheap frontier expansion
      that needs no model
- [ ] `P1-29` **Persist the robots cache across restarts.** It is in-process, so a
      worker restart re-fetches robots.txt for every origin it touches. Harmless at
      current scale and wasteful at corpus scale; revisit when the crawl is wide
      rather than deep
- [ ] `P1-32` **Replacing chunks orphans the edges that cite them.**
      `edges.supporting_chunk_ids` is an array of ids with no foreign key behind
      it, and `replace_chunks()` deletes the old set when a page's content
      changes. Nothing is orphaned today because no edges exist, and the fix is
      not obvious — superseding rather than deleting, a `superseded_by` column,
      or re-deriving affected edges — so it needs the graph to exist first. Do
      not let the first real edges land before this is decided
- [ ] `P1-31` **Nothing ever deletes from the raw store.** `P1-11` decides what
      gets written; §5.4 also says junk and near-duplicates are dropped *after*
      the novelty gate, and background sources get a snapshot only if cited.
      Both are deletions, and there is no sweep. Needs the novelty gate (`P2-*`)
      to exist first, so this is a phase-2 follow-up — but the disk fills at
      phase-1 speed, so watch `du` before then
- [x] `P1-30` **Supervision the loop deliberately does not provide.**
      `services/worker/Dockerfile` (multi-stage uv build on `python:3.12-slim`,
      `poppler-utils` for `P1-09`, unprivileged, runs read-only with `cap_drop:
      ALL`) plus `deploy/meridian.service`. The scaffold is specific that
      compose's `restart: unless-stopped` **or** systemd supervises, not both —
      two supervisors racing to restart one container is how a crash loop goes
      invisible — so the unit is `oneshot` and owns only the stack. Verified by
      running the image against the real database: fetched, stored, extracted,
      chunked and expanded the frontier, read-only, as an unprivileged user
- [ ] `P1-27` **Per-domain `render_js` learning.** `auto` re-fetches a shell through
      the browser every time it sees one, so a JS-only domain pays two requests per
      page forever. Record the escalation on `fetch_policy` after N confirmations and
      go straight to the browser. Cheap, and only worth doing once real crawl data
      shows which domains actually do this

## Phase 2 · Embeddings and search — the go/no-go

*Checkpoint: is searching the corpus already useful with no model involved?*

- [x] `P2-01` `embeddings.py` — bge-m3 through sentence-transformers, lazy-loaded
      and dimension-checked at load rather than at insert. Runs as a **separate
      backfill pass** (`python -m worker.embed`) over `embedding IS NULL` rather
      than inside the fetch loop: the crawler never carries a 2.3GB model, and
      the queue is a predicate so the pass is resumable with no state outside
      the table. `FakeEmbedder` gives `P2-03` and `P2-06` something to build
      against without the download. `sentence-transformers` is an optional
      extra, so the worker image stays lean
- [x] `P2-02` Chunking with `page_or_offset` captured at extraction time —
      **built in phase 1** (`v0.15.0`), because `P1-07` was discarding the text
      it extracted and a `background` source keeps no raw file to re-derive from.
      `worker/extract/chunk.py` cuts on structure (paragraphs, then sentences,
      then a hard cap) and every chunk is a verbatim slice: `text[offset:offset +
      len(chunk)] == chunk`. `meridian_core/chunks.py` writes them in the same
      transaction as the source row. Unchanged content is left alone; changed
      content is replaced, and the new ids are what make §6.3's high-water mark
      re-read the page
- [ ] `P2-03` `novelty.py` — cosine gate, drop above 0.95
- [ ] `P2-04` pgvector HNSW index; measure recall and latency at corpus size
- [ ] `P2-05` `tsvector` index and trigger
- [ ] `P2-06` `search.py` — hybrid retrieval, RRF fusion, **filters before vector search**
- [ ] `P2-07` `/api/explore/*` read endpoints on the read-only session
- [ ] `P2-08` Minimal Explore UI: search box, results, source tier and date visible
- [ ] `P2-09` ⚑ human — run the held-out questions; make the go/no-go call
- [x] `P2-10` ⚑ human — frontend framework chosen: **React + TypeScript + Tailwind CSS**,
      with Sigma.js v3 + graphology for the canvas
- [ ] `P2-11` Frontend scaffold: Vite + React + TypeScript + Tailwind, `web/` structure
      per scaffold §2, dev proxy `/api` → `localhost:8000` so no environment-specific
      base URL exists
- [ ] `P2-12` Design tokens in code — the two palettes, type scale, and surface rules
      from `docs/design/design-system.md` as CSS custom properties, wired into the
      Tailwind theme. Tokens must stay the single source: no raw hex in className,
      or the design system and the app drift apart immediately
- [ ] `P2-13` `web/src/lib/api.ts` — typed client over `/api/explore/*`, with the
      request/response types kept in step with the pydantic DTOs

## Phase 3 · MCP read surface

*Checkpoint: an external agent can retrieve usefully.*

- [ ] `P3-01` MCP server scaffold inside `api`
- [ ] `P3-02` Read tools: `search_chunks`, `get_source_metadata`, `list_new_since`
- [ ] `P3-03` Scoped tokens: `allowed_tools`, rate limit, expiry (spec §11.4)
- [ ] `P3-04` `run_readonly_query` behind the read-only role, statement timeout, row cap
- [ ] `P3-05` Cloudflare Tunnel + Access in front of the API

## Phase 4 · Graph and writes — the loop closes

*Checkpoint: a model can write validated, provenance-bearing edges.*

- [ ] `P4-01` Apache AGE setup, graph schema, typed node ontology
- [ ] `P4-02` Entity resolution: normalise → block → score → three-band decision
- [ ] `P4-03` Merge reversibility: redirects, `merged_from`, merge log
- [ ] `P4-04` Write tools: `add_edge`, `tag_entity`, `enqueue_seed`, `advance_mark`
- [ ] `P4-05` `validation.py` — server-side guards, node existence, domain allowlist, caps
- [ ] `P4-12` Allowlist growth: `fetch_policy.seed_allowed` + `first_seen_via`. Domains
      reached by frontier expansion auto-approve after N successful novel fetches;
      model-proposed domains queue for approval like gazetteer terms
- [ ] `P4-13` Refuse to start a synthesis run with no budget configured. §16 says caps
      must exist before the first autonomous run, and nothing currently enforces the
      ordering — the compounding seed→crawl→cost loop is first noticed as a bill
- [ ] `P4-06` Untrusted-data framing for all retrieved content in prompts (spec §11.8)
- [ ] `P4-14` **Quarantine and screening for unknown domains.** `sources.trust_state`
      (unscreened | cleared | quarantined | rejected) plus a domain-level verdict
      cached on `fetch_policy`, so screening is paid once per domain, not per page.
      A domain that is tier-mapped or has N clean fetches is cleared automatically;
      an unknown domain that trips `P1-23` is quarantined and queued for a frontier
      model to judge. Quarantined content is still stored — never deleted — but is
      excluded from the chunk set the slow loop pulls until cleared
- [ ] `P4-07` Agent registry, task-type routing, fallback chains
- [ ] `P4-08` Orchestrator run state machine + `runs` table resumability
- [ ] `P4-09` `--once` and `--dry-run` modes (print tool calls, apply nothing)
- [ ] `P4-10` `budget.py` — per-run token and seed caps, cost logging, monthly ceiling
- [ ] `P4-11` High-water mark advances only after writes commit

## Phase 5 · Autonomy

*Checkpoint: it runs itself, and tells you when it can't.*

- [ ] `P5-01` `frontier.py` — outbound links, citations, spaCy NER, TF-IDF co-occurrence
- [ ] `P5-02` Gazetteer into `EntityRuler` at worker startup; acronym auto-harvest
- [ ] `P5-03` Coverage scoring, schema-aware, topic × dimension
- [ ] `P5-04` Gap analysis and seed emission, capped and validated
- [ ] `P5-05` Diversity seeding — stance-imbalance counter-seeds first (spec §7.4)
- [ ] `P5-06` Scheduler reads its timetable from the DB — no cron files
- [ ] `P5-07` Telegram digest, alerts on sustained conditions only, inbound commands
- [ ] `P5-08` Health endpoint, watchdog, off-device snapshot job

## Phase 6 · Interface — the payoff layer

*Checkpoint: reading the graph is genuinely better than reading the sources.*

- [ ] `P6-01` Sigma.js canvas, focus + expand, depth-1 neighbours capped and ranked
- [ ] `P6-02` Canvas filters: topic, attribute, source tier, date, contested-only
- [ ] `P6-03` Path mode between two nodes
- [ ] `P6-04` Node detail panel: grouped tags with overflow, attribute list with confidence
- [ ] `P6-05` Annotation as first-class nodes
- [ ] `P6-06` Synthesis panel: collapsible toggle, thread, node chips, inline citations
- [ ] `P6-07` Conversation history within the synthesis panel
- [ ] `P6-08` Notifications panel, filterable by type
- [ ] `P6-09` Saved views
- [ ] `P6-10` Coverage grid and contested list as entry points
- [ ] `P6-11` Explore landing state with since-last-visit delta
- [ ] `P6-12` Admin: topic management — add, pause, archive with re-normalising weights
- [ ] `P6-13` Admin: agent registry, run history, fetch policy per domain, gazetteer approvals
- [ ] `P6-14` Figures panel with page-accurate raw file links
- [ ] `P6-15` Export: Markdown and BibTeX
- [ ] `P6-16` Shared UI primitives from the design system: the 17-icon set, source-tier
      and contested (dagger) badges, node chips, the top-right status/notification cluster
- [ ] `P6-17` Theme switching, honouring the system preference by default

## Phase 7 · Full design

*Checkpoint: the analytical layer the whole thing was for.*

- [ ] `P7-01` Attribute proposal gate: evidence requirement, discrimination test, hard cap
- [ ] `P7-02` Monthly attribute audit with hysteresis before retirement
- [ ] `P7-03` Schema evolution backfill, amortised across runs
- [ ] `P7-04` Analogical expansion with disanalogies recorded on every comparison edge
- [ ] `P7-05` Contradiction tracking and contested-pair marking
- [ ] `P7-06` Temporal decay flags in gap analysis
- [ ] `P7-07` Enrichment queue: figure VLM, quality-tier OCR, chart OCR — user-triggered
- [ ] `P7-08` Reprocessing, downgrade guard, old-vs-new disagreement logging
- [ ] `P7-09` Report generation with coverage pre-flight (spec §11.13)
- [ ] `P7-10` Edge precision sampling and entity resolution audit as routine

---

## Backlog — unscheduled

Things worth doing that don't belong to a phase yet.

- [ ] `B-10` `figures.linked_entity_ids` is `json`, not `jsonb`, and is the only
      column in the schema that stores a list of IDs as JSON at all — `entities.
      merged_from` is exactly the same shape and uses `ARRAY(BigInteger)`. `json`
      keeps the literal text, so it cannot be indexed and preserves whitespace and
      key order for nothing. Change it to `ARRAY(BigInteger)` with a migration while
      the table is still empty; add a drift test asserting no ID-list column uses
      `json`
- [ ] `B-01` Qdrant migration path, if pgvector recall becomes the measured bottleneck
- [ ] `B-02` App-level auth and roles, when Cloudflare Access stops being sufficient
- [ ] `B-03` Multimodal embeddings for figure similarity search
- [ ] `B-04` Offline corpora (OSM extract, filtered arXiv) — selective, storage-hungry

### Running it locally

For someone who wants to *run* Meridian rather than develop it. Today's quickstart
assumes `uv`, `npm`, and three terminals; this is the path that doesn't.

- [ ] `B-05` `docker-compose.local.yml` — the full stack building from source rather
      than pulling the private GHCR images, so a fresh clone needs no registry access
- [ ] `B-06` `make quickstart` — one command: bring up infra, wait for health, migrate,
      seed, start every service. Ends by printing the URL
- [ ] `B-07` First-run experience — pick topics and confirm cold-start sources from the
      UI instead of hand-editing `config/*.yaml` before the first crawl
- [ ] `B-08` Preflight check script — verify Docker version, available memory and disk
      against the stated minimums, and fail with a readable message rather than a
      container crash loop
- [ ] `B-09` Decide what a first run should *show*. Production starts empty by design
      (scaffold §1.7), so a fresh install has nothing to look at until it has crawled
      for a while. Options: ship a small real crawl snapshot as an opt-in demo corpus,
      or design an empty state that makes the first hour legible. Not synthetic
      fixtures either way — they don't resemble real extraction output
