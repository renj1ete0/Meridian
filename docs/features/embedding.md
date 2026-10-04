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

**Tiers** (`embed_tier`, `B-66`). Each batch is drawn from the highest tier that has work, and
the tiers are re-checked every batch:

| Tier | What is in it | Order |
|---|---|---|
| `first` | Directed pages (search results, seeds, cited papers) and pages on proven hosts | Newest first, so labels and host scores react to the latest crawl |
| `then` | Everything else that is not junk | Oldest first |
| `last` | Off-topic hosts; the rest of a long document whose sample did not earn it (`B-89`, with a higher bar from 1,000 passages, `B-133`); copies of earlier sources (`B-127`) | Oldest first |
| none | Junk | Never embedded |

**Long documents are sampled first** (`B-89`). The first 16 passages and every 16th after them
are embedded and labelled. The rest waits in `last` unless the sample's best topic score
clears the triage floor. Holding back delays a document; it never drops one. See
[topics.md](topics.md#triage-of-long-documents).

**What is embedded is a view of the text** (`B-49`). Link syntax becomes its visible text and
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
