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
following its links onward — and all of it with nobody watching. HTML and PDFs are
both read, Office documents too, and every page is screened for prompt injection on
the way past — and as of `v0.20.0` it does all of that from a container image, not
just from a checkout. 929 tests pass with a real Postgres.

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
   .store()     by media     checksum, etag, tier,
   path from    type (§6.6): raw path, title, date,
   the URL,     html.py or   language, doi,
   primary      pdf.py       text_available
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

**What does not exist yet.** No embeddings, no search, no API, no frontend. HTML,
PDFs and OOXML Office documents are read; `.doc`, `.xls` and EPub are deliberately
outside the converter allowlist and stay metadata-only. Scanned PDFs are detected and filed in `enrichment_queue`, and **nothing
ever runs that queue** — §6.6 makes OCR explicitly user-triggered, so the rows sit
there until a UI exists to spend against them. The frontier is the link half of
`P5-01` only: no citation-driven seeding, no spaCy NER, no TF-IDF.

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

**`pdftotext` and `pdfinfo` must be on PATH** (Fedora: `poppler-utils`). They are
the PDF extractor (`P1-09`), and their absence raises rather than degrading — a
worker that had quietly lost poppler would store every PDF and extract none of them.
The PDF *tests* additionally need `ghostscript` for `ps2pdf`, and skip without it.

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
real government sites — so a run leaves real `queue` and `fetch_attempts` rows
behind in the dev database. Reset the statuses afterwards if the next thing you do
depends on the frontier still being `pending`.

To exercise the shutdown path, `timeout -s TERM 5 uv run python -m worker.main` works
— `uv run` forwards the signal — but pipe it to a file rather than to `grep`, or the
signal takes the pipeline with it and you lose the last lines.

---

### The compose networks are a boundary, not organisation

`internal` is `internal: true`, so Docker installs no default route and nothing
on it reaches the open web. `egress` is an ordinary bridge. `worker` is the only
service on both, because it is the only one that fetches hostile content and
writes it to the database.

`crawl4ai` is on `egress` alone and that is the point of the whole split: it
drives a real browser against pages this crawler found by following links, so it
is the most likely thing in the stack to be compromised, and it has no route to
Postgres by construction rather than by policy.

It also receives no `env_file`. That is why `x-common` carries neither
`env_file` nor `networks` — a shared default for either is precisely how the
browser sandbox came to hold every database password in `.env`, which it did
until `v0.24.0`. `tests/unit/test_compose_topology.py` fails if any of this is
undone.

Note `internal: true` is not `P1-25`. It stops a container reaching the
*internet*; it does not stop `worker`, which must have a default route, from
reaching the LAN.

### A missing browser is silent, so the health line has to say so

`Crawl4aiClient.from_env()` returns None when `CRAWL4AI_URL` is unset and the
fetcher degrades to static — correct, and invisible. The health line carries
`browser: configured | unreachable | absent`, and `unreachable` logs at WARNING,
because a worker that lost its browser a week ago otherwise looks exactly like
one that never had it and simply extracts worse.

`/health` is unauthenticated on 0.9.2: a wrong token still returns 200, while
`/schema` and `/crawl` refuse. The probe sends the token anyway.

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

### A Cloudflare *managed* challenge is not beatable, and it is worth knowing why

One of the cold-start seed domains returns 403 on every URL including
`/robots.txt` and `/`. It is behind Cloudflare, and the response carries
`cf-mitigated: challenge`.

This was tested properly rather than assumed, because the obvious hypothesis
(wait for the JS challenge to auto-solve) is *usually right*. It is not here:
`UndetectedAdapter` + `enable_stealth` + `magic` + `simulate_user` +
`override_navigator`, run natively inside the crawl4ai container with no API
restrictions, with waits of 28s and 41s across `networkidle` and `load`, still
returns 403 with `just a moment`, `cf-chl` and `turnstile` in the body.

The distinguishing signal is the status code over time. An auto-solving JS
challenge serves 503 and then 200 within about five seconds. An interactive
Turnstile serves 403 and stays there. Only the first is worth waiting for.

Note also that the Docker REST API forbids `proxy_config`, `magic`,
`simulate_user` and `override_navigator` from untrusted bodies (0.9.x
hardening), but that is *not* what blocks this — the same flags set natively
fail identically. Do not spend a day building a custom image to route around
the API restriction expecting a different answer.

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

### A workspace package must declare its own dependencies, or only the container finds out

`meridian_core.policy` imports `yaml`, and `pyyaml` was declared on the *root*
project. Development never noticed, because the root install provides it to
everything. A container built with `uv sync --package meridian-worker` gets
`meridian_core` and nothing the root happens to also depend on, and died on
`ModuleNotFoundError` at import. Building the image did not catch it; *running* it
did. Worth remembering when `services/api` and `services/orchestrator` get images.

### MarkItDown's declared media type is a hint, not a gate

It runs magika over the bytes and then tries *every* converter that accepts any
guess, plus a final pass where converters see no type at all. So
`application/vnd.ms-excel` with `b"x"` converts as plain text, and a `.docx` labelled
`application/epub+zip` converts as a `.docx`. The gate has to be ours.

That matters because the default registry is not something to point at untrusted
bytes: it contains converters that fetch URLs (YouTube, Wikipedia, Bing), shell out
to `exiftool`, and a `ZipConverter` that extracts an archive to a temp directory and
re-dispatches its members by extension — and a `.docx` *is* a zip, so hostile input
reaches that path through sniffing however the `Content-Type` was set.
`extract/document.py` uses `enable_builtins=False` plus four explicitly registered
converters, and two tests fail if that is widened.

### An injection flag that fires on every article about injection protects nothing

`P1-23`'s hardest constraint is the false positive, not the false negative. A
research corpus about AI legitimately quotes "ignore all previous instructions" — in
an article about prompt injection, exactly the kind of source this system should be
reading — and a flag that fires on those is one someone learns to ignore. The rule
`worker/extract/injection.py` turns on is that **hiddenness** promotes a finding from
noise to signal: visible imperative phrasing is recorded and not escalated; the same
words in a `display:none` div are. Measured at 0 flagged across the 11 real
government pages crawled so far.

Two exceptions worth knowing before changing it: `tool_directive` ("add an edge",
"send the contents to") *is* suspicious when visible, and `markitdown`'s Python 3.14
problem below is unrelated but sits in the same module tree.

### `markitdown` needs an explicit `onnxruntime>=1.29` on Python 3.14

`markitdown` pins `magika~=0.6.1`, which requires `onnxruntime>=1.17.0` with no upper
bound — and uv resolves that to 1.20.1, which has no cp314 wheel. The install fails
with a message about Python ABI tags that does not name markitdown at all. Adding
`onnxruntime>=1.29` as a direct worker dependency fixes it; magika 1.x drops the
requirement entirely on ≥3.13, so this can come out when markitdown loosens its pin.

### A scanned PDF is not an empty one, and the difference is invisible

Run a scan through a text extractor and you get a page number and a running header —
which clears no threshold and reads exactly like a page with no content. Without
§6.6's chars-per-page check, a scanned planning report enters the corpus as
"extracted, nothing found" and nobody ever looks again. `extract_pdf` sets
`needs_ocr` instead, and drops the stray text layer rather than admitting it as
content.

### `%%Title` in PostScript never reaches the PDF

It is a DSC comment for the print spooler. Ghostscript writes the Info dictionary
from a `pdfmark` — `[ /Title (…) /DOCINFO pdfmark` — and `ps2pdf` has no
`-dDOCINFO=` flag despite it looking like it should. Relevant when building real
PDFs for tests, which is worth doing: a byte string starting with `%PDF-` exercises
the error path and nothing else.

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
| A 403 abandoned, not retried | a challenged domain → `http_error` 403 → `failed` at `attempts=1` |
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

And `v0.17.0`, against real government PDFs the crawl had queued for itself:

| Behaviour | Evidence |
|---|---|
| PDF native-text extraction | `lta.gov.sg/.../MTM.pdf` → 1,367 chars across 2 pages |
| Page-accurate chunks (§5.3) | `chunks.page_or_offset` = 1, 2 — the page a citation opens at |
| Title from the PDF's own Info dictionary | Land Transport ITM → `"20230301 Land Transport ITM e1"` |
| robots.txt still honoured on PDFs | two datamall user guides → `robots_denied`, abandoned |

And `v0.18.0`, where the number that matters is the one that stayed at zero:

| Behaviour | Evidence |
|---|---|
| Injection screen on real pages | 11 crawled government pages → 9 clean, 2 `hidden_text` noted, **0 flagged** |
| Hidden instructions caught | `display:none`, `hidden`, `aria-hidden`, white-on-white, offscreen, zero-size all detected in tests |
| An article *about* injection not caught | visible "ignore all previous instructions" → recorded, not suspicious |

And `v0.19.0`:

| Behaviour | Evidence |
|---|---|
| Office extraction | a real `.xlsx` built with xlsxwriter → text, chunks, character offsets |
| The converter allowlist holds | a plain zip and an HTML page mislabelled `.docx` → `markitdown-failed`, not converted |
| OOXML metadata is bomb-proof | a 200MB decompression bomb in `docProps/core.xml` → refused with an honest header *and* a forged one, 0MB RSS |

And `v0.20.0`, from inside the container rather than from a checkout:

| Behaviour | Evidence |
|---|---|
| The worker image runs the whole pipeline | `--read-only --cap-drop ALL`, unprivileged: 3 pages fetched, stored, extracted, chunked, 175 links queued |
| poppler is present and found | `HEALTHCHECK` and `pdf.available()` both true inside the image |
| The raw store works through a bind mount | files written to the host through the container's uid |

**Never confirmed against a real server:** the decompression-ratio cap (tested against a
local socket serving a synthetic bomb) and the 5xx-robots refusal path.

---

## 5. What to build next

`TASKS.md` is authoritative; this is just the reasoning behind the ordering.

**`P1-22` and `P1-26` before `P1-16`.** The worker image exists and runs, but the
*stack* does not: `docker-compose.yml` still admits in a comment that `internal: true`
blocks the outbound access worker, crawl4ai and searxng all need, and Crawl4AI has no
image and no health check the worker trusts. The 48-hour acceptance run needs both.

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
