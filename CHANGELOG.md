# Changelog

All notable changes to Meridian. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is `MAJOR.MINOR.PATCH` as defined in [AGENTS.md](AGENTS.md#versioning).

The version in [`VERSION`](VERSION) is the single source of truth. Documentation and
design-only changes do not require a version bump, but may be listed under Unreleased.

## [Unreleased]

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

Verified live against real LTA documents: `MTM.pdf` extracted across 2 pages,
the Land Transport ITM extracted with its own title read out of the PDF's Info
dictionary, and every chunk carrying the page number a citation resolves to.

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

Verified against the real web, not only against doubles: seven Singapore
government and standards sites fetched under their own robots.txt and delays, a
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
