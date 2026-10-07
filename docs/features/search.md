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
            over the half-precision index (vectorindex.indexed_distance)
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
- **The query vector is the caller's to supply.** `meridian_core` is imported by the API and
  the orchestrator, and neither should acquire a multi-gigabyte model dependency because a
  search function wanted one. Omitting it is lexical-only, and `SearchResult.degraded` says so.
- **Every hit carries its provenance**, not a source id to look up: a surface that returns
  text and leaves the caller to find where it came from is a RAG endpoint, and nothing is
  assertable without a citation that leads back to a file (§2, principle 3). The source's
  topic labels and the passage's own ride along too, so a reader can see why a hit is in a
  filtered set rather than trust the filter.
- **Ties break on chunk id**, so a repeated search returns the same order. Ties are common in
  a small corpus, and a result set that reshuffles between identical queries looks like one
  that changed.

### Filters

`SearchFilters` is a fixed set of fields, not a free-form mapping: they become SQL predicates,
and what is safe to filter on is the module's decision, not each caller's.

- **Topics and places match by overlap** (`P2-14`, `P2-23`). A source carries every topic and
  place it belongs to, so naming two means "either", which is what a reader narrowing a search
  expects; an AND across topics would return almost nothing, since a document rarely sits
  squarely in two. The SQL is `&&` (array overlap): `= ANY` is the other way round and `IN`
  does not apply to an array column, and both are easy to reach for. Naming a country finds its
  cities' sources too, because a city is only ever tagged beside its country.
- **NULL labels are excluded by a filter, not by its absence.** A source whose `topic_labels`
  (or `places`) is NULL has not been examined, so it cannot be claimed for a topic or place;
  claiming it would assert something no pass has established. With no filter it is included.
- **A passage matches on its own labels too** (`P2-24`). A topic filter keeps a chunk when its
  source carries the topic *or* the chunk does, so a chapter about one topic inside a document
  about another is found. The hit says which matched (`passage_topics`: None when no pass has
  examined the passage, `[]` when one has and found none). Every chunk of a source labelled
  with the topic still matches; a passage need not be about the topic when its document is.
  The passage test is an `EXISTS` by primary key: one index probe per candidate, no join that
  could multiply rows.
- **Screened-only is off by default and on for anything feeding a model** (`P4-14`, §2.5). The
  operator reading their own corpus should see quarantined material, since that is how a false
  positive is noticed; the MCP surface sets it, because what §11.8 protects is the path from a
  fetched page into a prompt. It means "only cleared", not "not quarantined": an `unscreened`
  page has not been examined, and admitting it would make screening optional in exactly the
  case it exists for.
- **Superseded passages are never searchable**, and no caller can turn that off (`P1-32`).
  They are text a page used to carry; retrieving one would quote a document as saying
  something it no longer says, with a citation that opens a page without the passage.

### Lexical ranking

`ts_rank_cd` rather than `ts_rank`: cover density counts how close the matched terms are to
each other, which is most of what separates a passage about the subject from one that
mentions every term once.

Ranking is two steps (`B-65`). The RUM index hands back the best `LEXICAL_POOL` matches by its
own distance, reading the index only, and `ts_rank_cd` orders just those. Ranking every match
with `ts_rank_cd` read each one's vector from the heap, which a common word made a few hundred
milliseconds. RUM's distance ignores how close the terms sit, so the pool must be deep enough
that cover density's best are in it: measured on a real corpus, at the chosen depth the
reranked top 50 matched the one-step ranking, or was within one or two rows for common
two-word questions; at a fifth of the depth, under half survived for some. The pool costs a
few tens of milliseconds. When a query matches fewer passages than the pool, the result is
exactly the one-step ranking.

### Vector arm depth

`hnsw.ef_search` is set per query as a multiple of the candidates asked for. **pgvector's
default of 40 is a ceiling on rows returned, not a quality knob**: an index scan yields at most
`ef_search` tuples, so a `LIMIT 100` returned 40 or fewer however large the corpus (measured at
33). The lexical arm meanwhile returned its full hundred, so fusion under-weighted the vector
side, and a recall benchmark at k=100 was capped at 40% by arithmetic. The multiple is two
because a filtered query spends candidates on rows the filter discards: the index cannot see
`_conditions()`, so `ef_search` has to cover the misses as well as the hits.

It is set with `set_config(..., is_local => true)` rather than `SET LOCAL`, because `SET` takes
no bind parameters; local, so it lasts the transaction and cannot leak to the next caller on a
pooled connection.

The filter sits inside the vector statement rather than being applied to its output, which is
the whole of §12.5's "filters before vector search". Postgres may use a filtered index scan or
a sequential scan depending on selectivity; it will not hand back a top-k drawn from the
unfiltered corpus.

### Per-source cap

`max_per_source` caps how many chunks one document may contribute (`B-30`). Measured on the
first real corpus, the vector arm drew 42% of its top ten from the probe chunk's own document,
and ten hits spanned about four sources. Adjacent chunks of one document genuinely are its
nearest neighbours, but a reader searching a concept got one paper four times.

Held-back hits are **backfilled, not dropped**: a page of six results where twenty exist is
worse than one that repeats a source. The walk keeps rank order, sets aside what exceeds the
cap, and puts those back at the end only if the page would otherwise be short. How many the cap
displaced is returned and shown, because a demoted result is otherwise one the reader cannot
audit.

### Ageing and the baseline

The per-source cap and ageing decay (`P2-20`, §9) are both **off by default**. Each changes
what a search returns, and switching it on for every existing caller (the MCP surface, the
`P2-04` benchmark, the novelty gate) would silently move a baseline that `P2-04` and `P2-09`'s
go/no-go are measured against. When ageing is on, the hit shows the factor applied (1.0 for no
adjustment, and always 1.0 for an undated document, which is neither old nor new): a result
quietly demoted is one the reader cannot audit.

### Citations

`page_or_offset` is a page number for paginated documents and a character offset otherwise
(§5.3). Rather than make every consumer apply that rule from a media type it does not have,
the hit carries the unit (`P2-18`), derived once from the media types whose extractor produces
pages (§6.6). When the media type was never recorded the unit is None, not a default: guessing
"offset" mislabels every PDF and guessing "page" every web page, and a confident wrong label on
a citation someone will try to follow is worse than "unknown".

### Text-search configuration

`TS_CONFIG` must match the configuration `chunks.search_vector` was generated under (`P2-05`).
A query parsed under `simple` against a vector built under `english` does not error; it
silently stops matching inflected forms, which reads as a corpus that does not discuss the
subject. It must reach Postgres as a `regconfig`: bound as a plain string the call is
`websearch_to_tsquery(varchar, varchar)`, which does not exist, and the error is an undefined
function rather than a type mismatch.

### Degraded mode

The vector comes from the embedding service, which runs the model the corpus was embedded
with (`P2-17`). That sameness is the whole requirement: a vector from another model is not less
accurate against this corpus, it is meaningless, and comparing it computes without error and
ranks confidently.

No service configured, or one configured and down, both give lexical-only, reported through
`degraded`; a search that still answers on one arm is better for the caller than an error,
provided it says so. The two reasons are worded differently because the reader acts on them
differently: "no embedder" is a deployment choice, "the embedder is down" is an outage someone
should fix. Reporting an outage as a choice is how a broken dependency goes unnoticed for a
week. The difference also shows in the log.

### Paging

Reciprocal rank fusion orders only what the two arms handed it, so a hit beyond `candidates`
was never a candidate. A page there is refused rather than returned empty, which would look like
the end of the results and stop the caller paging; refused, the client can widen the pool or
narrow the query.

`has_more` comes from asking for one hit more than the page needs, not from a counting query.
The fused ranking has no cheap total, and a total computed differently from the page would
eventually disagree with it.

### The answer page

A search returns a ranked list, and a reader asking how places compare has to group it in
their head. `answer.py` does that grouping mechanically over one search's candidate pool, so
the page shows where the evidence is, how much there is, and where it is thin. No model is
called and nothing is summarised: every item is a passage a source actually contains.

- **Grouping is by country.** A hit counts towards every country its source is tagged with,
  and a city rolls up into its country, so a source about two cities in one country is one
  source for it. Hits with no place are split into sources examined and found about no place,
  and sources never examined, two different facts.
- **One item per source.** A document's best-scoring passage stands for it, with how many of
  its passages matched. A group of five items is five documents.
- **Coverage is a count, not a verdict on credibility** (design-system.md §4).
  `COVERAGE_RULE` is the sentence the interface shows, built from the constants the rule uses,
  so the words cannot drift from the arithmetic.

### Watched questions

What a returning reader wants is not "N sources arrived" but "N arrived that answer this", so
each saved view is counted against its own words and topic filter from the moment it was last
opened (or saved, if never opened). Words match as the lexical arm matches them, the stored
`search_vector` against `websearch_to_tsquery`, so the count agrees with what opening the view
finds by words. The vector arm is not run: a count on the landing page must be cheap for every
view at once. Junk and duplicates are left out, as search leaves them out. It is read-only, so
it runs under the explore role (§12.6).

### Corpus counts

The Explore landing shows four counts (documents, nodes, edges, contested) as the design
system's default state asks. They live in `meridian_core` because it owns database access, and
because §12.5's daily health line asks the same question: a surface and a log line should not
be two queries that can disagree.

- **Counted, not estimated.** `count(*)` at this scale is milliseconds, and a rounded or cached
  count would make "nothing crawled since Tuesday" look like "the count is stale". If these
  stop being cheap, the fix is a materialised view with its refresh time displayed beside it.
- **`searchable` is not `chunks`.** A chunk is lexically searchable as soon as it exists, but
  the vector half needs an embedding and the novelty gate may mark it a duplicate; a reader
  must be able to tell "not collected" from "collected and filtered" (§12.5).
- **"New since" is None when nobody asked** (`P6-11`), not 0: 0 answers a question about a
  moment when nothing changed, and a returning reader shown "0 new" would believe it.
- **Topics come from `topic_config`** (`P6-24`), most-attended first, on the stats response so
  Explore needs no second request on first paint. Reading the labels present on sources would
  need `DISTINCT unnest(topic_labels)` over the whole corpus, which no GIN index answers.
- **Unexamined sources are counted** (`topic_labels IS NULL`). A topic filter excludes them,
  correctly and invisibly, and this count lets the filter say how many documents nobody has
  looked at. It is a plain COUNT with no index; if `sources` reaches millions it is the landing
  number that will be felt first.

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
