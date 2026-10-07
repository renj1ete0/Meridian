# The map

The Map shows the corpus as nested circles. **Regions** contain **areas**, which contain
**sub-areas**. Each one is a cluster of passages that sit close together in embedding space,
named by the field of work nearest to it. Readers zoom through the levels, see where evidence
is weak or stale, follow what connects two areas, and steer the crawl from an area. A
separate view shows passages as points in three dimensions.

- **Code:** `packages/meridian_core/meridian_core/areas.py` (clustering), `areabuild.py`
  (a build), `areaview.py` (reads), `bridges.py`, `bridgeview.py`, `fields.py`,
  `corpusmap.py`, `mapsteer.py`; `services/worker/worker/areas.py`;
  `web/src/explore/map/`, `web/src/explore/MapPage.tsx`
- **Tasks:** `P6-26`, `P6-29`–`P6-35`, `B-74`, `B-93`

## How it works

**A build** (`worker.areas --once`, daily):

1. Draw the passages search would return (no superseded, duplicate or junk text) through the
   same predicate search uses.
2. Fit seeded k-means on a hash sample of at most `FIT_MAX` (20,000) passages, three levels
   nested top down; then assign every passage to its nearest leaf in batches, gathering stats
   along the way.
3. Lay out each group of siblings with the corpus map's projection. Any area whose centroid
   matches one in the previous build (cosine ≥ 0.9) keeps that area's position, so the map
   moves only where the corpus moved.
4. Name each area from `config/fields.yaml` (OpenAlex fields and subfields): deeper levels
   take the nearest subfield, and a region takes the subfield most of its areas were given
   (the nearest field if none were), followed by the area's best distinctive phrase
   ("Transportation: road safety"). See [Naming](#naming).
5. Compute bridges and write the whole build in one transaction. Older builds are removed,
   except the previous one, which the next build reads for positions.

Reads (`areaview.py`) are always of the newest build. A build is written whole in one
transaction, and the caller of `build_areas` commits, so a reader never sees one with half
its areas.

**Weak and stale** are measured, not judged: weak means fewer than 3 independent sources;
stale means nothing new in 180 days. Each comes with its reason in words.

**Bridges** between sibling areas come in three kinds that are never merged:

- **cited**: a graph edge whose evidence spans both areas. This is the only kind that says
  a source states the connection.
- **similar**: the most similar pair of passages across the two.
- **shared terms**: distinctive terms both areas carry.

A concept's anchor, for the cited kind, is the area most of its own supporting passages sit in;
an edge counts when its passages fall in both areas or it joins concepts anchored in each. The
similar kind draws from each area's passages nearest the other's centre: near in meaning and
nothing more, since two passages can be close and disagree, or be close because they share
boilerplate. Bridges are computed for siblings only (regions with regions, areas under one
parent with each other), because those are what the map draws together: for every pair with a
cited claim, and for each area's `NEIGHBOURS` most similar siblings. They are written and
pruned with the build.

**The 3D corpus map** projects a sample of passages onto three PCA components and reports the
share of variance each axis carries, so a reader can see how thin a shadow of the space it is.

**Steering from an area** (`P6-35`). "More of this" boosts the area's dominant topic (when at
least half its passages are about one topic) and queues a search for its own terms. "Less"
is refused for an area about no topic, with that reason. Every action goes through the
ordinary steering machinery and is logged with the area it came from. See
[steering.md](steering.md).

## Design choices

- **An area is a cluster, not a topic.** Nothing in the clustering reads topic labels.
  Topics are a filter and a shade over areas, never their boundaries.
- **The passages search would return.** Builds and the 3D map both draw through
  `search._conditions`, so superseded, duplicate and junk text is absent for the same reason
  it is absent from results; a map of what search hides would be a picture of a different
  corpus.
- **Named from a fixed list** (`B-74`). Names built from passage words let licence strings,
  publisher badges and repository furniture through, and no list of words to refuse would
  ever be complete.
- **PCA, not UMAP or t-SNE.** It is linear and deterministic: distances mean something, the
  same corpus draws the same map, and there is no heavy dependency.
- **Deterministic and bounded.** Seeded clustering on a bounded sample means the same corpus
  fits the same way, and memory does not grow with the corpus.
- **Levels share coordinates** (`B-93`): each finer level is laid out inside its parent's
  circle, so zooming changes level without jumping.

### Clustering

Spherical k-means (cosine, the similarity search's vector arm uses), seeded, with a k-means++
start. An empty cluster is re-seeded at the vector worst served by its centroid rather than
left as a centroid nobody belongs to.

**Top down, not bottom up.** Grouping leaf centroids upward (centroid linkage) was tried first
and chained on a real corpus: one region took nearly every passage, because a dense mass merges
with each neighbour in turn. Splitting from the top asks each level the same question (where
does this set of passages divide), so every level is balanced by the same measure. Each region
is cut into areas, and each area into leaves, in proportion to its share of the vectors.

**Stable positions.** `inherit_positions` matches new areas to the previous build's at the
same level greedily, best similarity first, and each old area lends its position once, so two
new areas never land on the same spot.

### Naming

Areas were first named by their most distinctive words, and words drawn from text let
whatever the text carries through: licence strings, publisher badges, repository and citation
furniture ("arxiv", "doi", table placeholders, section labels), and the hosts of cited links.
A stop list of such furniture (`B-71`) and a rule dropping address-like tokens helped, but a
list of words to refuse never ends. So since `B-74` an area is named from a fixed list,
`config/fields.yaml`, by the entry nearest its centroid in the same embedding space as the
passages; a name outside the list cannot appear. The embedding happens in the build job, which
has the embedder; `fields.py` is pure apart from reading the file.

- **Centred vectors.** Matching raw label vectors to centroids let a few generic labels win
  everything (on a live map, one subfield named five of twelve regions), because they sit near
  the middle of the whole space. With the mean row subtracted, what is left is what is
  particular to each.
- **Regions take their areas' subfield.** There are too few regions to centre on, and a region
  named apart from its own contents reads as a contradiction one click down. Naming regions
  by field ("Social Sciences") was tried first and named most live regions alike. Each region
  takes the subfield most of its areas were given, weighted by passages; a region with no
  named areas falls back to the nearest field.
- **Told apart.** Names must be unique across a whole level, not only among siblings, since
  the map is read one level at a time. Areas that would share a name take their second choice
  ("Transportation & Urban Studies"; an ampersand because many subfield names already contain
  "and"). Those that still collide carry their own best phrase from their cleaned terms
  ("Transportation (fares and transit)"), so furniture cannot reach it. `B-102` drops the
  second field from a name that also carries a phrase, since the phrase already tells it
  apart and the long form was cut off on the Map; only where the short form stays unique.
- **Distinctive terms** are by class-based TF-IDF, `tf(t, c) · log(1 + A / f(t))`, with `A`
  the mean word count per cluster: frequent here, rare elsewhere. A term must appear in
  passages from `min_sources` different sources in the cluster, or one long document's own
  phrasing names the whole area; a cluster with fewer sources is held to as many as it has.
  Source spread is counted as a document frequency rather than a set of sources per term,
  which on a large corpus held millions of sets and took gigabytes.
- **Without a field**, an area's name is its best two-word phrase among the top terms, else
  its top term (`B-71`).

### The 3D projection

The 3D view (`P6-26`, `P6-29`) lets someone see the vectors: whether topics separate, whether
one source dominates a region, whether a crawl drifted.

- **PCA, not UMAP or t-SNE.** Those draw prettier clusters by way of a non-linear projection
  whose distances mean nothing globally, a random seed that moves every point on each refresh,
  and a dependency (`umap-learn` pulls in numba and llvmlite) larger than the service. PCA is
  linear and deterministic, so "these regions are far apart" is true of the embeddings and
  not of the layout.
- **Honest about how much it shows.** Three components of a 1024-dimensional embedding carry a
  small share of its variance; that share is returned per axis.
- **Three axes, not two** (`P6-29`). The reader expected to look into the vectors, and the
  third component separates regions the first two stack. A client wanting the flat picture
  drops `z`.
- **Subspace iteration, not `eigh`.** The full eigendecomposition of the `d × d` covariance
  was measured at seconds on every open, computing every component when three are drawn.
  Seeded subspace iteration on a block wider than three costs a few thin matrix products and
  is as deterministic. The block is widened well past three because each pass shrinks
  component `k`'s error by the ratio of the first unkept eigenvalue to `λ_k`.
- **Each axis scaled to [-1, 1] on its own**, which stretches the minor axes; with each axis's
  share shown, three legible axes serve better than a third flattened to a sliver.
- **Signs fixed** so each component's largest loading is positive; an eigenvector is defined
  only up to sign, and the same corpus could otherwise draw as its own mirror image.
- **Sampled by a hash of the chunk id**, not `random()`, so the map holds still between
  refreshes. Ordering by id instead would draw the oldest chunks: the first site the crawl
  reached, not the corpus.

### Steering from an area

The map adds no new kind of control: each action is a topic boost with an expiry, a seed on
the queue, or a saved view, so each is reversible where those are and lands in `steering_log`
with the area in the reason. Attention is a weight vector over topics (§10) and nothing draws
seeds per cluster, so "more of this area" has to say which topic it means: the one holding at
least `DOMINANT_SHARE` of its passages. A label naming no configured topic counts as none, since
a label can outlive its topic's row and a steer through it would find no weight to move (found
by the first live click on a copied corpus). "Less" of an area about no topic is refused: nothing
draws it on purpose, and host scores (`B-48`) already keep the crawl out of off-topic sites.

The search queued for an area (`B-106`) is its field name, then its topic, then its first
two-word term. Its top single words were the query before, and at the top level those are the
corpus's commonest words, which search answers with anything.

"This is noise" marks only sources the labeller read whole and found about no topic (empty
labels and no sample label) and that sit mostly in the area: the map says where noise is, the
labels say which of it is noise. A labelled source, or one a person seeded, stays.

## Failure modes and traps

<a id="exact-distance"></a>**Nearest within a subset needs an exact sort.** `ORDER BY
embedding <=> v LIMIT n` is planned as an HNSW scan on a large table, and that scan yields at
most `ef_search` rows *before* the WHERE clause. Asking for one area's passages nearest a point
then returns the few that happen to be among the corpus-wide nearest, often none. Bridges'
`exact_distance` adds `+ 0` so the index does not match, and rows are filtered first and sorted
exactly.

## Operating it

- Job: `areas` (daily). `python -m worker.areas --report` prints a build without writing;
  `--name-only` renames the newest build.
- A missed run only means the map shows yesterday's build. An area id from an older build
  gets a 404 that says the map was rebuilt.

## Tests

`tests/unit/test_areas_core.py`, `test_bridges_core.py`, `test_corpusmap_projection.py`,
`test_fields.py`, `test_area_names.py`; `tests/integration/test_areas.py`,
`test_bridges.py`, `test_area_fields.py`, `test_corpusmap.py`, `test_map_steering.py`.
