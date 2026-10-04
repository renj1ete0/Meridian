# 0007. A half-precision vector index, if it measures as well

- **Status:** Accepted, gated on a benchmark
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
