# Adding sources, and writing your own connector

Four ways to widen what Meridian can reach, cheapest first. Most of what looks
like it needs code does not, and the first thing to do with a new source is
check which level it actually lives at.

| Level | You are doing this when | Cost |
|---|---|---|
| **1 · Config** | A site is reachable and just needs to be *known about*, or needs different fetch treatment | No code, no restart of anything but the seed |
| **2 · A provider in an existing chain** | The path already exists and you want another place it looks — one more open-access host, one more citation service | One function, one entry in a list |
| **3 · A new task type** | The queue needs a row that means something the four existing kinds do not | Enum + migration + handler. Read §5 first |
| **4 · A sidecar container** | The fetch itself needs software the worker should not contain — a headless browser with a session, a vendor SDK, a paid API client, something with a licence you would rather isolate | A client module plus a compose service. The pattern exists twice already |

Level 4 is the one to reach for when a source "cannot be scraped" by the normal
path. It is also the one with the most existing shape to copy, because
`crawl4ai` and `searxng` are both exactly that and nothing else.

---

## 1. Config: a source that just needs to be known about

`config/seed_sources.yaml` seeds the queue at **first boot only** (`make seed`).
The database is authoritative afterwards — do not add a code path that re-reads
it at runtime (§13.1). So on a running system, config is for the *policy*, and
new targets go into the queue directly.

**A domain that needs different fetch treatment** is `fetch_policy`. Resolution
is per-domain row → global row → `config/fetch_policy.yaml` defaults, and the
per-domain row is the mechanism for every "this one site is special" case:

| Symptom | The knob |
|---|---|
| Served as a content type the allowlist refuses | `allowed_content_types` — this is what makes sitemaps work at all, since most are `text/xml` |
| A shell with no text until JS runs | `render_js: true`, instead of paying `auto`'s two requests per page forever |
| 429s, or a host that asks for slowness | `delay_per_domain_ms`, `concurrency_per_domain` |
| Legitimately http-only | `require_https_final: false`, for that domain and no other |
| A bot-challenge interstitial | `challenge_wait_s` (`P1-33`); 0 disables |

Everything in that table is a row, not a deploy.

---

## 2. A provider inside an existing chain

`worker/resolve_doi.py` is the worked example: given a DOI, find a legally
available copy, stopping at the first one. Adding a seventh provider is a
function plus its place in the order.

**Three outcomes, and they must stay three.** This is the whole design, and
collapsing any two of them loses papers:

| Outcome | Meaning | How the task settles |
|---|---|---|
| A copy | Found | Enqueued as an ordinary `url` row |
| `_ProviderSkipped` | No credential for this provider | Skipped, not failed — a bare deployment still gets the free providers |
| `_ProviderUnreachable` / `_ProviderRateLimited` | Nobody answered | **Retry.** Not "no copy exists" |
| Every provider answered "no" | There is no copy | `done` — an answer, not a failure |

The trap here already cost a release. Before `v0.28.0`, a rate-limited provider
read as "no copy anywhere", which settled the task `done` and lost the paper
permanently — the row was gone and nothing retried it. `_ProviderRateLimited`
subclasses `_ProviderUnreachable` for exactly this reason. **Read the handover's
§3 entry before adding a provider anywhere**, including outside this module.

Pacing is per provider: `PROVIDER_MIN_INTERVAL_S` plus a lock each, so throttling
one does not throttle the one above it in the chain.

---

## 3. A new task type

The queue's `task_type` is currently `url`, `sitemap`, `query`, `doi`. Adding a
fifth means all of:

1. **The enum**, in `packages/meridian_core/meridian_core/models/queue.py` —
   `TASK_TYPE = constrained(...)`.
2. **A migration** that hand-writes the CHECK. Autogenerate does **not** detect
   CHECK constraint changes on existing tables (`P0-21`); you get a column that
   accepts the value and a constraint that rejects it.
3. **A DTO Literal** in `schemas/enums.py`, or `test_drift.py` fails — which is
   the point of it.
4. **A handler** — `_process_<kind>` in `worker/main.py`, dispatched from
   `_process`, plus the name in `HANDLED_TASK_TYPES`.
5. **An integration test that reads a committed row back.**

Step 5 is not ceremony. `P1-28` shipped a sitemap handler that passed
`seed_source="sitemap"` to `enqueue()` when that value was in neither the model's
enum nor the database's CHECK. Every sitemap fetched, parsed cleanly, and then
raised at the insert — caught by the lane's outer handler, filed as "task failed
unexpectedly", and queueing nothing. `fetch_attempts` recorded a 200. The log
line said the sitemap had been read. **The feature had never worked and nothing
said so**, because the parser was exhaustively unit-tested and the parser was
never the problem.

So: for any handler that ends in a write, the test that matters drives it to a
committed row and reads it back, and it belongs in `test_worker_run.py` rather
than beside the unit tests for the parsing.

---

## 4. A sidecar container — your own scraper

This is the shape for "the standard fetch path cannot get this, and the thing
that can should not live inside the worker". A vendor SDK, a browser holding a
logged-in session, a paid API client, an extractor with a licence you would
rather keep at arm's length.

`crawl4ai` and `searxng` are both already this, so the pattern is established
rather than invented. Copy it rather than improvising, because three of its
properties are load-bearing and none of them are obvious.

### 4.1 The compose service

```yaml
  myscraper:
    <<: *common
    build: { context: ./deploy/myscraper }
    image: meridian/myscraper:0.1.0
    networks: [egress]          # NOT internal. No route to postgres.
    environment:
      MYSCRAPER_TOKEN: ${MYSCRAPER_TOKEN}
    # No env_file. No ports.
    healthcheck:
      test: ["CMD-SHELL", "..."]
      interval: 30s
      start_period: 30s
```

Three rules. `tests/unit/test_compose_topology.py` enforces the first
generically — `test_the_worker_is_the_only_writer_on_egress` fails if any new
service sits on both networks — and the other two **only for `crawl4ai`**, by
name. Your sidecar needs its own two assertions there; copy
`test_the_browser_holds_no_credentials` and `test_the_browser_publishes_no_ports`
and point them at your service. A rule nothing checks is a rule that lasts until
the next person simplifies the compose file:

- **`egress` only.** It fetches hostile content; it must not be able to reach
  Postgres. This is by construction, not by policy.
- **No `env_file`.** Name the one or two variables it needs explicitly. A shared
  `env_file` default is precisely how the browser sandbox came to hold every
  database password until `v0.24.0`.
- **No published ports.** It is reachable from the compose network and nowhere
  else.

Then add it to `worker`'s `depends_on`. Choose the condition deliberately:
`service_healthy` means the worker will not start without it (correct for
`crawl4ai`, which the fetcher needs); `service_started` means a missing one
degrades rather than blocks (correct for `searxng`, where the cost is unclaimed
`query` rows).

### 4.2 The client module

`worker/search.py` is the shortest one to copy. What matters:

```python
class MyScraperClient:
    @classmethod
    def from_env(cls) -> MyScraperClient | None:
        """None, not an exception: a worker without this is degraded, not broken."""
        url = os.environ.get("MYSCRAPER_URL")
        if not url:
            return None
        return cls(url, os.environ.get("MYSCRAPER_TOKEN") or None)

    async def healthy(self, timeout_s: float = 5.0) -> bool: ...
```

`from_env()` returning `None` is how an absent sidecar becomes a degraded worker
rather than a crashed one. Do not raise.

### 4.3 Make the degradation visible

This is the part that gets skipped and is the reason the pattern is worth
copying rather than reinventing.

A missing sidecar is **silent**. `Crawl4aiClient.from_env()` returning `None`
means the fetcher quietly falls back to static fetching — correct, and
invisible. A worker that lost its browser a week ago looks exactly like one that
never had a browser and simply extracts worse.

So the §12.5 health line carries a word per sidecar:

```
browser: configured | unreachable | absent
search:  configured | unreachable | absent
```

`unreachable` logs at WARNING. `absent` does not, because it is a deployment
choice rather than a fault. Add yours beside them — `search_health()` and
`browser_health()` in `worker/main.py` are four lines each.

### 4.4 If it needs its own queue rows

A sidecar that answers "fetch this for me" needs nothing more: call it from the
fetch path for the domains whose `fetch_policy` says so.

A sidecar that answers a *different question* — "search this vendor index",
"resolve this identifier" — needs a task type, so go through §3 first, and then
register the dependency:

```python
CONDITIONAL_TASK_TYPES = {"query": "_search", "doi": "_resolver", "mykind": "_myscraper"}
```

`_claimable_task_types()` reads that, and the effect is worth understanding: a
worker **without** your sidecar will not claim your rows at all. They wait for a
worker that can do them, rather than being claimed, failed, and retried until
they are abandoned while the sidecar is simply down for an afternoon. On a
single-worker stack this is the difference between a restart and a data loss.

---

## 5. Before you ship any of it

- [ ] Does an absent connector degrade, or crash? It must degrade.
- [ ] Does the health line say it is absent? A silent degradation is the bug.
- [ ] Are "nobody answered" and "the answer is no" distinct, all the way to the
      queue disposition? Retry versus `done`.
- [ ] Is every string literal that reaches a constrained column also in the
      enum *and* the CHECK? Grep for the literal; nothing else compares them.
- [ ] Is there a test that drives it to a **committed row** and reads it back?
- [ ] Does it hold credentials it does not need? Check `env_file`.
- [ ] Does it have a route to Postgres it does not need? Check `networks`.
- [ ] `docs/handover.md` — add what you learned that neither TASKS nor AGENTS
      would have told you.
