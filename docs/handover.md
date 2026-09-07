# Handover

**This file is not the task list.** [TASKS.md](../TASKS.md) is the source of truth for
*what* to build and [AGENTS.md](../AGENTS.md) for *how* to write it. This document
exists for the third thing: the working knowledge that is true about this repository
but written down in neither, and that otherwise has to be rediscovered by whoever
picks the work up next.

Everything below is something that cost real time to learn. If you learn another one,
add it here.

---

## 1. Where the build actually is

Phase 0 is closed. Phase 1 has its fetch path complete *and running*: a URL goes in,
bytes come out, politely, without becoming a route into the network, leaving a record
of itself, keeping what it read, reading it, cutting it into citable chunks, and
following its links onward — and all of it with nobody watching. As of `v0.16.0`,
793 tests pass with a real Postgres.

```
worker.main ──► claim (queueing.py) ──► Crawler.fetch (worker/crawl.py)
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
          resolve_policy()        RobotsCache          DomainLimiter
          (policy.py)             (worker/robots.py)   (worker/ratelimit.py)
          per-domain → global     RFC 9309, cached     concurrency + delay
          → file defaults         per origin, 24h      per domain
                    │
                    ▼
              Fetcher.fetch (worker/fetch.py)
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
   fetch_static             fetch_rendered
   httpx, address-pinned    Crawl4AI browser
   netguard on every hop    (validated, not pinned)
                    │
                    ▼
        Crawler._record — one transaction
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
   record_attempt()      apply_fetch_outcome()
   (attempts.py)         (policy.py) → blocked?
   one fetch_attempts    → limiter.forget(domain)
   row, every path
                    │
                    ▼
        worker.main._keep — the bytes, before the settle
                    │
        ┌───────────┬────────────┐
        ▼           ▼            ▼
   rawstore     extract/     upsert_source()
   .store()     html.py      checksum, etag, tier,
   path from    trafilatura  raw path, title, date,
   the URL,     or crawl4ai  language, doi,
   primary      fit_markdown text_available
   only (§5.4)       │              │
                     ▼              ▼
                chunk_text()   replace_chunks()   Prefilter.keep()
                verbatim       same transaction   normalise, shape,
                slices +       as the source row  blocklist, seen
                offsets                                 │
                                                        ▼
                                                   enqueue() at
                                                   tier priority
                    │
                    ▼
        worker.main settles the task
        queue_disposition() → fetched | done | retry | abandon
```

**What does not exist yet.** No embeddings, no search, no API, no frontend. HTML is
extracted and chunked; PDFs and Office documents are fetched, stored, and left
metadata-only until `P1-09` and `P1-08` — which is now the binding gap, because the
frontier finds far more of them than the crawler can read. The frontier is the
link half of `P5-01` only: no citation-driven seeding, no spaCy NER, no TF-IDF.

`fetch_health()` is logged hourly by the loop and displayed nowhere (there is no UI).
Nothing ever *deletes* from the raw store either — §5.4's junk drop needs the novelty
gate, which is phase 2 (`P1-31`).

---

## 2. Getting a working environment

```bash
docker compose -f docker-compose.dev.yml up -d postgres   # crawl4ai only if you need the browser
make migrate
make seed
make test
```

**Use `make test`, never a bare `uv run pytest`.** The Makefile does `-include .env.dev`
and exports it. Some tests shell out to subprocesses — `alembic check`, `scripts/seed.py` —
which read `PG_*` from the environment and cannot see it otherwise. They fail with
`PG_RW_URL is not set`, which reads as a code failure and is not one.

Without Postgres running, the integration tests **skip** rather than fail — currently
about a quarter of the suite. A green run that finished in under a second is a run that
tested almost nothing; read the skip count, not just the colour.

Crawl4AI is a 6GB image that runs a browser pool. Start it only when you are actually
exercising the browser path, and `make dev-down` when you stop.

**The dev database now holds a real crawl.** After `v0.16.0` a single run leaves a
few hundred pending frontier rows behind, which is the point — §6 asks for real
crawl snapshots rather than fixtures, and this is one. It also means a bare
`python -m worker.main` with no `MERIDIAN_WORKER_MAX_TASKS` will keep going for a
very long time. That is correct behaviour, not a runaway.

**Running the worker by hand.** `make test` exports `.env.dev`; nothing else does, so
the worker needs it sourced:

```bash
set -a; . ./.env.dev; set +a
MERIDIAN_RAW_ROOT="$PWD/.devdata/raw" MERIDIAN_WORKER_MAX_TASKS=4 uv run python -m worker.main
```

**Set `MERIDIAN_RAW_ROOT` or the worker writes to `/data/raw`,** which is the
container's bind-mount target and almost certainly not writable natively. It will not
crash — a fetch it cannot keep is retried and then failed with `storage_error:` — but
a run where every task retried three times and nothing was stored is this, not a bug.
`.devdata/` is gitignored and is the natural place for it.

Without `MERIDIAN_WORKER_MAX_TASKS` it runs until signalled, which is correct and not
what you want at a prompt. It crawls the real web — the seeded frontier is real
Singapore government sites — so a run leaves real `queue` and `fetch_attempts` rows
behind in the dev database. Reset the statuses afterwards if the next thing you do
depends on the frontier still being `pending`.

To exercise the shutdown path, `timeout -s TERM 5 uv run python -m worker.main` works
— `uv run` forwards the signal — but pipe it to a file rather than to `grep`, or the
signal takes the pipeline with it and you lose the last lines.

---

## 3. Traps that have already cost time

### Alembic does not see CHECK constraints on existing tables

This is `P0-21`, and it was hit again in `P1-21`. Autogenerate will happily detect that
a `constrained()` column needs to be wider and leave its CHECK constraint untouched —
producing a column that accepts the new values and a constraint that rejects every one
of them. Nothing fails until the first row is inserted in production.

Any migration that changes a `constrained(...)` value set must hand-write
`op.drop_constraint(...)` / `op.create_check_constraint(...)`. Use the **bare** name
(`"fetch_outcome"`, not `"ck_fetch_attempts_fetch_outcome"`) — the metadata naming
convention expands it on both create and drop. See
`migrations/versions/*_fetch_outcomes_for_the_content_.py` for the shape.

Test it by inserting through **raw SQL**, not the ORM. SQLAlchemy's
`validate_strings=True` rejects bad values in Python, so an ORM-based rejection test
passes whether or not the database constraint exists at all — which is exactly how
Phase 0 shipped unchecked VARCHAR columns while its tests were green.

### `urllib.robotparser` is version-dependent

Wildcards and longest-match arrived in Python 3.13. This project declares `>=3.12`, and
on 3.12 the stdlib gives the *opposite* answer for both `Disallow: /*.pdf$` and a
longer `Allow` overriding a shorter `Disallow`. That is why `worker/robots.py` exists.
Do not "simplify" it back to the stdlib.

### httpx hands you a whole network read, decoded

`response.aiter_bytes()` yields whatever one 64KB socket read inflates to — measured at
67MB from a gzip bomb, in a single object. Any size cap checked after that has already
lost. `worker/fetch.py` reads `aiter_raw()` and drives `zlib` in bounded steps for this
reason. If you touch `_read_capped`, keep `_Inflater.feed` a **generator**; materialising
its output into a list re-opens the hole.

### TEST-NET addresses are not usable as fake public IPs

`192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24` are classified non-global by
Python's `ipaddress`, so `netguard` correctly refuses them. Tests needing a stand-in for
a public host use real-looking globals (`93.184.216.34`, `104.18.32.7`). A test that
mysteriously returns `unsafe_target` is usually this.

### `httpx.MockTransport` responses are pre-read

`httpx.Response(content=...)` cannot be streamed — `aiter_raw()` raises `StreamConsumed`.
Use `streamed(...)` from `tests/http_doubles.py`, which builds a real `AsyncByteStream`
and lets a test control chunk boundaries, which is where the caps are enforced.

### `caplog` does not work in this suite

`addopts` carries `-p no:logging`, because pytest's logging plugin attaches handlers to
the root logger and interleaves plain-text records into the JSON stream the logging
tests parse. The fixture is gone with the plugin, and asking for it fails deep inside
pytest with a bare `KeyError` on a stash key rather than anything that names the cause.
To assert on a log record, attach a handler to the module's own logger — see
`tests/unit/test_fetch_signals.py`.

### `registrable_domain` keeps subdomains, so a blocklist needs suffix matching

It strips a leading `www.` and nothing else — correctly, because
`datamall.lta.gov.sg` is a different source from `lta.gov.sg` and tiering depends on
telling them apart. But an exact-match blocklist then blocks `facebook.com` and waves
`m.facebook.com` straight through. `Prefilter.is_blocked` matches on suffix.

### A test that seeds a URL must not use `seed_source="frontier"`

It is the default on `enqueue`, so a test seeding a row and then asserting on what
frontier expansion queued cannot tell the two apart. The seed is a hand injection —
`seed_source="user"` — which is also what it actually is.

### An offset recovered by searching for the text cites the wrong copy

The obvious way to attach an offset to a chunk is `text.index(chunk)` after the fact.
It is wrong on any document that repeats a passage — a boilerplate disclaimer, a
repeated table header, a navigation string that survived extraction — because it
resolves to the *first* occurrence and the citation silently points somewhere else.
`chunk_text` threads offsets through every split instead, and works in `(start, end)`
spans rather than substrings so a chunk is always a verbatim slice.

### `zip(xs, xs[1:], strict=True)` always raises

The pairwise idiom is inherently unequal in length, so `strict=True` — which is
otherwise the right default and what ruff's `B905` asks for — turns it into a
guaranteed `ValueError`. Use `itertools.pairwise`. This survived the unit tests and
was caught by a stress input that reached the sentence-splitting fallback.

### `iterlinks()` is not a link list

lxml's `iterlinks()` yields every URL in a document — favicons, stylesheets, scripts,
`apple-touch-icon` at six sizes. On www.lta.gov.sg that was 145 "links", of which 77
were documents. A frontier fed from it spends its budget fetching PNGs.
`extract/html.py` takes `//a/@href | //area/@href` instead.

### trafilatura wants bytes, not a decoded string

It does its own encoding detection, which is the entire point of handing it the raw
response body: a page that declares UTF-8 and serves Latin-1 is common, and decoding
here first turns a recoverable document into replacement characters. `extract_html`
accepts both and passes bytes straight through.

### A dev database that has actually crawled breaks absolute-count assertions

`test_seed_loads_no_content` asserted the content tables were *empty* after seeding,
which was the same thing as "seeding loaded no content" right up until the worker
started writing `sources` rows — and then it began failing on any development database
that had crawled, which is every one worth having (§6 asks for real crawl snapshots
rather than fixtures). It now measures the delta across the seed run. Expect the same
trap in anything that counts `chunks` or `edges` once those stages exist.

### The seeded source-tier map is `{tier: [domains]}`, not `{domain: tier}`

`config/source_tiers.yaml` groups domains *under* a tier — `exact: {government:
[lta.gov.sg, ...]}` — and `resolve_tier` iterates it that way. A test or fixture that
writes `exact[domain] = tier` produces a mapping that parses, resolves to the default
for everything, and fails nothing.

The map lives in the global `fetch_policy` row's `settings` blob, seeded once, and
`resolve_policy` deliberately strips it out because it is not a fetch setting.
`resolve_source_tier()` in `policy.py` is what reads it back.

### SQLAlchemy does not track in-place changes to a JSONB column

`row.extra["k"] = v` and `row.settings["k"] = v` are writes that never reach Postgres.
They fail by doing nothing, which nothing catches — an assertion against the in-memory
object passes, because the in-memory object *did* change. Always reassign the whole
dict (`row.extra = {**row.extra, "k": v}`), and force a real read with
`await sess.refresh(row)` in the test that proves it landed. `sess.expire(row)` is not
the tool: touching an expired attribute in async SQLAlchemy raises `MissingGreenlet`
rather than reloading.

### An integration test that does not filter by topic claims the seeded frontier

This dev database holds the 13 real seeded queue rows, all `pending` and all priority
100. A test that enqueues its own task and then runs anything built on `claim_next`
without a `topics` filter gets one of *those* rows instead, fetches it, and leaves the
test's own task untouched — which reads as "the loop never ran" and is actually the
loop working correctly. Every test in `tests/integration/test_worker_run.py` takes a
`run_topic` fixture for this reason. `test_queueing.py` documents the same trap from
the other side.

### A test asserting on an INFO log passes or fails depending on test order

`configure_logging()` sets the root logger to INFO, and once *any* test has called it
the level sticks for the process. A test that attaches a handler to a module logger
and expects an INFO record therefore passes in a full run and fails on its own, with
nothing to suggest why. Set the level explicitly in the test and restore it — see
`test_housekeeping_prunes_and_logs_the_health_line`. WARNING assertions are unaffected,
which is why `test_fetch_signals.py` never hit this.

### `asyncio_mode = "auto"` makes an explicit `pytestmark` counterproductive

Adding `pytestmark = pytest.mark.asyncio` to a mixed sync/async test module marks the
sync tests too, and pytest warns once per sync test. Auto mode already handles the
async ones; leave the mark off.

### Crawl4AI 0.9.2 binds loopback *inside* its container

Without `CRAWL4AI_API_TOKEN` set, its entrypoint binds gunicorn to `127.0.0.1` inside
the container, so any published port reaches nothing and the failure looks like a
network problem. With the token set, every request needs
`Authorization: Bearer <token>`. Both compose files now set it; `.env.dev` uses `dev`.

---

## 4. What is verified live, and what is only tested

Tests are hermetic by design, so "the tests pass" and "it works against the real web"
are different claims. As of `v0.10.0` the following were confirmed against real servers,
not doubles:

| Behaviour | Evidence |
|---|---|
| Static fetch, pinned to the validated address | example.com, iana.org, lta.gov.sg, arxiv.org |
| SSRF refusal | `http://169.254.169.254/` → `unsafe_target` |
| Plaintext-final refusal | `http://neverssl.com/` → `unsafe_target` |
| Browser path end to end | Crawl4AI 0.9.2, markdown and links carried through |
| `render_js: auto` escalating | excalidraw.com, 32 → 474 visible chars |
| `render_js: auto` *not* escalating | react.dev, vitejs.dev (both pre-rendered) |
| `Crawl-delay` honoured | arxiv.org publishes 15s; observed 15s gaps |
| Conditional request | iana.org returned a real `304`, zero bytes |

And as of `v0.12.0`, with the loop driving instead of a script:

| Behaviour | Evidence |
|---|---|
| Unattended drain of the seeded frontier | 7 tasks claimed, fetched and settled; queue statuses and `fetch_attempts` rows written and committed |
| A 403 abandoned, not retried | unece.org → `http_error` 403 → `failed` at `attempts=1` |
| Health line (§12.5) | `{"message": "health", "queue_depth": {...}, "fetch_success_rate": 0.857, "by_outcome": {...}}` |
| Graceful `SIGTERM` | two fetches in flight both finished and settled; process exited 0 |
| Two lanes not stampeding one host | per-domain `waited_ms` of 1000–1500 across concurrent lanes |

And `v0.13.0`, where the *second* run is the evidence:

| Behaviour | Evidence |
|---|---|
| Raw store, primary sources | 4 `.gov.sg` pages written to `<domain>/<shard>/<sha256>.html`, 956KB |
| Retention split (§5.4) | landtransportguru.net → `informal` → `background` → `"stored": null`, checksum recorded |
| Source tier from the seeded map | `lta.gov.sg` → `government` with no per-test override |
| Conditional requests, finally live | second pass: 4 of 5 returned a real `304`, zero bytes |
| Checksum change detection | the fifth answered `200` with `"content_changed": false` |

And `v0.14.0`:

| Behaviour | Evidence |
|---|---|
| HTML extraction over the seeded frontier | 7 fetched, 6 extracted, 265–987 chars each, titles and dates on every one |
| Metadata-only is a real resting state | www.sae.org → 62 visible chars, a JS shell → `text_available=False`, still a source row |
| Real bibliographic metadata | arxiv.org/abs/2401.02777 → title, `2024-01-05`, abstract, `10.48550/arxiv.2401.02777` |
| A paper does not cite itself | the same arXiv page → `citations == ()` after self-identifier exclusion |

And `v0.15.0`, where the round-trip is the claim worth checking:

| Behaviour | Evidence |
|---|---|
| Chunking in the fetch pass | 7 fetched, 6 chunked, including a `background` source that keeps no raw file |
| Offsets locate their passage | every stored `page_or_offset` re-extracted from the raw file on disk and matched exactly |
| A re-crawl rewrites nothing | 5 real `304`s plus one byte-identical `200` → `chunks: 0` across the run |

And `v0.16.0`, the run where the crawl stopped being a fetcher:

| Behaviour | Evidence |
|---|---|
| Frontier expansion | 13 seeds → 334 pending in one 12-task run, claiming frontier-discovered pages within the same run |
| Tier priority, unwired since `P1-17` | `.edu.sg` queued at 60, `.gov.sg` at 50, blogs below — nobody curated a list |
| Blocklist and shape gates | 45 blocked-domain drops across 8 pages; no social link, shortener or asset URL in the queue |
| Already-seen dedup | one deep `lta.gov.sg` page: 62 links considered, 43 already seen, 12 queued |

**Never confirmed against a real server:** the decompression-ratio cap (tested against a
local socket serving a synthetic bomb) and the 5xx-robots refusal path.

---

## 5. What to build next

`TASKS.md` is authoritative; this is just the reasoning behind the ordering.

**`P1-09` (PDF) and `P1-08` (MarkItDown), because the crawl now outruns what it can
read.** Frontier expansion queues every `.pdf` it finds and the fetcher stores them
faithfully, and nothing turns any of them into text. On a government corpus that is
not an edge case — it is a large share of the substance. The raw files are kept, so
the day the extractor lands §11.12's reprocessing recovers everything already
fetched; until then the corpus is thinner than the queue suggests.

**`P1-23` is now overdue rather than early.** The frontier follows links off
untrusted pages at volume, so the pages reaching extraction are no longer a curated
seed list.

**The remaining extractors: `P1-08` (MarkItDown), `P1-09` (PDF), `P1-10` (figures).**
`Worker._extract` dispatches on `result.media_type` against `HTML_MEDIA_TYPES`; adding
a format is a branch there plus a module under `worker/extract/`. Two constraints
carried from AGENTS.md and §6.6: MarkItDown gets `convert_local()` or
`convert_stream()` on already-fetched bytes, never `convert()` on a URL; and OCR never
runs inline — a scanned PDF is enqueued and the source stays metadata-only. For PDFs,
`page_or_offset` is a **page number** rather than a character offset (§5.3), so
`chunk_text` is the wrong tool there and a page-aware sibling is needed.

**Decide `P1-32` before the first real edges land.** `replace_chunks()` deletes a
source's chunks when its content changes, and `edges.supporting_chunk_ids` is an
array with no foreign key behind it. Nothing is orphaned today because no edges
exist. That stops being true the moment the orchestrator writes one.

**`P1-06` and `P1-28` are both cheap and now have a loop to feed.** The prefilter keeps
already-seen URLs out of the queue; sitemap discovery enqueues the sitemaps
`RobotsRules.sitemaps` already parses and throws away — Wikipedia's robots.txt lists
one today. `P1-28` also needs `HANDLED_TASK_TYPES` in `worker/main.py` extended, or
the `sitemap` rows it enqueues will sit in the queue unclaimed forever.

**`P1-23`'s injection pre-screen lands alongside extraction** rather than after.

**One thing the loop does not do yet:** there is no systemd unit in the repo. §13.4's
`Restart=always` is the supervision the process deliberately does not implement for
itself, and nothing currently provides it.
