# Embedding

Every passage gets a 1024-dimensional vector from one model, `BAAI/bge-m3`. Semantic search,
topic labels, the novelty gate, the map and host scores all depend on those vectors. So
embedding is the bottleneck that decides how fast a fetched page becomes useful. It runs as
its own service, separate from the crawl, ordered so that the most valuable passages are
embedded first.

- **Code:** `services/worker/worker/embed.py` (backfill), `embedserver.py` (the service),
  `embeddings.py` (model settings), `vectors.py` (choosing the service or a local model),
  `reembed.py`, `fetchmodel.py`; `packages/meridian_core/meridian_core/chunks.py` (tiers),
  `embedder.py` (client), `embedtext.py` (what text is embedded)
- **Tasks:** `P2-01`, `P2-17`, `P2-19`, `B-25`, `B-49`, `B-61`, `B-66`, `B-75`, `B-76`,
  `B-89`, `B-127`, `B-129`–`B-131`
- **Decisions:** [ADR 0001](../adr/0001-production-runs-on-a-server.md),
  [ADR 0007](../adr/0007-half-precision-vector-index.md)

## How it works

```
embedder (service)   one copy of the model, resident; POST /embed, GET /health
     ▲
     │ requests of ≤ 256 texts
embed (backfill)     loop: draw a batch from the highest tier with work ─► embed ─► store
     │
chunks.embedding IS NULL  is the whole queue
```

**Why a separate pass.** The model is gigabytes of weights the fetch path never needs. In the
crawler it would bloat every lane and stall fetches. A passage can therefore exist for a while
before it has a vector. Search reports that honestly (`degraded`), and the backlog is a number
on the health line.

<a id="tiers"></a>**Tiers** (`embed_tier`, `B-66`). Each batch is drawn from the highest tier that has work, and
the tiers are re-checked every batch:

| Tier | What is in it | Order |
|---|---|---|
| `first` | Directed pages (search results, seeds, cited papers), pages on hosts judged on a topic, and the sample of any page on a host not judged off-topic (`B-160`) | Newest first, so labels and host scores react to the latest crawl |
| `then` | Everything else that is not junk: in practice, the released rest of long documents | Oldest first |
| `last` | Off-topic hosts; the rest of a long document whose sample did not earn it (`B-89`, with a higher bar from 1,000 passages, `B-133`); copies of earlier sources (`B-127`) | Oldest first |
| none | Junk | Never embedded |

**An unjudged host's pages are how it gets judged** (`B-160`). Followed links into hosts nobody
has judged used to sit in `then`, oldest first, behind every page the crawl had just queued in
`first`; during a crawl that is roughly all of the embedding capacity, so after loop run 18 a
thousand such pages waited with no passage embedded, and the hosts `B-150` and `B-155` explore
(vouched-for, promising, unknown) could not be judged until the crawl stopped. Their samples now
go first. Exploration is capped per host, so this adds tens of passages per host, not a backlog.

<a id="sampling"></a>**Long documents are sampled first** (`B-89`). The first 16 passages and every 16th after them
are embedded and labelled. The rest waits in `last` unless the sample's best topic score
clears the triage floor. Holding back delays a document; it never drops one. See
[topics.md](topics.md#triage-of-long-documents).

<a id="the-embedding-view"></a>**What is embedded is a view of the text** (`B-49`). Link syntax becomes its visible text and
bare URLs are dropped, so pages do not cluster by the shape of their links. The stored text,
the lexical index and every citation are untouched. `reembed` updates vectors whose view
changed.

<a id="backpressure"></a>**Backpressure** (`B-61`). The crawl pauses while more than
`MERIDIAN_WORKER_MAX_EMBED_BACKLOG` passages in the `first` and `then` tiers wait. The `last`
tier does not count, or the crawl would pause for good behind off-topic text.

<a id="the-vector-index"></a>**The vector index** (`B-136`, ADR 0007). Passages are found by an
HNSW index built over half-precision copies of the vectors,
`(embedding::halfvec(1024)) halfvec_cosine_ops`; the stored vectors stay full precision. On a
200,000-passage benchmark against exact nearest neighbours, the half-precision index was a
third of the size, with recall@10 within a point of full precision and lower median and tail
latency. A query uses the index only if it orders by exactly the indexed expression, so
every passage nearest-neighbour query goes through `vectorindex.indexed_distance`. A test
fails if one orders by the plain column, which would silently become a full scan. Bridges
deliberately avoid the index (`+ 0`) to get an exact answer within a subset.

<a id="filtered-scans"></a>**Filtered scans** (`B-151`). An HNSW scan offers its nearest
candidates and the query's `WHERE` runs afterwards, so when the nearest rows are all filtered
out a query can return nothing or a far row. pgvector 0.8's iterative scan keeps going until
enough rows pass; `vectorindex.scan_past_filtered` turns it on, in strict order, for the rest
of the transaction. The novelty gate uses it in strict order, so its `LIMIT 1` is the nearest,
with the same raised scan limit as search (`B-161`): a boilerplate passage can have tens of
thousands of marked copies between a new copy and the original, and at pgvector's default the
scan gave up and the gate found no neighbour (seen as an intermittent test failure on a fresh
database after the whole suite had filled the index with near-identical rows).

Search's vector arm and the neighbourhood's similar passages use it in relaxed order, which is
cheaper and comes back almost sorted, and sort what comes back (`B-152`). They also raise
`hnsw.max_scan_tuples` to `MAX_SCAN_TUPLES` (100,000), because pgvector's default of 20,000
stopped a one-topic scan with a third of its results. Measured on a live corpus of about
850,000 passages, 20 sampled query vectors, 100 asked for, recall against an exact scan:

| Filter | Before: returned / recall | After: returned / recall | After: p50 |
|---|---|---|---|
| none | 95 / 0.91 | 100 / 0.96 | 8 ms |
| official sources | 43 / 0.41 | 100 / 0.94 | 14 ms |
| peer-reviewed | 27 / 0.27 | 100 / 0.95 | 54 ms |
| one topic (smaller) | 1 / 0.01 | 100 / 0.87 | 600 ms |
| one topic (larger) | 1 / 0.01 | 100 / 0.91 | 300 ms |

An exact scan of a topic's passages took about two seconds. The cost is the narrow filters'
latency, up to about a second and a half at the worst, against an arm that returned nothing. A test that wants to see the index at work on the small dev
table has to switch off sorting as well as sequential scans, or the planner sorts exactly and
the miss never shows.

## Design choices

- **One model, checked on every response.** A vector from a different model is meaningless
  against this corpus, and nothing downstream can detect the mix. So the client refuses a
  service that names a different model.
- **The service is preferred, the local model is the fallback.** If the service does not
  answer, the backfill loads the model itself and logs why. Remote-only mode
  (`MERIDIAN_EMBED_REMOTE_ONLY`) forbids that, for machines that cannot spare the memory or
  have no GPU beside a GPU service.
- **Requests are capped at 256 texts.** The backfill splits larger batches into several
  requests (`B-130`). Before that fix, a bigger batch silently moved embedding onto the
  backfill's CPU.
- **Batch size follows the hardware.** On a CPU it is one passage: measured, every larger
  batch was slower because of padding. On a GPU it is sized from the card's memory, not the
  container's RAM (`B-129`), up to `MAX_AUTO_BATCH`. bfloat16 is used on CPUs that support it
  in hardware, since it doubled throughput with vectors within 0.998 cosine. int8 was rejected
  because it reordered neighbours.
- **Vectors are normalised**, because the novelty gate compares raw cosine against a fixed
  threshold.
- **The service has no credentials and no route out.** Weights are fetched once, by a one-shot
  container (`fetchmodel`), into a volume the service mounts.

### The model

§4 picks bge-m3 for one reason, and it is the reason not to swap it for something smaller when
embedding is slow: much of the serious literature for the comparison set is not in English
(§14.1). An English-only model would bias the corpus toward Western sources while every
measurement of it looked fine.

- **Nothing loads until a vector is asked for.** `worker.main` imports the package tree and the
  fetch loop never embeds, so loading at import would put gigabytes in the crawler's memory for
  nothing. `sentence_transformers` is imported inside the model accessor, so a worker built
  without the `embed` extra can still import the module and report the absence.
- **The dimension is asserted.** `chunks.embedding` is `Vector(1024)`, so a 768-wide model
  would fail far from the cause, or succeed against a widened table.
- **Normalised, always**, and rescaled in float64 after the model: in bfloat16 the model's own
  normalising leaves a norm off by a few parts in a thousand, enough that a dot product stops
  being a cosine, and the novelty gate compares raw cosine against a fixed threshold.
- **Token cap.** bge-m3 takes 8192 tokens, far more than a chunk (2000 characters at most), but
  the cap is set lower (1024) so that anything else embedded, a query or a node description, is
  truncated deterministically.
- **CPU batch of one.** Measured on a 24-thread x86 machine, bge-m3 ran at 119 passages/min with
  a batch of 1, 113 at 2, 106 at 4, 99 at 8, 78 at 16, 67 at 32 and 51 at 64: a batch pads every
  passage to its longest, and a CPU gains nothing from grouping to pay for it.
- **Precision.** On a CPU with bf16 instructions (x86 `avx512_bf16`/`amx_bf16`, arm64 `bf16`),
  bfloat16 took throughput from 145 to 297 passages/min on the same machine, with vectors at a
  cosine of at least 0.998 to float32's and 98% of the same five nearest neighbours, close
  enough to sit beside stored float32 vectors. int8 dynamic quantisation reached 397/min but
  was rejected: cosine fell to 0.905 and a fifth of the neighbours changed. Without the
  instructions bfloat16 is emulated and slower, so the default follows the hardware.
- **Memory.** A container sees the host's `/proc/meminfo`, so the visible memory is the lowest
  of the cgroup v2 and v1 limits and `MemTotal`. A GPU is sized from its own memory: the
  embedder is held to a few GiB of RAM while the card may have tens, and sizing from RAM gave a
  GPU a batch of two (`B-129`).
- **A protocol, and a fake.** `Embedder` is a protocol, so the novelty gate, search and their
  tests run without the model, and another runtime is a substitution rather than a rewrite.
  `FakeEmbedder` returns real unit vectors derived from the text, so identical text embeds
  identically and cosine behaves, without a model download in CI.

### The backfill

§6.1 draws embedding inside the fast loop, and this runs it as a separate pass: the model is
weights the fetch path never touches, and in the crawler every lane would carry it, the image
would quadruple, and a slow encode would stall unrelated fetches. The schema was built for the
split (`chunks.embedding` is nullable). The cost is a window in which a passage exists but is
not semantically searchable, reported rather than hidden. It is deliberately not part of
`worker.main`'s housekeeping: the crawler must work on a machine with no model at all (§2.1).

- **Resumable by construction.** `embedding IS NULL` is the whole queue, with no cursor to
  corrupt. One batch is one transaction, so an interrupted run keeps everything up to its last
  batch. The queue is paged by id, never OFFSET, which would re-scan and shift as the crawl
  writes underneath. The cursor moves past a failed batch, which is counted and left for a later
  pass rather than retried for ever; with `newest_first` (`B-75`), which has no advancing
  cursor, the failed batch's ids are excluded instead.
- **The heartbeat is written before each batch** (`B-28`), and while idle. A batch can take
  minutes, so a heartbeat written only on completion went stale during normal work, the probe
  firing at a healthy process; and with an empty backlog the loop sleeps, which must not read
  as dead either.
- Text is read out of the ORM before encoding, so no database connection is held across it.
  The vector count must equal the text count (`strict=True`): a mismatch would pair every chunk
  with another's meaning, a corruption no later check catches. A chunk that vanished between
  read and write was replaced by a re-crawl and its successor is already queued, so it is skipped
  rather than failing the batch.
- **Why tiers.** The embedder is the slowest stage, so under a free crawl its backlog is
  permanent, and oldest-first would spend it on whatever was fetched first. Labels, host
  judgements and steering all wait on a vector, so the newest directed passages are the ones
  whose embedding tells the crawl something. The backlog is on the health line because one that
  only grows means the embedder stopped, otherwise invisible while the crawl keeps working.
- **Why sampling.** The large documents a crawl brings back are mostly off-topic (bills, data
  dictionaries, index pages of thousands of passages), and each cost hours of embedding before
  the labeller could say so. The sample spans the whole text rather than its front matter. The
  16/16 shape was chosen by labelling fully embedded sources both ways: the sample's best score
  was within about 0.015 of the whole text's at the median, for about a sixth of the embedding
  on sources of 40 passages or more.
- **Copies are last, not excluded** (`B-127`): nothing a reader sees uses a copy's vectors, but
  the mark is re-judged daily and the near rule compares mean vectors.

`reembed` (`B-49`) replaces a vector in place, never clearing it first, so a chunk stays
searchable until its new vector lands. Where the view equals the stored text it records only
the view version.

### The service

`P2-07` left the API unable to embed a query, and both obvious fixes were wrong. Loading the
model in the API puts gigabytes and a cold start inside a request path. Accepting a vector from
the caller is worse than it sounds: not mainly for security (`<=>` takes a vector, not SQL) but
because **a vector from a different model is meaningless against this corpus**. Two models'
embeddings of one phrase are points in unrelated spaces, and comparing them computes without
error: plausible, ranked, confident nonsense. So the query is embedded by the same model, on
this side of the boundary, by a sidecar: the pattern of
[guides/connectors.md](../guides/connectors.md) §4, a container with no credentials and a client
that returns None when it is absent. `meridian_core` holds only the client, since the API and
the orchestrator must not acquire a model dependency.

- **Absent is supported; unavailable is an outage.** No `MERIDIAN_EMBEDDER_URL` means
  lexical-only search, reported as `degraded`. A configured embedder that does not answer
  raises `EmbeddingUnavailable`; reporting it as "not configured" would hide an outage behind a
  feature flag. `EmbedderMismatch` subclasses it, because every caller's response is the same
  (degrade or fall back), and the danger of treating it as lesser is that the vectors *work*.
  The model name is checked on every response, since a sidecar can be restarted with another
  model under a running client.
- **The server** runs from the worker's image, which already carries the model's libraries, as
  a different command, the way `embed` and `novelty` are. It has no database connection, no
  `env_file` and no egress, on `internal`, for the reason `crawl4ai` holds no passwords
  (`P1-22`). Loading is lazy; `/health` reports liveness and whether weights are resident, so a
  supervisor can tell "starting" from "wedged", and stays open without the shared secret
  (`P3-12`) that guards the port when the sidecar is on another machine. The model runs in a
  thread so an encode does not stall `/health`, and in slices of texts (`B-82`), checking
  between slices that the client is still there: a backfill restarted mid-batch, which every
  deploy does, used to leave a batch running for nobody.

<a id="timeouts"></a>**Timeouts** (`B-27`). A batch's timeout is sized from a per-text allowance
times the batch. The two defaults once contradicted each other: the backfill sent 256 chunks per
request, each taking the better part of half a second on CPU, to a client that waited thirty
seconds. Every batch timed out and the backfill fell back to loading the model itself
(`P2-19`). The vectors were correct, so nobody noticed; the symptom was a second copy of the
weights, the thing the sidecar exists to prevent. The allowance is generous rather than
measured: over-waiting on a working sidecar costs nothing.

### Service or local model

`P2-17` put one resident model in the sidecar, and the backfill went on loading its own, so a
stack held two copies. `vectors.PreferRemote` asks the sidecar first and loads locally only when
there is none to ask.

- **Falling back is right; falling back silently is not.** The symptom would be memory pressure
  with no log line to explain it, so the switch is logged once, with the reason, and
  `build_embedder` logs which path a pass took, the first question asked of a slow or hungry
  backfill.
- **A sidecar running another model is refused**, logged apart from "down": somebody pointed the
  backfill at the wrong service, and one successful batch would leave the corpus unsearchable in
  a way nothing reports. The local model is the right one, so this falls back rather than fails.
- **The switch is one-way within a process.** Once the local model is loaded the memory is spent,
  and switching back mid-pass adds doubt about which produced what. The next pass asks the
  sidecar again. The local embedder is built from a factory, since avoiding its construction is
  the point, and it runs in a thread so a `SIGTERM` during a batch is honoured promptly.
- **The sidecar is asked for a while before falling back** (`B-76`). After a joint restart it
  takes most of a minute to load; asking once, the backfill gave up in seconds and loaded a
  second copy that competed for the CPU.
- **Requests are split at `MAX_TEXTS`** (`B-130`). Asked for more, the client raised
  ValueError, which reads as "unusable", so raising the batch for a GPU quietly moved embedding
  onto the backfill's CPU, or with remote-only failed every batch.

### Fetching the weights

The sidecar sits on `internal`, which has no gateway, DNS or route out, and the weights are not
in the image. On a fresh stack it once started, answered `/health` with `loaded: false` (truthful
and normal for an unused lazy model), and failed every embed after a timeout, for ever (`B-14`).
`fetchmodel` is a one-shot container on `egress` that downloads into the volume the sidecar
mounts and exits. Giving the sidecar egress would be a route out for anything that ever got into
a container that processes crawled text. Baking the weights into the image would push gigabytes
on every release, twice for a multi-arch build, for weights that do not change. The step loads
the model and embeds one string, so a half-downloaded cache fails here rather than at the first
real batch. It is idempotent and runs before every `make quickstart`. Without a configured cache
directory the library uses a container layer nothing mounts, and the weights are silently
downloaded again on every recreate.

The view (`B-49`) exists because a chunk's stored text keeps its Markdown links (its offsets are
citations), and a URL carries almost no meaning. Measured over a real crawl, one character in
eight of chunk text sat inside a link target, and one chunk in ten was more than 30% URL.
Embedded as-is, pages clustered by the shape of their links, and a search for a subject found
pages that merely linked to it. `link_text_share` measures how much of the view is link labels:
a chunk that is mostly labels is a listing (a table of contents, a directory, a publication
list) whose vector averages the things it links to, and `P2-24` reads it to keep listings from
being labelled as passages about a topic. A label that is itself a URL, as extractors render
most reference lists, counts as link text although the view drops it, pushing a bibliography
towards 1.0; that is deliberate and was measured.

## Configuration

All variables are in [reference/environment.md](../reference/environment.md#embedding).

**On a GPU:** use `deploy/gpu/gpu-embedder.yml`
([guides/deployment.md §3b](../guides/deployment.md)). It reserves an NVIDIA device for the
service, raises the backfill's batch, and sets remote-only. The start-up log should say
`memory_of: device`; `host` means the card was not seen.

## Operating it

- `docker compose logs embed` shows "embedded a batch", with a running total.
- Backlog by tier:
  `embedding_backlog(sess)` and `embedding_backlog(sess, valuable_only=True)` from
  `meridian_core.chunks`.
- Rough throughput on a 24-thread CPU: about 5 passages a second. A GPU should be one to two
  orders of magnitude faster. Measure it and record the figure in `B-131`.

## Failure modes and traps

- **A fresh stack's service never loads.** It has no route out, so the weights must be
  fetched first: `docker compose run --rm modelfetch`.
- **A NULL comparison in a tier predicate drops passages from every tier** silently and
  forever. The hold predicate spells out `IS NOT NULL`, and a test checks that every passage
  falls in exactly one tier.
- **Loop runs lift the ceiling and normal operation does not.** After a run the backlog can
  sit above the ceiling for hours while it drains. That is expected.

## Tests

`tests/integration/test_embed_tiers.py` (tiers, ordering, sampling, copies),
`tests/unit/test_embeddings.py` (settings, batch sizing, GPU memory), `test_vectors.py`
(service versus local, splitting, remote-only), `test_embedtext.py`,
`tests/unit/test_gpu_override.py`.
