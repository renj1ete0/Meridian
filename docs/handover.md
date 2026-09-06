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

Phase 0 is closed. Phase 1 has its fetch path complete: a URL goes in, bytes come out,
politely and without becoming a route into the network. As of `v0.10.0`, 387 tests pass
with a real Postgres.

```
queue ──► claim (queueing.py) ──► Crawler.fetch (worker/crawl.py)
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
```

**What does not exist yet.** There is no worker main loop (`P1-15`), so nothing calls
`Crawler.fetch` in production. Nothing writes `fetch_attempts` rows (`P1-19`) even
though every code path already returns a valid `outcome` for one. Nothing calls
`record_failure()` / `record_success()` (`P1-05`), which are written and tested in
`policy.py` and sit unused. No extraction, no embeddings, no API, no frontend.

The README's "Getting started" lists `uv run python -m worker.main` — that module is
still `P1-15` and does not exist.

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

**Never confirmed against a real server:** the decompression-ratio cap (tested against a
local socket serving a synthetic bomb), and the 5xx-robots refusal path.

---

## 5. What to build next

`TASKS.md` is authoritative; this is just the reasoning behind the ordering.

**`P1-19` + `P1-05` together, one sitting.** They close the loop between a fetch and the
policy governing the next one. Everything needed already exists and is unused:
`FetchResult` carries a valid `fetch_attempts.outcome`, a status code, byte count and
duration; `record_failure()` and `record_success()` are written and tested. What is
missing is the writer and the call. Do them together — `P1-05` without `P1-19` blocks
domains with no record of why, and `P1-19` without `P1-05` records failures nothing acts
on. Remember `DomainLimiter.forget()` when a domain becomes blocked.

**Then `P1-15`, the worker main loop**, which is what finally makes the fast loop run
unattended, followed by extraction (`P1-07`–`P1-11`) with `P1-23`'s injection pre-screen
landing alongside rather than after.

**Do not point this at a wide crawl before `P1-19` exists.** Not for politeness — the
rate limiting is done — but because an unattended crawler with no attempt log fails
silently, which §13.4 names as the thing this system must not do.

One task worth doing early because it is nearly free: **`P1-28`**, enqueueing the
sitemaps `RobotsRules.sitemaps` already parses out. Wikipedia's robots.txt lists one and
the value is thrown away today.
