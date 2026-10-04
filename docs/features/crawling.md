# Crawling

The crawler takes URLs from a queue in Postgres and fetches each one politely and safely. It
records every attempt, whether or not it succeeded, and leaves the result for extraction. It
runs unattended for weeks, so it is built to keep going through errors, restarts and dead
sites, and to make a stalled crawl visible rather than silent.

- **Code:** `services/worker/worker/main.py` (the loop), `crawl.py` (one polite fetch),
  `fetch.py` (bytes from a URL), `robots.py`, `ratelimit.py`;
  `packages/meridian_core/meridian_core/queueing.py`, `policy.py`, `netguard.py`,
  `attempts.py`, `robotscache.py`
- **Tasks:** `P1-01`–`P1-06`, `P1-15`, `P1-18`–`P1-30`, `B-107`, `B-112`, `B-114`, `B-115`
- **Decisions:** [ADR 0001](../adr/0001-production-runs-on-a-server.md)

## How it works

```
queue row (pending)
  └─ lane claims it ── FOR UPDATE SKIP LOCKED, with a lease
       └─ Crawler.fetch
            1. domain blocked?        (policy row, no network)
            2. robots.txt allows?     (cached per origin)
            3. wait for the domain's slot (concurrency + delay)
            4. conditional request    (ETag / Last-Modified from last time)
            5. fetch: static HTTP, or the browser when the page needs JavaScript
       └─ one fetch_attempts row + the policy consequence, in one transaction
       └─ extract, chunk, store; queue links found on the page
```

**Lanes, not a dispatcher.** The worker runs `MERIDIAN_WORKER_CONCURRENCY` independent
claim-fetch-settle lanes. The database is the queue and `SKIP LOCKED` is the dispatcher: two
lanes asking at once get different rows. The same holds for two worker processes, so scaling
out needs no coordination.

**Claims draw a topic.** Each claim picks a topic in proportion to the steering weights
([steering.md](steering.md)) and takes only tasks filed under it. One claim in
`MERIDIAN_WORKER_DIRECTED_EVERY` is reserved for directed work: search results, seeds and
cited papers, which find on-topic pages far more often than followed links. Tasks queued with
no topic are claimable only by a fallback that runs when no topic has work, so **every task
source must file its rows under a topic**.

**Leases, not status flags.** A claim expires after `MERIDIAN_WORKER_LEASE_SECONDS`, so a
crashed worker strands nothing. On shutdown, held leases are released rather than left to
expire.

**Backpressure.** While more than `MERIDIAN_WORKER_MAX_EMBED_BACKLOG` valuable passages wait
for a vector, the loop stops claiming ("crawl paused for embedding"). A page that is not
embedded is unsearchable and unlabelled, and the host scores that steer the crawl cannot see
it. See [embedding.md](embedding.md#backpressure).

## Design choices

- **Cheapest refusal first.** Blocked domain, then robots.txt, then the rate limiter. Waiting
  in a domain's queue for a URL that was going to be refused anyway is wasted time.
- **Two limits per domain.** Concurrency bounds requests in flight; delay bounds how often a
  request *starts*, because a server sees arrivals. The delay is jittered so that a fixed
  interval is neither a fingerprint nor a way to synchronise bursts.
- **Policy resolves per domain, then global, then file defaults** (§6.4). The database is
  authoritative; the YAML layer exists only so that an unseeded database fails predictably.
- **robots.txt follows RFC 9309 exactly**, with its own parser. Python's standard parser
  changed behaviour between versions, so conduct would have depended on the interpreter.
  A 4xx on robots.txt means allow everything; a 5xx or timeout means refuse everything,
  and that refusal is cached for minutes, not a day.
- **SSRF protection after DNS, on every hop** (`netguard`). Hostnames are resolved and the
  addresses judged, including odd spellings (`::ffff:127.0.0.1`, decimal IPs). The request is
  then **pinned** to the validated IP, with `Host` and SNI set to the name, so a second DNS
  answer cannot redirect the socket (DNS rebinding). Redirects are followed by hand and each
  hop is re-validated. Anything unclassifiable is refused.
- **The browser only when needed.** `render_js: auto` sends a page to Crawl4AI only when the
  static fetch looks like a JavaScript shell or a challenge page. Most documents (PDFs,
  official pages) need none of it.
- **Every attempt is logged, including refusals that never touched the network**, so a quiet
  crawler can be told apart from a stuck one. The attempt row and its policy consequence
  commit together.
- **A refusal is not a failure to retry.** `queue_disposition` abandons a robots denial or an
  unsafe target once, rather than asking again three times over an hour. Real failures back off
  exponentially per domain.
- **Domains that refuse every request are blocked** for 30 days (`B-114`), by note
  `updated_by='refusals'`. An unblock by hand starts a fresh window.
- **Nothing raises to the top.** A lane catches, logs, settles its task and goes back for the
  next. Cancellation is the exception, because that is the shutdown path: the first signal
  drains in-flight fetches and the second cancels.

## Configuration

- Worker variables: [reference/environment.md](../reference/environment.md#crawl-worker).
- Fetch policy (concurrency, delay, `render_js`, allowed types, size cap, user agent,
  blocked domains): the `fetch_policy` table, editable in **Admin → Fetch policy**, seeded
  from `config/fetch_policy.yaml`.
- `MERIDIAN_CONTACT_EMAIL` is sent where an API asks for a contact address. Some sites refuse
  clients that send none.

## Operating it

- **Admin → Crawl health** gives a verdict in words (fetching, idle, stalled), with 24 hours
  of attempts by outcome and the queue beside the embedding backlog.
- Logs: `docker compose logs -f worker`. "crawl paused for embedding" is normal while the
  backlog drains.
- The healthcheck reads a heartbeat file the loop touches every iteration (`worker.liveness`),
  so a wedged worker is restarted, not just one that exits.

## Failure modes and traps

- **The queue looks busy and nothing is fetched.** Check for tasks with `topic IS NULL`: only
  the fallback claims them. Test a new task source with a topic-scoped `claim_next`.
- **Search runs dry quietly.** Count pending `query` rows before trusting a run's search share.
- **Links queued now are fetched days later**, because older links sit ahead of them; a 1-hour
  run cannot measure links queued in the same hour.
- **Rebuild every app service together.** The scheduler has its own image. Deploy with
  `--no-deps`, or Postgres is recreated too and `embed` crashes until it returns.

## Tests

`tests/integration/test_queueing.py`, `test_queue.py`, `test_crawl.py`, `test_worker_run.py`,
`test_refusing_domains.py`; `tests/unit/test_netguard.py`, `test_fetch_pinning.py`,
`test_robots.py`, `test_ratelimit.py`, `test_queue_disposition.py`, `test_worker_loop.py`,
`test_liveness.py`.
