# The knowledge graph

The graph holds **entities** (concepts, places, organisations, interventions, findings and the
other node types the schema defines) and **edges** between them. Every edge names the
passages that justify it. Edges are written by synthesis
([synthesis.md](synthesis.md)) through four narrow, validated write tools, and readers'
own notes join the same graph as annotation nodes. Readers explore it one neighbourhood at a
time: a node page, its evidence, routes between nodes, the neighbourhood of a free-text term,
and the list of contested pairs.

- **Code:** `packages/meridian_core/meridian_core/models/graph.py`, `writes.py`,
  `validation.py`, `resolution.py`, `mentions.py`, `graphview.py`, `route.py`,
  `neighbourhood.py`, `annotations.py`, `export.py`; `services/api/api/routes/graph.py`,
  `connect.py`, `neighbourhood.py`
- **Tasks:** `P4-01`–`P4-05`, `P4-16`, `P6-01`–`P6-05`, `P6-10`, `P6-15`, `P6-32`, `P6-33`,
  `B-41`, `B-60`
- **Decisions:** [ADR 0002](../adr/0002-model-routing.md),
  [ADR 0003](../adr/0003-external-assistants-over-mcp.md)

## How it works

**Storage.** `entities`, `edges`, `attribute_values` and `observations` are ordinary tables,
and they are the source of truth. Apache AGE is installed and can be rebuilt from them, but
nothing reads from it today: the reads use recursive queries and breadth-first search over
the tables.

**Two invariants the columns enforce:**

- every edge has non-empty `supporting_chunk_ids`, because a claim without provenance cannot
  be asserted (§2 principle 3);
- contradictions are kept, not resolved. Both edges stay and are marked `contested_with`
  (§9).

Each edge also records its producing agent, model, `quality_tier` and schema version, and
optionally a stance and a certainty (observable properties of the text, not verdicts).

**Writes** (`writes.py`, `validation.py`). The only writes a model's output ever causes go
through four tools: `add_edge`, `tag_entity`, `enqueue_seed`, and `advance_mark` (the run's
high-water mark). Validation lives below them,
so the orchestrator, an MCP client and a CLI all meet the same rules. Every guard refuses by
raising, never by returning a boolean. The same claim found again is corroboration (a citation
added to the edge, not a second edge). A lower quality tier never overwrites a higher one.

**Resolution** (`resolution.py`, `mentions.py`). A name a model read becomes a node at write
time, in four steps: normalise, block (never across node types or stated jurisdictions),
score, decide. A confident match merges into the existing node. The uncertain middle band
creates a second node and raises a notification for adjudication, because a visible
duplicate is safer than a silent conflation. A model may not create an annotation node.

**Reading** (`graphview.py`), following §12.2's rule never to render the whole graph:

- a node's neighbours to depth 1, ranked by **support** (how many distinct passages justify
  the edge; there is no weight column, so nothing pretends there is one);
- filters by tier, date or topic act on the *evidence*: an edge survives if at least one
  passage behind it passes every filter;
- facet counts are taken before filtering, so ticking a box never hides the option that would
  bring a neighbour back.

**Routes** (`route.py`, `P6-32`). Between two subjects, the shortest route mixes **cited**
hops (edges) and **similar** hops (names that read alike). Each hop is labelled, and the
claims-only answer is always computed beside the mixed one, so "no stated connection within N
hops" is a finding Gaps can use.

**A term's neighbourhood** (`neighbourhood.py`, `P6-33`). The inner ring holds what a passage
states a link to; the outer ring holds what only reads alike (name-vector cosine ≥ 0.70). An
entity in both rings is shown only in the inner one. The anchor node is chosen by name (case,
spacing and regular plurals aside), never by nearest vector.

**Annotations** (`P6-05`). A reader's note is an `entities` row of type `annotation`,
attached by `annotates` edges, with its author set by the server. Its own
`supporting_chunk_ids` hold the passages the reader was looking at.

**Export** (`P6-15`): BibTeX and Markdown, built only from stored fields. A missing field is
omitted, never guessed.

## Design choices

- **Ranking says what it counts.** "Support" counts passages; it is never presented as a
  weight somebody computed.
- **Merges are reversible and fold repeated edges** (`B-41`). The fold is logged on the
  merge, so undoing the merge splits them again.
- **A unique constraint on edges would break merges** (`B-60`), so merge re-points edge by
  edge and the uniqueness check is deferred.

### Storage

The graph is relational adjacency tables, not Apache AGE storage. §12.1 loads a *filtered
subgraph* client-side and runs topology (communities, centrality, pathfinding) there, and
recursive queries over these tables serve that retrieval directly; AGE can be layered on later
(`P4-01`) without the schema changing. The one piece of topology done on the server is path
mode's breadth-first search, because the client only ever holds one neighbourhood and a route
usually leaves it.

- **Jurisdiction is in an entity's uniqueness key.** One name routinely denotes unrelated
  things in different countries, and alias and string matching both succeed on those; without
  the key the graph would be forced to conflate them.
- **An entity's own `supporting_chunk_ids`** exist for annotations only (see
  [Annotations](#annotations)). A derived entity is justified by the edges and attribute values
  that cite it, each carrying their own chunks, so it leaves this empty.
- **A comparison edge carries its axis and its disanalogy** (§7.2). Two cases can match on one
  attribute and diverge completely on the ones that decide the outcome; "they share one
  attribute, so findings transfer" is the shallow inference this guards against.
- **An edge's validity period** is when the *fact* held, distinct from `created_at` (when the
  row was written), `produced_at` (when a model derived it) and the source's publication date.
  Without it, a programme that ended is indistinguishable from one still running. Null means
  open-ended or unknown.
- **One row per claim** (`B-60`): subject, relation and object are unique, because two writers
  that each find no row would both insert and only the database sees both. The check is
  deferred to commit, since a merge moves edges first and folds the duplicates that makes
  before it commits.
- **Attribute definitions are capped** at roughly a dozen active (§7.1, §7.3), with the
  lowest-utility one retired when a stronger candidate qualifies, and only after failing two or
  three consecutive audits: every schema change costs a backfill, so slow-moving schema is a
  feature.

**Observations** (`observations`) hold measured quantities: `(metric, value, unit, denominator,
geography, period)`. They were added after the §14.3 design exercise found the schema could not
answer a "what share of X, by Y" question at all. None of that fits a `finding` node, whose only
text fields are a name and a description, nor `attribute_values`, which holds the capped,
audited comparison dimensions rather than arbitrary facts. **One entity, many observations:**
the subject is the recurring claim, not each reading, so a time series accumulates against one
stable node. A node per reading would produce many near-identical names, and resolution, which
matches on exactly string, vector and neighbourhood similarity, would eventually merge two
periods into one. Kept in the graph, observations are reached by edges, contested pairs and
traversal unchanged. Their breakdown dimensions (segment, time of day, purpose) are open-ended
JSONB qualifiers: a column per dimension does not scale, and pushing them into the subject name
would again multiply near-identical nodes.

### Writes and validation

§11.6 calls the write surface "narrow, validated, orchestrator scope only". *Narrow* is
`writes.py`: four functions, not an ORM handed to a model. *Validated* is `validation.py`
(`P4-05`), built first so the tools are callers rather than authors of the rules. *Orchestrator
scope only* is `grants.py`, where no shared profile, including `operator`, holds a write tool;
nothing in `writes.py` is reachable over MCP. Three callers reach the same writes (the
orchestrator, an external agent, an agent CLI with a scoped token) and none gets privileged
access (§11.1b), so guards live below them: a fourth caller cannot mean a fourth, weaker copy of
the rules.

What this defends against is concrete (§11.8): the crawler fetches arbitrary pages, the pages
become chunks, and chunks are handed to a model holding write tools. A page carrying injected
instructions is the ordinary path with hostile content in it. "Anything enforced only by
prompting will eventually be talked around."

- **Every guard refuses by raising**, never by returning a boolean. A boolean is assigned and
  not checked, and a guard that did not run looks exactly like one that passed.
  `ValidationError` carries the rule it broke, so the model can be told something it can act on
  and refusals are greppable by rule.
- **`check_edge` runs every guard `add_edge` needs in one call**, so a tool cannot pass four by
  forgetting the fifth. Cheapest first: pure checks before the two queries.
- **A refusal writes nothing, including the queue.** A rejected seed that reached `queue` would
  sit `pending` and be retried with backoff: a rejection that has scheduled the thing it
  rejected.
- **Nodes are checked before the foreign key would be.** The key raises at flush, far from the
  tool call and taking the batch with it; checking first tells the model which id was wrong
  while it can still fix it. Named in §11.8 as the first mitigation, since "add an edge to node
  99999999" is what a model does after reading a page that told it to.
- **No self-edges.** "X relates to X" is what a model emits after resolving two mentions to one
  node without noticing: a symptom of a resolution failure, cheaper to refuse than to meet later
  in a traversal.
- **Every cited chunk must exist, and a derived edge must cite one.** A derived edge citing
  nothing cannot be re-derived (§2.4) or checked; only a reader's note may cite nothing, since
  its author is the justification.
- **Provenance is mandatory**: agent, exact model and tier. The downgrade guard compares tiers,
  so a row without one is a row nothing can protect, and the agent and model let `P7-10`'s
  sampling say *which* model produces bad edges.
- **A lower tier never overwrites a higher one** (§11.12). The schedule makes this load-bearing:
  cheap tagging runs far more often than the frontier sessions producing the best edges, so
  without the guard the cheap work overwrites the good on a timer while every run reports
  success. An absent existing value is not a higher tier.
- **`human` is reserved.** No agent may write `produced_by = 'human'`; `scripts/seed.py` refuses
  to register an agent under it as well. See [Annotations](#annotations).
- **A relation type is an identifier, not prose.** §5.4 closes the node types and leaves
  relations open, so a fixed list here would be inventing schema; the check refuses a *shape*
  (length and characters), the one injected instructions arrive in, where every edge would
  become its own relation and grouping would stop meaning anything.
- **Seeds** are refused in the order that makes the logged reason useful: a non-web scheme
  (`file:`, `gopher:`, `data:`, the classic SSRF escalations; `netguard` refuses them at fetch
  time too, but a rejected seed must not reach the queue); a literal private address, judged
  without DNS, since a lookup at seed time is a second answer that can disagree with the one
  `netguard` gets at fetch time (`P1-24`); a domain an operator blocked; and, with
  `require_seed_allowed` (on for the tool surface, off for the crawl's own frontier), a domain
  nobody approved (`P4-12`). There, *declined* and *not yet looked at* are refused with
  different messages, because they lead to different actions. A domain the crawl discovered
  itself is still seedable: `seed_allowed` gates proposals, not the crawl's own reach.
- **The seed cap** (§11.9) bounds the one loop nothing else does: gap analysis emits seeds,
  seeds become documents, tomorrow's batch is larger. It compounds unattended, and the first
  signal would be the bill. `cap=None` refuses (`P4-13`): a missing config must never read as
  unlimited. Reservation is all or nothing, or the cap would depend on the order the model
  listed its seeds; and the run row is locked (`FOR UPDATE`), since concurrent tool calls in one
  run would otherwise both pass at nine of ten.
- **The same claim twice is corroboration.** Same subject, relation and object is one relation
  with more citations; inserting both would make contested pairs, coverage and the digest's
  "edges added" count extraction passes rather than knowledge. Confidence, stance and certainty
  are replaced only by a *strictly* better tier: an equal tier disagreeing with itself is a
  contradiction to record (`P7-05`), not a value to overwrite. `WriteResult.created` separates a
  new row from a corroborated one, and only new rows count towards `edges_added`, so a model is
  told when it is re-asserting what the corpus holds.
- **`tag_entity` needs an active attribute.** Creating a definition on first use would route
  around the cap and `P7-01`'s gate. One value per entity, attribute and schema version, so a
  re-tag updates.
- **`advance_mark` is a tool** because moving the high-water mark is a claim ("everything to
  here has been reasoned over") that deserves the same refusals, and `runs.mark` refuses while
  writes are unflushed.
- **Every tool counts what it did on the run**, since §11.9 compares cost and volume per run.

### Resolution

Without resolution the graph fragments: an organisation's acronym, its full name, "the
Authority" and the acronym with a country appended become four nodes, and coverage, the
attribute tests and cross-topic edges all break silently. Resolution happens **at write time**,
not as periodic cleanup, because a duplicate propagates into edges before anyone notices.
`resolution.py` decides and does not act: the scorer and the thing that rewrites rows (`merge`,
`P4-03`) are separately arguable, so a threshold change is never a change to a function that
writes.

1. **Normalise.** Lower case, punctuation stripped, Unicode to NFKD without combining marks
   (accented and plain spellings of a place are one mention), and abbreviations expanded per
   token from the gazetteer's alias lists (§5.6), so an acronym with a word appended expands
   without that phrase being curated. A short stop list drops words with no identity ("the",
   "Ltd"); it stays short, because a long one starts removing words that do distinguish
   ("National" always gets added and always should not be).
2. **Block.** Same node type always (§5.5's first cheap win), enforced here because no
   threshold compensates for merging an organisation into a place. Two rules unioned: name and alias overlap, and embedding
   kNN, which catches names sharing no words. The name is searched as written *and* as expanded
   (`B-35`): expanded alone, an acronym never found the node literally named by it and every
   re-read founded a copy. Any shared token is enough to be considered (blocking is generous;
   scoring is strict), and the exact name is always a candidate, since a two-letter name has no
   token long enough to search by. Exact matches sort first, so a common word's hits never push
   the node out past the candidate limit, then by id, so ties break the same way on every read
   (`B-37`). Entities that already redirect are not destinations.
3. **Score.** Three signals. String similarity is token-set overlap with a `difflib`
   character tiebreak, deliberately not `rapidfuzz`: a set intersection and a stdlib call are
   hard to get subtly wrong, and a subtle bug here is a silent bad merge. Token-set leads
   because names differ by word order more than by character. Context is the overlap of the two
   entities' evidence (Jaccard, so an entity cited by hundreds of chunks does not overlap with
   everything) and is weighted heaviest: a city and a university sharing a name sit in entirely
   different neighbourhoods. **Absent signals are dropped and the weights renormalised**, never
   counted as zero, or an exact name with no vector or chunks could not score above 0.3.
4. **Decide.** High merges, low separates, the middle band is for adjudication. The thresholds
   keep the middle band narrow, the only place a model adds value.

**Turning a mention into a node** (`mentions.py`, `P4-16`) is the one caller that acts:
extraction has a name and a type and needs an id before `add_edge`.

- **A duplicate is preferred to a merge nobody asked for.** The middle band creates a second
  node and a notification naming both rows and why. A duplicate is visible; a conflation leaves
  one plausible node and no evidence it was ever two. The notification is written here, not left
  to the caller, so the band cannot be dropped in silence.
- **A model may not mint an annotation**, refused here as well as left out of the prompt: the
  prompt is what the model reads, this is what the database gets.
- **Jurisdiction separates before scoring does.** Candidates whose stated jurisdiction differs
  from the mention's are dropped before scoring (an unstated one is compatible). One name in two
  countries is exactly where string and vector similarity are confidently wrong, so two stated,
  different jurisdictions are a fact about identity, not a signal to weigh.
- **A mention is scored with no context.** It has been seen once, so sharing no neighbours is
  an absence, not a disagreement. Scoring with the batch's chunks put an exact name match below
  the separation threshold as soon as its second mention was in a different chunk, so every
  mention founded a new node. The chunks are still recorded on the row, giving the entity a
  neighbourhood next time. The mention is scored against a transient row so the signals are
  identical to those between two stored entities.
- **The verdict is kept with its signals**, so the journal says "merged at 0.94, string 0.99,
  context 0.88": a number nobody can argue with is a threshold nobody can tune.
- With the mention's name vector (`B-40`), blocking and scoring use embeddings too; without one
  they fall back to the name alone.

### Merges and reversal

§5.5: "Merges must be reversible … Bad merges are worse than duplicates because conflation is
invisible once done." The source is **kept as a redirect**, never deleted, which would break
every citation naming it. Merges are refused across node types, into itself, from an entity that
already redirects, and into one that redirects (no chains to follow).

- **The rows moved are recorded**, not just the fact. `merged_from` cannot say which edges came
  along; reversing the second of two merges into one target would take the first's rows too.
  Ids are read first and updated by primary key, because a bulk `UPDATE … WHERE` cannot say
  afterwards which rows it touched. Which *columns* moved is recorded per row (`B-41`): an edge
  between source and target moves one end only, and moving both back would hand the source an
  edge it never had. Merges logged before that fall back to moving back every column naming the
  target, wrong only for rows joining the two.
- **A claim the target already holds is folded, not duplicated** (`B-41`): citations, topic
  labels and contradictions unite; the judgement and its provenance move only to a strictly
  higher tier, as in `add_edge`; empty paired fields (a validity period; a comparison's axis and
  disanalogy) are filled in pairs, so a half-borrowed period cannot end before it begins. Edges
  naming the folded edge as their contradiction name the survivor. Only *moved* edges are
  folded; a duplicate the target already held is not this merge's doing. Attribute values fold
  the same way, since the table allows one per entity and the move would be refused; the
  attribute's usage count drops and `reverse` restores it.
- **Observations are not folded.** Two identical readings from two sources are two pieces of
  evidence for one figure, which is how time series and §9's contested figures are built, and
  deciding when two readings are one claim inside a merge would be inventing schema.
- **Snapshots take every column** from the table, so a column added later is kept by a fold
  without anyone remembering it.
- **The target's aliases, `merged_from` and chunks** are logged before and after: a reversal
  that left the source's name among the target's aliases would send the next mention straight
  back by resolution.
- **Reversal undoes exactly this merge.** Folds split newest first; the survivor loses what the
  fold gave it and nothing it gained since (a later citation stays; a field a later write
  replaced stays). The log row is stamped, not deleted: "merged then reversed" is the signal
  that a threshold is wrong, which `P7-10`'s sampling looks for, and the score and its signals
  are only knowable at the moment of decision.
- **`fold_repeated_edges`** repairs duplicates that merges made before `B-41`. It reports by
  default. Each fold is logged on the newest unreversed merge that moved one of the group's edges
  and whose target the triple names, so reversing that merge splits it as if the merge had made
  it; a group no merge explains is reported and left, since a fold that cannot be undone is the
  one thing this module never does. Idempotent.

<a id="logging-extra-keys"></a>**Trap:** a log call's `extra` key may not shadow a `LogRecord`
attribute (`name`, for one). `logging` raises rather than dropping it, and only once logging is
configured, so it passes in isolation and fails in the suite. Hence `entity_name`.

### Deciding a possible duplicate

Resolution queues the uncertain middle band for a person as a `merge_adjudication`
notification: a run read a name it could not place, created a separate node, and named the
existing one it might be (a duplicate is recoverable and a bad merge is not). Until `B-202`
nothing could decide one, and they became most of the bell.

`meridian_core.duplicates` lists the undecided pairs (`GET /api/admin/duplicates`), each with
both nodes as they stand: type, place, aliases, description, how many stated links touch it,
and two passages behind it, so the call is made on evidence rather than on two names. A pair
either side of which is gone or already redirects is left off the page, since `merge` would
refuse it. `POST /api/admin/duplicates/{id}` takes `merge` or `keep`:

- **Merge** goes through `resolution.merge` (created into candidate), the same reversible path a
  run uses: the created node becomes a redirect and the log records what moved. Refusals
  (across node types, into a redirect) come back as a 409 in the resolver's own words, and the
  pair stays open.
- **Keep apart** changes no node.
- **Undo** (`POST …/undo`) reverses the merge exactly (`resolution.reverse`) or reopens a pair
  kept apart.

The decision is written onto the notification (`payload.decision`), which is where the bell
reads it as settled ("since merged", "since kept apart"), and the bell's row links here. Not yet:
a pair kept apart is not remembered by resolution, so the same two names can be queued again.

The page is Admin › Possible duplicates (`DuplicatesPanel`).

### Reading the graph

`graphview.py` returns at most one node's neighbourhood, one node's evidence, or one route.

- **Support, not weight.** §12.2 asks for "edge weight" and there is no such column; the
  nearest measured quantity is distinct passages, named for what it is.
- **Filters act on evidence**, all together on one passage. Filtering on the node's own labels
  would keep an edge whose only evidence is what the reader excluded. A passage whose source has
  no date fails any date bound: an undated passage is not evidence "from 2020 onwards".
- **The contested list** (`P6-10`) shows each pair once, though §9 names it on both edges; the
  lower edge id is `ours`, an arbitrary order stated as one. Newest first, since a returning
  reader wants what changed; `total` counts before the cap; a `contested_with` id naming a
  vanished edge is dropped.
- **Shortest path** is breadth-first, one query per level, bounded by `max_depth` and
  `MAX_PATH_VISITED` rather than graph size; the best-supported edge is tried first, so of two
  equally short routes the better-evidenced one is returned.

### Routes

`route.py` widens `shortest_path` with similar hops (names whose vectors clear `SIMILAR_FLOOR`,
or a free-text term and its nearest nodes). Hop sources are pluggable `HopSource`s; areas could
join as a third kind of stop without changing the search. Fewest hops wins, then fewer similar
hops, then better support, so resemblance is never preferred to an equally short route of
claims. Merged-away redirects and readers' notes are never stops: a route through the reader's
own note is not the corpus connecting two things. Similar expansion is bounded per node and per
level, because resemblance connects everything to something.

### A term's neighbourhood

The rings never mix: an entity both cited and similar is shown inner only, since listing it
twice would let resemblance pass as corroboration. The anchor is chosen by name because picking
the nearest node by vector would put a stranger's stated links in the inner ring under the
reader's term; near-miss names are offered as candidates. The anchor's name vector is preferred
to embedding the term (it is what other names were embedded as, and costs no call); with
neither, the outer ring is skipped and `similar_basis` is `none`.

Passages for the term come through `search._arm`, so search's filters apply from one site, one
per source (five chunks of one document are one voice). Their similarity floor sits on a
different scale from name-to-name: a short name against a paragraph scores lower however
relevant; passages plainly about the name scored about 0.58–0.69 when measured.

### Annotations

A note is an `entities` row because traversal, path mode and canvas filters all read `entities`
and `edges`; a side table would make the layer §12.5 calls the highest-quality one the only one
the graph cannot see.

- **Authorship is set by `annotations.py` and nowhere else** (§11.8: never trust structure in a
  request). The layer is worth having only while a reader can tell their thinking from the
  corpus's, and a model with a write tool will eventually call into this service. The reserved
  `produced_by` is `human`, deliberately not shaped like an agent id.
- **No tier, no model.** `quality_tier` is an ordinal over models; a person ranked on it would
  make "tier only moves up" a rule about a person.
- **Citations in two places.** The note's own `supporting_chunk_ids` is the source of truth,
  since a note with no target yet has no edges. Each `annotates` edge carries the same list,
  because every edge names its chunks. They cannot drift: this module is the only writer and
  rebuilds the edges wholesale on every change (an `annotates` edge holds nothing the note does
  not, and a diff would be a second code path).
- **Every cited chunk must resolve** and a write is checked whole before anything is written: a
  partly applied write is a note with a dangling edge, which looks fine on review, and a reader
  trusts this layer without re-checking.
- **Editing `about` replaces it**: a note accumulating every node it was pointed at would end up
  attached to the reader's search history. Only the reader's own notes are editable; a hand-edit
  surviving in a derived node could not be re-derived (§2.4).
- **Listed by `produced_at`**, so a rewritten note returns to the top. `about` matches attachment,
  not text, so the panel does not depend on wording. Targets are named, not numbered (`P6-04`),
  in one query per page.

### Export

§12.5: "avoid trapping material in a bespoke store". BibTeX goes into reference managers and
LaTeX; Markdown into anything.

- **Nothing is generated.** A field the document did not carry is omitted; a fabricated author
  or year is wrong in a file somebody pastes into a paper.
- **The entry type is a format, not a ranking.** It follows the tier because that decides which
  fields a reader expects (`@techreport` for an issuing body with no journal, `@online` for the
  web, which carries `urldate`), and `note` carries the tier verbatim.
- **Citation keys are stable and unique**: stable because bibliographies are re-exported and
  diffed, unique because `source_id` is appended (two reports from one body in one year is
  ordinary, and colliding keys drop entries silently).
- **Each entry carries its own access date**, what the page said when the corpus read it, which
  the raw file backs up.
- **Markdown groups passages under their source**, since a reader asks "what did this document
  say"; each citation line carries URL, page or offset, and tier. Notes export with their
  targets by name and a trailing line of chunk ids, the thread back into the corpus.

## Current state

The graph is still small, and everything in it came from one attended relay session. It grows when synthesis runs unattended, which waits on a model
being configured (ADR 0002).

## Tests

`tests/integration/test_edges.py`, `test_writes.py`, `test_validation.py`,
`test_resolution.py`, `test_mentions.py`, `test_merge_folds.py`, `test_graph_workspace.py`,
`test_graph_store.py`, `test_route.py`, `test_neighbourhood.py`, `test_annotations.py`;
`tests/unit/test_edge_schema.py`, `test_export.py`, `test_neighbourhood.py`.

## In the web app

### The graph workspace

`/nodes/{id}` lives in `web/src/explore/graph/` (`P6-01`–`P6-03`). Its drawing decisions
(which node is brass, which edge dashed, what is labelled) are made in pure modules
(`scene.ts`, `style.ts`, `layout.ts`) that the renderer only copies into Sigma, so a test can
read them without WebGL. The client (`graph/api.ts`) sits beside the workspace rather than in
`lib/api.ts` because nothing else reads these routes.

**The canvas.** §12.1 picks Sigma for WebGL, so it stays smooth past where canvas libraries
struggle. What WebGL cannot draw (the meridian graticule, halo rings, a dashed cross-topic edge)
is an SVG layer *under* the WebGL canvas, placed from Sigma's own coordinate conversion every
frame, so it pans and zooms with the nodes. Labels are drawn by hand to design-system.md §2:
Archivo 12 under the node, a 3.5px halo in the canvas ground, and §6's dagger on a contested
node's label; Sigma's default puts the label to the right with no halo, unreadable where edges
cross text. On a dense neighbourhood labels ran into each other, worst on a phone (`B-124`), so
a label overlapping one already drawn this frame moves above its node, then to its right, then
its left (`B-169`), and is left out only when all four are taken; its node still names itself
on hover. Below stays first because that is where the design puts it; a captioned label (a
cross-topic node) stays above or below, where its caption can hang. On a fourteen-neighbour
node one label in fourteen had been dropped at desktop width and about a third on a phone;
after the change every one was drawn at both widths. Sigma is imported lazily: it needs WebGL at construction and jsdom has none, so tests
stay off the GPU path, and a machine without WebGL gets a sentence instead of a blank rectangle.
Colours are read from the tokens at runtime, because WebGL takes colour strings, not CSS
classes, and `tests/tokens.test.ts` forbids a literal outside `tokens.css`. The brass tint never
appears without the dagger: strip the colour and the reading survives. Several edges between one
pair are drawn as one line, a contested one winning, since WebGL would stack them; the table view
lists every edge.

**The layout is radial and deterministic**, not force-directed. §12.2 draws one focus and its
neighbours, so the geometry already has a centre; a force layout would spend its iterations
rediscovering that, would place the same neighbourhood differently on every visit, and would
pull in a second dependency. The focus is at the origin, neighbours on a ring in rank order, and
second-hop hints just outside the neighbour they hang from, so the same data draws the same
picture twice and "strongest first" reads clockwise from the top. The graticule is drawn in the
same abstract units, so it scales with the nodes. A path is laid out left to right on a shallow
arc: a route is a sequence, a line reads as one, and the arc keeps a long route's labels off
the straight edges between them.

**The URL carries the filters**, in the API's parameter names, so the URL, the request and a
saved view spell a filter the same way, and a saved view's `topic` filters Find too. A
workspace whose filters lived only in component state could not be linked. **Parsing refuses
rather than guesses**: an unknown tier or a date that is not `YYYY-MM-DD` is dropped rather
than sent and answered with a 422, so a hand-edited link opens the workspace, not an error.

**The filter rail's counts are of this focus's neighbours**, taken before any filter
(`GraphFacetsRead`). The artboard's thousands are corpus-wide document counts; beside one
node's neighbourhood they would suggest a box brings back thousands of things when it brings
back three.

**The matrix view** (§12.3, "which clusters are dense? where are the gaps?") makes *absence*
visible: an empty cell is a pair with no relation, which a node-link picture shows only as a
missing line nobody looks for. Second-hop hints are left out: the API returns only their edge to
the neighbour that leads there, so a row for one would be empty by construction, a gap in the
request rather than in the graph. The matrix is symmetric (a cell counts edges either way);
direction survives in the readout.

The node search box is a combobox with the keyboard behaviour one expects, because a reader who
types a name wants to land on it without the mouse; its requests are debounced and aborted when
superseded, so a fast typist never sees results for a prefix already typed past. Recently
focused nodes are kept per browser for the landing's "where you were", like the since-last-visit
stamp. Returning to a node already on the breadcrumb trail cuts the trail there.
