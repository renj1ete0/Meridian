# The knowledge graph

The graph holds **entities** (concepts, places, organisations, interventions, findings and the
other node types the schema defines) and **edges** between them. Every edge names the
passages that justify it. Edges are written by synthesis
([synthesis.md](synthesis.md)) through four narrow, validated write tools, and readers'
own notes join the same graph as annotation nodes. Readers explore it one neighbourhood at a
time: a node page, its evidence, routes between nodes, the neighbourhood of a free-text term,
and the list of contested pairs.

- **Code:** `packages/meridian_core/meridian_core/models/graph.py`, `writes.py`,
  `validation.py`, `contested.py`, `resolution.py`, `mentions.py`, `graphview.py`, `route.py`,
  `neighbourhood.py`, `annotations.py`, `duplicates.py`, `graphaudit.py`, `export.py`,
  `attributeschema.py`; `services/worker/worker/contested.py`, `attributes.py`, `graphaudit.py`;
  `services/api/api/routes/graph.py`, `connect.py`, `neighbourhood.py`, `admin.py` (notes,
  possible duplicates and the graph audit)
- **Tasks:** `P4-01`–`P4-05`, `P4-16`, `P6-01`–`P6-05`, `P6-10`, `P6-15`, `P6-32`, `P6-33`,
  `P7-01`, `P7-02`, `P7-05`, `P7-10`, `B-41`, `B-60`, `B-201`, `B-202`, `B-208`
- **Decisions:** [ADR 0002](../adr/0002-model-routing.md),
  [ADR 0003](../adr/0003-external-assistants-over-mcp.md),
  [ADR 0020](../adr/0020-a-note-is-withdrawn-not-deleted.md)

## How it works

**Storage.** `entities`, `edges`, `attribute_values` and `observations` are ordinary tables,
and they are the source of truth. Apache AGE is installed and can be rebuilt from them, but
nothing reads from it today: the reads use recursive queries and breadth-first search over
the tables.

**Two invariants the columns enforce:**

- every edge has non-empty `supporting_chunk_ids`, because a claim without provenance cannot
  be asserted (§2 principle 3);
- contradictions are kept, not resolved. Both edges stay and are marked `contested_with`
  (§9). A passage arguing against a held claim becomes its own edge rather than a citation
  of the claim, so each side keeps its own evidence ([Contradictions](#contradictions)).

Each edge also records its producing agent, model, `quality_tier` and schema version, and
optionally a stance and a certainty (observable properties of the text, not verdicts).

**Writes** (`writes.py`, `validation.py`). The only writes a model's output ever causes go
through four tools: `add_edge`, `tag_entity`, `enqueue_seed`, and `advance_mark` (the run's
high-water mark). Validation lives below them,
so the orchestrator, an MCP client and a CLI all meet the same rules. Every guard refuses by
raising, never by returning a boolean. The same claim found again is corroboration (a citation
added to the edge, not a second edge), unless it argues the other side. A lower quality tier
never overwrites a higher one.

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
- **A claim is held once per side** (`P7-05`): for, and against. Folding an opposing passage
  into the claim it opposes would resolve the disagreement by discarding one side's stance.

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
  feature. See [the attribute schema](#attribute-schema).

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

<a id="attribute-schema"></a>
### The attribute schema: gate, audit and retirement

`attributeschema.py` (`P7-01`, `P7-02`, §7.3) decides which attributes are active. No model is
called and no person is asked; every decision is a row in `attribute_events` with the numbers it
was made on (`signals`), so a retirement can be read back against what caused it.

**Where a proposal comes from.** The tagging prompt lists the active attributes as a closed set.
When a model tags with a name off that list anyway, the tag is no longer dropped: the name is
spelled the schema's way (`Canopy Cover` → `canopy_cover`), a `proposed` definition is created
on first sight (global, no description; event `proposed`), and the value is stored on it like any
tag, with its chunk and full provenance (`writes.tag_candidate`). A name that only differs from
an active one in case or punctuation is tagged to the active one. A retired name is refused, so
retiring a junk proposal by hand keeps it rejected. A local model held to the answer's schema
(`B-247`) can name only active attributes, so proposals come from models that are not held.

**The gate** (`judge`), run after each tagging batch over the candidates it touched, and daily
over every proposed attribute:

| Check | Rule | On failure |
|---|---|---|
| Evidence | values cite passages from at least `EVIDENCE_SOURCES` (3) distinct sources, a copy folded into its original (`B-44`) | `refused`, stays proposed |
| Enough to judge | at least `MIN_ENTITIES` (4) entities carry a value | `refused`, stays proposed |
| Discrimination | normalised entropy of the values over entities ≥ `MIN_DISCRIMINATION` (0.5) | `refused`, stays proposed |
| Cap | fewer than `CAP` (12) active attributes apply where it would | see below |

A refused candidate stays `proposed` so evidence keeps accumulating, and a refusal is logged only
when its reason changes. **The cap counts what applies to one topic**: the globals plus that
topic's own, at the fullest topic for a global candidate. The seeded schema has 27 active
attributes across five topics but at most 11 apply to any one, which is the reading of "~12
active" under which the seeded schema is not already over it. **At the cap**, a candidate may
displace the lowest-utility attribute *of its own scope* (a global only a global; a topic-local
attribute only one of the same topic), and only one that has already failed at least one audit,
and only with a higher utility. Otherwise it is `held`. Displacing on utility alone would let one
good week of a new name retire an attribute that has never failed, which is the churn §7.3 calls
expensive.

**Discrimination** is Shannon entropy over the value classes, divided by its maximum for the
classes seen: one value everywhere is 0, an even split is 1. Text values are compared without
case or spacing; a number is classed by sign and order of magnitude, since every distinct reading
would otherwise be its own class. When there are more classes than half the entities the score is
scaled down to 0 at one class per entity: values all worded differently split nothing (`B-170`),
though their raw entropy is the maximum.

**Utility**, used to rank at the cap: `discrimination × ln(1 + entities) + coverage + 2 ×
ln(1 + explanatory)`. Coverage is the share of entities carrying any attribute that applies here
that carry this one. Explanatory power counts comparison edges whose dimension or disanalogy names
the attribute (`fleet size`, `fleet_size`, `fleet-size`), and is weighted as the strongest signal
(§7.3). Contradiction resolutions, the other half of §7.3's explanatory power, do not exist yet
(`P7-05`).

**The audit** (`audit_one`, run by `worker.attributes --once`, daily on the timetable). An active
attribute is audited when it has not been in the last `AUDIT_GAP` (28 days), so the pass can run
daily, or twice by hand, and hysteresis still counts months. An audit:

- passes during a `GRACE` (30 days) after activation or restoration: measured, not judged;
- passes when the attribute appears in any comparison;
- fails when its values do not split its entities (discrimination below the bar, with enough
  entities to judge);
- fails when it had no values written in the last 30 days while other attributes did. When
  nothing at all was tagged in the window, use is not judged: an idle synthesis (no model
  configured, a month without runs) is no evidence of disuse, and judging it would retire the
  whole schema;
- otherwise passes.

A pass resets `consecutive_audit_failures`; a failure increments it. **A topic-local attribute
is retired after 2 consecutive failures, a global after 3** (`RETIRE_AFTER`; §7.1 gives globals
the higher bar). The pass audits before it judges, so a retirement opens the slot a held
candidate is then judged against.

**Retirement deletes nothing.** It sets `status = 'retired'`: the attribute leaves the tagging
prompt and `tag_entity` refuses it, but every value stays where it is, with its citations. Values
of a proposed attribute become the attribute's own when it is activated, so no evidence is spent
twice. `python -m worker.attributes --restore NAME` brings a retired attribute back with its
values (refused when the cap is full); `--retire NAME` retires one by hand, proposed ones
included. Both are logged.

**Thresholds are constants** in `attributeschema.py`, not settings: they are the gate's
definition, and changing one is a decision to record, not a field to edit.

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
  are replaced only by a *strictly* better tier, and only within one side of the claim: a
  passage arguing against it is a contradiction to record (`P7-05`, [below](#contradictions)),
  not a value to overwrite, whatever its tier. `WriteResult.created` separates a
  new row from a corroborated one, and only new rows count towards `edges_added`, so a model is
  told when it is re-asserting what the corpus holds.
- **`tag_entity` needs an active attribute.** Creating an *active* definition on first use would
  route around the cap and `P7-01`'s gate; a tag off the list becomes evidence for a `proposed`
  one instead ([the attribute schema](#attribute-schema)). One value per entity, attribute and
  schema version, so a re-tag updates.
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

- **A merge marks the contradictions it creates** (`P7-05`). Two spellings of one subject,
  one argued for and one against, become one claim held on both sides; the merge never folds
  them, marks the pair instead, and logs each mark in `merge_log.combined` (`"kind":
  "contested"`) so the reversal takes back exactly those marks and keeps any made since.

<a id="logging-extra-keys"></a>**Trap:** a log call's `extra` key may not shadow a `LogRecord`
attribute (`name`, for one). `logging` raises rather than dropping it, and only once logging is
configured, so it passes in isolation and fails in the suite. Hence `entity_name`.

### Contradictions

§9: "Contradictions are signal, not error. When sources conflict, store both edges and mark
the pair contested." Until `P7-05` nothing marked a pair: the contested list (`P6-10`) and
every surface reading `contested_with` were built, but the write path folded an opposing
passage into the claim it opposed as one more citation, and its stance was dropped (equal
tier) or replaced the held one (higher tier). Either way the disagreement left no trace. On
the local corpus at the time, over a third of edges had been corroborated by more than one passage
and none was marked contested; how many of those folds hid a disagreement cannot be recovered
without re-reading the passages, which `P7-08` will.

`contested.py` holds the rule; `add_edge`, `merge` and the sweep apply it. Two edges
contradict when they join the **same subject to the same object** and either:

- **assert the same relation from opposite sides.** `edges.opposed` is a generated column,
  true when the stance is `opposes`, and it is part of the claim's key (`uq_edges_claim`), so a
  claim can be held once for and once against, each with its own citations, confidence and
  tier. `mixed`, `neutral`, `unclear` and no stance take no side: they corroborate the claim as
  stated. Generated rather than written by the code, so the key cannot disagree with the stance;
  a test writes every stance the schema allows and compares the column with
  `contested.opposed`.
- **assert opposite relations, both argued for.** `OPPOSITE_RELATIONS` lists directional
  pairs extraction produces (`increases`/`reduces`, `improves`/`worsens`, `enables`/`obstructs`
  and a few more). Relations are open (§5.4), so an unlisted antonym is simply not detected.
  Denials are left alone: "does not increase" agrees with "reduces", and two denials can both
  be true.

The other direction (`Y reduces X`) and another object are not contradictions. Both edges stay
and each names the other in `contested_with`; marking is additive and idempotent, and nothing
ever removes a mark.

- **At write time.** `add_edge` marks every edge the new or corroborated one contradicts, and
  its `detail` ends "contested with edge(s) …" so the model, and the run's journal, see it. An
  opposing edge is a new edge and counts toward `edges_added`: a disagreement is knowledge.
- **A cheaper model may oppose a better one.** The tier rule stops a lower tier overwriting a
  judgement; an opposing assertion overwrites nothing, so a tier-2 model's "against" sits beside
  a tier-4 "for" as a contested pair. The contested list shows each side's stance and certainty,
  not its tier.
- **The sweep**, `python -m worker.contested` (report; `--apply` marks), finds pairs written
  before the rule existed. It reads only subject-object pairs holding more than one edge, and
  it reports, never removes, a mark the rule does not explain. On the local corpus at the time
  it found no pair: only one subject-object pair held two edges, and they did not contradict.
- **Downgrading the migration** refuses while any claim is held on both sides; the old key
  cannot hold them, and folding one side would be resolving them.
- **Observations** carry `contested_with` too (§9's contested figures), but nothing writes an
  observation yet, so nothing marks one.

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

### The graph audit

§14.1 asks for two measurements nothing else takes: "Read 30 random edges monthly, count how
many are wrong", because a graph with many bad edges cannot ground anything written from it; and
"sample merges …; conflation is invisible once done, so it must be looked for deliberately".
`graphaudit.py` (`P7-10`, `B-273`) draws the samples and measures the grades; a person grades. It never
changes the graph: a relation graded wrong stays, a merge graded wrong is not reversed, and
what to do about either is a person's call through the tools that already exist (Possible
duplicates' undo, a re-derivation).

**The draw** (`worker.graphaudit`, on the timetable every 30 days; `graph-audit` row). The window
is `[since, now)`, `since` being where the last draw ended (30 days back for the first), so no
relation is in two draws and none falls between them. It samples up to 30 relations created in
the window, up to 20 merges logged in it and up to 20 matches at resolution (`--edges`,
`--merges`, `--matches`), and records them with a
notification (a `job_complete` row whose payload names the job, so the bell's row says "Grade"
and opens Admin › Graph audit) and a Telegram message when one is configured. What it leaves out:

- **A reader's own note** (`produced_by = 'human'`): it is the reader's claim, not the corpus's.
- **A reversed merge**: somebody has judged it wrong already. The draw counts them instead
  (`merges_reversed`), since "merged then reversed" is a signal in itself.

Guards: a window under a day is refused ("too soon"), so a second run minutes after the first
does not split a month into two thin samples; the scheduler sees no failure, since refusing is
what it should do. A window with nothing in it records nothing, and the next draw covers it.
`window_start` is unique, so two schedulers racing over one period cannot both record a draw.

**Random and repeatable.** Items are ordered by `md5(seed:id)` and the first *n* taken. The seed
is random per draw unless given (`--seed`), and recorded, so the same seed over the same rows
draws the same items in the same order: a draw can be checked by drawing it again. A plain
`ORDER BY random()` could not be.

**What a grader sees is kept with the sample** (`shown`): for a relation, both nodes by name,
the relation, its stance, certainty, comparison axis and disanalogy, validity period, every cited
chunk id and the first three passages; for a merge, both sides as they stood (the target's
passages and aliases from before the merge, from `merge_log.target_fields`, so the source's
evidence is not shown as the target's own), the score and its signals, who decided, and how many
links moved; for a match, the name as the model wrote it (type, jurisdiction, the passages it
was read in) beside the node as the resolver saw it (`resolution_matches.node_name`,
`node_aliases` and `node_chunk_ids`, from before the mention joined it, so the mention's own
passage is not shown as the node's evidence), the score and its signals, and the run. A relation
corroborated later, or a merge reversed, does not change what was graded.

**Grades** are `correct`, `wrong` or `unsure`, with an optional note; a grade replaces an earlier
one (`POST /api/admin/graph-audit/samples/{id}`, permission `graph.audit`). The card does not
name the producer, so a grade is about the claim and not about a model's reputation.

**Precision** is correct / (correct + wrong); `unsure` is counted and left out, since it is
neither. Each figure comes with its 95% Wilson interval, because thirty grades say a range, not a
number: 27 of 30 is "90% (74–97%)". Wilson rather than the plain normal interval, which collapses
to zero width at 30 of 30 and leaves [0, 1] near the ends. It is reported per draw (the measure
over time, `GET /api/admin/graph-audit`) and per producer within a draw: an edge's agent and
model, a merge's `decided_by`, a match's agent and model (the model that named the mention, since
the resolver's own thresholds are the same for every match). §2.3's provenance exists so this
can say *which* model writes bad edges.

**Matches at resolution** (`B-273`). `merge_log` holds only merges made through
`resolution.merge`, which today is a person deciding a possible duplicate. The commoner "these
are one thing" is made while a run writes: the high band resolves a mention to an existing node
(`mentions.resolve_mention`), and the mention becomes that node with no merge. Those are where
conflation would happen unattended, so each is written to `resolution_matches` before the node
takes the mention in, and the draw samples them as a third kind. Every match is recorded,
exact-name ones included: a homonym (one name, two things) is a conflation too. A match has no
undo of its own; a grade of wrong is for a person to act on through the node page. Matches made
before `v0.207.0` were never recorded and cannot be drawn. The near-misses §14.1 also names
(pairs kept apart) are not drawn either.

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
- **A note is withdrawn, not deleted** (`B-201`, [ADR 0020](../adr/0020-a-note-is-withdrawn-not-deleted.md)).
  `withdraw` stamps `entities.withdrawn_at` and keeps the row and its `annotates` edges, so it
  is undone by clearing the stamp (`POST /api/admin/annotations/{id}/withdraw` and
  `/restore`) and nothing that names the note breaks. A withdrawn note leaves the listing, the
  node panels, the export, node search and every graph walk that already leaves out a merged
  node (the neighbourhood, its expansion, path mode); `?withdrawn=true` lists them. It is put
  back before it is edited, since a rewrite of something not shown is a change nobody sees.
  Withdrawing twice keeps the first time.
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

The graph audit is built and on the timetable; no draw has yet been graded on a live corpus, so
there is no precision figure to quote.

## Tests

`tests/integration/test_edges.py`, `test_writes.py`, `test_validation.py`,
`test_resolution.py`, `test_mentions.py`, `test_merge_folds.py`, `test_graph_workspace.py`,
`test_graph_store.py`, `test_route.py`, `test_neighbourhood.py`, `test_annotations.py`,
`test_duplicates.py`, `test_attribute_schema.py`, `test_graph_audit.py`;
`tests/unit/test_edge_schema.py`, `test_export.py`, `test_neighbourhood.py`,
`test_contested.py`, `test_attributeschema.py`, `test_graph_audit.py`;
`web/tests/graph-audit-panel.test.tsx`.

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


**What stands behind a node, and which scope a count is in** (`B-208`). The panel's header now
carries the design's summary line, "41 links · 12 sources · newest 2026-07", from the node's own
counts across the graph, so its weight can be judged before reading its evidence. The table's
columns name their two scopes: "Passages here" (joining that neighbour to this node), then "Its
links" and "Its sources" (the neighbour's totals across the graph). As "Passages, Edges,
Sources" a row reading 1, 5, 4 looked like a contradiction.