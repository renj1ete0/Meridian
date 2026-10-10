# Duplicates

The same text reaches the corpus in three ways: a passage repeated across pages, a whole
document fetched under two addresses, and a claim recorded twice in the graph. Each has its
own pass. All three mark rather than delete, so a mark can be re-judged when a threshold
moves, and the system can report how much it caught.

- **Code:** `packages/meridian_core/meridian_core/novelty.py`, `docdupes.py`;
  `services/worker/worker/novelty.py`, `docdupes.py`, `edgedupes.py`;
  `resolution.fold_repeated_edges`
- **Tasks:** `P2-03`, `B-41`, `B-44`, `B-88`, `B-127`, `B-151`

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

Two nodes that may name one thing are not a duplicate of this kind: resolution decides those,
and the ones it cannot decide are settled by a person in **Admin → Possible duplicates**
(`B-202`, [knowledge-graph.md](knowledge-graph.md#deciding-a-possible-duplicate)).

## Design choices

- **Mean-vector similarity alone was measured not to be enough.** It also matched distinct
  documents built on one template (consecutive rules of one body, agreements from one
  library), so the near rule also requires the same title and a similar length.
- **Copies are left out of search, the map, Gaps and synthesis**, and since `B-127` they are
  embedded last and do not count toward backpressure. They are still embedded, because the
  near rule compares mean vectors, and the daily pass re-judges its marks.
- **The gate needs no model**, only Postgres and arithmetic, so it runs on any machine.

### The novelty gate

§6.1 draws one line, "cosine vs existing vectors; drop if > 0.95", and three decisions hide in
it.

- **Which of two identical chunks survives.** Compared naively, each is the other's nearest
  neighbour, both clear the threshold, and both are dropped: the corpus loses the text instead
  of deduplicating it. So a chunk is compared only with chunks written *before* it
  (`chunk_id <`). Ids are monotonic, so the first copy to arrive stays, whatever order a batch
  was read in. A whole batch is judged against the corpus as it stood before the batch and only
  then written; judging one chunk at a time would give the same answer more slowly.
- **Nothing is deleted.** §5.4 says a near-duplicate loses its raw file, and §12.5 wants a
  novelty pass rate on the daily health line. A gate that deleted could report neither, and
  could not be re-run when the threshold moves. So the verdict is three columns on `chunks`,
  and the retention sweep (`P1-31`) spends it: the same shape as §2.5's rule for steering,
  adjust what is generated, never destroy what was recorded.
- **A duplicate points at a survivor, never at another duplicate.** Chunks already marked are
  excluded from the candidates, and a verdict that still lands on one, because both were judged
  in the same batch (B of A, A of an older Z), is followed through to its target in `judge`.
  Otherwise `duplicate_of` would be a chain every consumer has to walk.
- **Excluded candidates must not hide the survivor** (`B-151`). The filters (earlier, not a
  duplicate, not retired) apply after the vector index offers its nearest candidates. A
  passage copied many times has its copies nearer to a new copy than the original is, so the
  index offered only copies, the filter removed them all, and the gate found no neighbour and
  kept the new copy. The query now scans on until a candidate passes; see
  [filtered scans](embedding.md#filtered-scans).

The threshold is strictly greater-than, as §6.1 writes it: a chunk exactly on the line is kept,
because the cheap error is keeping a duplicate and the expensive one is dropping the only copy
of something.

The nearest earlier neighbours of a batch come from one statement with a LATERAL join, not a
query per chunk, since round trips dominate at useful batch sizes. The `chunk_id <` predicate
is applied *after* the HNSW scan, so the index speeds the search without making it exact. That
is acceptable: a missed near-duplicate is a chunk that stays, not one wrongly dropped.

The pass is paged by id, not OFFSET, for the embedding backfill's reason: the crawl writes new
chunks underneath a long pass, and an offset page would re-scan some rows and skip others. A
chunk with no vector is not novel but unjudgeable, and waits for the embedder. Verdicts are
written one statement each, rather than one batched `UPDATE … FROM (VALUES …)`: the batched
form is faster but its rowcount cannot say which rows it skipped, and the pass is bounded by
the vector scan anyway. Only still-unjudged rows are written, so a second pass racing the first
can neither overwrite a verdict nor re-time `novelty_checked_at`.

**Its own process.** §6.1 puts the gate inside the fetch loop, between extract and store, but
the vector arrives one pass later (`P2-01`), so when a chunk is written there is nothing to
compare. Nor is it a stage of the embedding backfill: it needs no model, and binding it to the
process that carries the model's weights would mean the corpus could only be deduplicated on a
machine that could also embed it. It is resumable: `novelty_checked_at IS NULL` is the whole
queue, one batch is one transaction, and the cursor moves past each batch, so a pass killed
part-way keeps everything it committed.

<a id="retired-text"></a>**Retired text** (`P1-32`). Superseded chunks are neither judged nor
candidates. Otherwise a re-crawl of a changed page would mark each of its new chunks a duplicate
of the generation it just replaced: true, and exactly backwards, because the copy pointed at is
the one that is gone.

<a id="the-pass-rate"></a>**The pass rate.** `novelty_health` returns the three numbers the
health line needs. A pass rate that collapses means the crawl has found a mirror, a paginated
view of one document, or a site serving the same boilerplate under every URL, all of which look
like a healthy crawl by every other number on the line.

### Demoting a source

The gate's only source-level act, and the input to `P1-31`'s sweep: §5.4 keeps a raw file for
primary sources, extracted text for background ones, and nothing for junk. The tier is the
decision and the sweep is the deletion.

- **The fraction is 0.9**, not 1.0: a page republished across three sites differs in its
  header, date line and boilerplate, so demanding every chunk match would demote almost
  nothing. Not 0.5 either: half a page of new material is a source.
- **A primary source is never demoted**, whatever its chunks say. The raw file is kept for
  official documents and papers because link rot makes them unrecoverable, and a mirror that
  happened to be crawled second is still the citable copy of a real document. Keeping a
  duplicate PDF costs a few megabytes; dropping the only local copy of a page since reorganised
  away costs a citation that can never be checked again.
- **Only once every chunk is judged.** Demoting on a partial view would junk a long document
  because its first page happened to be boilerplate.

### Document copies

The novelty gate judges passages one at a time and never demotes a primary source, so a
document reached under two addresses (with and without a query string, `http` and `https`, a
trailing slash, a PDF and its HTML page) was searched and would be synthesised twice. On a real
corpus a 0.96 cosine between mean vectors caught genuine copies and also distinct documents on
one template, hence the stricter rules above. For a translation the English source is canonical
whatever the fetch order, as the operator prefers the English version where one exists.

<a id="loops"></a>**Loops** (`B-88`). The rules can disagree about which way round two pages
go: a translation points at its English version, a near copy at the older one. Walking such a
loop came back to where it began and marked a page a copy of itself. A loop now gets one root
for all its members, the smallest id, and that page stays unmarked.

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
