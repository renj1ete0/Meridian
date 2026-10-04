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

## Failure modes and traps

- A test that reads `/api/explore/gaps` must call `KEPT_GAPS.forget()` first, or it sees
  another test's cached answer.
- `question-set` needs a run file in `eval/runs/` (or `MERIDIAN_EVAL_RUNS_DIR`). Without one it
  says so.

## Tests

`tests/integration/test_gaps.py`; `tests/unit/test_gaps.py`.
