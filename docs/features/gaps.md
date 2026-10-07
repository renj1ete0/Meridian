# Gaps

Gaps is one ranked list of what the corpus cannot answer yet. Each item is a concrete finding,
with its reason in numbers, and comes with an action that uses existing machinery: queue a
search seed, or boost a topic. Every action is reversible and leaves a `steering_log` row.

- **Code:** `packages/meridian_core/meridian_core/gaps.py`; `services/api/api/routes/gaps.py`,
  `cache.py`; `web/src/explore/GapsPage.tsx`
- **Tasks:** `P6-36`, `P6-37`, `P6-42`, `P2-23`, `P2-24`, `B-121`

## How it works

A gap source is an async function from a session to a list of gaps, registered by name. The
list reads every source and ranks the results together.

| Source | Finds |
|---|---|
| `topic-coverage` | Per topic: thin (few sources), weak (few official or peer-reviewed), stale (newest dated source is old). Counts on-topic passages too, including those inside documents labelled with another topic |
| `place-coverage` | Per topic, places in the comparison set with too few sources about them |
| `areas` | Map fields mostly about the topics that rest on few sources, hold no official or peer-reviewed passage, or have had nothing new in months |
| `search-queries` | Answered searches that found nothing new, grouped per topic and kind |
| `search-results` | Topics whose search-found pages turned out mostly off-topic |
| `routes` | Pairs of topics whose most-cited nodes no chain of stated links joins within a few hops |
| `question-set` | Items of the held-out set that scored low in the newest run |

A source that the design names but nobody has built is listed as *pending*, so an empty list
never reads as "no gaps of that kind".

**Actions** are Admin routes: `POST /api/admin/gaps/seed` queues a search; `POST
/api/admin/gaps/boost` boosts a topic for a while. The list itself is
`GET /api/explore/gaps`, on the read-only role.

## Design choices

- **The held-out rule shapes the question-set actions.** A question from the evaluation set is
  never used as a seed or a steering reason (`eval/README.md`), so a low-scoring item offers
  "search it in Find" and nothing that steers.
- **Kept in memory, refreshed behind the reader** (`B-121`). Counting every on-topic passage
  takes seconds and grows with the corpus, while the list changes over hours. The route keeps
  its answer for 15 minutes and refreshes in the background (stale-while-revalidate). The
  response carries `computed_at`.
- **A field mostly off the topics is not an evidence gap.** The Map shades it instead.
- **A source nobody built is *pending*, not absent**, so an empty list is never read as "no
  gaps of that kind". `place-coverage` is likewise *unavailable* until some source has been
  examined for places: with nothing examined, every cell would read as a gap and none would be
  one.

### Thin

A topic is thin by its count of **sources**, even when many on-topic passages sit inside
documents labelled with something else (`P2-24`). A passage label is one chunk's vector,
noisier than a document's mean, and a topic that exists only as asides in other documents has
no document to cite as being about it. Those documents are counted and shown in the evidence
(`passage_sources`), and do not lift the topic out of "thin".

### Places

Place gaps rank below every other kind (`B-97`, the operator's call). Each topic is paired with
every place in the comparison set, so most empty cells are pairings nobody asked about, and at
their old weight they led the list whenever few other gaps were open. They stay listed, last,
for the reader who does want a topic in a place.

### Search queries

`search-queries` replaced `P6-36`'s per-topic "search-yield" source rather than sitting beside
it. That source could say only "no search for this topic ever queued a page", because the queue
did not record what a query returned. With each query's yield on its row (`B-56`), the same
finding is the case where every answered query failed (share 1.0, the top severity), and a topic
whose searches mostly work but where some words find nothing becomes visible too. Keeping both
would list the all-failed topic twice.

Queries answered before `B-56` have NULL yields. They are *not measured*, not failures:
counting a NULL as zero is the absent-signal-as-zero trap.

`search-results` is per topic, not per query, because of the queue's shape: a result row carries
its query's topic but not its query. "These words find off-topic pages" is not answerable;
"searches for this topic do" is. Its gap offers a seed but no boost: boosting a topic whose
searches land elsewhere spends more of the crawl landing elsewhere.

### Ranking

Severities are chosen so that symptoms rank below causes. An empty topic is 1.0 and an
operator-graded question 0.7–0.9. A failed search is a symptom, so both query kinds stay under
those: "nothing found" outranks "found only pages already known", since the second at least
shows the words reach the topic and the crawl has simply been there. A route through
resemblance only is a weaker finding than no route at all, and a route search cut short by its
work bound is ranked down further.

### Routes

Each topic's most-cited node (the most passages behind the edges touching it, the node a reader
of that topic is likeliest to start from) is checked against every other topic's, using
`P6-32`'s claims-only route search. A missing link between two of those is a missing link
between the topics, not between two obscure names. One node per topic keeps the pair count at
topics², and `ROUTE_PAIRS` caps it, because each pair is a graph search and the list runs on
every read.

### Seeds

A seed from Gaps is queued at the priority `worker.seedsearch` gives its own queries, so a
person's seed neither jumps nor trails the crawl's own questions, and is logged under the same
`seed` field as `POST /api/admin/seeds`.

## Failure modes and traps

- A test that reads `/api/explore/gaps` must call `KEPT_GAPS.forget()` first, or it sees
  another test's cached answer.
- `question-set` needs a run file in `eval/runs/` (or `MERIDIAN_EVAL_RUNS_DIR`). Without one it
  says so.

## Tests

`tests/integration/test_gaps.py`; `tests/unit/test_gaps.py`.
