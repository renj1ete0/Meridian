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

### The loop

`worker.main` composes pieces that existed before it (`P1-15`): `queueing` hands out a task
once, `Crawler.fetch` gets one URL politely and records it, `attempts` says what the last day
looked like. The loop runs them with nobody watching, which is the only mode the system is in.

- **Politeness is not the lane's business.** A lane claims whatever is next, which may be the
  fourth URL in a row from one domain. `DomainLimiter`, shared across lanes and enforced inside
  `Crawler.fetch`, is what stops that becoming four simultaneous requests; a loop that
  scheduled around domains would duplicate it badly.
- **Errors back a lane off, never end it.** A worker that dies on an unexpected exception
  stops crawling on Saturday and is noticed on Monday. A database that has gone away backs the
  lane off (1 s up to 60 s, capped low because the usual cause is Postgres restarting), since
  the outage that matters is the one that outlasts the retry. A task hit by an unexpected
  exception is failed with a retry rather than left claimed for its whole lease, because nobody
  yet knows whether the bug was in the task or in the worker. Swallowing cancellation would turn
  `SIGTERM` into a process that has to be killed.
- **One lane raising cancels the rest.** `gather` returns the moment one lane raises and leaves
  the others running, so a caller of `run()` would have lanes still claiming behind its back and
  would release their leases out from under them.
- **Shutdown is graceful once and immediate twice.** The first signal stops claiming and lets
  fetches in flight finish, so a task is never abandoned mid-request. Held leases are dropped
  on the way out (`release_worker_claims`, pending rows only), because otherwise every deploy
  shows fifteen minutes of a queue that looks busy and does nothing. A second signal cancels,
  for a wedged fetch and an operator who has stopped being patient. Signal handlers are
  POSIX-only; a worker that cannot install them still crawls. Restart supervision lives outside
  the process (the container's restart policy; `Restart=always` in §13.4).
- **Both endings are ordinary.** Ctrl-C and a second signal end the run without a traceback,
  and one `run_id` is on every record the process emits, so a six-hour crawl is one thing to
  grep for.
- **Conditional task types.** `query` needs a search backend and `doi` a resolver. A worker
  without one does not claim those rows rather than failing tasks that are not broken: on a
  single-worker stack a query waits for SearXNG to come back instead of being abandoned while it
  is down (`_claimable_task_types`).
- **Housekeeping runs in the loop** because nothing else is awake often enough to bound
  `fetch_attempts`, which gains a row per request. It is cancelled, not stopped, on shutdown: a
  prune half done is a prune, and the next tick finishes it.
- **Config is read once at start-up.** The frontier blocklist and the topic vocabulary change
  when someone edits them in Admin, not between two pages of one crawl. A worker that cannot
  read either starts with none (every sitemap URL lands unmatched) rather than refusing to run:
  a worse crawl is a bad day, no crawl is an outage.

### Claiming

The queue is the decoupling point between the planes: the worker and the orchestrator never
call each other, they only leave rows (§2 principle 2). So the claim has to be correct under
concurrency without either side knowing the other exists.

- **No task is claimed twice.** `FOR UPDATE SKIP LOCKED`: a competing claimer skips a locked
  row instead of blocking, so N workers drain the queue without a queue server. The row lock
  lasts only the claim statement; the claim commits and its session closes before fetching
  starts, so the claimed task travels as plain values (`Claim`) rather than an ORM instance,
  because holding a connection across a network fetch would pin one per lane and there are
  more lanes than pool slots to spare.
- **Eligible** means pending, past its backoff time, and unclaimed or holding an expired lease.
  Ordered by priority then age, so tier-upranked results (§5.2) go first and nothing starves.
- **Busy hosts are skipped** (`skip_domains`, `B-112`). Priority order alone sent every lane to
  the same few hosts at the top of the queue, and one with a long crawl delay held the whole
  worker to its pace. Lookups and queries are never skipped; they do not wait on a host.
- **A claim takes only types the caller can handle** (`task_types`). A claimer that takes a row
  it cannot process can only fail a task that was never broken, or hand it back and reclaim it
  forever.
- **Topics are read per claim, not cached.** The topic table is a handful of rows and the lane
  is about to spend a second or more on a fetch; a cache would make a steering change land at a
  time nobody could predict, and "steer back later" (§10) has to mean now.
- **An empty topic is redrawn, not surrendered.** The first version fell straight through to an
  unfiltered claim. On a real frontier that gave the share of every topic with nothing queued to
  whichever topic had most queued, the concentration steering exists to correct: measured on the
  live stack, half the active topics held no rows, so 45% of the weight went to the largest
  pile. A drawn topic with nothing claimable is dropped from the pool and another drawn; only an
  exhausted pool claims unfiltered, because a lane idling while the queue holds work trades the
  crawl for its shape.

<a id="directed-slots"></a>**Directed slots** (`B-61`, `B-68`, `B-86`). `directed` narrows a claim
to work something *chose* rather than followed: search results, queries, seeds and cited papers
above the priority floor. A 12-hour run without it fetched a few hundred times more followed
links than search results, because a large frontier out-ranked them all by tier. The slot is
drawn through the same attention vector as any claim, so it never spends a topic's share
outside the pool, and falls through to the ordinary draw when nothing directed waits.
`MERIDIAN_WORKER_DIRECTED_EVERY` went from one claim in three to one in two (`B-86`): search
results were on a topic about three times as often as followed links, and at one in three they
were a sixth of fetches. Within the slots, cited-paper lookups take one turn in three
(`LOOKUP_EVERY`) and search results, seeds and queries the rest. Ranked together, a topic with
thousands of well-cited DOIs spent every slot on lookups; alternating evenly (`B-68`) still gave
lookups, on a topic about as often as a coin toss and often deferred for want of an API key, as
many slots as search results. Lookups are claimed only in their own turn, never in a topic's
ordinary draw, which is for pages.

**Backpressure** pauses claiming at `MERIDIAN_WORKER_MAX_EMBED_BACKLOG` (`B-61`) because a CPU
embedder falls behind a free-running crawl by an order of magnitude.

### Settling

- **Attempt numbers count from one.** `queue.attempts` counts *finished* attempts, so the
  attempt being made is `attempts + 1`; passing it through unchanged would file every retry as
  a first try, and the log exists to tell a URL that failed once from one failing all week.
- **A page stops at `fetched`; a sitemap or query goes to `done`.** Extraction and embedding
  are still ahead of a page; reading a sitemap or running a query *is* the whole of its work,
  and leaving it at `fetched` would advertise a source row that will never exist.
- **Retries back off exponentially with full jitter**, a draw from `[0, d]` rather than
  `d ± ε`, because tasks that failed together otherwise retry together and turn a recovered
  outage into a thundering herd. A dead site then costs one attempt per backoff window, not one
  per loop iteration (§13.4).
- **Failed rows stay.** Past `max_retries` a task is marked `failed` with its error, the only
  record of why a URL never made it in.
- **A 304 is done.** The conditional request paid off and the content is already in the
  corpus, so there is nothing to pass down the pipeline.
- **Retry floors for cached causes.** An unreadable robots.txt refuses its origin for
  `ERROR_TTL_S`, and the ordinary backoff (seconds) would spend every retry inside that window
  being served the cached "no", failing the URL permanently over one blip. `fail(floor_s=…)`
  holds the retry back until the refusal expires.
- **Unreadable is a refusal.** An HTML error page or a document with a DTD gives the same
  answer every time.

<a id="refusals"></a>**Refusal or failure: two questions.** `queue_disposition` asks whether
*this URL* is worth asking for again; `policy.domain_signal` asks whether *the domain* is still
worth crawl budget. A 404 is the domain working perfectly and the URL permanently gone, so the
two disagree about it. Refusals are deterministic in the retry window: robots.txt and the block
list say the same thing in five seconds, the page is still the size it is, the body still will
not decompress, and the address is still the one `netguard` refused. `abandon` ends those at
once, still counting the attempt so `queue.attempts` stays an honest count of what a URL cost.
`too_many_redirects` and `decompression_bomb` are refusals for the URL *and* count against the
domain: a hostile or misconfigured response answers yes to "back off the domain" and no to "ask
for the URL again".

For the domain there are three answers, because two would force outcomes into judgements they
do not support (`P1-05`):

- **Alive** resets the consecutive-failure counter: the domain answered, and what went wrong
  was about the URL (a 404, an oversized file, a media type off the allowlist). A domain
  serving nothing but 404s is a different problem; `fetch_attempts` shows it.
- **Unreachable** counts: timeouts, connection errors, redirect loops, and a decompression bomb
  or an address `netguard` refuses, which it is affirmatively wrong to keep requesting.
- **No evidence**: no request went out. Counting a robots denial would auto-block every
  well-behaved site with a strict robots.txt, and counting a refusal to fetch a blocked domain
  would make the block deepen itself. `robots_unreachable` is the same: one failed read is
  cached and served to every task on the origin.
- `http_error` is decided by its status: 5xx is the server failing and 429 is the server saying
  stop, both reasons to back off; every other 4xx is the domain answering correctly. An outcome
  nobody classified is logged, not fatal; a drift test over `FETCH_OUTCOME` keeps that branch
  unreachable.

<a id="refusing-domains"></a>**Domains that refuse everything** (`B-114`). A 403 is "alive" for
one URL, so a domain that answers 403 to *every* request never accumulates failures, and each
link to it costs a politeness slot for nothing. Publishers that refuse crawlers outright are the
common case, and scholarly search hands their URLs back constantly. The rule is not a run
length: domains that serve pages also return long runs of 403s (a forbidden section, a bot check
on some paths), so any run short enough to help would block sites the corpus reads. It is "never
once anything but a refusal", counting only requests that went out, and only those after a
person last edited the domain's row, so an unblock in Admin gets a fresh window. The block
expires after the window, because a blocked domain is never requested and could otherwise never
show it had stopped refusing (a bot check relaxed, a contact address configured). Blocks anyone
else made are not touched.

### Keeping a fetch

- **The loop keeps the bytes, the crawler logs the attempt.** The attempt log is written inside
  `Crawler.fetch` because every caller wants one, and a log with holes in the paths nobody
  thought about is worthless. The corpus is not something a liveness probe or an ad-hoc refetch
  should write to, so keeping lives in the loop, before the settle, because `result.content`
  exists only in memory. For the same reason the fetcher carries the robots.txt sitemaps it saw
  on the result instead of queueing them.
- **A fetch that could not be kept is retried** (`NotKept`). The cause is almost always local
  and transient (a full disk, Postgres restarting). Advancing anyway would leave a URL the
  queue believes was fetched and the corpus has never heard of, with nothing ever asking for it
  again; failing it means a backoff, two more tries, and then a visible `failed` row.
- **Unchanged content is a checksum match, not only a 304.** Most origins do not implement
  conditional requests, so a 200 with byte-identical content is the commoner way to learn a page
  has not changed, and it lets extraction and embedding be skipped on a re-crawl.
- **Why a URL was fetched** (`crawled_for_topics`, `P2-14`, `P2-21`) is the claim's topic,
  recorded at fetch time because only then is the queue row in hand: a URL can be queued under
  several topics, and after a redirect the fetched URL is often not the queued one. What a page
  turned out to be about is `topic_labels`, written later from vectors. The two used to be one
  column, and a crawl pursuing one topic stamped it on every page a site's navigation led to. An
  empty list means the claim carried no topic, a fact worth keeping.
- **The domain verdict is folded in before the page is written** (`P4-14`), so the page is
  stored under its domain's state including what this fetch showed; afterwards would leave the
  first flagged page on a domain looking clean.
- **Novel documents earn a domain its seeding allowance** (`P4-12`), counted at store time
  rather than per fetch, so a site serving one page under a thousand URLs cannot approve itself
  on volume.

<a id="loop-stages"></a>**Stages the loop runs for other features.** These happen in the fetch
pass because what they need lives only in memory then; their reasoning belongs to their own
features.

- *Injection screening* (`P1-23`, [source-quality.md](source-quality.md)) runs on the raw HTML
  and on the extracted text, because they answer different halves: hiddenness is a DOM property
  extraction discards, and what survived extraction is what a model reads. The DOM half is
  HTML-only (a PDF or `.docx` hides text by other means and needs a different screen); the text
  half runs on anything that extracted.
- *Extraction* ([extraction.md](extraction.md)) is routed by media type (§6.6). A format with no
  extractor yet returns nothing rather than raising: a metadata-only source (§6.5) is a resting
  state, not a failure. Extraction failing is not `NotKept`: the bytes are stored and can be
  re-extracted when the extractor improves (§11.12). A missing PDF tool is logged loudly, once
  per document, because a worker that quietly lost poppler stops growing the corpus with no
  other symptom. The extractor name is stored including failure names (`P1-44`), the only way to
  tell `pdftotext-failed` from a document that simply had no text. OCR columns are written after
  the source upsert, so a scan's `text_available=False` is not overwritten by the view that it
  had no text.
- *Bibliographic fields* fill only what an extracted document provides, so a format with no
  extractor writes nothing over what a previous fetch established. Citations ride in
  `sources.extra`.
- *Chunking* happens here, not in a later sweep, because a `background` source keeps no raw file
  (§5.4). It is skipped when the checksum is unchanged, or the slow loop would get a day of
  "new" material it already read (§6.3's high-water mark is a chunk id). Chunks, figures and the
  source row are one transaction (`P1-10`): a source whose text came from one fetch and figures
  from another describes a document that never existed. Furniture is left out of the chunks but
  never cut from the text (`B-43`), and the document kind (`B-59`) is classified from that same
  text so a footer of links cannot make an article look like an index, and the fetch path reads
  what `worker.dockind` reads back. A listing is not chunked at all: its value is its links, and
  every downstream pass reads chunks, so not chunking is the one exclusion no pass can forget.
  What an earlier fetch cut is superseded, never deleted.
- *The health line* is §12.5's, less "edges added", which is the orchestrator's. It carries the
  novelty pass rate since `P2-03` made the gate a worker pass: a collapsing rate means a mirror
  or a site serving one page under every URL, which looks healthy in every other number. Search
  and the browser report `configured` / `unreachable` / `absent`: absent is a deployment that
  never meant to search or render; unreachable is the silent failure, since the fetcher
  degrades to static and an unsearchable crawl drains its frontier into an idle that looks like
  a finished one. A missing search backend is logged louder than a missing browser for that
  reason. The browser probe never raises.

### One polite fetch

`worker.crawl` is the seam between the fetcher, which knows how to get bytes safely, and the
decisions about a domain, which need the database. Refusals are cheapest first (`P1-04`): a
blocked domain costs a policy lookup, robots.txt one cached request per origin, and only then
does the request wait for its domain's slot, followed by the validators from last time (an
unchanged page becomes a 304 with no body). Every path ends in one `fetch_attempts` row and the
policy consequence of its outcome, in the same transaction (`P1-19`, `P1-05`).

`policy_overrides` exists for fetches that are not corpus content (sitemaps, as robots.txt
already does): the media allowlist is the wrong question for them. Everything that makes a
fetch *safe* rather than *selective* (robots, the rate limit, `netguard`, the size and
decompression caps) still comes from the resolved policy, so an override cannot make a fetch
the policy would refuse.

**Rate limits** (§6.4). `concurrency_per_domain: 2` with `delay_per_domain_ms: 1000` means at
most two requests outstanding and starts no closer than a second apart. Concurrency exists so a
domain taking 30 seconds per response cannot consume the worker; delay spaces *starts*, because
spacing completions would let two slow requests start together. The limiter holds its gate
across the sleep: released first, every waiter would read the same next start and fire
together, a burst wearing a delay's clothing. State is per process on purpose; a shared limiter
would need a coordination service the queue design exists to avoid, so two workers against one
domain double the figure knowingly. A policy edited mid-run applies to new requests while those
holding the old semaphore finish, briefly exceeding the new figure rather than tracking permits
by hand.

<a id="robots"></a>**robots.txt** (§14.2). `urllib.robotparser` was rewritten for RFC 9309 in
Python 3.13; before that it ignored wildcards and took the first matching rule rather than the
longest. On two ordinary patterns, `Disallow: /*.pdf$` and an `Allow` longer than a
`Disallow`, it gave opposite answers on 3.12 and 3.13, so conduct depended on the interpreter.
The parser here is RFC 9309 §2.2: longest match wins, `Allow` breaks a tie, `*` matches any run
and `$` anchors the end. Group selection (§2.2.1) takes the group whose name is the longest
prefix of the product token, and `*` only when no named group matches: a site with a
`MeridianBot` group and a stricter `*` group is saying to ignore `*`, not to obey both.

- robots.txt goes through the same rate limiter and SSRF guard as pages: honouring a delay for
  pages and not for robots.txt misreads which is the courtesy, and `/robots.txt` on a hostile
  host is a fetch like any other. The content-type allowlist and the browser are dropped for
  it: servers label it `text/plain`, `text/html` and `application/octet-stream` about equally.
- One lock per origin, because at the start of a crawl a lane claims many URLs from one domain
  and every one would otherwise miss the empty cache and fetch the same file.
- An unreadable robots.txt (5xx, timeout) refuses the origin under its own outcome,
  `robots_unreachable`, not `robots_denied`: the site has said nothing yet, so the URL is
  retried once robots.txt can be read. The refusal is cached for minutes (owned by
  `robotscache`, because the queue's retry floor must agree); a day would take a domain out of
  the crawl over one 503.
- Sitemaps a robots.txt advertises are read whether or not the requested path is allowed: a
  domain disallowing one path still states what it wants crawled.
- **The cache persists** (`P1-29`), or a restart re-fetches robots.txt for every origin through
  the same per-domain slots as pages and spends its first minutes asking permission. In memory,
  entries expire on the monotonic clock, which NTP cannot move; persisted, on the wall clock,
  because a stored monotonic deadline means nothing after a reboot. `missing` and `unreachable`
  both store no body and mean opposite things (allow everything, refuse everything), so the
  outcome is a column; inferring it from `body IS NULL` would turn every outage into consent.
  Entries are written on their own committed transaction, because the crawl loop's transaction
  spans a page fetch and an entry held until then is lost whenever the page fails. Every
  cache function swallows database errors, since a cache that can stop the crawl is worse than
  none: a failed load is a miss and a failed save is a fetch that happens again. `load_many`
  (`B-90`, for the prefilter) raises to the caller's savepoint instead, because a failed
  statement must be rolled back before the caller's transaction is usable. Expired rows are not
  deleted on read, so a read-only session can load; the table is bounded by distinct origins,
  and `purge_expired` exists only for a clean start.

### Fetching safely

`worker.fetch` has two paths under one policy (§6.4, §11.8; `P1-03`, `P1-21`, `P1-24`): static
HTTP with `httpx`, and Crawl4AI's browser for pages that need JavaScript. Every refusal returns
a `FetchResult` with a `fetch_attempts.outcome` rather than raising, so refusals are counted,
not swallowed at some call site.

- **SSRF** (`netguard`). Crawl targets come from untrusted pages, so a link to
  `169.254.169.254` or `192.168.1.1` is the ordinary case. Checks run after DNS, on addresses:
  a public name can have an A record of `127.0.0.1`. Literal spellings (`::ffff:127.0.0.1`,
  `2130706433`, `0x7f000001`) are parsed first; `getaddrinfo` resolves them without complaint,
  and refusing early logs the honest reason. `block_mixed_dns` refuses a name if *any* address
  is private, the rebinding defence against a name that answers with one public and one private
  address. Anything unclassifiable is refused: failing closed loses a page, failing open loses
  the network.
- **Pinning.** An HTTP client does its own DNS lookup when it opens the socket, and nothing says
  the second answer matches the first; that gap is DNS rebinding. Requests go to the validated
  IP with `Host` and SNI set to the name, so the certificate is still checked against the name.
  httpx then pools per address, which is right, since the address is what the connection is to.
- **Redirects by hand.** A client-followed redirect connects without handing the new URL back
  for judgement, so each hop is resolved, re-validated and re-pinned. Resolution happens on
  every hop even with `revalidate_each_redirect: false`, which relaxes only the *verdict* on later
  hops, a per-domain escape for a chain that trips the classifier, not a way to turn the guard
  off. An `http://` link may be followed, but a final response still in plaintext is refused
  when `require_https_final` is set: anyone on the path can rewrite it, and this crawler feeds
  a model with write tools. That check runs after the response, because only then is the last
  hop known, and re-running DNS would open a fresh rebinding window.
- **The browser path is validated, not pinned.** Crawl4AI does its own DNS and connecting in its
  own container. The URL is validated before and the URL it landed on after, since the browser
  follows its own redirects. Crawl4AI's egress proxy helps, but the defence that survives an
  application bug is `P1-25`: no route to private address space from the fetching process.
- <a id="decompression"></a>**Decompression is driven by hand.** Letting httpx decode hands back
  whatever one network read inflates to before any cap can look: a 64 KB read of a gzip bomb
  arrived as one 67 MB chunk. Raw bytes are pushed through `zlib` in bounded steps
  (`decompress(data, max_length)` parks the rest in `unconsumed_tail`) and both caps are
  rechecked between steps. `max_page_bytes` bounds an honestly large page; the ratio bounds a
  small one lying about its size and fires about twenty times sooner. Ordinary HTML gzips around
  5:1 and repetitive pages reach 20:1 honestly, so the ratio counts only above
  `RATIO_FLOOR_BYTES`, which a bomb clears in its first few chunks. Only gzip, deflate and
  identity are handled: that is what the fetcher asks for, and guessing at anything else is
  worse than refusing it.
- **Content types.** An empty allowlist means no restriction (the state before the global row is
  seeded, where refusing every page would be worse); a response with no Content-Type passes an
  empty allowlist and fails any other.
- **Why a 403 happened.** A bot challenge, a geo-block, a forbidden path and an expired
  credential are four problems that arrive as the same three digits, so the reason headers are
  recorded (`P1-19`). The one interpretation made is naming Cloudflare's managed challenge
  (`cf-mitigated: challenge`), the commonest reason a public page refuses a well-behaved
  crawler, which will 403 forever until something renders JavaScript. The browser path gets the
  same diagnosis, since Crawl4AI's error message does not say.

<a id="rendering"></a>**Rendering.** `render_js: auto` fetches statically first and re-fetches
through the browser only when the HTML looks like a shell: one cheap request on JS pages, no
browser launch on everything else. The text floor is checked first, so a page with a paragraph
of prose is never rendered however many scripts it loads. Crawl4AI is used for markdown and
citations only: no LLM extraction (§2 principle 1), no stealth, no proxy escalation; it needs
`CRAWL4AI_API_TOKEN`, without which it binds loopback in its container and is unreachable. The
browser gets its own generous timeout. A *challenge* is the one refusal a browser can sometimes
turn into a success: the non-interactive kind runs a few seconds of JavaScript and serves the
page, so the render holds the page open (`challenge_wait_s`, bounded because the non-interactive
kind clears in about five seconds; 0 disables the re-fetch) and is tried once. The interactive kind never resolves (§6.4 declines to defeat it).

A domain that needed the browser on three consecutive `auto` fetches (`P1-27`) goes straight to
it: two is a coincidence and ten is a day of paying double. One static success resets the
count. Only `auto` fetches are evidence, since a domain already going to the browser renders by
construction; and the conclusion expires after a week, because such a domain can never produce
evidence to the contrary, so the first correct conclusion would otherwise be permanent. Three
double-fetches per domain per week is nothing against a crawl. Learning only ever turns `auto`
into `always`, so it cannot overrule an operator's choice, and keys off the merged value because
the global row carries `auto` as the shipped default. The count is written in the attempt's
transaction.

### Fetch policy

Resolution is per-domain row, then the global `'*'` row, then file defaults, each filling gaps:
one setting is wrong for a large API or a small municipal server in one direction or the other.
The YAML is a floor so an unseeded database fails predictably, not with a `KeyError` mid-fetch
(§13.1). Keys in the global row that are not fetch settings (`source_tiers`, `frontier`,
`search_languages`, `steering_proposal_window_hours`, `display_timezone`) are stripped by
`resolve_policy`, from one list shared with Admin's resolved view because two copies had drifted
and the missing key showed as a setting on every domain. `frontier` is there because it says
what enters the queue, not how a request is made. The source tier map is read from the global row
by its own lookup: a tier is a property of a source, mechanical (§5.2), not of a request.

### The attempt log

`fetch_attempts` gets one row per fetch, refusals included (`P1-19`). `fetch_policy` holds a
counter that resets and `queue.error` only the last message, so neither answers "what is the
success rate today" or "has this domain served nothing but 404s for a week". A robots denial or
a blocked domain is something the crawler did, and a log of only requests that went out cannot
tell a quiet crawler from a stuck one. The caller owns the transaction, so the row and its
policy consequence commit together. A 304 counts as success, or the rate would fall as caching
got better. `domain` is the *requested* URL's, so a redirect off-site is charged to the domain
that caused it. `prune_attempts` deletes in batches so a long-overdue prune never locks the
table.

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
