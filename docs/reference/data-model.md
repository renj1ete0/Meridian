# Data model

Why the tables in `packages/meridian_core/meridian_core/models/` are shaped the way they are.
The column definitions and their short comments are in the code; this page holds the reasons,
the history and the traps, table by table. What each feature does with these tables is in
[features/](../features/README.md).

## Conventions

- **Constrained strings, not native enums.** `mixins.constrained()` makes a string column
  with a CHECK. `create_constraint=True` is not optional: SQLAlchemy has defaulted it to False
  since 1.4, so without it the column is a plain VARCHAR that silently accepts any string, and
  that is visible only when someone tries to insert a bad value.
- **Provenance columns are mixins.** §2 principle 3 requires every node, edge and tag to
  record what justified it, and §11.12 requires every artifact to record which model produced
  it, so quality can be improved retroactively. Both are easy to forget on a new table, so they
  live in `mixins.py` rather than being retyped per model.
- **NULL and `{}` are different.** On every "examined" array (`sources.topic_labels`,
  `sources.places`, `chunk_topics.topic_labels`), NULL means nothing has examined the row and
  `{}` means it was examined and matched nothing. Only NULL is a pass's queue.
- **"Examined at" timestamps, not booleans.** A timestamp lets a re-run target everything
  read before a date when the rules change; a boolean can be reset only for the whole corpus.
  NULL is the queue (`chunks.novelty_checked_at`, `sources.acronyms_harvested_at`).
- **Lists of ids are `ARRAY(BigInteger)`.** `figures.linked_entity_ids` was `json` until
  `B-10`, which nothing chose: `json` keeps the literal text, so `'[1, 2]'` and `'[1,2]'` are
  unequal, nothing can be indexed, and reading one back means parsing JSON to recover integers
  Postgres could return directly.

## Sources

Two things are load-bearing and easy to get wrong later: `chunks.page_or_offset` is captured
at extraction time, because reconstructing it afterwards is painful and often impossible
(§5.3); and `source_tier` is assigned mechanically from the domain and the document structure,
never by a model (§5.2). It is the first tiebreaker when sources conflict.

- **`etag`, `last_modified`.** HTTP cache validators from the last successful fetch, echoed
  on the next (§6.4 `conditional_requests`). `last_modified` is Text, not a timestamp: the
  origin compares it as an opaque string, and re-serialising a parsed date into another format
  would make a strict origin stop returning 304, silently turning the cheapest request in the
  crawl back into the most expensive one.
- **`raw_root`** (`P1-45`). The raw store `raw_file_path` is relative to. Provenance, not a
  lookup: resolution still goes through `MERIDIAN_RAW_ROOT`, because an absolute path would
  bake in a container's mount point. Without it, a corpus spanning two roots (one worker run
  natively, one in a container) produces rows that dangle from either root's point of view,
  indistinguishable from a lost file. NULL means "written before the column existed".
- **`extractor`** (`P1-44`, §6.6). Which extractor produced the text. §6.6 routes each format to
  a different tool and HTML to two, so it is not derivable from the media type; without it the
  only way to tell a browser-extracted page from a locally extracted one was to look for
  Markdown link syntax in the text, which is how `P1-43` was found. **Deliberately not
  `constrained()`**: the value set grows whenever an extractor is added, and a CHECK would
  recreate `P1-28`'s trap, where a literal missing from the enum raises at the insert after the
  fetch, the parse and the log line have all reported success. A diagnostic that can fail a
  write is worse than none.
- **`topic_labels`** (`P2-14`, `P2-21`). Which topics the content is about, best first, written
  by `worker.retopic` from the chunk vectors, never by the fetch path. Named to match
  `entities.topic_labels` and `gazetteer.topic_labels`.
- **`crawled_for`** (`P2-21`). Which queue topics caused the fetch: provenance and nothing more.
  It used to *be* `topic_labels`, and that is how a crawl pursuing one topic stamped it on every
  page a site's navigation led to. Accumulates across fetches, written at fetch time while the
  queue row is in hand.
- **`topic_basis`.** A fingerprint of the model, the thresholds and every topic's prototype
  text. A topic added, archived or re-described changes it, and every source whose basis
  differs is re-examined. Opaque on purpose: compare, never parse.
- **`topic_scores`.** Every labelling topic's similarity under `topic_basis`, kept so a label
  can be explained and an off-topic demotion reads the numbers the labelling did.
- **`topic_sample_best`** (`B-89`). The best topic score when labels were read from a sample of
  the passages; NULL when read from the whole text or never read. A long document is labelled
  from its sample first, so the embedder learns whether the rest is worth its time; a non-NULL
  value is also the labeller's note to read the source again once it is whole.
- **`places`** (`P2-23`, §7.2). Normalised codes, most-evidenced first: ISO 3166-1 alpha-2 for a
  country (`EU` for the union), UN/LOCODE without its space for a city. A city code's first two
  characters are its country, and the country is always tagged beside it. Written by
  `worker.places`; the method is in `meridian_core.places`.
- **`acronyms_harvested_at`** (`P5-02`). When §5.6's acronym harvest last read the document.
  The harvest's queue index is partial: everything with text and not yet read, because a
  metadata-only source has nothing to harvest and would otherwise be re-skipped forever.
- **`trust_state`** (`P4-14`, §2.5). The domain holds the screening verdict
  (`fetch_policy.trust_state`) because screening is paid per domain; this is the page's own copy
  of the state it was stored under, so chunk queries filter without a join and clearing a domain
  later does not rewrite what was true at the time. A page the pre-screen flagged on an
  unscreened domain is `quarantined` whatever the domain says. **Quarantined is stored, never
  deleted**: the page keeps its raw file, extraction and chunks, and loses only eligibility for
  what the slow loop reads.
- **`doc_kind`** (`B-59`). What a document *is*, beside `source_tier`'s who published it,
  assigned mechanically at fetch from the document's structure. `listing` is a page whose value
  is its links (an index, a feed, a search result page), followed but not chunked. NULL means
  nothing has classified it (the backfill's queue), unlike `other`, which means the rules looked
  and none applied. The deciding rule and its link measurements are in `extra["doc_kind"]`, so a
  verdict can be audited. Overwritten on every fetch.
- **`duplicate_of`** (`B-44`). The earlier source this one is a copy of: the same document
  under another URL, or its PDF and its HTML page. Document-level, where `chunks.duplicate_of`
  is passage-level; the novelty gate judges passages one at a time and never hides a primary
  source, so a document fetched twice was searched and synthesised twice. Set by
  `worker.docdupes`; the earliest source is canonical and a copy points at it directly, never at
  another copy. Nothing is deleted.
- **Array columns have GIN indexes**, because the queries are overlap (`&&`), which a btree
  cannot answer; without them a topic filter is a sequential scan over every source.

## Chunks

- **`embedding_view`** (`B-49`). Which `embedtext.VIEW_VERSION` the vector was computed from;
  NULL for vectors from the raw text, before the view existed. Lets `worker.reembed` find
  vectors a view change made stale without taking any out of search.
- **The novelty verdict is recorded, not acted on** (§6.1, `P2-03`). §6.1 says "drop if
  >0.95" and §5.4 says a near-duplicate loses its raw file, but a gate that deletes leaves
  nothing to audit, nothing to re-judge when the threshold moves, and no way to compute
  §12.5's novelty pass rate. The gate writes a verdict; the retention sweep (`P1-31`) spends it.
  - `novelty_checked_at`: NULL is the gate's queue. One-shot: a chunk can only duplicate
    something older, which is already present, so a second judgement would agree. The queue
    index is partial (embedded, not judged, not superseded), since judging text no longer on
    the page spends the gate on a verdict nothing reads.
  - `nearest_similarity`: cosine similarity to the nearest earlier chunk. NULL means there was
    nothing to compare against; the first chunk in an empty corpus is unjudgeable, and a
    sentinel would be indistinguishable from a real orthogonal neighbour.
  - `duplicate_of`: self-referential, `ON DELETE SET NULL` rather than CASCADE. If the survivor
    is deleted by a re-crawl, this chunk is now the only copy of the text.
- **`search_vector`** (§12.5, `P2-05`). A STORED generated column, not the trigger the task
  named. A generated column cannot be bypassed by a write path that forgot a trigger, cannot
  drift from `text` after a bulk UPDATE, and needs no ordering with other BEFORE triggers; a
  trigger's failure here is silent, a chunk that is embedded but lexically unfindable. The
  regconfig is a literal: `to_tsvector(text)` resolves through `default_text_search_config`, a
  session setting and therefore not IMMUTABLE, which Postgres refuses in a generated column;
  naming it also pins the stemming. NOT NULL because `text` is: a stopword-only string gives the
  empty tsvector, not NULL, so a NULL would mean the column was added without being generated.
- **Lexical indexes.** GIN answers "which passages match" and stays for every unranked `@@`.
  RUM (`B-65`) keeps term positions, so `ORDER BY search_vector <=> query` reads matches best
  first without touching the heap. Its inserts cost about ten times GIN's (about 2 ms a
  passage), under a minute an hour at the busiest hour seen.
- **Vector index** (§12.5, `P2-04`). `vector_cosine_ops`, because `embeddings.py` normalises and
  everything compares by cosine; an index for another operator class is not slower, it is
  unused, and every search becomes a sequential scan. Not partial on `duplicate_of IS NULL`: a
  duplicate verdict can be re-judged when the threshold moves, and such an index would need a
  rebuild to follow. HNSW rather than IVFFlat, which needs a representative sample to build its
  lists and is wrong to create on the empty table a migration sees. Half precision (`B-136`,
  ADR 0007): queries must order by `vectorindex.indexed_distance` to use it.
- **`superseded_at`** (§2.3, `P1-32`). A re-crawl of a changed page used to DELETE the old
  chunks. `edges.supporting_chunk_ids` is an array with no foreign key (Postgres cannot enforce
  one on array elements), so a deleted chunk left every edge citing it pointing at nothing, and
  such an edge fails no check: it reads as having provenance and the citation does not resolve.
  So old chunks are stamped instead. That keeps every citation resolvable, keeps the text an
  edge was derived from (§2.4 re-derives from source chunks), and leaves the sweep free to
  reclaim what nothing cites. NULL is the live set, and every query serving the corpus filters
  on it.

## Chunk topics

Which topics one passage is about (`P2-24`). `P2-21` labels a source from its mean chunk vector,
which is right for a page and coarse for a long report: a chapter on another topic is invisible
to a topic filter. This is the same method applied to each chunk's own vector
(`meridian_core.passagetopics`).

**A side table, not columns on `chunks`.** `chunks` carries HNSW and GIN indexes, and an UPDATE
that cannot be HOT writes a new entry into every one of them, so re-labelling the corpus after a
topic change would re-index every vector for a few bytes of labels and bloat the table the
embedder and search live on. Here a re-label rewrites only narrow rows; the cost is one join by
primary key. No row means not examined (usually no vector yet); `{}` means examined and about
none.

## Page lines

Which candidate lines a page's *uncleaned* text holds (`B-43`), one row per distinct line hash
per source, with the host beside it so per-host counts are one GROUP BY. Written at fetch from
the text as extracted, never from cleaned text: counted after cleaning, a banner would vanish
from the pages it was removed from, fall below the threshold, stop being removed and come back,
a rule that switches itself off by working. Replaced whenever the page is re-chunked from a
fresh fetch.

## Host scores

How much of what a host serves is about the topics (`B-48`). Derived wholesale by
`worker.hostscore` from the content labels and the pending queue, and read by the fetch loop to
decide whether a link is worth queueing. The loop never computes it, so no model or vector is
anywhere near the crawl.

## Translation lookups

What a phrase is called in other languages, per Wikipedia (`B-52`). §7.4 makes non-English
seeds mandatory, and a language prefix on English words was measured to return English pages:
the query needs the words. The worker may not call a model, so the words come from Wikipedia's
interlanguage links, written by people who speak the language. A phrase with no article is
recorded too (`article` NULL), so it is not looked up again until stale.

## Figures

- **`image_url`** (`P1-10`). Nothing downloads figure images and `file_path` is a local raw
  path, so without this a row describes a picture nobody can look at, and the enrichment §6.6
  defers (`P7-07`) would have nothing to fetch. NULL for a figure found in a PDF's text layer,
  where the caption is extractable and the image is not addressable.
- **`linked_entity_ids`**: the graph nodes the figure illustrates (§6.6). See the convention on
  lists of ids above.

## Queue

The crawl queue (§5.1) is also the decoupling point between planes: the worker and the
orchestrator never call each other, they only leave rows here (§2 principle 2).

- **`seed_source`.** Frontier expansion is model-independent and is most of the queue;
  model-emitted seeds are capped per run (§11.4). Every other value is distinguishable from
  `frontier` on purpose: "how did this URL get here" is what §5.2's seed provenance answers, and
  a link a person placed on a page, a site's own index, a search engine's ranking, a work a
  paper cited, and an open-access copy found by resolving that citation are different answers.
- **`seed_mechanism`** (`P5-05`). Which of §7.4's five diversity mechanisms wrote a `diversity`
  query. NULL for every other row and for a diversity query that only widens a topic. Kept on
  the row, not in a log, because "which mechanism's questions find anything" joins it to
  `search_results` and `search_queued`, which are on the row too.
- **A lease, not a status** (`claimed_at`, `claimed_by`). Claiming by status would need a new
  state, and a worker that dies mid-fetch would strand the task there; an expired lease is
  simply reclaimable, which running unattended for weeks requires (§13.4).
- **`search_results`, `search_queued`** (`B-56`). For a `query` task, how many result URLs came
  back and how many were new. NULL until answered. Without them "this question found nothing"
  was invisible, and that is the signal a gap list wants.
- **`parent_source_id`** (`B-58`). For a `doi` task, the page whose references named it. A
  cited paper is worth what the citing page is worth: named by an on-topic page it is the best
  material the crawl can reach, named by an off-topic one it is the drift `B-48` stopped. A
  ranking input, not provenance, so it is set NULL rather than blocking a source's deletion.

## Fetch attempts

One row per fetch attempt. `fetch_policy.consecutive_failures` resets and `queue.error` holds
only the latest message, so neither answers "what is the fetch success rate today" (§12.5's
health line) or "has this domain served only 404s for a week". The counter says something is
wrong now; this says what has been happening. High volume by design and pruned on a retention
window.

The fetch safeguards each have their own outcome rather than one "blocked": an `unsafe_target`
spike means frontier expansion is chasing internal addresses, `content_type_rejected` means the
allowlist is too narrow for a domain, and a `decompression_bomb` is someone being hostile.

## Robots cache

The robots.txt cache, kept across restarts (`P1-29`). In-process only, a restart re-fetched
`/robots.txt` for every origin, paid against the same rate limiter the pages queue behind, so
the first minutes after a restart were spent not crawling.

- **The raw file is stored, not the parsed rules.** Re-parsing is cheap, compiled matchers are
  not worth serialising, a parser fix then applies to everything cached, and the file is the
  evidence for "why was this URL refused".
- **One row per origin, overwritten**, so the table is bounded by the origins ever touched.
- **`outcome` is kept, not inferred from `body IS NULL`.** "The server said 404" and "the server
  could not be reached" both store no body and mean opposite things: the first permits the
  whole origin, the second refuses it until it can be read (§2.3.1.3).
- **`expires_at` is wall clock.** The in-process cache expires on `time.monotonic()`, which
  counts from an arbitrary origin (usually boot); a persisted monotonic deadline would be
  compared against a different clock after the restart it exists to survive.

## Configuration tables

Configuration lives in the database (§13.1). The YAML in `config/` seeds these tables once at
first boot and is not read again; topic weights, fetch policy and model routing are UI or MCP
actions. Credentials are the exception: `agents` stores the *name* of the environment variable,
never the value, because the database is snapshotted off-device and keys would travel with
every snapshot (§11.11).

### Seeding

`scripts/seed.py` loads `config/*.yaml` once at first boot and is safe to re-run.

- **It inserts what is missing and leaves what exists alone.** A weight changed in Admin months
  ago must survive a re-run, or re-seeding would silently undo steering and §10's promise that
  nothing is destroyed would be false. The timetable and the budget are seeded on first boot
  only: re-reading the schedule would undo every change made in the interface (§13.2), and an
  existing budget row is left as it is, nulls included, because an operator who cleared a cap
  decided "stop until I think about this", and refilling it would mean "carry on with the
  default".
- **Configuration only, never content.** Production starts empty (scaffold §1.7). There is no
  fixture path, because synthetic fixtures do not resemble real extraction output and UI built
  against them gets rebuilt; development corpora are snapshots of real crawls, restored
  separately. Cold-start seeds become queue rows (URLs to crawl, not content), and the list is
  empty until a person writes it (§15 phase 0), since seed quality propagates through
  everything downstream.
- **The source-tier mapping rides in the global `fetch_policy` row**, as one blob of domain
  policy read with the fetch settings on every request; leaving it in a file the worker
  re-reads would make the YAML authoritative again.
- **No agent may be registered as `HUMAN`.** `produced_by = HUMAN` marks the reader's own notes
  (`P6-05`, §12.5), and that layer stays distinguishable only while nothing else can write it.

### Topic config

Attention as a weight vector over topics (§10). Steering rewrites the vector and never deletes,
so returning to a topic costs no rebuild or re-crawl. `archived` releases a topic's share and
stops seeding, but leaves every node, edge and tag it produced (§10.2). `description`
(`P2-21`) is most of what the content labeller compares a page against: a slug is two words an
embedder reads without context. A topic without one is labelled from its name and vocabulary,
which works and is less discriminating.

### Steering proposals

A change the system proposes and applies unless refused (`P6-38`), between §10.2's
self-steering default and §10.1's logged changes: stated in plain words with its numbers, held
for `apply_after`, and applied through `meridian_core.steering`, so it lands in `steering_log`
like any other change. **At most one pending proposal per topic and kind**, by a partial unique
index: two pending boosts would apply one after the other, and the second would be a decision
nobody saw.

### Fetch policy

- **Learned rendering** (`P1-27`). `render_js: auto` fetches statically and re-fetches through
  the browser when the HTML is a shell, which suits mostly static pages but has no memory: a
  JS-only domain paid both requests on every page, in the same per-domain slot as the pages.
  `render_js_escalations` counts *consecutive* escalations and resets when a static fetch was
  enough, like `consecutive_failures`. `render_js_learned_at` makes the learning **expire**:
  a domain going straight to the browser never fetches statically again, so the counter could
  never reset and a redesign would never be noticed.
- **`seed_allowed`** (`P4-12`, §11.4). Distinct from `status` (whether to fetch what is
  queued) and `trust_state` (whether a model may read what came back): whether new URLs on the
  domain may be queued at all. **NULL is undecided, and undecided is not permission.** The crawl
  earns a domain its allowance by fetching from it successfully; a domain a model proposes and
  nobody has seen waits, like a harvested gazetteer term (§5.6).
- **`first_seen_via`.** How the domain first entered the crawl, a `seed_source` value, never
  overwritten: a domain discovered by a link and later proposed by a model was still discovered
  by a link.
- **`trust_state`** (`P4-14`, §2.5). Cached at the domain because screening is paid once per
  domain: a large site is not judged per page, and a domain cleared on Monday does not have one
  page quarantined on Friday because it quoted something. `clean_fetches` counts consecutive
  unflagged fetches and resets like the other counters.

### Grants and their audit

**The unit of sharing is a person, not a credential** (`P3-06`, shared-read-access §3).
Somebody holds several tokens (a browser session, an MCP client on a laptop, another on a
server), and revoking their access must revoke all of them: `agent_tokens.grant_id` points at
the grant, and revoking it revokes every token beneath it in one statement. `subject_kind` is a
column rather than inferred from whether the subject looks like an email address, because a
person (via SSO) and a machine (via a service token) are audited differently.

`grant_audit` (`P3-11`) has one row per tool call, **indexed by grant, not token**: the question
is "what has this person's model been reading", which a per-token log cannot answer once they
hold three clients; the token is recorded too. **Arguments are recorded, results are not**: what
someone searched for is the audit, what came back is the corpus, and copying it would be a
second store without the first one's retention rules (§5.4). `rows` says how much. High volume,
pruned like `fetch_attempts`.

### Budget config

The caps, and the fact that somebody set them (`P4-10`, §16, §11.9). §16 calls runaway cost
"manageable if caps are set before first autonomous run", an ordering requirement nothing
enforced; this table is what "set" means, and `P4-13` refuses to start without it.

- **One row, enforced by a CHECK.** A settings table that can hold two rows eventually does, and
  then "the budget" is whichever the query ordered first.
- **NULL caps mean unconfigured, not unlimited.** `reserve_seeds` refuses a `None` cap, and a
  row that leaves `max_seeds_per_run` empty has not been configured for seeds, so the run does
  not start. "Nobody decided" must never read as "no limit".

### Scheduled jobs

§13.1: no cron files; the scheduler reads its timetable from the database, so a schedule change
is a UI action. A crontab is invisible from the interface, needs SSH to change, and does not
travel with a snapshot. **An interval, not a cron expression**: "every 6 hours" is a number in a
form, `0 */6 * * *` is a support question. A job that must land at a time of day gets
`next_run_at` set once and the interval keeps it there. `module` is run as `python -m`, never as
a shell string, because a row editable from a UI that could name a shell command would make the
table a remote execution surface. See [scheduled-jobs.md](scheduled-jobs.md).

## Saved views

A saved view (`P6-09`, §12.5) is a filter set plus a focus node. It is a table because it is a
piece of research method: it belongs with the corpus, travels in the snapshot, and survives a
cleared cache and a second device. `P6-11`'s last-visit stamp is in `localStorage` for the
opposite reason: per reader, per device, worthless to anybody else.

- **`filters` is a JSON object** shaped like `SearchFilters`. Columns would mean a migration per
  filter, and a view saved before a filter existed would be indistinguishable from one that left
  it out.
- **`last_opened_at`** drives the landing order. NULL ("never opened") sorts last rather than
  being hidden: a view saved and never revisited is still its owner's.

## Areas

Areas (`P6-30`) are derived data, rebuilt wholesale by `worker.areas`. Reads use the newest
build; older builds are removed except the previous one, which the next build reads for stable
positions. Deleting them breaks no promise that nothing is deleted: an area cites nothing and is
re-derived from the passages. See [features/map.md](../features/map.md).

## Runs and reports

Orchestrator state is a plain table, not a workflow framework. A crash at `stage='tagging'`
resumes there on the next wake, and with the high-water mark advancing only after writes commit
(§6.3), that gives full resumability without a layer between the orchestrator and its validated
tool calls.

- **`heartbeat_at`** (`P4-08`). Touched after each step. A crashed run and a live one are both
  `status='running'`; this separates them, and without it a resume would never happen or would
  happen alongside the run it replaces. NULL ("claimed, no step done") reads as stale: a run
  that died before its first beat most needs taking over.
- **At most one unfinished run, by a unique partial index** (`P4-08`). Two orchestrators on one
  corpus means double spend and two sets of writes against one high-water mark, and §11.9 notes
  the first signal of runaway cost would be the bill.
- **`reports.coverage_snapshot`** (§11.13). A report is always bound to a scope (a saved view, a
  coverage cell, the contested list, or a node pair) plus a question; the snapshot records what
  the evidence looked like at submit time, so a draft written over thin coverage stays auditable
  rather than reading as confident prose.

## Boundary schemas

The Pydantic DTOs in `meridian_core/schemas/` describe every service boundary (AGENTS.md:
"pydantic for all boundaries"; §2.6, §11.8: "never trust model output for structure"). They live
in `meridian_core`, not in `services/api`: services import schemas and never define them, so the
API, the orchestrator and the MCP tools share one shape that cannot drift.

- **Read and create variants.** A table gets the variants useful at its boundary, usually a
  `*Create` (what a caller may submit) and a `*Read` (built from an ORM row with
  `from_attributes`). Where the shapes coincide, the module says so rather than generating a
  second class (`P0-10`). `AgentToken` has no DTO: it is never a boundary object, only read and
  written by the auth layer against the ORM row.
- **Enum literals come from the models.** `schemas/enums.py` builds each `Literal` from the
  constraint's own `.enums` tuple instead of retyping the value set, so a status added to a model
  is valid in the API too; an API accepting a status the database rejects is the boundary bug
  this package exists to prevent (`P0-10`). Values with no column, such as the retrieval arm, are
  still Literals so the set crosses into the web package's types: a frontend inventing its own
  union is the one type a cross-language drift test cannot protect.
- **No embedding vectors in any `*Read`.** A 1024-float array per row has no place in an API
  payload.
- **Unknown fields are an error on create** (`CreateBase`). Pydantic ignores unknown keys by
  default, which is wrong where model-generated tool calls arrive (§11.6): an agent inventing a
  field, or trying to set a column the worker owns such as `queue.status`, must fail loudly.
  Silently discarding input is not validation (§11.8).
- **Shared rules live once** in `schemas/common.py` (confidence is a probability, quality tier a
  small ordinal, provenance mandatory). `ProvenanceFields` is required on write so quality tier
  can only move up automatically: a lower tier must never silently overwrite a higher one
  (§11.12), which is checkable only because every row records the tier that produced it.

### Admin DTOs

- **Server-computed verdicts.** `BudgetRead.ready` ("every cap set, and the month not at its
  ceiling"), `FirstRunRead.is_first_run`, `AgentRowRead.key_present` and an agent row's
  unroutable reasons are computed by the server, never derived by the client. A screen that
  recomputes a rule eventually disagrees with the server that enforces it; the worst case is a
  green light for a run that cannot start, or a setup wizard reappearing after a week.
  `key_present` is read from the environment the API sees, because a screen that cannot tell a
  configured agent from an unconfigured one is how somebody enables an agent and waits a day to
  learn it never answered.
- **First run is "no sources yet", not "no seeds yet"**: `make seed` queues seeds at first boot,
  so a fresh install always has them. The setup screen lists the cold-start seeds still pending,
  since §16 calls cold-start seed quality worth an evening, and shows how many are already
  claimed or attempted rather than implying they can still be removed.
- **Edits: omitted versus null.** In every `*Edit`, an omitted key leaves the field alone. Where
  clearing must be possible it is a distinct edit: `GazetteerTermEdit` clears by sending the
  column's empty value, and `BudgetEdit` clears a cap with `null`, which un-configures it and
  stops runs, told apart from omission by `exclude_unset`. `BudgetEdit` duplicates the table's
  bounds so a bad value is a 422 naming the field rather than a 500 from the CHECK, which still
  makes it true.
- **A reason is optional on a topic edit and never in the log**: the server writes a factual one.
  §10.1 requires a reason on every change, and making a person type one to move a slider fills
  the column with "update".
- **A topic row shows `weight` and `share`.** `weight` is stored; `share` is what a draw uses
  after the boost, floors and ceilings, and 0 for anything not active. They differ exactly when
  something interesting is happening, and showing only the weight would make a paused topic
  look like it still competes.
- **A domain row shows three layers**: what was set here (`settings`), what a fetch actually gets
  after the global row and file defaults are merged under it (`resolved`), and what the crawl
  learned or screening concluded. Only the first can be changed on that screen, so the operator
  needs to see which layer sent a domain through the browser or into quarantine; `trust_reason`
  is the sentence the screen justifies itself with. `seed_allowed` NULL ("nobody has looked") is
  shown apart from refused, since they lead to different actions.
- **Gazetteer rows wrap the term rather than widening it.** Whether a term *loads* is not a
  column: a surface form two approved rows share is withheld from the matcher whichever row you
  look at. Flattening it into the term DTO would put a derived field beside stored ones and
  weaken the drift test that checks every DTO field has a column. The surface exists because an
  approved term that silently never matches is the failure it prevents. `withheld_reason` is
  named, not boolean, because the reasons have different fixes: a collision needs one of the two
  rows changed, ambiguity leaves the mention to the resolver, and unapproved or rejected terms are
  just the queue.
- **Enabling an agent and choosing its model are Admin's; endpoints and task types are not.**
  Those are deployment configuration in `config/agents.yaml` and its migrations, where a change
  is reviewable. Enabling is the switch that starts spending, and the model a local server runs
  is the operator's frequent choice (`P6-06`); a `${VARIABLE}` is read from `.env`.
- **Bulk gazetteer decisions** carry the same three verdicts as the per-term routes: a queue of
  thousands of harvested terms is not a weekly task one click at a time.
- `P6-23` (the agent and run screens) was held open while both tables were empty, since an empty
  screen teaches nothing about the full one; both now have rows.

### Model proposals

`schemas/proposals.py` describes the one boundary where input is *generated* (`P4-16`, §11.6,
§11.8, §2.6). These are the strictest schemas, and deliberately not the last word: the write
tools re-check everything through `validation.py`, because a DTO is a shape and the guards are
the rules.

- **A proposal cites passage numbers, never chunk ids.** Passages are shown to the model as
  `[1]`, `[2]`, … by `framing.frame_passages`, the only handle it gets. An id it supplied could be
  invented, and an invented one that exists attaches a fabricated claim to a real chunk. The
  caller, which holds the batch, maps numbers back to chunks. At least one is required: an
  uncited claim is not assertable (§2 principle 3), and a missing citation is the commonest
  malformed answer, so the type refuses it.
- **A mention is a name and a type, not an id.** Resolution happens at write time against the
  graph (§5.5), so the model cannot point an edge at an arbitrary row. `Mention` is not a
  `CreateBase`: it is read out of an answer, and refusing unknown keys would discard a good edge
  because a model added a field. It is the only schema where ignoring extras is the right trade.
- **`stance` and `certainty` are optional** (§8): observable properties, not verdicts. A model
  that must guess one will, and an invented stance is worse than none, since the graph reads a
  missing value as unknown and a wrong one as fact.
- **A tag names an attribute the prompt listed as active**, which `tag_entity` checks; it is not
  a proposal of new schema. `P7-01` is the only route to a new attribute.
- **`comparable_to` states its limits three times**: a CHECK constraint, `add_edge`, and this
  schema. The constraint cannot be bypassed; the schema names the rule while the batch is parsed,
  so one comparison that forgot its disanalogy does not lose the whole batch.

### Graph and notes DTOs

- **Shaped for the neighbourhood, not a view** (§12.3). The node-link canvas and the table read
  the same `NeighbourhoodRead`; a view wanting another shape would be a second query that can
  disagree. Everything is derived from the relational tables, and a quantity with no column is
  named for what it counts (`support`, in passages), so the interface cannot present an invented
  number as measured. A `hint` node is a second-hop dot (design-system.md §2) that says "there is
  more past here" without rendering depth 2, which §12.2 rules out.
- **Graph filters act on evidence, not labels** (`P6-02`). An edge survives when one passage
  behind it satisfies every evidence filter at once; passing an edge on a 2020 press item plus a
  2012 journal article would answer a question nobody asked.
- **Evidence carries stance and certainty only from a citing edge** (§8). A chunk cited only by
  an attribute carries neither, rather than a default that would read as a measurement.
- **The node panel is one request**, not four: every part is about the same node, and four
  requests are four chances for a partly rendered panel that looks like a node with no
  attributes. Its notes are newest first and capped, and default to empty, so a missing notes
  query is an empty section rather than a 500.
- **Notes have their own DTOs** (`P6-05`), though a note is an `entities` row. This is a reading
  surface, and a notes API speaking in `canonical_name` and `is_annotation` would push the graph
  schema through to the reader. **No provenance on the way in**: a person wrote it, the server
  sets that, and `extra="forbid"` refuses a request that tries, because otherwise anything could
  claim to be the reader's own thinking. A note may be about no node yet (a thought that has not
  found its node is worth keeping) and about at most a few: two or three is the interesting case,
  fifty is a tag, and the cap stops one request writing fifty edges.
- **Routes label every hop** as `cited` (a passage states the link) or `similar` (the ends only
  read alike), count them apart, and carry the claims-only answer beside the mixed one, so "no
  cited route within N hops" is a value Gaps (`P6-36`) can read rather than infer (`P6-32`).
- **Map areas and bridges** report measurements, not verdicts: an area's name is its three most
  distinctive terms, and `weak` and `stale` carry their thresholds in `reasons`. A bridge's
  `cited_claims` (the only kind that says two areas connect) and `similar_pairs` are counted
  apart and never added together.

### Crawl health

The `waiting` verdict (`P6-25`) is computed from `fetch_attempts` and `queue` at read time in
`crawlhealth.judge`, not stored. It is the one easy to mistake for `stalled`: work is pending and
nothing is fetching, but every pending row is inside its backoff. That is the backoff working,
and calling it a stall would send someone to restart a worker that has nothing it may do.
