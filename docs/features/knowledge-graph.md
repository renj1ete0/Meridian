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

## Current state

The graph is still small, and everything in it came from one attended relay session. It grows when synthesis runs unattended, which waits on a model
being configured (ADR 0002).

## Tests

`tests/integration/test_edges.py`, `test_writes.py`, `test_validation.py`,
`test_resolution.py`, `test_mentions.py`, `test_merge_folds.py`, `test_graph_workspace.py`,
`test_graph_store.py`, `test_route.py`, `test_neighbourhood.py`, `test_annotations.py`;
`tests/unit/test_edge_schema.py`, `test_export.py`, `test_neighbourhood.py`.
