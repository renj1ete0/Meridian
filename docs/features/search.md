# Search

Find is hybrid search over every live passage: a lexical arm (Postgres full-text) and a
vector arm (pgvector, using the same model the corpus was embedded with), fused by
reciprocal rank. Filters (topic, place, tier, date, kind) apply inside both arms. Every hit
carries its source, its age, and the rank each arm gave it. A search that could run only one
arm says so.

- **Code:** `packages/meridian_core/meridian_core/search.py`, `answer.py`, `watch.py`,
  `stats.py`, `ageing.py`; `services/api/api/search_service.py`,
  `routes/explore.py`
- **Tasks:** `P2-06`, `P2-07`, `P2-17`, `P2-20`, `B-65`, `P6-43`

## How it works

```
query ──► lexical arm: websearch_to_tsquery over chunks.search_vector
            RUM index: best LEXICAL_POOL (1000) by match, re-ordered by ts_rank_cd
      ──► vector arm: query embedded by the embedding service, HNSW nearest neighbours
      ──► the same filter predicate inside both (search._conditions)
      ──► reciprocal rank fusion (k = 60), then ageing decay per source tier
      ──► one page of hits, each with source, ranks per arm, age and decay factor
```

**The answer page** (`/api/explore/answer`) groups the candidate pool of one search by
country, with one item per source (its best passage, plus how many passages matched).
Coverage is stated per place as a count: which places have evidence, how much, and which
are thin. Hits with no place are split into "examined, about no place" and "not examined".
No model writes anything on this page.

**Watched questions** (`P6-43`). A saved view counts what has arrived since it was last opened
that matches its own words and topic filter, using the lexical arm only, so the count is
cheap for every view at once.

**Corpus counts** (`/api/explore/stats`): documents, nodes, edges and contested pairs, counted
exactly rather than estimated.

## Design choices

- **Filters inside both arms, never after.** Filtering after the top 50 returns an
  unfiltered search with holes in it, which looks like a thin corpus rather than a bug.
  `_conditions` is the one place the predicate is expressed.
- **Rank fusion, not score fusion.** The arms' scores are on unrelated scales that change as
  the corpus grows; only their orderings are comparable.
- **A missing arm is reported.** With no embedding service configured, or with one that is
  down, search is lexical-only and the response has `degraded: true` with a reason, so a
  reader does not mistake half a search for a thin corpus.
- **Duplicates are excluded by default and counted anyway.** Each hit carries
  `duplicate_of`, so a surface can explain why something is missing.
- **Paging stops at the candidate pool.** A page past the pool is refused, rather than
  returned empty as if it were the end of the results.
- **RUM for the lexical arm** (`B-65`). It returns the best matches directly from the index,
  2–3× faster on broad words, with rankings unchanged or within a row.

## Configuration

- `MERIDIAN_EMBEDDER_URL` enables the vector arm. Without it, search is lexical-only.
- Filters and defaults are request parameters on `/api/explore/search`; see the route
  docstring.

## Operating it

- `make bench-search` measures recall, latency and agreement between the arms, and reports
  whether the planner used the vector index (below a few thousand vectors it will not).
- With the dev environment exported, `scripts/benchmark_search.py` reaches the database
  directly.

## Failure modes and traps

- **The GIN index may now be redundant.** The planner answers plain `@@` from RUM too.
  Check the watch and area-view plans before dropping it.
- A configured embedding service that is down makes search lexical-only and logs a warning.
  The response still answers, flagged `degraded`.

## Tests

`tests/integration/test_search.py`, `test_search_candidates.py`, `test_search_index.py`,
`test_explore_api.py`, `test_explore_answer.py`, `test_watch.py`;
`tests/unit/test_search.py`, `test_answer.py`.
