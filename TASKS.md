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

**`v0.98.0`. Phases 0–3 are built; phase 1's checkpoint is not.** 2675 backend tests
against a real Postgres, 317 frontend.

The crawl runs unattended and widens its own frontier through four channels — links,
sitemaps, search and citations. The corpus is **searchable**: hybrid retrieval over
pgvector and `tsvector` fused by reciprocal rank, behind an HTTP API, a web interface
and an MCP surface. Admin steers topics, per-domain fetch policy and the gazetteer.

**Three things gate almost everything that is left.**

1. **`P1-16`, the 48-hour run.** It has not happened, and it is what `P2-09` — the
   go/no-go on whether searching this corpus is useful with no model involved — has to
   be judged against. Judging search quality over a corpus this small measures nothing.
   `P0-15`'s held-out question set must be written *before* that judgement, not after.
2. **A Cloudflare account**, for `P3-05` and the rest of `P3-09`. The code side is done
   and tested; what is missing is a tunnel, an Access application and an AUD tag.
3. **The graph.** Phase 4 is designed and entirely unbuilt — nothing has ever written an
   edge — so `P4-*`, most of `P5-03`–`P5-05`, `P6-01`–`P6-03`, `P6-06`, `P6-07`,
   `P6-10` and all of phase 7 wait on it. Apache AGE supports PG17 (v1.6.0), so
   `P4-01` is not blocked on a Postgres downgrade; only on not swapping the image
   mid-deploy.

**Buildable today** is now a short list, because a session went through it.
`P4-10`, `P4-13`, `P4-14`, `P4-12`, `P3-06`, `P3-10`, `P3-11`, `B-07`, `B-09`,
`B-11`, `P2-20` and `P5-07` are done; `P5-01` and `P6-23` are open on their own
arguments, both written into their entries — one has no consumer for its
output, the other would be designed against an empty table. What is left needs
one of the three gates.

Formerly buildable, for the record: `P6-23` (agent registry and run history,
better after there is a run to show) and `P1-35` (a Semantic Scholar key, ten minutes,
felt during the 48h run). `P6-05` and `P5-07`'s inbound half are done — an annotation is
a node somebody writes by hand, so it is the one graph-shaped feature that never needed
the graph, and the bot's commands needed the steering that `P5-06` had since shipped.

**Explicitly parked, with reasons.** `P2-15` is gated by its own text on `P1-16` *and*
on `P2-09` being marginal, and means a second embedding column plus a full re-embed for
a model you may never adopt. `P6-18` and `P6-19` are ⚑ human: published-design decisions
that an agent would be inventing rather than implementing.

**What was verified live** — real government PDFs extracted with page-accurate chunks,
the injection screen clean across every page crawled, `render_js: auto` escalating and
not escalating on real sites, `Crawl-delay` honoured. And, as of `v0.78.1`, the stack
running whole in containers: the crawl storing what it fetches, the embedding sidecar
serving, and **hybrid search end to end outside a test** — which had never happened.
Four defects had to be fixed to get there, every one of them invisible to the suite and
present in the production compose file too (`B-12`, `B-13`, `B-14`, `B-16`), plus a
deploy runbook whose first two commands could not work (`B-17`).
**What is still unverified**: anything needing the server, Cloudflare or scale.
`docs/handover.md` §4 carries that list, and it is the checklist for the first deploy.

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
- [ ] `P0-15` ⚑ human — held-out question set. Deferred through phase 1 by
      decision — §14.1 uses it to measure whether the graph improves month to
      month, and there was nothing to measure until a corpus existed — with
      "re-open when phase 2 starts" as the condition. **Phase 2 has started, so
      it is re-opened here.** Write it before `P2-06` is judged, not after: a
      question set written once results are visible is a description of those
      results, and `P2-09` is then a go/no-go against a target drawn around the
      shot
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
- [x] `P1-10` `extract/figures.py` — figure extraction with captions, `v0.51.0`.
      §6.6's "start with captions, not vision": captions and alt text at
      ingestion, no image bytes, no bbox, no model. HTML has semantics to read
      (`<figure>`, `alt`); a PDF has only the convention that a caption line
      begins "Figure 3:", and the page is exact while the position on it is
      unknown — so no bbox is invented. Adds `figures.image_url`, because
      `file_path` is local and nothing downloads images, so a row would
      otherwise describe a picture nobody could look at. Vision is `P7-07`, the
      panel is `P6-14`

- [~] `P1-25` **Egress restriction** — host-level control shipped in `v0.39.0`,
      the proxy option still open. `deploy/egress-restrict.nft` gives the
      fetching process no route to RFC1918: not in the application, not in the
      container, and not reachable from either. Compose subnets are pinned so
      the rules have a stable target — Docker reallocates them otherwise, and a
      rule against a stale subnet matches nothing, protects nothing and looks
      exactly like one that works. `tests/unit/test_compose_topology.py` fails
      if the pinning is removed or a network moves outside the supernet the
      rules cover, and `docs/deployment.md` §4b has the four verification
      commands that have to behave as stated.
      **Stays `[~]`**: the task names an egress proxy as the alternative, and it
      is not a drop-in one. A forward proxy resolves the hostname itself, taking
      DNS away from the worker and undoing `P1-24`'s address pinning — adopting
      it means deciding the proxy's destination ACL *replaces* pinning, which is
      a design decision rather than a deployment one. ⚑ human decides that;
      until then the host rules are the defence and they are applied, not
      merely written
- [x] `P1-22` **Network topology.** `internal: true` blocks outbound, but worker,
      crawl4ai and searxng all need it — the compose file admits this in a comment
      and never resolves it. Split into `internal` (postgres, api, web) and `egress`
      (worker, crawl4ai, searxng, orchestrator, cloudflared), with worker on both.
      crawl4ai drives a browser against hostile content and must hold no credentials
      and have no route to postgres
- [ ] `P1-16` 48h unattended acceptance run → `make snapshot-corpus`
- [x] `P1-26` **Crawl4AI needs a Dockerfile and a health check the worker trusts.**
      `Crawl4aiClient.from_env()` returns None when `CRAWL4AI_URL` is unset and the
      fetcher degrades to static — correct, but silent. A worker that has quietly
      lost its browser for a week should say so on the health line (§12.5), not just
      extract worse
- [x] `P1-28` **Sitemap discovery from robots.txt.** (Enqueueing was broken until
      `v0.26.0` — `seed_source="sitemap"` was never added to the enum, so every
      sitemap parsed and then raised at the insert. Fetch, parse and settle all
      succeeded, which is why nothing noticed.) `worker/sitemaps.py` parses
      urlsets and indexes; the loop grows a `sitemap` handler so the rows are
      claimed rather than orphaned. Two independent defences against XML entity
      expansion, because lxml expands by default — measured, not assumed. A
      sitemap may not name another site, since a hostile robots.txt would
      otherwise write to the frontier at its target's tier priority. Verified
      against the real seed list: most advertise one, the largest runs to
      several thousand URLs, and all are served as `text/xml` — which is *not* in
      `allowed_content_types`, so `policy_overrides` is what makes the feature
      work at all. Paired with `worker/topicmatch.py`: a sitemap URL gets the
      topic its path implies, not the one the triggering page happened to carry,
      and an unmatched URL is deprioritised to -10 rather than dropped
- [x] `P1-29` **Persist the robots cache across restarts** — `v0.66.0`. A
      `robots_cache` table holding the *raw file*, re-parsed on load, so a parser
      fix reaches everything already cached. Two layers on two clocks:
      `time.monotonic()` in memory, where NTP cannot move it, and wall clock in
      the row, because a stored monotonic deadline would be compared against a
      different clock after exactly the restart it exists to survive. `missing`
      and `unreachable` stay distinct — both store no body and mean opposite
      things, and collapsing them would turn every origin that was down at
      restart into one that granted permission. Plus a per-origin lock, since a
      lane claims many URLs from one domain at once and every one of them used to
      miss the empty cache
- [x] `P1-32` **Replacing chunks orphans the edges that cite them** —
      `v0.67.0`. Decided: **supersede, never delete.** Re-deriving affected edges
      needs the slow loop and produces a different edge anyway, so the old one
      would have to be invalidated regardless; a foreign key is impossible,
      since Postgres cannot enforce one on array elements. Stamping
      `superseded_at` keeps every citation resolvable, keeps the text an edge was
      actually derived from (§2.4 re-derives from source chunks, and the page has
      changed), and leaves reclamation to the sweep — a decision a person makes
      rather than one a crawl makes at write time. The unique constraint on
      `(source_id, chunk_index)` became partial over the live set, or the
      replacement it exists to allow would be refused at write time. Every query
      that serves the corpus filters on it, the novelty gate included: without
      that, a changed page's new chunks are all marked duplicates of the
      generation they replaced
- [x] `P1-31` **The retention sweep exists** (`v0.35.0`).
      `meridian_core/retention.py` plus `python -m worker.sweep`. Measuring the
      real corpus before writing it changed what it is: there was nothing to
      reclaim and no orphans, and **three sources whose `raw_file_path` pointed
      at nothing**. The first is structural rather than lucky — `P1-11` never
      writes the files §5.4 says to drop, and `retention_for` only moves a tier
      *up*, so a file that exists was written under a tier that keeps files and
      cannot have fallen below it. The sweep says that out loud rather than
      reporting as though it did work. Three verdicts and only one deletes:
      `droppable` and `orphaned` go with `--apply`, `dangling` is reported and
      never touched — the row is the only record the fetch happened and its text
      is still in the corpus. Primary is refused when the plan is built and
      again when it is applied. Dry run is the default because a re-crawl
      returns today's web, not the page that was fetched
- [x] `P1-45` **`sources.raw_root`** — `v0.38.0`. Provenance, not a lookup:
      `raw_file_path` stays relative and `MERIDIAN_RAW_ROOT` still resolves it,
      because an absolute path would bake in a container's mount point. The
      sweep now separates `elsewhere` from `dangling`, and `make
      snapshot-corpus` warns when sources reference a store it is not
      archiving — it tars one root, so a multi-root snapshot was silently
      incomplete. Not backfilled: rows written before this do not record their
      root, and guessing would turn "unknown" into a confident wrong answer for
      exactly the rows the column explains
- [ ] `P1-35` **Get a Semantic Scholar API key, or accept the retries.** The
      anonymous quota throttles hard and the penalty outlasts the burst by
      minutes, so under a real crawl a share of `doi` rows will retry rather
      than resolve on the first pass. Correct behaviour — nothing is lost — but
      it spends queue slots. `SEMANTIC_SCHOLAR_API_KEY` is free to request and
      is read already; this is a registration, not code. Measure the retry rate
      during `P1-16` before deciding it matters. ⚑ human
- [x] `P1-43` **The browser path did no boilerplate removal of its own** —
      fixed in `v0.31.0`. It took `fit_markdown` as-is on the reasoning that
      `PruningContentFilter` had seen a rendered DOM this process never had,
      and that premise was simply wrong: the rendered HTML comes back in the
      same response and is already what the extractor receives. So whether a
      page kept its navigation depended on whether the fetcher escalated it to
      a browser — a decision made on how much text the *static* fetch found,
      which is unrelated to how much boilerplate the page carries. Now
      trafilatura extracts from the rendered HTML at the same precision as
      everywhere else, the payload contributes metadata and JS-inserted links,
      and `fit_markdown` is the above-floor fallback for pages with no semantic
      structure to detect. Superseded text: ~~
      `extract/html.py` has two inputs and treats them very differently. The
      static path runs `trafilatura` configured to favour precision — it would
      rather lose a sentence of body than gain a navigation menu. The browser
      path uses Crawl4AI's `fit_markdown` as-is, and `PruningContentFilter` is
      a far more permissive filter than that. The asymmetry is visible in the
      dev corpus: chunks that are repeated station lists, an app promo banner,
      and a footer link block, and their markdown link syntax is what identifies
      which path produced them. This matters more than it looks — boilerplate
      becomes entities, entities become edges, and it also inflates the novelty
      gate's duplicate count with text that was never content. Either run
      trafilatura over the rendered HTML too, or tighten the filter Crawl4AI is
      asked for~~
- [x] `P1-44` **A source now records which extractor produced its text**
      (`v0.31.0`). `sources.extractor`, written at keep time, plain Text rather
      than `constrained()` — the names grow whenever an extractor or a failure
      mode is added, and a CHECK would recreate `P1-28` exactly. Nullable with
      no backfill: rows extracted before the column existed get NULL, which is
      the truth. Superseded text: ~~
      `extra->>'extractor'` is NULL on every source in the dev corpus, so
      answering "did this come through the browser or the static path" means
      inferring it from whether the text contains markdown link syntax. That is
      how `P1-43` was found, and it should not have needed detective work:
      `ExtractedDocument` already carries `extractor`, and it is dropped at
      `upsert_source`. One column, written at keep time~~
- [x] `P1-36` **`make snapshot-corpus` now calls a script that exists**
      (`v0.34.0`). Snapshot and restore, with the database and the raw store
      travelling together — a dump without the files its `raw_file_path` values
      point at is a catalogue, not a corpus. `pg_dump` runs inside the container
      so client and server versions cannot mismatch. The restore verifies
      checksums before touching anything, refuses a non-empty target unless
      `--replace` and then asks for the source count to be typed back, and
      afterwards samples `raw_file_path` to prove the two halves match.
      `tests/unit/test_scripts.py` stops the whole class recurring
- [x] `P1-37` **`make backup` and `make build-push` both work** — `v0.76.1`.
      `scripts/backup.sh` shipped in `v0.42.0` — unattended, asks nothing, fails
      loudly, warns when the backup root shares a filesystem with the data root
      (a backup on the disk it protects survives an accidental delete and
      nothing else), checks the dump is non-empty because `pipefail` does not
      reach across a redirect, and rotates only after the new one is written.
      `build_and_push.sh` landed in `v0.76.1`: one multi-arch manifest per
      application image, tagged by commit SHA. It **refuses a dirty working
      tree** and never tags `latest`, because both would undo the thing the SHA
      tag is for — scaffold §5 pins the SHA in compose so a bad build does not
      roll out on restart and rollback is a one-line edit, and a tag naming a
      commit whose code is not what was built is discovered to be wrong while
      rolling back. `orchestrator` has no Dockerfile yet (`web` gained one in
      `B-05`) and is skipped *loudly*; a drift test checks the image list against the services
      compose actually builds, since one added there and not here never gets
      built for arm64 and fails on the Pi days later
- [x] `P1-27` **Per-domain `render_js` learning** — `v0.70.0`. Built before
      `P1-16` rather than after, because the mechanism is self-tuning: the 48h
      run both benefits from it and produces its evidence, where waiting means
      paying double for two days first. Consecutive escalations, reset by a
      single static success; only ever upgrades `auto` to `always`, so an
      operator's `never` stands; and the conclusion **expires** after a week —
      without that, a domain skipping the static fetch produces no evidence about
      itself, so the first correct conclusion becomes permanent and a redesign is
      invisible

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
- [x] `P2-03` `novelty.py` — cosine gate, drop above 0.95. Built as a **mark, not
      a delete**: `meridian_core/novelty.py` records `novelty_checked_at`,
      `nearest_similarity` and `duplicate_of` on the chunk, and `P1-31`'s sweep
      is what spends the verdict. A gate that deleted could report no pass rate
      (§12.5), could not be re-tuned against the corpus it collected, and would
      leave nothing to audit. A chunk is only compared against chunks written
      *before* it — otherwise two identical chunks are each other's nearest
      neighbour, both clear the threshold, and the text is lost rather than
      deduplicated. `worker/novelty.py` is the pass
      (`python -m worker.novelty`): its own process, because the gate is
      Postgres and arithmetic and needs no model at all. Source-level demotion
      follows §5.4 — `background` → `junk` at ≥90% duplicate chunks, and
      **never** `primary`. Verified against a real crawled corpus: it found the
      boilerplate a site repeats under every URL, at similarity 1.0, and
      demoted nothing
- [~] `P2-04` pgvector HNSW index; measure recall and latency at corpus size —
      **index built in v0.30.0, measurement outstanding.** `vector_cosine_ops`,
      matching the operator everything here already uses; an index built for
      another operator class is not slower, it is unused, and the planner
      declines it silently. Built before the long run rather than after because
      maintained incrementally it costs nothing per insert, where building one
      over a finished corpus is a single operation wanting more
      `maintenance_work_mem` than the target has. `m`/`ef_construction` left at
      defaults — tuning them is a measurement against a real corpus, and
      re-tuning later is a REINDEX rather than a migration. **Stays `[~]` until
      `scripts/benchmark_search.py` is run against `P1-16`'s corpus**
- [x] `P2-05` `tsvector` index and trigger — shipped as a **generated column,
      not a trigger**. Postgres 12 made the trigger unnecessary and a generated
      column is strictly stronger: it cannot be bypassed by a write path that
      forgot to fire it, cannot drift from `text` after a bulk UPDATE, and needs
      no ordering agreement with other BEFORE triggers. `chunks.search_vector`
      is `to_tsvector('english', text)` STORED, with a GIN index. The regconfig
      is named because the one-argument form reads a session GUC and is
      therefore not IMMUTABLE — and naming it pins the stemming too. Note
      `alembic check` **cannot** guard this: it warns "Computed default on
      chunks.search_vector cannot be modified" and moves on, so the drift test
      compares the model's expression against the database's own record of it
- [x] `P2-06` `search.py` — hybrid retrieval, RRF fusion, **filters before
      vector search**. `meridian_core/search.py`: two arms fused by reciprocal
      rank, because the arms' scores are not comparable — `ts_rank_cd` is
      unbounded and length-dependent, cosine distance is bounded — and RRF needs
      only the ordering, which is the part both agree is meaningful. Filters are
      predicates *inside* both arm queries; the anti-pattern returns a truncated
      set with nothing to say it was truncated, and an empty page then reads as
      a thin corpus rather than a query built the wrong way round. A missing arm
      is reported (`SearchResult.degraded`), not hidden: lexical-only is
      legitimate, since the embedder is a separate pass, but a caller that
      thinks it ran hybrid and ran half will conclude the wrong thing. The query
      vector is supplied by the caller — `meridian_core` is imported by the API
      and the orchestrator and neither should acquire a 2.3GB model dependency.
      Verified live: both arms ran over the real dev corpus and fusion
      reordered rather than rubber-stamping either arm
- [x] `P2-14` **A source records no topic, so search cannot filter by one** —
      `v0.68.0`. `sources.topic_labels`, written at keep time from two kinds of
      evidence: the claim's topic (provenance — why the URL was fetched) and a
      match against the **final** URL. Labels accumulate rather than replace, or
      the label would depend on which crawl ran last. NULL and `{}` stay
      distinct — "never examined" versus "examined, matched nothing" — and the
      topic filter excludes both, since neither has been established as
      belonging to a topic. Already-crawled sources: `python -m worker.retopic`,
      which deliberately records **less** than the live path, because the crawl's
      own topic is not recoverable after the fact and the join that would
      recover it is the one this task rejected
- [x] `P6-24` Topic filter control in Explore — `v0.72.0`. `/stats` now carries
      the configured topics and a count of sources nothing has examined. The
      second is the point: a topic filter excludes those, correctly and
      silently, so a reader who narrows and sees three results cannot otherwise
      tell the corpus holds three hundred nobody looked at. The control says so
      once, while narrowing. Filtering re-runs the search rather than filtering
      results in place, because fusion ranks a candidate pool
- [x] `P2-20` **Age-aware ranking, by document kind** — `v0.90.0` and
      `v0.95.0`. A half-life per source tier, overridable per topic, applied as
      a **decay on the fused score** rather than a filter — a filter removes,
      a decay reorders, and reordering is what "probably less current" means.
      `peer_reviewed` does not decay at all, which is the point rather than a
      detail: a global "newer is better" multiplier buries the foundational
      paper, and for a corpus with an academic spine that is the failure that
      matters. **An undated document is neither old nor new** — around a third
      of crawled pages have no date and whichever default you pick is wrong for
      the other kind, so decay applies only where a date exists and the hit
      says so. **The adjustment is shown**: the hit carries its age, its factor
      and its pre-decay score, because a result silently demoted is one the
      reader cannot audit. Floored at 0.25 so decay cannot become deletion by
      arithmetic, and **off by default** — it changes what search returns, and
      `P2-04`'s benchmark and `P2-09`'s go/no-go are measured against the
      current baseline. **Second half, `v0.95.0`**: `urgency_for_tier` reads the
      *same* table and lifts a fast-rotting source's place in the queue, for two
      reasons pointing the same way — its claim stops being current, and the
      page itself is likelier to be gone. `peer_reviewed` gains nothing, having
      no half-life. Bounded so it reorders *within* a tier and cannot promote an
      informal page above a government one (§5.2); all four of the crawl's
      enqueue sites use it. `P7-06` should read these half-lives too rather than
      inventing a second set — the way two sets diverge is that nobody notices
      there are two
- [ ] `P2-15` **Benchmark embedding models against each other.**
      `scripts/benchmark_search.py` measures the index and the methods over
      whatever vectors are in the corpus; comparing bge-m3 against an
      alternative is a different job, because it means re-embedding the corpus
      into a second column and running both. Worth doing once, after `P1-16`,
      and only if `P2-09` is marginal — swapping the embedder is a full re-embed
      and a migration, so it needs a measured reason
- [x] `P2-07` `/api/explore/*` read endpoints on the read-only session —
      `v0.43.0`. Five routes over `meridian_core.search`, plus `/health`. The
      read-only guarantee is tested twice: through the session's
      `SET TRANSACTION READ ONLY`, and against the catalogue — the first masks
      the second, so a test that only saw the transaction error would keep
      passing if the grants were widened. DTOs live in `meridian_core.schemas`,
      not the API. **No embedder**: depending on `sentence-transformers` puts
      gigabytes in an HTTP path and accepting a client-supplied vector puts an
      unauthenticated float array into a distance operator, so `embed_query()`
      is a seam returning None and every response reports `degraded`. `P2-17`
      is the sidecar that closes it
- [x] `P2-17` **An embedding sidecar, so search stops being lexical-only** —
      `v0.53.0`. `python -m worker.embedserver` from the worker's own image with
      a different command, on `internal`, no credentials. The API must not carry
      the model and must not take a vector from a caller — not mainly for
      security, but because a vector from a different model is *meaningless*
      against this corpus and compares without erroring, ranking nonsense
      confidently. Verified end to end: both arms ran with `degraded: false`,
      and killing the sidecar left the search answering on one arm. Fixed a real
      bug found that way — an absent embedder and a broken one read identically.
      `P2-19` is unifying the backfill onto the same service
- [x] `P2-16` **Explore landing components** — `v0.41.0`. §8's default state as
      components taking typed props: search field with the `hybrid` marker,
      the four counts, §12.5's three entry points as cards, and "where you
      were". Deliberately not a page — `/api/explore/*` does not exist, and a
      page would have to show fabricated numbers, which inverts the one thing
      §12.5's first state is for. `P2-08` is the wiring
- [x] `P2-08` Minimal Explore UI — `v0.46.0`. The page, the results list, and
      the client between them; the components existed from `P2-16`. Every result
      shows source, tier, date and page/offset. The degraded search is
      **rendered**: shown whenever set, escalated when the result set is empty,
      and the test that makes that mean something is its converse — with
      `degraded: false` and no hits the caveat must not appear, or readers learn
      to skip it
- [ ] `P2-09` ⚑ human — run the held-out questions; make the go/no-go call
- [x] `P2-10` ⚑ human — frontend framework chosen: **React + TypeScript + Tailwind CSS**,
      with Sigma.js v3 + graphology for the canvas
- [x] `P2-11` Frontend scaffold: Vite + React + TypeScript + Tailwind v4, `web/`
      structure per scaffold §2. The app only ever calls `/api/...` relative, so
      no environment-specific base URL exists in the source; the proxy target is
      a property of the machine and reads `VITE_API_PROXY`, defaulting to
      `localhost:8000` (a natively-run uvicorn) with `localhost:21114` for the
      compose stack. The landing state states the absence rather than promising
      a feature, per the voice guide — there is no retrieval path yet and it
      says so
- [x] `P2-12` Design tokens in code — both palettes, the type scale and §5's
      surface rules as CSS custom properties in `web/src/styles/tokens.css`,
      wired into Tailwind v4's `@theme` so the token file *generates* the
      utilities rather than being mirrored into a second config that can drift
      from it. "Tokens stay the single source" is enforced rather than asserted:
      `web/tests/tokens.test.ts` parses the palette out of the design system and
      checks it both ways — every published colour defined at its published
      value, and no colour literal anywhere outside the token file. It caught
      two things on its first run, one of them in its own docstring
- [x] `P2-13` `web/src/lib/api.ts` — typed client over `/api/explore/*`,
      `v0.45.0`. No base URL and no environment read, enforced by a test that
      greps for them. The cross-language contract is held by two links —
      `tsc` ties each interface to a runtime field list, and a drift test ties
      that list to the pydantic class — and the second is the one that matters:
      drop a field from both the interface and the list and `tsc` stays green
      while the server contradicts it
- [x] `P2-18` **Four things `P2-07`'s surface made awkward to consume** —
      `v0.49.0`. `page_unit` derived in core so §5.3's rule is applied once
      rather than by every consumer, and None when the media type was never
      recorded — a wrong label on a citation someone opens is worse than an
      honest hedge. `arms` is a `Literal` so it crosses the boundary like
      `SourceTier`. `detail` is always a string, with the structured form kept
      under `errors`. `/stats` carries `as_of`

- [x] `P2-19` **The backfill still loads its own copy of the model** —
      `v0.69.0`. `worker/vectors.py`: `PreferRemote` asks the sidecar,
      `LocalEmbedder` drives the in-process model off the loop. Falls back when
      the sidecar is unreachable — a backfill can afford the wait — and the
      switch is logged with its reason, because a pass that quietly loads a
      second model looks exactly like one using the sidecar and the only symptom
      is memory pressure. One-way within a process: the memory is already spent.
      A sidecar naming a **different** model is refused, checked on every
      response rather than once at startup, since that is the one failure here
      nothing downstream can detect — mixed vectors compare without erroring

## Phase 3 · MCP read surface

*Checkpoint: an external agent can retrieve usefully.*

- [x] `P3-01` MCP server scaffold inside `api` — `v0.44.0`. Mounted at
      `/mcp` on the same app as `/api/explore/*`: same corpus, same read-only
      role, same provenance, and §11.1b is explicit that all three integration
      directions hit one validation layer. DNS-rebinding protection is on and
      defaults to loopback, because it is off by default in the SDK and matters
      the moment a tunnel is in front
- [x] `P3-02` Read tools: `search_chunks`, `get_source_metadata`,
      `list_new_since`, plus `corpus_overview` — `v0.44.0`. The **instructions
      are load-bearing**: they are the only thing a model reads before deciding
      how to treat the results, and they name the three mistakes it would
      otherwise make. The retrieval mode rides on every result, not just at
      connect, because a client summarises individual calls; and when degraded
      the wording says what to *do* about it, since a client told only that a
      flag is true will not think to try synonyms. `list_new_since` is §11.1a's
      entry point and needs no embedder at all
- [x] `P3-03` Scoped tokens: `allowed_tools`, expiry (spec §11.4) — `v0.47.0`
      mechanism, `v0.48.0` enforcement. Secrets are never compared in code: the
      presented token is hashed and the hash looked up. NULL `allowed_tools`
      grants nothing. Every rejection returns None, because naming which of
      unknown/revoked/expired applies confirms a token exists. **Anonymous
      access is an explicit opt-out**, so a deployment that forgets to configure
      credentials refuses rather than serves, and the scope is checked *per
      tool* — at the transport, the tool asked for is the only thing separating
      a read session from a write one. Rate limiting is `P3-11`
- [x] `P3-04` `run_readonly_query` behind the read-only role, statement timeout,
      row cap — `v0.54.0`. Runs as `meridian_guest` (`P3-07`), not
      `meridian_ro`, because the latter can read the table holding every token
      hash. The role is the enforcement; the textual checks only make a refusal
      legible. The timeout is what makes it exposable at all — the role stops a
      query reading what it must not and does nothing about one that reads what
      it may forever. Every query is logged either way, which is §12.4's actual
      request: the queries an agent writes here are the next curated tools.
      Registered only when `PG_GUEST_URL` is set
- [~] `P3-05` Cloudflare Tunnel + Access in front of the API — the code side
      is done and the rest is **your Cloudflare account**. `cloudflared` is in
      compose, `P3-08` verifies assertions, `P3-03` enforces scopes, and
      transport security defaults to loopback so a tunnel is refused until the
      real hostname is named. `docs/setup.md` §8a–8e is the runbook: tunnel,
      Access application, the four environment values, and the verification
      that must be done before trusting any of it
- [x] `P3-07` **`meridian_guest` role** — `v0.36.0`. SELECT on the corpus and
      the graph (eight tables), nothing else. `meridian_ro` can read every table
      including `agent_tokens`, whose `token_hash` is the one secret in the
      schema. Grants are a migration (tables must exist first), the credential
      stays in `init-roles.sh` (§11.11), and the role is created NOLOGIN when no
      password is configured — so a deployment that shares nothing gets correct
      privileges on a role that cannot connect. **No default privileges on
      purpose**: a table added later is invisible to guests until granted, which
      is the fail-closed direction. The privilege matrix is tested over
      `pg_tables`, so a new table forces the decision rather than inheriting one
- [x] `P3-06` `grants` table + `agent_tokens.grant_id` — `v0.87.0`. **The unit
      of sharing is a person, not a credential.** Somebody given access holds
      several — a browser session, an MCP client on a laptop, another on a
      server — and revoking their access has to revoke all of them at once; a
      per-token model leaves you chasing credentials, and the one you miss is
      the one that still works. `revoke_grant` does both in one transaction,
      with a test that proves it. Tokens are marked revoked rather than
      deleted, because an audit entry points at a token row and deleting it
      would leave the history unable to say whose credential made a call.
      **A person grant must have an expiry**, enforced by a CHECK: §3 says an
      access grant with no end is one nobody revisits, and a code path that
      forgot would otherwise create one. A service grant may be open-ended — it
      belongs to a machine somebody is already running. Profiles are named sets
      defined in code, never free-form lists (§3), **no profile carries a write
      tool** including `operator`, and an unknown profile grants nothing rather
      than everything
- [x] `P3-08` Access JWT verification — `v0.50.0`. Verified against the team's
      published keys with audience and issuer checked, on every request, and
      **no path where an unverifiable assertion is treated as
      anonymous-but-allowed**. An unreachable JWKS refuses rather than bypasses:
      letting requests through when keys cannot be fetched turns a dependency
      outage into an auth bypass. Both env vars required — a team domain alone
      verifies that *some* application on the team signed it. Identity comes
      from the verified claims, never from the unsigned
      `Cf-Access-Authenticated-User-Email` header Cloudflare also sends.
      Mapping identity → grant is `P3-06`
- [~] `P3-09` **OAuth is the path for a hosted client, not service tokens** —
      server half done in `v0.52.0`. The surface advertises
      `/.well-known/oauth-protected-resource/mcp` when authentication is on, so
      a client handed only a URL can discover where to authenticate; anonymous
      mode advertises nothing, since offering an endpoint that is not enforced
      sends a client through a flow for no reason. Tokens issued for another
      resource are refused. The Cloudflare side is `P3-05`'s configuration.
      Service tokens for CLI agents (§11.1a) are still unbuilt
- [x] `P3-10` Grant scoping in the tool layer — `v0.88.0`. `filters_for`
      **intersects rather than replaces**, which is the whole property: a guest
      may narrow their own search further and cannot widen it, whatever they
      send. A guest asking only for topics they do not hold gets nothing rather
      than everything they do hold — answering the question they did not ask
      would be the friendlier bug. `max_source_tier` names a floor in authority
      order, and **an unrecognised tier admits nothing**: treating a typo as
      "no ceiling" is a mistake failing in the direction that widens access.
      §5's two defaults are both off — `raw_files`, because serving the raw
      store to somebody else is redistribution of third-party material rather
      than sharing what was extracted from it, and the operator's annotations,
      which §12.5 predicts become the highest-quality layer precisely because
      they are the most personal thing in the system. A guest's search is
      always `cleared_only` (`P4-14`), since this is content going to somebody
      else's model
- [x] `P3-11` Per-grant audit log and per-token rate limiting — `v0.89.0`.
      **Audited by grant**, because "what has this person's model been reading"
      is unanswerable from a per-token log once they hold three clients; the
      token is a column, not the index. **Arguments are kept and results are
      not** — what somebody searched for is the audit, what came back is the
      corpus, and copying it here would be a second store of the same content
      with none of the retention rules the first one has (§5.4). Refusals are
      recorded too: a log of successful calls answers half the question, and a
      grant repeatedly refused a tool is the more interesting signal.
      **Rate-limited per token, not per grant**, even though everything else
      here is per grant — what is being throttled is a client in a retry loop,
      which is a property of one client, and limiting the grant would let one
      misbehaving laptop silence the same person's phone. Refused calls do not
      count against the limit, or one misconfiguration becomes two. An audit
      write that fails never raises: monitoring that takes the read surface
      down is the outage it exists to detect

## Phase 4 · Graph and writes — the loop closes

*Checkpoint: a model can write validated, provenance-bearing edges.*

- [~] `P4-01` Apache AGE setup, graph schema, typed node ontology — **the
      store, `v0.91.0`**. `deploy/postgres/Dockerfile` compiles AGE 1.7.0 onto
      the pgvector image, so `make quickstart` starts a database that already
      has both and nobody installs an extension by hand. §3 chose one store;
      no published image carries both. Verified on the database holding the
      crawl: the image swap preserved every row, and a Cypher `CREATE` and
      traversal run as `meridian_rw`, the role the application actually uses.
      The typed ontology already existed — `NODE_TYPE` has constrained nodes to
      fourteen kinds since `P0-07`. **Four failures on the way in, each naming
      something nobody wrote**, all in `docs/handover.md`: a sample config file
      that `initdb` alone reads, a graph named after the project colliding with
      the role in `"$user"`, `create_graph` needing `ag_catalog` on the path for
      `graphid_ops`, and `ALTER TABLE ... INHERIT` requiring *ownership* rather
      than `GRANT ALL`.
      **Outstanding, and deliberately not decided here**: whether AGE is the
      source of truth for nodes and edges or a projection of the `entities` and
      `edges` tables that already exist. That is every write going two places
      and a reconciliation story when they disagree — cheap now, expensive
      after fifty thousand edges, and not a call to make inside a migration.
      Nothing writes to the graph yet
- [~] `P4-02` Entity resolution: normalise → block → score → three-band
      decision — `v0.92.0`. §5.5's four steps as four functions, each testable
      alone. **Decides and does not act**: `merge` is `P4-03`, because §16 calls
      bad merges harder to detect than duplicates and a change to a threshold
      should not be a change to a function that rewrites rows. **Never across
      node types**, enforced in `block` rather than left to callers — an
      organisation and a place sharing a name are two things and no score
      should overturn that. **Context is weighted heaviest** because §5.5 says
      so: "Cambridge" the city and "Cambridge" the university share every
      character and no neighbours, and before edges exist the neighbourhood is
      the set of chunks each was drawn from. **Absent signals are dropped and
      the weights renormalised**, not counted as zero, or a corpus that has not
      finished embedding could resolve nothing. Token-set plus `difflib`
      rather than a new dependency: both are hard to get subtly wrong, and a
      subtle bug here is a silent bad merge. **Outstanding**: nothing calls it
      yet — the write path is `P4-04`, and the middle band's queue lands as a
      `merge_adjudication` notification once there is a run to raise it in
- [x] `P4-03` Merge reversibility: redirects, `merged_from`, merge log —
      `v0.93.0`. §5.5: "bad merges are worse than duplicates because
      conflation is invisible once done", and that sentence shapes all of it.
      The source is **kept as a redirect, never deleted** — deleting it breaks
      every citation that already named it. Everything pointing at it moves:
      edges both ways, attribute values, observations both ways, aliases and
      supporting chunks. **The log records which rows moved, not just that a
      merge happened**: `merged_from` cannot say which edges came with an
      entity, so reversing one of two merges into the same target would take
      the other's rows. Each merge stores the ids it reassigned and a reversal
      moves exactly those back, with a test that merges twice and reverses the
      second. Four refusals no score may overturn — self, across node types,
      from a redirect and into one. The log row survives reversal and is
      stamped: "merged then reversed" is the signal a threshold is wrong, which
      is what `P7-10` samples for
- [ ] `P4-04` Write tools: `add_edge`, `tag_entity`, `enqueue_seed`, `advance_mark`
- [x] `P4-05` `validation.py` — server-side guards, node existence, domain
      allowlist, caps — `v0.76.0`. Built before the write tools that call it
      (`P4-04`), because §11.8 specifies the rules precisely enough for the test
      to be a transcription. A module rather than checks inside a tool: §11.1b
      has three callers reaching the same writes and says none gets privileged
      access, so a guard inside one path is a guard the other two lack. Every
      function raises rather than returning a boolean — the failure mode here is
      a guard that never ran, which looks exactly like one that passed.
      **`cap=None` refuses**, because "nobody configured a cap" must never read
      as unlimited (§16). Seed reservation locks the run row, since two calls
      reading `seeds_emitted` at 9 against a cap of 10 would both pass. Seeds are
      refused at seed time as well as fetch time, or a rejected injection sits in
      `queue` being retried with backoff. Hostnames are deliberately **not**
      resolved here — a second DNS answer can disagree with the one `P1-24`
      pinned at fetch time — though literal private addresses are refused
- [x] `P4-12` Allowlist growth: `fetch_policy.seed_allowed` + `first_seen_via`
      — `v0.84.0`. A third question beside `status` (may we fetch what is
      queued) and `trust_state` (may a model read what came back): may new URLs
      on this domain be *queued at all*. **NULL is undecided, and undecided is
      not permission** — a boolean defaulting to false would have said the same
      thing worse, since "declined" and "not yet considered" need different
      screens and different messages. A frontier-discovered domain approves
      itself after three **novel** documents (novel, not fetches: a site
      serving one page under a thousand URLs would approve itself on volume);
      an operator's own seed is allowed immediately, because typing a URL is
      consent. **A model-proposed domain never approves itself, however much
      evidence accrues** — evidence gathered after the proposal is evidence the
      proposal caused, which is the exact shape of a model talking the crawl
      into a domain. It queues for a person like a harvested gazetteer term.
      `first_seen_via` is never overwritten, and discovery is recorded inside
      `enqueue` rather than at its four call sites, so a fifth call site cannot
      leave a domain with no provenance and therefore no path to approval
- [x] `P4-13` Refuse to start a synthesis run with no budget configured —
      `v0.81.1`. §16 states the ordering — caps before the first autonomous run
      — as a mitigation, and a mitigation nothing enforces is a sentence.
      `check_can_start_run` refuses three ways, in the order somebody would fix
      them: no budget row at all, a budget with caps missing, and a month
      already at its ceiling. It returns the budget it approved so the run
      enforces the same numbers it was checked against, rather than re-reading
      caps that may have moved in between. **A missing cap refuses even though
      the others are set**, because a run capped on seeds and uncapped on
      tokens is an uncapped run. The ceiling is checked *before* a run rather
      than during it: a run cannot know what it will spend, and killing one
      halfway leaves a half-written graph — so the month's next run is the one
      refused, which is why §11.9 also asks for trend alerting
- [x] `P4-06` Untrusted-data framing for all retrieved content in prompts —
      `v0.94.0`. §11.8 mitigation 1, wired into the surface it is about: the
      MCP tool returns arbitrary crawled text to a model holding tools, and now
      returns a `framed` block beside the structured hits. **The delimiter is
      random per call**, so a page cannot contain it — a fixed marker is one a
      document can simply include, closing the fence early and putting the rest
      of its text back in instruction position. Per call rather than per
      process, since a leaked one would work for every later call in that
      worker's life. **Nothing is stripped**: `P1-23` established that an
      article *about* injection quotes the phrases, so rewriting documents
      would break the corpus's ability to answer questions about them. The
      instruction precedes the payload, attribution travels inside the fence so
      a model reads the citation rather than reconstructing it, and an empty
      result says so rather than presenting an empty fence. **This is not the
      control** and the module says so — §11.8 is explicit that server-side
      validation is load-bearing (`P4-05`) and this is defence in depth
- [~] `P4-14` **Quarantine and screening for unknown domains** — the
      mechanical half, `v0.82.0`. `P1-23` built the pre-screen and deliberately
      blocked nothing; this is what acts on a flag. `sources.trust_state` and a
      domain verdict cached on `fetch_policy`, so **screening is paid once per
      domain** — a site with four thousand pages is not judged four thousand
      times, and a domain cleared on Monday does not have page 3,001
      quarantined on Friday for quoting something. A domain clears on sight if
      it is in the curated tier map (somebody's judgement, already made) or
      after five consecutive unflagged fetches, and the streak resets on any
      flag. **The filter is `IN (cleared)`, not `!= quarantined`**: a page
      nothing has examined is not a page that has been checked. Quarantined
      content stays stored, extracted and chunked (§2.5) and stays visible in
      the operator's own search — that is how a false positive gets noticed —
      while the MCP surface sets `cleared_only` and cannot be asked not to.
      **Outstanding**: the queue that hands a quarantined domain to a frontier
      model to judge (`P4-07`). Until then a quarantine is lifted by a person,
      which is the correct failure — the alternative is admitting unscreened
      content because nothing was available to screen it
- [x] `P4-07` Agent registry, task-type routing, fallback chains — `v0.97.0`.
      The registry has existed since `P0-07` and nothing read it; this reads
      it. **Nothing here calls a model** — routing answers "who", the caller
      asks, and keeping them apart makes every rule testable against rows.
      **The strongest agent is not the right agent**: §11.3 assigns each task a
      profile, and `TARGET_TIER` encodes it, so attribute tagging goes to the
      mid tier rather than spending frontier money on schema-constrained work
      that would look fine either way. Ties go to the stronger agent, because
      overshooting costs money and undershooting costs quality. **The stated
      fallback is followed, then everything else that could do the task** — the
      seeded chain points the mid tier at a model that does not declare
      attribute tagging, so following `fallback_agent_id` alone would leave
      that task with no fallback at all. A cycle ends the chain rather than
      hanging the run. Disabled is never routed and an empty `task_types`
      declares nothing, and the refusal separates "not seeded" from "never
      filled in" from "nothing declares this task".
      **Found and fixed on the way in**: the seeded registry named a task type
      nothing routes (`tagging` for `tag_attributes`), which is invisible by
      construction — `text[]` accepts anything and the symptom is an agent that
      is never chosen. The seed is insert-only, so the YAML fix does not reach
      an already-seeded database and a migration does
- [x] `P4-08` Orchestrator run state machine + `runs` table resumability —
      `v0.98.0`. §11.10's rejection of workflow frameworks, as a module that
      moves one row. **Nothing here does the work**: which chunks a stage
      reads and which model it asks are the stages' own business, which is
      what lets every resumability rule be tested without a model, a corpus or
      a stage that exists yet. **At most one unfinished run, enforced by two
      unique partial indexes** — two orchestrators means double spend and two
      sets of writes racing one high-water mark, and the application check that
      precedes them is a race the index closes. **A heartbeat**, because a
      crashed run and a live one are both `status='running'` with a stage and
      nothing else separates them; NULL reads as stale, which is right for a
      run that died before its first step and for every row written before the
      column existed. **Deferred is not failed** (§13.4): the stage is kept and
      the next cycle continues from it, so a provider outage costs synthesis
      rather than the crawl. Stages move forward only and the mark refuses to
      go backwards — the stage is a claim about what has already committed, and
      redone work produces duplicates nothing can tell from the originals.
      Counters accumulate, for the reason `budget.py` gives
- [ ] `P4-09` `--once` and `--dry-run` modes (print tool calls, apply nothing)
- [x] `P4-10` `budget.py` — per-run token and seed caps, cost logging, monthly
      ceiling — `v0.81.0`. `budget_config` is a **single row the database
      enforces**, because a settings table that can hold two eventually does and
      then "the budget" is whichever one the query ordered first. Every cap is
      nullable and **null means unconfigured, not unlimited** — the same
      position `reserve_seeds` already took, now with somewhere for the caps to
      come from. `reserve_tokens` mirrors it: all-or-nothing, `FOR UPDATE` so
      two concurrent tool calls cannot both fit under one cap, and it returns
      the remainder so a caller can stop before it is refused. Cost accumulates
      rather than being assigned, or the per-run figure §11.9 compares week on
      week would mean "the last call" in some runs and "all of them" in others.
      The ceiling is the **calendar month in UTC**, measured on `started_at` so
      a run spanning the 1st is not invisible while it keeps spending. Admin
      gets `GET`/`PUT /api/admin/budget`, since a refusal that points at a
      screen needs the screen to exist
- [ ] `P4-11` High-water mark advances only after writes commit

## Phase 5 · Autonomy

*Checkpoint: it runs itself, and tells you when it can't.*

- [ ] `P5-01` `frontier.py` — outbound links, citations, spaCy NER, TF-IDF
      co-occurrence. **Two halves are already built and the other two have no
      consumer**, which is why this is still open after a session that went
      looking for buildable work. Links and citations land through `P1-06`'s
      frontier expansion and `_seed_citations`; the `EntityRuler` and the
      acronym harvest are `P5-02`. What is left is entity co-occurrence, and
      its only readers are `P5-03` and `P5-04` — both gated on the graph.
      Writing it now means a pass whose output nothing reads, which is the
      exact shape `B-15` found five instances of: code that runs correctly when
      invoked and is never invoked. Build it with `P5-03`, or with a consumer
      named first
- [x] `P5-02` Gazetteer into `EntityRuler` at worker startup; acronym auto-harvest
      — `v0.63.0`. §5.6's "do not hand-write it — bootstrap it", built as two
      pure halves plus a pass. `compile_patterns` turns approved rows into
      patterns; `python -m worker.harvest` reads documents nobody has read yet
      and files each `Full Name Here (ACRONYM)`. Because the ruler *overrides*
      statistical NER, three things do not load: unapproved rows, rows flagged
      ambiguous, and any surface form two rows share — the last one observed at
      compile time, because the flag is hand-maintained and will drift. Short
      all-caps forms match case-sensitively, or a three-letter acronym becomes a
      curated entity on every occurrence of the ordinary English word.
      Corroboration is counted in documents, not occurrences. spaCy is the
      optional `ner` extra rather than a dependency: `P5-01` is the task that
      introduces NER, and the patterns are built in `meridian_core`, which needs
      none of it
- [ ] `P5-03` Coverage scoring, schema-aware, topic × dimension
- [ ] `P5-04` Gap analysis and seed emission, capped and validated
- [ ] `P5-05` Diversity seeding — stance-imbalance counter-seeds first (spec §7.4)
- [x] `P5-06` Scheduler reads its timetable from the DB — no cron files,
      `v0.59.0`. `scheduled_jobs` plus `python -m worker.scheduler`, reusing the
      queue's `SKIP LOCKED` + lease so two schedulers cannot both run the same
      backup and a dead one releases by expiry. Missed runs run **once**, not
      caught up — rescheduled from now, or a machine that was off returns to a
      burst. Intervals rather than cron, because §13.2 wants these editable from
      a UI. `python -m <module>` with args as a list and no shell, so a row a UI
      can write is not a remote execution surface. Four jobs seeded; sweep
      without `--apply`
- [x] `P5-07` Telegram digest, alerts on sustained conditions only, inbound
      commands — outbound `v0.58.0`, inbound `v0.96.0`.
      `python -m worker.digest`: §12.5's health line plus four sustained
      conditions, suppressed by a cooldown held in `notifications` (the digest
      exits between runs, so in-memory suppression would forget and re-alert
      every timer tick). Findings are recorded before they are sent, so a failed
      delivery loses the message and not the evidence. Inbound is
      `worker/commands.py` (parse and run) and `worker/bot.py` (the loop),
      behind a `bot` service. **Authorisation happens before parsing** and an
      unknown chat gets silence rather than a refusal — an error message is a
      map of the surface, and an unset chat id refuses everyone rather than
      allowing anyone. The backlog is dropped at startup, because a command is
      an instruction about now and Telegram replays a day of them. The offset
      advances before the work, so one bad message cannot wedge the loop.
      Commands needing phase 4 refuse **by name**; nothing reachable deletes
- [x] `P5-08` Health endpoint, watchdog, off-device snapshot job — `v0.62.0`.
      `/health` shipped with `P2-07`; this adds the two that were missing. A
      liveness heartbeat the worker touches each iteration **before** the work,
      so a lane wedged inside a fetch stops beating — `restart: unless-stopped`
      only ever covered a worker that *exits*, and a wedged one looks exactly
      like a busy one. And systemd units for the off-device backup, which is a
      timer rather than a `scheduled_jobs` row because `backup.sh` needs the
      Docker socket, and giving that to the container that fetches hostile pages
      is not a trade worth making. §13.4's remaining item — "no successful
      synthesis run in N days" — waits for runs to exist (phase 4);
      `check_no_recent_success` already covers the fetch half
- [x] `P5-09` Fix: the alert suppression test expired on a calendar date —
      `v0.75.1`. `record_alert` takes `created_at` from the database clock while
      the file's `NOW` is a fixed instant, so "48 hours after the alert" meant
      48 hours after a date that kept receding. It went red five days after it
      was written, with nothing changed. Suppression tests now set the row's age
      explicitly, the way `attempts()` always set `attempted_at`, and the
      cooldown's *holding* half is asserted too — the expiry assertion alone
      passes against a function that never suppresses anything

## Phase 6 · Interface — the payoff layer

*Checkpoint: reading the graph is genuinely better than reading the sources.*

- [ ] `P6-01` Sigma.js canvas, focus + expand, depth-1 neighbours capped and ranked
- [ ] `P6-02` Canvas filters: topic, attribute, source tier, date, contested-only
- [ ] `P6-03` Path mode between two nodes
- [x] `P6-04` Node detail panel: grouped tags with overflow, attribute list with
      confidence — `v0.73.0`. Built before the graph on purpose: the hard parts
      are about how a claim is presented, and waiting for rows does not make them
      easier. Tags group by §7.1's scope, because a flat row asserts that a
      corpus-wide dimension and a topic-local one are the same kind of claim.
      Confidence is a number on the tag, since rounding to "high" throws away the
      difference between 0.61 and 0.94. And this is the **only** read path that
      shows superseded chunks — the tag was derived from that text, so the
      citation has to resolve even after the page changed
- [x] `P6-05` Annotation as first-class nodes — `v0.75.0`. A note is an
      `entities` row with ordinary `annotates` edges, not a side table: §12.1's
      traversal, path mode and canvas filters all read `entities` and `edges`,
      and a notes table would make the layer §12.5 calls the highest-quality one
      in the system the only layer the graph cannot see. **Authorship is
      assigned by the server and cannot be claimed** — `AnnotationCreate` has no
      `produced_by` and forbids extra keys, and `seed.py` refuses to register an
      agent under the reserved `human` id. `quality_tier` stays null, because
      §11.12's tier is an ordinal over models and a person is not on it. A note
      may be about nothing, which with phase 4 unbuilt is every note — the
      thought that has not found its node is the one the corpus cannot
      re-derive. Its citations ride on the edges too, since every edge here
      names the chunks behind it. Composer on both reading surfaces, never in
      Admin (§12.6)
- [ ] `P6-06` Synthesis panel: collapsible toggle, thread, node chips, inline citations
- [ ] `P6-07` Conversation history within the synthesis panel
- [x] `P6-08` Notifications panel, filterable by type — `v0.60.0`. Reads the
      rows `P5-07` writes before it delivers, so a deployment with no bot token
      still sees what would have been sent. By type rather than read state, per
      the model's own reasoning — a read/unread split turns findings into an
      inbox, and an inbox gets cleared without being read. Counts cover every
      type so the filter cannot hide what the reader came for
- [x] `P6-09` Saved views — `v0.74.0`. A table rather than `localStorage`: a
      saved view is research method, so it survives a cleared cache, reaches a
      second device and travels in the snapshot. Reads sit on `/api/explore` and
      writes on `/api/admin`, which looks inconsistent and is the right split —
      views are shared state with no per-viewer scoping, so a guest should open
      the owner's and not add to them. Filters are validated against
      `SearchFilters` before storing, because a view that silently drops a filter
      when reopened hands back a result set the reader believes is narrowed
- [ ] `P6-10` Coverage grid and contested list as entry points
- [x] `P6-11` Explore landing state with since-last-visit delta — `v0.61.0`.
      `stats?since=` plus a `localStorage` stamp read once per session and
      advanced immediately, so the delta means "since you were last here" and
      does not vanish as you look at it. Three states kept distinct: `null` for
      a first visit (no moment to measure from), `0` for nothing arrived (worth
      saying, or a silent panel reads as a failed load), and the delta itself
- [x] `P6-12` Admin: topic management — add, pause, archive with re-normalising
      weights — `v0.65.0`. §10's vector, with the three places the obvious
      implementation is wrong. Normalising is **not** dividing by the total: the
      floor is a guarantee ("nothing fully stalls"), and a violated floor is the
      guarantee not existing while the vector still sums to 1.0 and looks fine.
      Bounds relax on read and are refused on write — pausing every topic but one
      leaves a single topic with a 0.6 ceiling, and the seeds still have to come
      from somewhere. A boost is applied at read time and never cleared, because
      "steer back later without remembering" breaks the moment it depends on a
      cleanup job. `steering_log` records the weights that moved because somebody
      steered a *different* topic, which is the question §10.1 exists to answer
- [x] `P6-13` Admin: gazetteer approvals — `v0.64.0`. Split: this ID is now the
      Admin shell plus §5.6's "approve in the UI", and the other three surfaces
      it used to name are `P6-22` and `P6-23`. Every row reports whether the
      matcher will **actually load it**, computed against the whole approved set
      — an approved term whose wording another row claims is withheld, so it
      reads approved and matches nothing, and nothing else in the system says
      so. It found a real one on the first page, which is `v0.63.1`. Rejection
      is a tombstone (`rejected_at`) rather than a delete, because the harvest
      re-reads the same documents and would re-create the row. `/api/admin/*`
      fails closed: 503 unless Access is configured or
      `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` says the instance is not exposed
- [x] `P6-22` Admin: fetch policy per domain — `v0.71.0`. Delay, concurrency,
      timeouts, render mode and status. The editable set is an **allowlist**, and
      what it excludes is the point: the SSRF guards and `respect_robots` and
      `user_agent` are deployment settings, because a form that could switch off
      private-address blocking would sit one click from controls about
      politeness. Editing the global row needs a server-checked `confirm`, since
      a client-side dialog is a promise. Three layers shown separately — set,
      resolved, learned — because a value a reader cannot find anywhere to change
      is this screen's characteristic failure. `seed_allowed` is `P4-12`'s column
      and is not built yet
- [ ] `P6-23` Admin: agent registry and run history. **Left open deliberately,
      on the task's own argument**: both tables are empty until phase 4 has run
      something, and "an empty screen teaches nothing about what the full one
      should look like" is its own sentence. Building it now would mean
      designing a run-history view against zero runs and discovering its real
      shape the first time one exists
- [x] `P6-14` Figures panel with page-accurate raw file links — `v0.56.0`.
      No thumbnails, because nothing downloads figure images (`P1-10`) and a
      placeholder grid would promise what the corpus cannot keep; the caption is
      the content, per §6.6. Two links with different meanings — the publisher's
      live image, and this corpus's own copy at `#page=N`, which is what §5.4
      keeps raw files for. **Raw serving is off unless `MERIDIAN_SERVE_RAW` is
      set**, because the store holds third-party material and serving it is
      redistribution; the link is then absent with a line saying why, rather
      than broken. The raw path comes from the row, never the request
- [x] `P6-15` Export: Markdown and BibTeX — `v0.55.0`.
      `meridian_core/export.py` plus `/api/explore/export/{bibtex,markdown}`.
      Nothing is generated: a field the document did not carry is omitted, since
      a fabricated year is wrong in a file somebody pastes into a paper. TeX
      escaping, because an unescaped `&` fails in *their* document rather than
      here. Keys are stable across exports and carry the source id. The entry
      type is a format decision and the tier rides verbatim in `note`, because a
      bibliography is exactly where `@article` vs `@misc` would read as the
      credibility verdict §8 refuses to compute. A UI action is still to come
- [x] `P6-16` Shared UI primitives from the design system — `v0.33.0`. The
      17-icon set on §7's grid (11 interface icons, 6 node glyphs), source-tier
      and data chips, and the contested mark in its three forms. `Icon` owns
      every shared attribute rather than repeating it per icon, including §7's
      optical-size rule that drops interior detail below 20px. Tier chips take
      no variant, tone or colour prop on purpose: §2 says the palette has no
      green and no red and that colour must not imply a verdict, so there is
      nowhere for one to go. The drawings follow the published grid and are a
      first pass — the geometry is right, the draughtsmanship is where a
      designer should still put hands on
- [x] `P6-17` Theme switching, honouring the system preference by default —
      `v0.32.0`. Three states, not two: `system` is the default and is expressed
      by the *absence* of `data-theme`, so the `prefers-color-scheme` block
      applies. Two-state theming is the common bug and is invisible in the
      working case — a toggle that only ever writes `light` or `dark` looks
      correct to whoever built it and silently overrides the preference of every
      reader who never touches it. Restructured `tokens.css` into palettes
      (each published colour written once) and per-theme role mappings, which is
      what makes the mapping drift testable
- [x] `P6-21` **Source detail page** — `v0.57.0`. `/sources/{id}`: the header
      with tier, date, DOI and `extractor`, the passages in document order, the
      figures panel and both exports. The first screen where the corpus reads as
      documents rather than results, and where `P6-14`/`P6-15` finally have
      somewhere to live. A textless source is a finding, not a failure (§6.5)
- [ ] `P6-20` **Replace the hand-rolled router when the screen count justifies
      it.** `v0.57.0` added forty lines rather than a dependency, because two
      routes do not justify inheriting an upgrade path — and URLs had to be real
      so a source page can be linked into a citation. Phase 6 has fifteen more
      screens; when nested layouts or route-level data loading arrive, replace
      it rather than growing it
- [ ] `P6-19` **Three lockup values are inferred, not published.** §1 gives the
      mark's geometry exactly and the lockup's gaps (22px) and wordmark weight,
      but not the cap-height ratio the hairline rule sits at, nor the
      mark-to-wordmark size ratio. Both were picked to look right in `Lockup`
      and neither is a design decision anyone made. Also: §1 states "Bounding
      box 76 × 86.2" immediately after the *compact* table, and it matches the
      **full** mark's extents exactly — the compact variant computes to 76 × 88
      because its zenith and base nodes are larger. Publish the values, or
      confirm the inferences. ⚑ human
- [ ] `P6-18` **Four light-theme roles are inferred, not decided.** The design
      system publishes nine light tokens against thirteen dark, and the four it
      omits are all canvas roles — its graph-canvas table is headed "Dark" and
      has no light column. `tokens.css` maps them to values the design system
      *does* define rather than inventing hexes (recolouring outside the palette
      is an explicit Never), but a mapping made by the person wiring the tokens
      is not a design decision. Decide whether the canvas stays dark in both
      themes, and publish the light values if it does not. ⚑ human

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

- [x] `B-10` `figures.linked_entity_ids` is an array, not a JSON document —
      `v0.75.2`. It was the only `json` column in the schema and the only list of
      ids not stored as `ARRAY(BigInteger)`. Two probes rather than one assertion,
      both derived from the live schema so they grow with it: every `*_ids` column
      is an array of bigint, and nothing uses `json` where `jsonb` was meant.
      **The migration is four statements**, because there is no cast from `json`
      to `bigint[]` and supplying one needs a subquery, which Postgres rejects in
      `USING` — autogenerate's version would have failed on the server and passed
      here, since `figures` is empty locally. Caught by migrating rows put there
      on purpose, and the downgrade round-trips
- [x] `B-11` **Licence audit** — `docs/licences.md` and a gate, `v0.83.0`.
      Every verdict read off the installed artefact rather than recalled:
      distribution metadata, image labels, the model card on disk. **Nothing
      blocks commercial use.** All 116 Python distributions are permissive — no
      GPL, no AGPL, no non-commercial terms — and the weights the task called
      the likeliest problem are clean (`BAAI/bge-m3` is `license: mit`, read
      from the snapshot). Two things needed a judgement rather than a reading.
      **SearXNG is AGPL-3.0-or-later** (confirmed from its image label): run
      unmodified, in its own container, over HTTP, with no published port —
      aggregation, not derivative work, and the document states exactly which
      changes would turn §13 on. **`tld` is tri-licensed** MPL-1.1 / GPL-2.0
      / LGPL-2.1+, and we take MPL-1.1; the choice is recorded in code, and a
      test checks the package still offers it. Also found: Meridian's own three
      packages declared no licence at all, which is the worst case rather than
      a neutral one — no licence is no grant. **Both ⚑ human calls
      accepted 2026-09-20** and recorded in the document beside the evidence:
      the SearXNG boundary, and `tld` under MPL-1.1. `en_core_web_sm` and
      Apache AGE are named but unverified — neither is installed yet, and both
      should be confirmed from an artefact when they arrive (`P4-01` brings
      AGE)
- [x] `B-12` Fix: `docker-compose.yml` set `MERIDIAN_EMBEDDER_CACHE` and the code
      reads `MERIDIAN_EMBED_CACHE` — `v0.76.3`. The `/models` volume was
      therefore never used and 2.3 GB re-downloaded on every recreate, silently,
      because the whole point of `os.environ.get(name, default)` is not to
      raise. A misspelt variable cannot fail loudly, so the test is the only
      mechanism available: every name any compose file sets must appear in some
      Python source, with a short exemption list for the ones read by the
      Postgres entrypoint and the upstream images — and a second test that the
      exemptions are all still set, so the list cannot become a museum
- [x] `B-13` Fix: a workspace package must declare what it imports — `v0.76.4`.
      The third time, and every time found by *running* a container rather than
      building one: `meridian_core.embedder` imports `httpx`, declared on
      `meridian-worker`; `services/api` imports `jwt`, which arrived
      transitively through `mcp`; and `worker.embedserver` imports `fastapi`
      from the `embed` extra the worker image did not install, so `P2-17`'s
      sidecar had never once started from a container. `P1-30` wrote the rule
      down and the handover predicted the recurrence; predicting it was not
      enough. Now derived from the AST on both sides, per workspace member
- [x] `B-14` Fix: the embedding sidecar could never obtain its weights —
      `v0.76.5`. `embedder` is on `internal`, which has no DNS and no route out,
      and the weights are not in the image — the compose comment claimed they
      were, two lines above the mount that exists because they are downloaded at
      runtime. So a fresh stack's sidecar answered `loaded: false` for ever and
      timed out every request, and search stayed lexical-only in *production*
      too, which puts this on `P2-09`'s critical path. `python -m
      worker.fetchmodel` is the one-shot with egress that writes into the volume
      the sidecar reads, profile-gated so `up` never waits on it. It loads and
      encodes rather than only downloading, because a cache missing one file
      fails at the first real batch instead. The isolation is kept deliberately:
      the sidecar runs corpus text through a model, and a route out from there is
      a route out for anything that ever gets in
- [x] `B-16` Fix: every fetched page was being thrown away — `v0.76.6`. Docker
      creates a missing bind-mount source on the host as **root**, and every
      application image runs as `meridian` (uid 1001, `cap_drop: ALL`, read-only
      root). So the first containerised crawl fetched a handful of real pages,
      could not create `/data/raw/<domain>/`, logged a traceback per page and
      settled each task `"outcome": "success", "stored": null` — because the
      *fetch* had succeeded. It kept crawling and kept nothing, and the same
      would have happened on the server: the deploy docs chown `/srv/meridian/
      app` for the checkout and say nothing about `raw`, `figures` or `models`.
      A `chown` one-shot on `network_mode: none`, in both compose files, of the
      three top-level directories only — everything created beneath them
      inherits the owner, and a `chown -R` over a 100 GB raw store is a
      different mistake. The uid is now tied to the Dockerfiles by a drift test,
      since three copies of 1001 is three chances to move one
- [x] `B-17` Fix: the documented way to create the database could never have
      worked — `v0.76.7`. `docs/setup.md` and `docs/deployment.md` both said
      `docker compose run --rm worker alembic upgrade head`, and the worker
      image has no `alembic` — it is in the root project's `dev` group and every
      application image syncs `--no-dev` — and never copies `scripts/`, so the
      seed line failed too. Both are the *first* commands an operator runs, on
      the server, and neither had been run there. `deploy/tools/Dockerfile`
      (written for `B-05`) is promoted to production compose, profile-gated, and
      the docs name it: the thing worth preventing was a stack that migrates
      *itself* on boot, and the profile is what prevents that, not the image's
      absence. `make migrate` is not the answer on the server — docs/setup.md §2
      never installs `uv`, and `postgres` resolves only inside the compose
      network. New test walks every `docker compose run` in the deploy docs and
      checks the named service's Dockerfile actually carries what is invoked
- [x] `B-18` Fix: `docker compose up -d` could not bring up the production
      stack — `v0.78.2`. `orchestrator` is phase 4 and its Dockerfile does not
      exist, and **compose does not skip a service it cannot build**: it fails
      the whole command with `lstat ...: no such file or directory`. The same
      shape as `web`, which had no Dockerfile either until `B-05`; that one was
      written, this one profile-gated until the image exists. Also removed
      `api`'s `ports: 127.0.0.1:21114:8000`, which published nothing — a
      container attached only to an `internal: true` network has no gateway for
      the host to forward to, so the line was inert under a comment promising a
      port to curl. Both generalised rather than patched: every non-profiled
      service must build from a Dockerfile that exists, and nothing may publish
      a port on networks that are all internal. Each verified by reintroducing
      the bug
- [x] `B-15` **The timetable now has something reading it** — `v0.79.0`.
      `P5-06` was ticked and its code worked, and no compose file had ever run
      `python -m worker.scheduler` — so `seed.py`'s five `scheduled_jobs` rows,
      all `enabled`, all with `next_run_at` already past, were a timetable
      nobody read. A stack left alone fetched, extracted, chunked and stopped:
      nothing embedded, deduplicated, swept or harvested, ever, in either
      stack. Found by bringing the stack up for `B-05` and watching
      `embedded_chunks` sit at zero against a growing backlog.
      A `scheduler` service in both files — the worker image under a different
      command, like `embedder`, because the jobs it spawns are `python -m`
      entry points from the same tree and inherit its environment. **It is a
      second service on both networks**, which `P1-22`'s boundary previously
      allowed only `worker` and `orchestrator`: one of the five jobs
      (`worker.digest`) reaches Telegram and the other four want nothing
      outside `internal`. The trade is argued in the test rather than hidden —
      the same image already makes it, and a scheduled send that fails into
      `last_error` is the failure mode this task existed to remove
- [x] `B-20` Fix: the sidecar spent two and a half minutes per cold start
      asking a host it cannot reach — `v0.79.1`. `embedder` sits on `internal`
      by design and reads its weights from the cache `modelfetch` filled, but
      `huggingface_hub` does not know that: every load issued HEAD requests for
      the optional config files the cache does not hold, got `Temporary failure
      in name resolution`, and retried five times with backoff *per file*
      before proceeding from cache anyway. Correct, slow, and logged as a wall
      of warnings that look exactly like the failure they are not — which is
      how `B-14` was nearly mistaken for still broken. `HF_HUB_OFFLINE=1` says
      the true thing about where that container is standing: **144.6s → 4.9s**
      to first embed, measured both ways on the same populated cache, and no
      warnings. It is also the honest failure mode, since a genuinely missing
      required file now raises "not in cache" instead of timing out against a
      host that was never reachable. Asserted both ways — a model loader on an
      isolated network must be offline, and the fetcher must never be
- [x] `B-19` **The scheduler can be probed** — `v0.80.0`. `B-15` left it
      unprobed and, worse, *silently* unprobed: omitting `healthcheck:` does
      not give a container none, it inherits the image's — and the worker
      image's imports `worker.main` and checks poppler, so a wedged scheduler
      would have read `healthy` for ever while suppressing the restart that no
      probe at all would have left to `restart: unless-stopped`.
      The loop now writes `P5-08`'s heartbeat itself, and the two placements
      are the design. **After each claim, not before it** — `worker.main` beats
      before its work because a lane wedged inside a fetch should stop beating
      within the iteration, whereas here the database round trip *is* the thing
      that hangs, so a beat before it would be refreshed by a scheduler that
      never gets an answer. **And throughout a running job**, because a
      backfill legitimately takes half an hour and a probe that fired during
      normal work would restart the scheduler in the middle of the job it was
      reporting on. That second beat proves less — a job is in flight and has
      not hit `--timeout-seconds` — and the timeout is what keeps it honest.
      Generalised: a long-running service overriding its image's command must
      declare a healthcheck or disable one explicitly
- [ ] `B-01` Qdrant migration path, if pgvector recall becomes the measured bottleneck
- [ ] `B-02` App-level auth and roles, when Cloudflare Access stops being sufficient
- [ ] `B-03` Multimodal embeddings for figure similarity search
- [ ] `B-04` Offline corpora (OSM extract, filtered arXiv) — selective, storage-hungry

### Running it locally

For someone who wants to *run* Meridian rather than develop it. Today's quickstart
assumes `uv`, `npm`, and three terminals; this is the path that doesn't.

- [x] `B-05` `docker-compose.local.yml` — the full stack from source, so a fresh
      clone needs no registry access — `v0.77.0`. Also the two images
      `docker-compose.yml` had always referenced and nobody had written:
      `web/Dockerfile` (node builds, nginx serves, no Node in the runtime) and
      `deploy/tools/Dockerfile` for Alembic and the seed, which no service image
      carries because they sync `--no-dev`. **The network split is kept**: it is
      `P1-22`'s boundary, and a local stack that flattened it would let someone
      develop against a topology production does not have. One deliberate
      difference — `api` and `web` sit on a third `frontdoor` network, because a
      container on an `internal: true` network cannot publish a port at all and
      the `ports:` line is silently inert rather than an error. Two defects found
      by running it: `api` was never given `MERIDIAN_EMBEDDER_URL`, so every
      search reported "this deployment has no embedder" next to a healthy
      sidecar; and `.localdata/` was not gitignored
- [x] `B-06` `make quickstart` — one command from a fresh clone to a running,
      seeded stack — `v0.78.0`. Preflight, build, Postgres alone first, migrate,
      seed, fetch the weights, start the rest, wait for the API, print the URL.
      **Idempotent by construction**, because "run it again" is the only thing
      anybody tries: compose converges, `alembic upgrade head` is a no-op at
      head, `seed.py` skips what it has written and the weights resolve from
      cache. Postgres starts alone so a failed migration is readable rather than
      interleaved with six services' startup logs
- [x] `B-07` First-run experience — seeds editable from the UI — `v0.85.0`.
      §16 calls cold-start seed quality "worth spending an evening on", and
      until now that evening had to be spent editing `config/seed_sources.yaml`
      **before** the first boot, because the file is read once and never again
      (§13.1) — by somebody who has no idea yet what belongs in it. Now a Seeds
      section in Admin lists what is still pending, takes new URLs and search
      queries, and drops ones nobody wanted. **Not a wizard and not a gate**:
      the crawl has already started by the time anyone opens it, so the screen
      shows what is still changeable *and* what has been reached, and the
      second is not styled as an error. Admin opens on Seeds when nothing has
      been crawled yet, because the default section on a fresh machine is
      otherwise an empty gazetteer queue with no hint of what to do. A typed
      URL is still validated — a private address is no safer for having been
      typed than proposed — and a seed cannot be removed once claimed, which is
      two conditions rather than one: **claiming is a lease, so a seed being
      fetched right now is still `pending`**, and checking only the status
      would delete a row out from under a worker mid-fetch
- [x] `B-08` Preflight check script — `v0.76.2`. `scripts/preflight.sh` checks
      Docker, Compose v2, cores, memory and free disk against the README's
      minimums and names the *consequence* of each shortfall, not just the
      number. **Warnings are not failures**: only a missing daemon or Compose v1
      exit non-zero, because those mean nothing can start — being under the
      stated minimum is a legitimate choice on hardware somebody already owns,
      and the figures are sized for a 50k-document corpus. `MemTotal` rather than
      `MemAvailable` (available is mostly page cache and says nothing about
      whether the stack fits), disk measured at `DATA_ROOT` rather than the
      checkout, colour only on a TTY. A drift test ties the numbers to the README
      table, which is where a user reads them
- [x] `B-09` **What a first run shows — decided and built**, `v0.86.0`. The
      task offered two ways out and the decision is recorded where it is
      enforced: **make the first hour legible rather than ship a demo corpus.**
      A snapshot of a real crawl is third-party content, and whether it may be
      redistributed is the question §14.2 keeps separate from everything else —
      the same reasoning that keeps `MERIDIAN_SERVE_RAW` off by default, and
      `B-11` reached it independently the same week. Shipping a corpus in the
      repository would have answered that question the other way without saying
      so. Synthetic fixtures were ruled out by the task itself. So
      `GET /api/explore/progress` reports the queue by status, both halves of
      the last hour's fetch rate, and the domains most recently fetched — every
      number true of that machine right now. **The two failures that both look
      like "no results" are separated**: a crawl with nowhere to begin says so,
      and a crawl where every fetch failed says that rather than reporting
      twenty attempts as progress
