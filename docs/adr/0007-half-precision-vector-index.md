# 0007. A half-precision vector index, if it measures as well

- **Status:** Accepted; the benchmark passed and the index is built (`B-136`, `v0.157.1`)
- **Date:** 2026-10-04
- **Tasks:** `B-136`

## Context

Semantic search reads mostly the HNSW index, which holds a full-precision copy of every
vector and grows with the corpus. An index built over 16-bit copies of the vectors is
about half the size, so more of it stays in memory.

## Decision

Benchmark a half-precision expression index against the current one, comparing recall at
10 and latency on the project's question set and random queries. Switch only if the top
results stay within about one percent and latency is no worse. The stored vectors stay at
full precision; only the index changes.

## Result

Measured on a 200,000-passage sample of the live corpus against exact top-10 neighbours
(`meridian-calibration/loop/b136/`): the half-precision index was 482 MB against 1,443 MB;
recall@10 was 0.934 against 0.932 at the default search width (40) and 0.969 against 0.976 at
100; median query time was 1.9 ms against 2.4 ms. The two returned the same top 10 for 96–98%
of results. Within the bar on every measure, so the index was switched.

## Consequences

- Needs a migration and a change to every vector query, so that it uses the index's
  expression. Nothing is deployed yet, so the migration costs no downtime.
- If the benchmark fails, the record stays here with its numbers, and the index is left as
  it is.

## Alternatives considered

- **Store the vectors themselves at half precision.** Rejected: re-embedding or
  re-deriving would then start from a lossy copy.
- **Rely on server memory alone.** It also works. The smaller index still helps as the
  corpus grows.
