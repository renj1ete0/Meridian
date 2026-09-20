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

**`v0.76.2`. 1990 backend tests against a real Postgres, 296 frontend.**

Phase 0 is closed. Phase 1's fetch path is complete and running. Phase 2 is
complete except its human checkpoint: the corpus is searchable over HTTP, through
a UI, and through MCP. Phase 3's read surface is built and waits only on a
Cloudflare account. **Phase 4 does not exist at all** — there is no graph, no
orchestrator, and nothing has ever written an edge.

The single thing standing between here and phase 2's go/no-go is `P1-16`: the
48-hour unattended run. It has not happened.

### The four processes

§6.1 draws the fast loop as one pipeline. It is not one, and holding the split in
your head explains most of the operational surprises:

```
worker.main       fetch → extract → chunk          24/7, no model, no vectors
worker.embed      embedding IS NULL → vector       needs the model (or the sidecar)
worker.novelty    novelty_checked_at IS NULL       Postgres and arithmetic only
worker.scheduler  the timetable in `scheduled_jobs` spawns the above as subprocesses
```

Each queue is a predicate on a column, so each pass is resumable with no state
outside the table, and any of them can lag the others without anything breaking.
What it costs is a window where a chunk exists, is not searchable, and is not yet
known to be a duplicate.

Three more passes are on demand rather than on the loop:

```
worker.sweep      retention report; deletes only with --apply
worker.harvest    §5.6 acronym definitions → gazetteer, unapproved
worker.retopic    topic labels onto sources crawled before P2-14
```

### What exists that the older version of this document said did not

- **Retrieval.** `meridian_core/search.py` fuses a lexical arm (tsvector/GIN) and
  a vector arm (pgvector HNSW) with RRF. `_conditions()` is the single filter
  source for both arms, deliberately — two filter sites is how one arm silently
  returns material the caller excluded.
- **An API.** `/api/explore/*` on the read-only role, `/api/admin/*` on the
  read-write one. The prefix *is* the role boundary (§12.6) and a test asserts
  nothing under `/api/explore` accepts a write.
- **A UI that renders data.** Explore with search, a topic filter, the corpus
  counts, a since-last-visit delta, saved views, notifications and the reader's
  own notes; a source page at `/sources/{id}` with passages, figures, both
  exports and a note composer; a node panel at `/nodes/{id}`; and Admin with
  three screens (gazetteer, topics, domains).
- **Annotation** (`P6-05`), which is the only thing in the graph tables that has
  rows on a fresh install — a note is an `entities` row somebody wrote by hand.
  It is also the only write in the system whose author is assigned rather than
  declared — see the exceptions below.
- **An MCP read surface**, mounted on the same app, same read-only role, same
  provenance. Scoped tokens, a statement-timeout SQL escape hatch on a separate
  `meridian_guest` role, and Access JWT verification.
- **An embedding sidecar**, and as of `P2-19` the backfill uses it too, so a
  stack running both holds one copy of the weights rather than two.

### What still does not exist

- **The graph.** `entities`, `edges`, `observations` and `attribute_values` are
  tables with DTOs, drift tests and provenance rules, and nothing *derives* a
  row into them. `P6-05`'s annotations are the one exception and are written by
  hand, so "no edges exist" is now "no edge was produced by a model". Apache AGE
  (`P4-01`) is not installed; it does support PG17 (v1.6.0), so it is not blocked
  on a Postgres downgrade, only on not swapping the image mid-deploy.
- **Any LLM call.** The orchestrator does not exist. Nothing in this repository
  has ever called a model that generates text. `P4-05`'s `validation.py` is the
  part that is ready for one: every guard §11.8 asks for, tested, with no write
  tool yet calling them.
- **spaCy NER.** `P5-02` built the gazetteer and the `EntityRuler` patterns, and
  spaCy is an optional extra (`uv sync --extra ner`) that the worker image does
  not carry. `P5-01`'s frontier NER and TF-IDF are unbuilt.
- **OCR.** Scanned PDFs are detected and filed in `enrichment_queue`, and nothing
  ever runs that queue — §6.6 makes OCR user-triggered, so the rows wait for a UI
  to spend against them.
- **Anything that needs a *derived* node to exist.** The node panel (`P6-04`) is
  built, tested and reachable at `/nodes/{id}`, and the pipeline has put nothing
  in it. Likewise a saved view's `focus_entity_id`. Both were built ahead of the
  graph deliberately — their hard parts are about how a claim is presented, and
  those do not get easier by waiting for rows — but do not mistake "the screen
  exists" for "the feature works end to end".

  `P6-05` is the exception that proves it: annotations *are* entity rows, so a
  reader can fill `/nodes/{id}` with their own notes today. That is a real
  end-to-end path, and it is not the graph.

### The shape of the read path

```
GET /api/explore/search ──► paged_search (api/search_service.py)
                             │
                             ├─► RemoteEmbedder.embed_one()  ── sidecar, or absent
                             │                                   (absent ⇒ degraded)
                             ▼
                        search() (meridian_core/search.py)
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
        _lexical()                     _vector()
        websearch_to_tsquery           embedding <=> query
        ts_rank_cd                     HNSW, vector_cosine_ops
              └──────────────┬──────────────┘
                             ▼
                      rrf() fusion → hydrate → SearchHit
```

Both arms narrow through `_conditions()`, which always excludes superseded
chunks (`P1-32`) and, unless asked otherwise, near-duplicates and junk-tier
sources.

### Conventions that are load-bearing and easy to miss

- **Nothing is deleted.** A re-crawl supersedes chunks rather than deleting them
  (`P1-32`); a rejected gazetteer term keeps its row as a tombstone (`P6-13`); an
  archived topic keeps its weight (`P6-12`); a retention sweep reports and only
  deletes with `--apply`. The pattern is consistent and each instance has a
  different reason — follow the citation in the code.
- **NULL and empty are different facts** in at least three places:
  `sources.topic_labels`, `chunks.novelty_checked_at`, `sources.acronyms_harvested_at`.
  NULL means no pass has looked; empty means one looked and found nothing. Each
  distinction is what makes a backfill queue finite.
- **Admin fails closed.** `/api/admin/*` refuses everything with 503 unless
  Cloudflare Access is configured or `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` is set.
- **Run `uv lock` in the same commit as a version bump**, or the Docker build
  breaks. See §3.

### Three exceptions, all deliberate, all easy to mistake for bugs

- **`GET /api/explore/nodes/{id}` returns superseded chunks**, and it is the only
  read path that does. Everywhere else a superseded chunk is text the page no
  longer carries; there it is the text an attribute was *derived from*, and §2.4
  re-derives from source chunks — so the citation has to resolve even after the
  page changed.
- **Saved views read on `/api/explore` and write on `/api/admin`.** §12.6 splits
  by mutation, not by audience, and here that is the useful split: views are
  shared state with no per-viewer scoping, so a guest opens the owner's and
  cannot add to them. Saving therefore needs Access configured, or the
  anonymous opt-out.
- **Annotations take the same split, and `produced_by` is not an input.** Notes
  read on `/api/explore/annotations` and are written under `/api/admin`, for a
  sharper version of the same reason: a note is the one thing here that reads as
  the owner's own thinking, which makes it the worst thing on this system to be
  able to forge. So `AnnotationCreate` has **no** `produced_by` field and
  forbids extra keys — `meridian_core/annotations.py` assigns the reserved
  `human` id and nothing else can. `scripts/seed.py` refuses to register an
  agent under that id for the same reason, and that refusal will look like an
  over-zealous validation until you know why it is there.

  Two consequences that look wrong and are not. A note's `quality_tier` and
  `model` are **null**, because §11.12's tier is an ordinal over models and a
  person is not on that scale — "quality tier only moves up" must not become a
  rule about a person. And a note's `annotates` edges carry the note's own
  `supporting_chunk_ids`, duplicating what the node already holds; the node's
  copy is the source of truth (a note with no target has no edges at all), and
  the duplicate exists because *every* edge here names the chunks behind it.
  They cannot drift — `annotations.py` is the only writer and rewrites the edges
  from the note on every change.

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
635 of 1910, a third of the suite. A green run that finished in a few seconds is a run
that tested almost nothing; read the skip count, not just the colour.

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

**Running the read surface by hand.** Same environment, two more processes:

```bash
set -a; . ./.env.dev; set +a
uv run uvicorn api.main:app --reload --port 21114   # API, Admin and /mcp
cd web && npm run dev                               # UI on :21115, proxying to the API
```

The UI reaches the API through Vite's dev proxy, so there is no base URL anywhere in
`web/` and nothing to configure. If the API is not running, Explore says the corpus
counts are unavailable rather than rendering zeros — which is the distinction
`CorpusCounts` exists to preserve, not a bug.

**A stale uvicorn on :21114 is the trap here.** It serves old code and every new route
404s, which reads exactly like a route that was never registered. Kill it before
assuming the mount is wrong.

**Admin needs `MERIDIAN_ADMIN_ALLOW_ANONYMOUS=true` in `.env.dev`,** or every
`/api/admin/*` route answers 503. That is the intended behaviour on an exposed
instance without Cloudflare Access (`P6-13`), and it is a confusing five minutes
locally if nobody told you. The 503 body names both fixes.

**The optional extras.** `uv sync --extra ner` installs spaCy for `P5-02`'s
`EntityRuler`; without it the pattern tests still run — the compiler lives in
`meridian_core` and needs nothing — and the tests that drive the real matcher
skip. `uv sync --extra embed` is the 2.3GB model. Neither is in the worker image
by default.

**`uv run` re-locks; `uv run --no-sync` does not.** Worth knowing while iterating
on a `pyproject.toml`, and worth *not* relying on: the lock must be committed with
the version bump either way (§3).

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

### The raw store can span more than one root, and nothing records which

`sources.raw_file_path` is relative. To what is not written down anywhere, so a
corpus is only interpretable if you already know the `MERIDIAN_RAW_ROOT` each
row was written under.

On this machine three sources dangle against `.devdata/raw` and their files are
in `.devdata/containerraw`, put there by `P1-30`'s containerised verification
writing to its own bind mount. Nothing is lost and nothing is broken; the
corpus simply has two roots and no way to say so.

Consequences, before assuming a missing file is a missing file:

- `python -m worker.sweep`'s dangling arm lists every source whose file is under
  a *different* root, so on a multi-root corpus it is noisy rather than wrong.
- `make snapshot-corpus` tars one root. A snapshot taken here today omits those
  three files silently — `restore_corpus.sh` samples `raw_file_path` after
  restoring and is the only thing that would say so.

`P1-45`. Check the root before concluding anything.

### The browser and static extraction paths were not equivalent

Until `v0.31.0` the browser path used Crawl4AI's `fit_markdown` directly while
the static path ran trafilatura at `favor_precision`. Those are very different
filters, so the same page could keep or lose its navigation depending on whether
the fetcher escalated it to a browser — and that decision is made on how much
visible text the *static* fetch found, which has nothing to do with how much
chrome the page carries.

The premise in the docstring was that re-extracting locally would throw away a
filter that saw a rendered DOM. It is worth knowing why that was wrong, because
the same reasoning is tempting anywhere a sidecar returns processed output:
**the rendered HTML comes back in the same response** (`fetch.py` stores it as
the body), so the local extractor was never working from less than the filter
was. Check what the sidecar already handed you before deciding it knows
something you cannot.

Two consequences worth holding on to:

- Boilerplate does not just add noise. It becomes entities and entities become
  edges, and it **inflates the novelty gate's duplicate count** — every page on
  a site repeats the same chrome, so real pages get marked as duplicates of each
  other's furniture.
- Precision filtering drops DOIs written in stripped regions. Links survive
  (they come from the DOM, not the text) and a page's own identifier survives
  (meta tags), but a bare DOI in a sidebar does not.

### `extra={"module": ...}` raises at runtime

`logging` refuses to let an `extra` key shadow a `LogRecord` attribute, and it
raises rather than dropping the key. `module` is one; so are `name`, `args`,
`filename`, `levelname`, `process`, `message`, `lineno` and `funcName`.

It fails **only on the line that logs it**, so a scheduler that logged
`{"job": ..., "module": ...}` started fine, claimed a job fine, and died the
moment it tried to say which module it was about to run. The traceback points at
`logging/__init__.py` and names the key, which is the one mercy.

Prefix them — `job_module`, not `module`.

### Two ways a benchmark lies on a small corpus

Both were live in `scripts/benchmark_search.py` before its own output exposed
them, and both have the same shape: a number that is produced by arithmetic
rather than by the thing being measured.

- **ANN recall is 1.0 when the index is not used.** Below a few thousand
  vectors the planner prefers a sequential scan, which is exact by definition,
  so "approximate" and "exact" are the same query and recall is perfect. It
  looks like a flawless index. Always check `EXPLAIN` for the index name before
  believing a recall figure.
- **Arm agreement is 100% when the corpus is smaller than the candidate pool.**
  The vector arm takes 100 neighbours; with 26 searchable chunks it returns all
  of them, so every lexical hit is necessarily also a vector hit. That reads as
  "fusion is buying nothing", which is a strong conclusion drawn from a corpus
  that cannot support one.

### pgvector values need pgvector's type on the way in *and* out

Two separate traps, an hour apart:

- Selecting `embedding` through a raw `text()` query returns its **text
  representation** — asyncpg has no reason to know the type — and `list()` of
  that string is a list of single characters. Select through the mapped column.
- Binding a vector as a plain string into `CAST(:v AS vector)` fails the same
  way from the other direction. Use `bindparam(..., type_=Vector(DIM))`.

Neither fails where the mistake is. Both surface as
`could not convert string to float: '['` at the next bind, several frames away.

### `websearch_to_tsquery` needs a `regconfig`, not a string

Binding the configuration name as a parameter produces
`websearch_to_tsquery(varchar, varchar)`, which does not exist — there is no
implicit cast from varchar to regconfig. The error is "function does not exist",
which reads as a missing extension rather than a type problem. `cast(TS_CONFIG,
REGCONFIG)` is the fix.

### `alembic check` cannot see a generated column's expression

`P2-05` added `chunks.search_vector` as `GENERATED ALWAYS AS
(to_tsvector('english', text)) STORED`. Autogenerate warns

```
UserWarning: Computed default on chunks.search_vector cannot be modified
```

and moves on. So model-versus-database drift — the one thing `alembic check`
normally guards, and the reason `make migrate` is trusted — is exactly what it
does **not** guard for this column. A model changed to a different text-search
configuration with no migration behind it passes `alembic check` cleanly and
leaves the corpus indexed under the old one, with no error at any point.

`tests/integration/test_search_index.py::test_generation_expression_matches_the_model`
is the replacement: it reads `information_schema.columns.generation_expression`
and compares it to the model's `Computed.sqltext`, normalising what the parser
adds (`'english'` comes back as `'english'::regconfig`).

Two other things about that column worth not rediscovering:

- **The regconfig must be a literal.** `to_tsvector(text)` resolves through
  `default_text_search_config`, which is a session GUC, so the one-argument form
  is not IMMUTABLE and Postgres refuses it in a generated column outright.
- **`attgenerated` comes back as bytes.** It is Postgres's internal `"char"`
  type, so `attgenerated == "s"` is False and `attgenerated == b"s"` is True.
  Cast it in SQL rather than comparing in Python.

### `make build-push` needs a buildx builder that is not the default one

Four Makefile targets once pointed at scripts nobody had written, and
`snapshot-corpus` was the one that mattered — `P1-16`'s deliverable is literally
"48h unattended acceptance run → `make snapshot-corpus`", so the run's output was
a target that failed at the shell. `P1-36` wrote that one, plus `restore-corpus`
and `backup`.

`make build-push` landed in `P1-37` and has one prerequisite that is not
obvious. A multi-platform build needs a buildx builder using the
`docker-container` driver; the **default `docker` driver cannot produce a
manifest list at all**, and the error it gives when asked for two platforms —
"docker exporter does not currently support exporting manifest lists" — names
neither the cause nor the fix. Once:

```bash
docker buildx create --name meridian --driver docker-container --use
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

The second line registers the QEMU handler that lets an x86 box emit arm64
layers. The script checks the driver and prints both commands rather than
letting Docker's message stand.

Two behaviours of the script that look like obstruction and are not: it
**refuses to push from a dirty working tree**, and it never tags `latest`.
Both protect the same thing — scaffold §5 pins the image SHA in compose so a
bad build does not roll out on the next restart and rollback is a one-line
edit, and a tag naming a commit whose code is not what was built turns rollback
into a guess that is only found to be wrong while rolling back. Use
`--dry-run` to see the commands without either check stopping you.

`orchestrator` and `web` have no Dockerfile yet, so the script skips them and
says so. `docker compose build` on the server remains the way round all of this
for a first deploy, and [deployment.md](deployment.md) §6 has both paths.

### A value used in code but absent from the enum fails at the insert, not at import

`P1-28` shipped a sitemap handler that passed `seed_source="sitemap"` to
`enqueue()`, and that value was in neither the model's `constrained()` set nor
the database's CHECK. Every sitemap it fetched parsed cleanly, and then raised
`LookupError` on the insert — caught by the lane's outer handler, filed as
"task failed unexpectedly", and queueing nothing. The fetch succeeded, the parse
succeeded, `fetch_attempts` recorded a 200, and the log line said the sitemap
had been read. **The feature had never worked, and nothing said so.**

This is the `P0-21` trap in its worst form. `P0-21` is "the model was widened
and the migration was not"; drift tests catch that, because there are two
sources of truth to compare. Here there was only one place the value existed —
the code that used it — and nothing compares a string literal against an enum.

What actually catches it is an integration test that drives the feature to a
**committed row**. `tests/unit/test_sitemaps.py` covers the parser exhaustively
and passed throughout, because the parser was never the problem. So: for any
handler that ends in a write, the test that matters is the one that reads the
row back, and it belongs in `test_worker_run.py` rather than beside the unit
tests for the parsing.

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

### An empty table hides a migration that would fail on the server

`alembic upgrade head` passing locally proves nothing about a data migration
when the table is empty, and most of this schema's tables are. `B-10` changed
`figures.linked_entity_ids` from `json` to `bigint[]`; autogenerate emitted a
bare `ALTER COLUMN ... TYPE`, which ran clean against zero rows and would have
failed on the first deployment that had crawled a PDF with a figure in it —
Postgres has no implicit cast between those types.

**Insert rows on purpose before running a data migration**, covering the cases
the column actually holds — here NULL, `[]`, and two JSON spellings of the same
list — then migrate, check the values, downgrade, and check them again. It takes
two minutes and it is the only thing that distinguishes "the migration ran" from
"the migration is correct".

Two Postgres specifics that cost time in that one:

- **`USING` cannot contain a subquery** ("cannot use subquery in transform
  expression"), so anything needing `jsonb_array_elements_text` aggregated back
  into an array cannot be done as a type change. Add a column, `UPDATE` it, drop
  the old one, rename — an `UPDATE` may contain a subquery.
- **`ARRAY(SELECT ...)` over a NULL input yields `{}`, not NULL.** If the column
  distinguishes "nothing recorded" from "recorded as empty", the `UPDATE` needs
  `WHERE col IS NOT NULL` or the distinction is silently collapsed.

### A test whose clock is fixed and whose rows' clock is not expires on a date

`tests/integration/test_alerts.py` pins `NOW` to a literal instant, which is
right: a window test that used the wall clock would measure a different window
every run. But `record_alert` takes `created_at` from the *database* clock, so
a test that wrote a row and then asked about it "48 hours later" was comparing
a fixed `NOW` against a timestamp that kept moving. It passed for two days and
then went red with nothing changed, which is the worst possible shape for a
failure: the blame lands on whatever was committed that morning.

The rule, and `attempts()` in that file had followed it from the start: **a test
about a window must own both ends of it.** If the code under test derives one
end from a clock you did not set, set it yourself afterwards
(`row.created_at = when`) rather than assuming the two clocks stay close.

Worth checking the same way: anything calling `func.now()` or a `server_default`
timestamp and then asserting against a literal date. Grep for `dt.datetime(20`
in the suite — each one is a fixed end of some window, and the question is
always what the other end is.

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

### `uv.lock` goes stale on a version bump, and only the Docker build says so

The lock records all four workspace versions. Both Dockerfiles use
`uv sync --frozen`, which refuses when the lock disagrees with the pyprojects — so
a release that bumps `VERSION` without `uv lock` leaves the images unbuildable.
Nothing local notices, because `uv run` re-locks in place. It had been stale
across a dozen commits before anyone looked. `tests/unit/test_lockfile.py` now
catches it; **run `uv lock` in the same commit as the bump.**

### Walking `app.routes` finds nothing in FastAPI 0.141

`include_router` stores an `_IncludedRouter`, which exposes neither `path` nor
`routes`. A test that walks `app.routes` looking for `/api/...` paths therefore
finds none and asserts an empty list against an empty list — passing, forever,
for the wrong reason. The handles are on `route.original_router.routes`. Any test
that enumerates routes should first assert it found a known one.

### Staging a whole file commits whatever else is in it

Twice now: `git add <path>` on a file that had accumulated two separate changes
put an unrelated addition into a fix commit whose message said nothing about it.
The rule in AGENTS.md is one task per commit, and the way it is broken is never
`git add -A` — it is a single path that happens to hold more than one thing.
Check `git show --stat` against the message before moving on.

### `TimestampMixin` indexes `created_at`, and a migration that forgets it fails

Every table using the mixin gets `ix_<table>_created_at`. A hand-written
`create_table` that adds the column and not the index passes its own tests and
fails `alembic check` — which is `test_migrations_match_the_models`, so it shows
up as one unrelated-looking integration failure. The index is not decoration; add
it in the migration.

### `constrained()` is a VARCHAR with a CHECK, not a native enum

So there is no Postgres enum type to drop in a `downgrade`. Writing
`sa.Enum(name=...).drop(...)` looks right, does nothing, and suggests to the next
reader that these are native enums.

### SQLAlchemy cannot negate a `text()` fragment

`~sql_text("EXISTS (...)")` raises an assertion deep inside `elements.py` rather
than producing `NOT EXISTS`. Write the negation into the SQL string. It fails
loudly and immediately, which is the good case — the bad version of this bug is
a clause that silently matches everything.

### Apache AGE cost four separate failures to install, none of them obvious

`P4-01` put AGE into the database. Every step failed first, and each failure
named something nobody had written, so they are all here.

**`shared_preload_libraries` in the image is only half the answer.** The
Dockerfile appends it to `postgresql.conf.sample` — and a *sample* is read only
by `initdb`. An existing data directory never sees it, silently, and every
Cypher call then fails with `unhandled cypher(cstring) function call`. The
compose files pass `-c shared_preload_libraries=age`, which works for both a
fresh database and one that predates AGE.

**Do not name the graph after the project.** `create_graph` creates a Postgres
*schema* of that name. Called `meridian`, it collides with the `meridian` role,
so `"$user"` in the default `search_path` resolves to it — the graph silently
becomes the default schema, `alembic check` proposes dropping AGE's internal
tables, and a later `CREATE TABLE` with no schema would put an application
table inside the graph. It is called `graph`.

**`create_graph` needs `ag_catalog` on the search path**, not merely
schema-qualifying. The label tables it creates reference `graphid_ops`
unqualified, so without it you get `operator class "graphid_ops" does not exist
for access method "btree"`. `SET LOCAL` inside the migration's transaction is
enough and leaves nothing behind.

**The writer must *own* the graph, not be granted on it.** AGE creates a table
per label on first use and attaches it with `ALTER TABLE ... INHERIT`, which
Postgres permits only to the parent's owner. `GRANT ALL` is not enough: the
first edge a model writes fails with `must be owner of table _ag_label_vertex`.
Ownership of the schema and the two base tables goes to `meridian_rw`.

And one for anything that sends Cypher through SQLAlchemy: **`:Label` collides
with `:param`.** `text()` parses `(:Finding)` as a bind parameter named
`Finding` and refuses to run without a value for it. Use `exec_driver_sql`.

### Absent is refused, and the codebase now says so in four places

A pattern worth naming because it recurs and because the wrong version of it is
always the friendlier one. `reserve_seeds` refused a `None` cap before anything
could supply one; `budget.py` (`P4-10`) makes the same choice for tokens and
the monthly ceiling, and nothing seeds a default budget; `trust.py` (`P4-14`)
admits `cleared` rather than excluding `quarantined`; `P4-12` treats a NULL
`seed_allowed` as undecided rather than permitted.

In every case the ergonomic default — unlimited, allowed, unexamined-is-fine —
is the shape in which forgetting to configure something becomes an incident,
and the loop is unattended so the first signal is a bill or a leak rather than
a log line.

The exception proves the rule and is worth knowing: **`within_rate_limit`
treats `None` as no limit**, deliberately. A rate limit throttles something
already authorised, so a token issued without one is a decision somebody made.
A budget with no cap is a decision nobody made. The two look identical in code
and are opposite in meaning.

### An unset cap and a wrong cap fail in different directions

Three places now refuse to widen access on a mistake, and the reasoning is the
same each time. `tiers_allowed` returns *nothing* for an unrecognised
`max_source_tier`, because reading "unknown ceiling" as "no ceiling" turns a
typo in a grant into a widening. `ResolvedGrant.tools` returns an empty set for
an unknown profile, so a profile added by a later migration fails closed.
`half_life_for` gives an unknown source tier the default decay rather than
exemption, because exemption is the valuable state and should be granted
deliberately.

The general form: when a lookup misses, ask which way the mistake fails, and
pick the direction that does not quietly grant more than somebody intended.

### Compose does not skip a service it cannot build

It fails the whole command. A `build:` pointing at a Dockerfile nobody has
written gives `lstat ...: no such file or directory` and nothing starts — so
`web` before `B-05`, and `orchestrator` until `B-18`, each meant
`docker compose up -d` could not bring up the production stack at all.

The fix for a service that genuinely does not exist yet is `profiles:`, not
silence: `up` ignores a profiled service, and naming the profile says out loud
that this is declared ahead of its image. `orchestrator` carries `phase4` for
exactly as long as `services/orchestrator/Dockerfile` is missing.

The neighbouring one, found at the same time: `ports:` on a service attached
only to `internal: true` networks publishes **nothing**, and is not an error.
`api` carried `127.0.0.1:21114:8000` with a comment calling it loopback-only,
which is worse than having no line at all — it tells the next person to curl
21114 on the server and read the silence as an API that is down. Verified
directly rather than assumed: a container on an internal-only network with a
published port refuses the connection.

Both are asserted now, over every compose file rather than the two services
that happened to be wrong.

### The first two commands in the deploy runbook could not work

`docs/setup.md` and `docs/deployment.md` both said

```bash
docker compose run --rm worker alembic upgrade head
docker compose run --rm worker python scripts/seed.py
```

and the worker image has neither. `alembic` is in the root project's `dev` group
and every application image syncs `--no-dev`; the worker Dockerfile copies
`packages/`, `services/` and `config/`, never `scripts/`. These are the first
commands an operator runs on a new server, and nobody had run them there.

Documentation rots quietly because nothing executes it, which is the general
lesson. `tests/unit/test_documented_commands.py` now resolves every
`docker compose run` in those two files to the Dockerfile that builds the
service and checks the invoked thing is in it. It cannot tell you the command
*succeeds* — only that it is not missing — and that is still most of the value.

### Docker creates a bind-mount source as root, and nothing here runs as root

The worst of the four found by first running the stack in containers. Every
application image creates and uses `meridian`, uid 1001, with `cap_drop: ALL`
and a read-only root filesystem — all correct. Docker, meeting a bind-mount
source that does not exist on the host, creates it as **root**. So the first
crawl fetched a handful of real pages, hundreds of kilobytes each, and could not `mkdir
/data/raw/<domain>/`, and settled every task as

```
"outcome": "success", "disposition": "retry", "stored": null, "chars": null
```

which is *true* — the fetch succeeded. The traceback is there at ERROR, one per
page, in among a stream that otherwise reads like a healthy crawl.

It would have done the same on the server. `docs/setup.md` and
`docs/deployment.md` chown `/srv/meridian/app` because that is the checkout, and
neither says anything about `raw`, `figures` or `models`. `B-16` added a `chown`
one-shot to both compose files, run by `make quickstart` and belonging in the
runbook before `up`.

Three things about the shape of it. The directories are chowned, not `-R`:
what is created beneath them inherits the owner, and a recursive chown over a
100 GB raw store is its own outage. The one-shot runs `network_mode: none`,
because omitting `networks:` silently puts a container on compose's default
bridge — which has egress, and this is the only container in the stack running
as root. And nothing in the test suite could have caught it: tests write to
`tmp_path` as whoever ran them, and a container's view of a bind mount does not
exist until there is a container.

### The embedding sidecar sits where it cannot fetch its own weights

`embedder` is on `internal`, which is `internal: true` — no gateway, no DNS. The
weights are *not* in the image: the worker image installs `sentence-transformers`
and never downloads `BAAI/bge-m3`, which is why `MERIDIAN_EMBED_CACHE` and the
`/models` mount exist at all. So on any stack nobody had hand-seeded, the sidecar
started, answered `/health` with `loaded: false`, and failed every embed request
after a 30-second timeout — for ever.

Nothing says so. `/health` is honest, and an unloaded model is the ordinary state
of a lazy sidecar nobody has used. The symptom is one line in a search response:
`degraded_reason` saying the embedding service "did not answer", which is easy to
read as a transient outage rather than a permanent impossibility.

`B-14` added `python -m worker.fetchmodel` — a one-shot on `egress` that writes
into the same volume and exits. **Run it before `up` on any new machine**; it is
in `make quickstart` and in the deploy runbook, and a populated cache makes it a
no-op. Keep the sidecar off `egress`: it runs corpus text through a model, and a
route out from there is a route out for anything that ever gets in.

Two smaller things fell out of the same hour. `worker.embed` *appears* to work
anyway, because `P2-19`'s fallback loads the model in-process when the sidecar
does not answer — it just downloads 2.3 GB into a layer that dies with the
container, every run, and says so only at INFO. And the compose comment asserting
the weights were in the image sat two lines above the mount that exists because
they are not; a comment stating a fact about the build is worth checking against
the build.

### A service that is running is not a service anything talks to

`docker-compose.local.yml` started the embedding sidecar and never gave the API
`MERIDIAN_EMBEDDER_URL`. Nothing failed: `RemoteEmbedder.from_env()` returns None
when the variable is unset, because "this deployment has no embedder" is a
supported state (`P2-07`) — so search ran the lexical arm alone and reported
exactly that, truthfully, a few hundred bytes of network away from a running
model.

The general shape: an absent-is-fine default plus a service nobody wired to means
a stack that is fully healthy and half functional. `tests/unit/test_compose_
topology.py` now asserts the pairing — if a compose file runs the sidecar, the
services that would use it must be able to find it — and `test_fetchmodel.py`
asserts the fetcher and the sidecar agree on the path, because two containers
agreeing by coincidence is the version of this that looks like success.

### A library that does not know it is offline retries until it gives up

`embedder` is on `internal` with no route out, and after `B-14` its weights are
in the cache. It still took **144.6s** to answer its first embed, because
`huggingface_hub` checks for the optional config files the cache does not hold:
a HEAD to huggingface.co per file, `Temporary failure in name resolution`, five
retries with backoff, then on to the next file — and finally a correct load
from cache. The logs are a wall of WARNING lines about a host being
unreachable, which is precisely what a *broken* sidecar looks like, and is how
`B-14`'s fix was nearly mistaken for not having worked.

`HF_HUB_OFFLINE=1` on that service takes it to **4.9s** with no warnings, on
the same cache, measured both ways. Set it wherever a container reads the model
cache and has no egress — and never on `modelfetch`, whose whole job is the
download. Both directions are asserted in `tests/unit/test_fetchmodel.py`.

The general shape is one this codebase keeps meeting: a component that is
*correct* about its own state and wrong about its surroundings will spend a
long time finding out, loudly, in a way that reads as a different fault.

### The scheduler is a supervisor, and for a long time nobody supervised it

`P5-06` was ticked, its code worked, and no compose file ran `python -m
worker.scheduler`. `seed.py` writes five `scheduled_jobs` rows — embed and
novelty hourly, digest, sweep and harvest daily — all `enabled`, all with
`next_run_at` in the past. Reading that table told you embedding ran every
hour. It had never run once, in either stack.

So a stack left alone fetched, extracted, chunked and stopped. `embedded_chunks`
stayed at zero while the backlog grew, which looks like an embedder problem and
is not one. `B-15` added the service.

**The wider point is the one to carry.** A task is done when it runs, and "its
code runs when invoked" is not the same claim as "something invokes it". The
scheduler had tests, a lease, SKIP LOCKED and a careful argument about not
passing module names to a shell — all of it correct, none of it reached by any
code path outside the suite.

**`scheduler` is the second service on both networks**, and that is a real
widening of `P1-22`'s boundary rather than an oversight. Of the five jobs it
spawns, only `worker.digest` needs `egress`; the other four want nothing
outside `internal`. They inherit the container's environment, so `worker.harvest`
now parses crawler-fetched text somewhere with a route to the internet. The
trade was taken because the alternative is a scheduled send that fails into
`last_error` and nowhere else, and because `worker` already makes exactly this
trade in exactly this image. `test_only_named_services_write_from_egress` holds
the allowlist, and the reasoning is written into it so the next person can
disagree with it rather than discover it.

**Omitting `healthcheck:` does not give a container none.** It inherits the
image's, and the worker image's probe imports `worker.main` and checks poppler
— which passes for as long as the package tree is intact. The scheduler shipped
that way for one commit, so a wedged one would have read `healthy` for ever
while also suppressing the restart that no probe at all would have left to
`restart: unless-stopped`. Caught by watching the container come up and report
`health: starting` a minute after the comment claiming it had none was written.

`B-19` closed it properly: the loop writes `P5-08`'s heartbeat itself, and
**where** it beats is the design. After each claim rather than before it,
because here the database round trip is the thing that hangs and a beat in
front of it would be refreshed by a scheduler that never gets an answer — the
opposite of `worker.main`, which beats first because a lane wedged inside a
fetch should stop beating within its own iteration. And continuously while a
job runs, because a backfill takes half an hour and a probe firing during
normal work would restart the scheduler in the middle of the work it was
reporting on. That second beat is the weaker claim — a job is in flight and has
not hit `--timeout-seconds` — and the timeout is what stops it covering for a
permanently hung child.

A long-running service that overrides its image's command must now declare a
healthcheck or disable one explicitly, so the next one cannot inherit a probe
for a process it does not run.

### A control surface is a service, and a service is a thing that can be down

`P5-07`'s inbound half is `worker/bot.py`, and it is a separate compose service
rather than a lane inside the worker. Three things about it are worth knowing
before the first command is typed.

**It drops whatever was sent while it was down.** Telegram holds undelivered
updates for 24 hours and replays them on the next poll, so a bot restarted after
a night off would work through yesterday's queue at breakfast — `/run` at
midnight starting a run in the morning, a `/boost` applied a second time. The
first poll therefore acknowledges the backlog without reading it. If a command
seems to have been ignored, this is why, and the fix is to send it again.

**It polls rather than being called.** No inbound port, no certificate, no
hostname — which is the whole reason it works on a machine with no ingress. The
cost is that it is one more long-lived process to notice the death of, so it
touches the same liveness file the worker does and carries the same healthcheck.

**An unknown chat gets silence, not a refusal.** A reply would confirm that the
bot exists and is listening. If your own messages are being ignored, the
unauthorised chat id is in the log — that is what it is logged for, because the
likeliest cause is a `TELEGRAM_CHAT_ID` that is wrong rather than an intruder.

### A container on an `internal: true` network cannot publish a port

Docker installs no gateway on it, so there is nothing for the host to forward to
— and the `ports:` line is not an error, it is inert. Production gets away with
it because `cloudflared` sits on `internal` and proxies inward, but the
`ports: ["127.0.0.1:21114:8000"]` on its `api` does nothing whatsoever. The local
stack needs a third network (`frontdoor`) carrying only `api` and `web`, for no
reason other than having a gateway to publish through.

### A key set on the global `*` policy row is set for every domain

`P1-27` nearly shipped dead because of this: the rule was "learn only where
nobody configured `render_js`", and the global row ships `render_js: auto` as its
default — so every domain counted as configured and the learning never applied.
Any per-domain rule of the form "only when this key is absent" has to be written
against the *merged* value, not against the presence of a key in some row.

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

And `v0.25.0`, against the chunks a real crawl had already produced rather than
against constructed vectors:

| Behaviour | Evidence |
|---|---|
| The gate finds real duplicates | the boilerplate block a site repeats under every URL, caught at cosine 1.0 |
| It does not find false ones | every genuinely distinct page kept; nothing demoted to `junk` |
| `0.95` is nowhere near the noise floor | unrelated real bge-m3 chunks cluster around 0.71 and bottom out near 0.55 — the threshold has real headroom, which is *not* what a hash-based `FakeEmbedder` would have told you |
| The pass is cheap | the whole corpus judged in well under a second, with no index yet |

That last row is the one to re-check at scale: the nearest-neighbour scan is
sequential until `P2-04` adds the HNSW index, and "fast" here means "fast on a
corpus small enough that nothing is fast or slow".

And `v0.26.0`, against a real SearXNG rather than a mock transport:

| Behaviour | Evidence |
|---|---|
| A seed query becomes frontier | a query pending since `make seed` → 47 results → 44 queued at tier priority, task `done` |
| The prefilter earns its place here | 3 of 47 were blocked domains, dropped before a request was spent |
| §6.4's routine engine failure | one upstream engine unresponsive throughout; the query succeeded and nothing retried |
| Sitemaps enqueue at all | four loop-level tests, all of which fail against the pre-`v0.26.0` enum |

And `v0.27.0`, against real Unpaywall and OpenAlex:

| Behaviour | Evidence |
|---|---|
| A paywalled publisher paper resolves | an Elsevier transport paper → an institutional repository copy, `submittedVersion` |
| Open-access papers resolve to a PDF | two publishers → direct PDF links, `publishedVersion` |
| An arXiv DOI costs no request | resolved from the DOI itself, network handler asserted untouched |
| A genuinely closed paper is an *answer* | one publisher DOI → no copy anywhere → `done`, not retried |
| Semantic Scholar earns its place | 12 of 12 DOIs that OpenAlex could not resolve → an open-access PDF, asked one per second |
| Europe PMC does not, for this corpus | 0 hits across a transport-research sample; it is a biomedical index and will matter for health-adjacent work, not this |

### A rate-limited API looks exactly like an API with no answer

`v0.28.0`, and worth reading before adding any provider to any chain in this
codebase. Semantic Scholar's anonymous quota is strict. Resolving 75 real DOIs
back to back returned an open-access copy from it for **none** of them; the same
DOIs asked one per second returned a PDF for **every one** of the twelve
sampled. The first measurement read as "this provider adds nothing" and would
have justified deleting it.

The cause was a 429 folded in with connection errors — both "this provider could
not be asked", both skipped, chain continues. So the resolver reported *no
open-access copy exists*, the task settled `done`, and the paper was never looked
for again. A false negative indistinguishable from a true one, produced by the
system's own throughput.

**The penalty outlasts the burst, which is the part that will waste your
afternoon.** After the 75-DOI runs, the same provider returned nothing even at
one request per *three* seconds; twelve seconds apart it answered 200 with a PDF
every time. So a paced re-measurement taken straight after an unpaced one
reproduces the unpaced result and looks like confirmation. Wait it out, or use a
key.

Three rules came out of it:

- **A provider that refused because you asked too fast has not answered.** A
  resolution that found nothing while being throttled is incomplete and must
  retry rather than settle.
- **Any "provider X adds nothing" measurement taken at full speed is worthless.**
  Re-run it paced, from a cold start, before believing it — and note that the
  first paced re-run may still be inside the penalty window.
- **Per-process pacing is a floor, not a quota solution.** The queue's own
  exponential backoff is the right timescale for waiting out a quota; the
  interval only stops the worker throttling itself.

The same shape applies to SearXNG's engines — §6.4 already says engine failure is
routine — and to anything else this codebase queries in a loop.

**Never confirmed against a real server:** the decompression-ratio cap (tested against a
local socket serving a synthetic bomb) and the 5xx-robots refusal path.

### Nothing after phase 1 has met a real deployment

This is the largest gap in this document and it is worth stating plainly. Every
claim about phase 2, 3 and 6 rests on tests — a real Postgres, a real ASGI
transport, real HTTP doubles — and on nothing else, because **the stack has never
been deployed to the server**. Specifically unverified outside tests:

- the API and UI behind `cloudflared`, and Access JWT verification against real
  Cloudflare JWKS;
- an external assistant connecting over MCP from a phone, which is what §11 and
  `P3-05` exist for;
- the embedding sidecar under a real backfill — including `P2-19`'s fallback,
  which has only been exercised against fakes;
- the systemd units: `meridian.service`, and `meridian-backup.timer`'s
  `Persistent=true` catch-up after a machine was off;
- the worker healthcheck actually restarting a wedged container, as opposed to
  the liveness file being stale in a unit test;
- anything at corpus scale: HNSW recall (`P2-04`'s open half), search latency,
  the acronym harvest's precision over real documents, and whether any domain
  triggers `P1-27`'s render learning at all.

When the 48-hour run happens, that list is the checklist.

**Four of those are now shorter.** `v0.76.3`–`v0.78.1` brought the whole stack
up in containers for the first time — not on the server, but not natively
either — and the four things it broke on are the four in §3 above. What that run
established, against the real web and a real Postgres:

| Behaviour | Evidence |
|---|---|
| The stack comes up whole, from source | seven services healthy from `make quickstart` on a clean clone; migrations and the seed through the `tools` image, topic, policy and gazetteer rows written |
| The UI is served and reaches the API | nginx on `21116`, `/api` proxied, and a client-side route (`/sources/42`) surviving a reload rather than 404ing on the static root |
| The crawl stores what it fetches | after `B-16`, nearly every source in the corpus has a raw file, HTML and PDF alike. Before it, none did — each settled `"outcome": "success", "stored": null` |
| The embedding sidecar serves from a container | `loaded: true`, 1024 dimensions, 143s for a cold load off disk — which is what `start_period: 180s` is for |
| **Hybrid search, end to end, outside a test** | `arms: ["lexical", "vector"]`, `degraded: false`, both ranks populated. It had never run anywhere but in the suite |
| The timetable is read (`B-15`) | `scheduler` claimed `digest`, settled it `ok` in 747ms and rescheduled it, then claimed `embed` and began writing vectors against a backlog that had stood at one embedded chunk |

Still untouched by any of this: everything needing the server, Cloudflare or
scale. `P2-19`'s fallback is the one that moved without being verified — it
fired, loaded the model in-process and embedded correctly, but only because the
sidecar was broken at the time, which is not a test anybody designed.

---

## 5. What to build next

`TASKS.md` is authoritative; this is the reasoning behind the ordering, and the
short version is that **almost everything left is gated on one of three things**:
the 48-hour run, a Cloudflare account, or the graph.

### The gate is `P1-16`

Every phase-1 task that could be done without a real corpus is done. What remains
is running the thing for two days and looking at what comes out — which is also
the only way to answer `P2-09`, the human go/no-go on whether hybrid retrieval
over this corpus is better than reading the sources.

Bound a smoke run with `MERIDIAN_WORKER_MAX_TASKS`, not a timer: polite
per-domain delays mean a fixed wall-clock window yields wildly different volume
depending on which domains the frontier hands you, and a task count is
reproducible. `P1-16` itself is the timed one.

**The failure to watch for** is a queue that drains. A crawl that empties its
frontier and idles logs exactly what a healthy one logs. `pending` falling
monotonically to zero is the signal; a healthy run keeps finding more than it
drains. Two of the three non-link discovery channels were dead once before and
nothing reported it.

### Gated on the graph (phase 4)

`P6-01`–`P6-07` — canvas, path mode, node panel, synthesis — are the payoff
layer and need edges to exist. `P6-10` needs `P5-03`'s coverage scoring, which
needs the schema-aware pass, which needs the graph. `P4-02`'s entity resolution
is the largest single piece of unbuilt design in the repository, and §5.5
specifies it closely enough to be written test-first.

**`P1-32` is decided and built**, so the thing that had to happen before the
first edge has happened: chunks are superseded rather than deleted, and a
citation keeps resolving after the page changes. Do not undo that by adding a
delete path.

**`P6-04` already reads the graph tables**, so `tests/integration/test_node_detail.py`
is a worked example of writing `entities`, `attribute_values` and `edges` rows
against their real constraints — including the one that catches people, the CHECK
on `observations` requiring a value.

**`P4-13` before `P4-08`.** §16 says budget caps must exist before the first
autonomous run, and nothing enforces the ordering — the compounding
seed→crawl→cost loop is first noticed as a bill.

### Gated on a Cloudflare account

`P3-05` and the rest of `P3-09`. The code side is done and tested: Access JWT
verification refuses rather than bypasses when JWKS is unreachable, and the MCP
surface advertises its protected-resource metadata only when authentication is
on. What is missing is a tunnel, an Access application, and an AUD tag.

### Buildable today

The shortlist is genuinely short now. `P6-24` (topic filter), `P6-04` (node
panel), `P6-09` (saved views) and `P6-05` (annotation) are done, which was the
last of the interface work that did not need the graph.

1. **`P6-23`** — admin: agent registry and run history. Both tables exist and
   stay empty until phase 4 runs something, so this is worth building *after*
   there is a run to show: an empty screen teaches nothing about what the full
   one should look like.
2. **`P1-35`** — a Semantic Scholar key, or accept the retries. Ten minutes, and
   the 48-hour run is when it is felt.

### Explicitly *not* worth doing yet

**`P2-15`** (benchmark embedding models against each other). Its own text gates
it on `P1-16` and on `P2-09` being marginal, and it means a second embedding
column plus a full re-embed for a model you may never adopt. The benchmark that
*does* exist — `make bench-search` — measures the index and the methods over the
vectors already in the corpus, and that is the one worth running after the crawl.

**`P6-18` and `P6-19`** are marked ⚑ human. They are published-design decisions:
four light-theme canvas roles and three lockup values that were inferred rather
than decided. An agent picking values for those is inventing design, not
implementing it.

### A rule that keeps paying

Every handler that ends in a write should have a test that reads the row back.
That single rule is what caught the sitemap handler that never enqueued anything,
the digest that reported zeros forever, the gazetteer term that loaded no
patterns, and the route walk that asserted an empty list against an empty list.
The common shape is not a crash — it is a success message about work that did not
happen.
