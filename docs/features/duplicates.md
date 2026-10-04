# Duplicates

The same text reaches the corpus in three ways: a passage repeated across pages, a whole
document fetched under two addresses, and a claim recorded twice in the graph. Each has its
own pass. All three mark rather than delete, so a mark can be re-judged when a threshold
moves, and the system can report how much it caught.

- **Code:** `packages/meridian_core/meridian_core/novelty.py`, `docdupes.py`;
  `services/worker/worker/novelty.py`, `docdupes.py`, `edgedupes.py`;
  `resolution.fold_repeated_edges`
- **Tasks:** `P2-03`, `B-41`, `B-44`, `B-127`

## How it works

**The novelty gate (passages).** Each embedded passage is compared with passages written
*before* it (lower `chunk_id`). If the nearest one has a cosine above
`MERIDIAN_NOVELTY_THRESHOLD` (0.95), the passage gets `duplicate_of` pointing at it.
Comparing only backwards means the first copy always survives, whatever order a batch was
read in. A duplicate always points at a survivor, never at another duplicate. When most of a
source's passages are duplicates (`MERIDIAN_NOVELTY_SOURCE_FRACTION`), the source is
demoted.

**Document copies** (`docdupes`, daily). A source is a copy of another when one of these holds:

- **exact**: at least 90% of its passages, whitespace-normalised, appear in the earlier
  source. This is what URL variants produce. Passages shared by more than 50 sources are
  ignored as template text.
- **near**: the same normalised title, a mean-vector cosine of at least 0.985, and lengths
  within 60% of each other. This is what a PDF and its HTML page produce.
- **translation**: a non-English source whose declared English version (its `hreflang`
  alternate) is already in the corpus. The English version is canonical whichever was
  fetched first.

The copy points straight at the canonical source, never along a chain. For exact and near
copies that is the earliest source. A source that anything cites is never marked, because
its passages are somebody's evidence.

**Repeated edges.** Merging two entities could leave one claim as two rows. `merge` now folds
them as it goes, and `edgedupes` folds those left from before. Each fold is logged on the
merge, so reversing the merge splits them again.

## Design choices

- **Mean-vector similarity alone was measured not to be enough.** It also matched distinct
  documents built on one template (consecutive rules of one body, agreements from one
  library), so the near rule also requires the same title and a similar length.
- **Copies are left out of search, the map, Gaps and synthesis**, and since `B-127` they are
  embedded last and do not count toward backpressure. They are still embedded, because the
  near rule compares mean vectors, and the daily pass re-judges its marks.
- **The gate needs no model**, only Postgres and arithmetic, so it runs on any machine.

## Operating it

- Jobs: `novelty` (hourly), `docdupes` (daily, disabled in the seed; enable it in the
  timetable).
- `python -m worker.docdupes` without `--apply` prints the copies it finds with examples.
- Search hides duplicates by default. `include_duplicates` shows them, with `duplicate_of` on
  each hit, so a reader can tell a filtered copy from a page that was never crawled.

## Failure modes and traps

- Pages fetched after the day's `docdupes` run are not marked until the next run. Expect a
  day's delay.
- Identical checksums are not themselves a rule. Many sources share the checksum of an empty
  body and have no passages; they cost nothing.

## Tests

`tests/integration/test_novelty*.py`, `test_docdupes*.py`, `test_merge_folds.py` (folding repeated edges),
`test_embed_tiers.py` (copies are last).
