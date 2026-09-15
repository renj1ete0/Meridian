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

**Current phase: 2, phase 1 not yet closed. The corpus is searchable.** The crawl runs unattended,
expands its own frontier from links *and sitemaps*, and reads HTML, PDFs and Office
documents: `P1-01`–`P1-09`, `P1-11`–`P1-15`, `P1-17`–`P1-24`, `P1-26`,
`P1-28`, `P1-30`, `P1-33`, `P1-34` and (pulled forward) `P2-02` are done, at
1807 backend tests and 205 frontend.
`P2-01` adds embeddings, so chunks carry vectors — written by a separate backfill
pass, not by the fetch loop — and `P2-03` judges them, so a chunk now knows what
it duplicates. `P1-22` gave the stack a topology, so it is now a stack rather
than an image. Verified live — real government PDFs extracted with
page-accurate chunks, and the injection screen clean across every page crawled.

Phase 1's checkpoint (`P1-16`, the 48h run) is the gate on phase 2's go/no-go
(`P2-09`), because a search-quality judgement over a corpus this small measures
nothing. The agreed sequence:

1. ~~`P1-28` sitemaps~~ — **done in v0.22.0**, with topic matching
2. ~~`P1-22` network topology, then `P1-26`~~ — **done in v0.24.0**. The stack
   has a topology, the browser has an image, and the health line says whether
   it is actually there
3. **← next.** A short bounded run (`MERIDIAN_WORKER_MAX_TASKS`, not a timer) as
   a **stack** smoke test, deployed to the server rather than run from a
   checkout. Its purpose is "do the containers come up and talk to each other",
   not corpus volume
4. ~~`P2-03` novelty gate~~ — **done in v0.25.0**, ahead of the smoke run
   because it needed neither the stack nor the server. The gate went before the
   long run deliberately: nothing deletes from the raw store (`P1-31`), so an
   ungated 48h run keeps every near-duplicate it finds — it now at least
   *knows* which ones they are.
   ~~`P2-05` tsvector~~ — **done in v0.29.0**, for the same reason: an index
   definition needs neither a corpus nor a server, and building it before the
   run means the run's chunks arrive already indexed rather than needing a
   backfill afterwards. `P2-04` HNSW and `P2-06` search still want real crawl
   output — `P2-04`'s task is half index and half *measurement*, and the
   measurement is the half that cannot be faked
4b. ~~`P1-34` query handler~~ — **done in v0.26.0**, and it moved ahead of the
   long run for a reason worth keeping: a 48h window is only worth paying for
   if the frontier can widen when it drains. Building it surfaced that `P1-28`
   had never enqueued anything either, which would have made the same window
   much narrower than anyone expected
5. `P1-16` the 48h run, then `P0-15` held-out questions — which must be written
   before `P2-06` is judged, not after — and `P2-09`, the call

The frontend is a third track and blocks on none of it: `P2-11` and `P2-12`
are done in `v0.29.0`, so `web/` has a build, a token layer and a test that
stops the palette drifting from the design system. `P2-13` (the typed API
client) is the first frontend task that genuinely waits — it types
`/api/explore/*`, which `P2-07` has not written yet.

`P1-10` (figures) is the remaining phase-1 extraction work and does not block
the checkpoint. `P1-14` is done: it moved up because `P1-34` wired an academic
search feed into the frontier, and roughly a third of what that returns are
publisher landing pages — an abstract, a paywall, nothing to extract. `P1-25` (egress restriction)
is **not** closed by `P1-22`: `internal: true` stops a container reaching the
internet, and does nothing about the worker — which must have a default route —
reaching the LAN.

Worth knowing before the run: **`P1-28`'s sitemap handler had never enqueued a
URL** — `seed_source="sitemap"` was missing from the enum, so every sitemap
fetched, parsed, and then raised at the insert while the logs said it worked.
Fixed in `v0.26.0`, along with `P1-34`, which was the other reason the frontier
could only narrow. Both were found by asking what a 48h run would actually do
when its queue drained.

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
- [~] `P1-37` **`make backup` works; `make build-push` still does not.**
      `scripts/backup.sh` shipped in `v0.42.0` — unattended, asks nothing, fails
      loudly, warns when the backup root shares a filesystem with the data root
      (a backup on the disk it protects survives an accidental delete and
      nothing else), checks the dump is non-empty because `pipefail` does not
      reach across a redirect, and rotates only after the new one is written.
      `build_and_push.sh` is outstanding: a first deploy can build on the
      server, so it is not on the critical path, but it should exist before the
      stack is something anyone would rather not rebuild in place
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
- [ ] `P2-14` **A source records no topic, so search cannot filter by one.**
      §12.5 lists topic among the filters and §12.3 lists it among the canvas
      filters, and `sources` has no such column: the crawl knows the topic — it
      is on the queue row that produced the fetch — and drops it at
      `upsert_source`. Filtering through a join back to `queue` on the URL would
      be wrong often enough to be worse than not offering it, since a URL can be
      enqueued more than once under different topics and a redirect means the
      fetched URL is frequently not the queued one. Needs the column, the write
      at keep time, and a decision about what the already-crawled sources get
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

- [ ] `P2-19` **The backfill still loads its own copy of the model.**
      `P2-17` put one resident in the sidecar for the query path, and
      `worker.embed` continues to construct a `BGEEmbedder` of its own — so a
      stack running both holds two. `Embedder` is a Protocol whose docstring
      already anticipates "a remote service", so this is a substitution rather
      than a rewrite; what it needs is a sync-over-async shim or an async batch
      path, and a decision about what the backfill does when the sidecar is down
      (probably: load locally, since a backfill can afford the wait)

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
- [ ] `P3-06` `grants` table + `agent_tokens.grant_id`. The unit of sharing is a
      person, not a credential — they will hold several — so revoking a grant
      must revoke every token beneath it in one statement, with a test that
      proves it
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
- [ ] `P3-10` Grant scoping in the tool layer: `topics[]`, `max_source_tier`,
      `raw_files` (default **false** — serving raw files to other people is
      redistribution, not sharing), and annotation exclusion
- [ ] `P3-11` Per-grant audit log and per-token rate limiting. Audit by grant,
      not by token: "what has this person's model been reading" is unanswerable
      from a per-token log once they have three clients

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
- [~] `P5-07` Telegram digest, alerts on sustained conditions only, inbound
      commands — **outbound done in `v0.58.0`, inbound outstanding**.
      `python -m worker.digest`: §12.5's health line plus four sustained
      conditions, suppressed by a cooldown held in `notifications` (the digest
      exits between runs, so in-memory suppression would forget and re-alert
      every timer tick). Findings are recorded before they are sent, so a failed
      delivery loses the message and not the evidence. **Inbound is deliberately
      not built**: §13.3 makes the bot a control surface that can trigger runs
      and change steering, so the single-chat restriction and the command
      authorisation have to exist before the first command does — and most
      commands need steering (`P5-06`) or the orchestrator (phase 4) anyway
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

## Phase 6 · Interface — the payoff layer

*Checkpoint: reading the graph is genuinely better than reading the sources.*

- [ ] `P6-01` Sigma.js canvas, focus + expand, depth-1 neighbours capped and ranked
- [ ] `P6-02` Canvas filters: topic, attribute, source tier, date, contested-only
- [ ] `P6-03` Path mode between two nodes
- [ ] `P6-04` Node detail panel: grouped tags with overflow, attribute list with confidence
- [ ] `P6-05` Annotation as first-class nodes
- [ ] `P6-06` Synthesis panel: collapsible toggle, thread, node chips, inline citations
- [ ] `P6-07` Conversation history within the synthesis panel
- [x] `P6-08` Notifications panel, filterable by type — `v0.60.0`. Reads the
      rows `P5-07` writes before it delivers, so a deployment with no bot token
      still sees what would have been sent. By type rather than read state, per
      the model's own reasoning — a read/unread split turns findings into an
      inbox, and an inbox gets cleared without being read. Counts cover every
      type so the filter cannot hide what the reader came for
- [ ] `P6-09` Saved views
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
- [ ] `P6-22` Admin: fetch policy per domain — rate limits, `render_js`,
      `seed_allowed`. The one admin surface with an immediate effect on the
      crawl, so it wants a confirmation step the gazetteer queue does not
- [ ] `P6-23` Admin: agent registry and run history. Both tables exist and both
      are empty until phase 4 has run something, so this is worth building
      *after* there is a run to show — an empty screen teaches nothing about what
      the full one should look like
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
