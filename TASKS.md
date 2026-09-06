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

**Current phase: 1** — phase 0 complete and pushed. Next: `P1-01`, the queue
claim/pop semantics. The three ⚑ human tasks below are still open and are worth
doing before much of phase 1, since seed quality propagates downstream.

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
- [ ] `P0-15` ⚑ human — held-out question set of 20–30 for monthly regression (spec §14.1)
- [ ] `P0-16` ⚑ human — hand-seed 15–25 cold-start sources into `config/seed_sources.yaml`
- [ ] `P0-17` ⚑ human — expand `config/gazetteer_seed.yaml` toward ~50 terms

## Phase 1 · Ingestion

*Checkpoint: runs 48h unattended without failing; the result becomes the dev corpus.*

- [ ] `P1-01` Queue claim/pop semantics: status flow, attempts, exponential backoff
- [ ] `P1-02` `fetch_policy` resolution: per-domain row → global row → file default
- [ ] `P1-03` `fetch.py` — httpx for static, Crawl4AI for JS-dependent, `render_js: auto`
- [ ] `P1-04` Robots handling, per-domain concurrency and delay, conditional requests
- [ ] `P1-05` Blocked-domain marking after N consecutive failures
- [ ] `P1-06` `prefilter.py` — domain blocklist + already-seen check before fetching
- [ ] `P1-20` **SSRF guard.** Resolve DNS and reject private, loopback, link-local and
      cloud-metadata addresses before connecting; re-validate on every redirect hop;
      scheme allowlist; reject when any resolved address is private (DNS rebinding).
      Crawl targets come from untrusted pages — this is the control that stops the
      crawler reaching the LAN
- [ ] `P1-21` Content safeguards: content-type allowlist, streaming abort at
      `max_page_bytes`, decompression-ratio cap, reject a plaintext final response
      unless the domain overrides `require_https_final`
- [ ] `P1-23` **Injection pre-screen, mechanical (no LLM).** At extraction time flag
      hidden text (`display:none`, `visibility:hidden`, white-on-white, 0px fonts,
      offscreen), imperative HTML comments, and instruction-like phrasing addressed
      to a model. Regex and DOM work only, so the fast loop stays model-free
- [ ] `P1-17` Tier-derived queue priority: resolve a URL's tier from
      `config/source_tiers.yaml` (patterns, then exact, then default) and set
      `queue.priority` from `priority_by_tier`, so search results self-sort
- [ ] `P1-18` Randomised per-domain delay — `delay_per_domain_ms` as a floor plus a
      random draw from `[0, delay_jitter_ms]`
- [ ] `P1-19` Record every fetch attempt in `fetch_attempts`, success or failure, and
      derive the health line's fetch success rate from it. Add a retention prune
- [ ] `P1-07` `extract/html.py` — Crawl4AI markdown, `PruningContentFilter`, citation extraction
- [ ] `P1-08` `extract/document.py` — MarkItDown, `convert_local`/`convert_stream` **only**
- [ ] `P1-09` `extract/pdf.py` — native-text detection (chars/page), page offsets preserved
- [ ] `P1-10` `extract/figures.py` — figure extraction with captions
- [ ] `P1-11` Raw store writer: path scheme, checksum, retention tiers
- [ ] `P1-12` Source tier assignment from `config/source_tiers.yaml` domain map
- [ ] `P1-13` `ocr_queue.py` — enqueue scanned PDFs, never OCR inline
- [ ] `P1-14` `resolve_doi.py` — Unpaywall → OpenAlex → CORE → preprint chain
- [ ] `P1-15` Worker main loop, supervision, graceful restart
- [ ] `P1-22` **Network topology.** `internal: true` blocks outbound, but worker,
      crawl4ai and searxng all need it — the compose file admits this in a comment
      and never resolves it. Split into `internal` (postgres, api, web) and `egress`
      (worker, crawl4ai, searxng, orchestrator, cloudflared), with worker on both.
      crawl4ai drives a browser against hostile content and must hold no credentials
      and have no route to postgres
- [ ] `P1-16` 48h unattended acceptance run → `make snapshot-corpus`

## Phase 2 · Embeddings and search — the go/no-go

*Checkpoint: is searching the corpus already useful with no model involved?*

- [ ] `P2-01` `embeddings.py` — bge-m3 wrapper, batching, arm64 sanity check
- [ ] `P2-02` Chunking with `page_or_offset` captured at extraction time
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
