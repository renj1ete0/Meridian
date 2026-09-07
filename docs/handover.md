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
of itself — and now with nobody watching. As of `v0.12.0`, 519 tests pass with a real
Postgres.

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
        worker.main settles the task
        queue_disposition() → fetched | done | retry | abandon
```

**What does not exist yet.** No extraction, no embeddings, no API, no frontend. The
loop fetches bytes and drops them — nothing writes a `sources` row or a raw file
(`P1-07`–`P1-11`), which has one visible consequence today: `conditional_requests` is
on and `not_modified` can never fire, because nothing stores the ETag that would make
a request conditional. The 304 path is tested and correct and will stay unreachable
until `P1-11` lands.

`fetch_health()` is logged hourly by the loop and displayed nowhere (there is no UI).

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

**Running the worker by hand.** `make test` exports `.env.dev`; nothing else does, so
the worker needs it sourced:

```bash
set -a; . ./.env.dev; set +a
MERIDIAN_WORKER_MAX_TASKS=4 uv run python -m worker.main
```

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

**Never confirmed against a real server:** the decompression-ratio cap (tested against a
local socket serving a synthetic bomb), the 5xx-robots refusal path, and — because
nothing stores an ETag yet — the loop's `not_modified` → `done` disposition.

---

## 5. What to build next

`TASKS.md` is authoritative; this is just the reasoning behind the ordering.

**Extraction (`P1-07`–`P1-11`), because the loop currently throws the bytes away.**
The worker fetches successfully and then does nothing with the body: no `sources` row,
no raw file, no chunks. Two consequences worth knowing before starting. First, a
re-run refetches everything in full — `conditional_requests` is on and works, but
nothing has ever stored an ETag for it to send. Second, `Crawler.fetch` returns the
body in memory and the loop drops it, so whatever writes the raw store has to be
called from `Worker._process` before the settle, or the bytes are gone.

`P1-11` (the raw store writer) is the one that unblocks the others, and it is what
makes the 304 path reachable for the first time.

**`P1-06` and `P1-28` are both cheap and now have a loop to feed.** The prefilter keeps
already-seen URLs out of the queue; sitemap discovery enqueues the sitemaps
`RobotsRules.sitemaps` already parses and throws away — Wikipedia's robots.txt lists
one today. `P1-28` also needs `HANDLED_TASK_TYPES` in `worker/main.py` extended, or
the `sitemap` rows it enqueues will sit in the queue unclaimed forever.

**`P1-23`'s injection pre-screen lands alongside extraction** rather than after.

**One thing the loop does not do yet:** there is no systemd unit in the repo. §13.4's
`Restart=always` is the supervision the process deliberately does not implement for
itself, and nothing currently provides it.
