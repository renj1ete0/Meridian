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
4. Name each area from `config/fields.yaml` (OpenAlex fields and subfields, plus the kinds of
   public record a research list has no name for): deeper levels take the nearest subfield
   that fits well enough, and a region is named from what its areas were given: a subfield or
   field holding a majority of its passages, else its two largest together, else its terms.
   See [Naming](#naming).
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
- **Regions are named from their areas.** There are too few regions to centre on, and a region
  named apart from its own contents reads as a contradiction one click down. Naming regions
  by field ("Social Sciences") was tried first and named most live regions alike. Since
  `B-157`, weighted by passages, with areas named by nothing counting against every share: a
  subfield holding a majority of the region's passages names it; else a field holding a
  majority (its areas' subfields summed by field); else its two largest subfields, or else
  its two largest fields, as "A & B" if together they hold `REGION_PAIR`; else its terms. A
  region is never named from its own centroid: matched to the fields, live regions came out
  as "Dentistry" or "Economics" for a cluster of legislation.
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

<a id="naming-floor"></a>**What a name must fit** (`B-157`, [ADR 0017](../adr/0017-map-names-must-fit.md)).
On a live build the Fields level read "Speech and Hearing", "Algebra and Number Theory",
"Geometry and Topology" and "Emergency Medical Services". Their terms told a different story:
statutes ("shall", "subsection", "amended"), occupational statistics, a mixed science region,
congressional documents. Three causes:

- *The list had no names for most of the corpus.* A mostly public-sector corpus is largely
  legislation, appropriations, regulations, forms, statistics and site furniture, and a
  research classification names none of them, so each cluster took whichever subfield was
  least far away. Even by plain cosine, "Law" ranked 10th to 90th for clusters of statute
  text. `fields.yaml` now carries a "Public Records" field with ten such kinds; on the live
  build all ten were used, and the legal, budget and forms clusters took them.
- *Almost any fit was accepted.* `MIN_SIMILARITY` was 0.05 after centring, which nearly every
  label clears. Read against each area's terms, names under 0.20 were mismatches ("Small
  Animals" for a page of trading listings, "Genetics" for document-index furniture); above
  it, mostly right. Raised to 0.20; on the live build 70 of 81 subfield-level areas and 350 of
  401 below them keep a listed name, the rest their terms.
- *A plurality named a mixed region.* "Geometry and Topology" named a 72,000-passage region
  after one area holding a seventh of it. Regions now need a majority, or a named pair.

**A name must also win clearly** (`B-159`, [ADR 0018](../adr/0018-a-map-name-must-win-clearly.md)).
Above the floor, near-ties still named areas wrongly: statute text took "Pharmacy" a few
thousandths ahead of two other medical names. Judged by hand against their terms, about 41% of
the live build's names did not fit, and only 6 of 25 near-tied ones did. A subfield now names an
area at a centred similarity of 0.33 or more, or at 0.25 or more when it beats the third-nearest
by 0.03 (`CONFIDENT_SIMILARITY`, `MIN_SIMILARITY`, `MIN_MARGIN`). Checked on 30 more areas judged
before the rule was applied, wrong names fell from 9 to 3 and right ones from 21 to 19; a floor
of 0.30 alone was as accurate but kept 9 of the 21 right names. High near-ties keep their name,
because up there they are siblings that both fit.

With more areas on their terms, two top-level regions both read "Data" (`B-172`). Areas listed
together whose names come from terms and coincide are told apart on read by their next term of
their own, as the build already does for listed names: "Data (health)", "Data (research)". Only
clashing names are qualified; a single term's qualifier is often noise ("Sep-2026 (docs)"), so
qualifying every one-word name was measured and left out.

Re-matching every area against its own terms, rather than its centroid, was also tried and was
worse: as plain cosine it gave one generic subfield to most areas, and centred it gave
oddities of its own. The terms stay a fallback, not a matcher.

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
  reached, not the corpus. The hash is Knuth's multiplicative one modulo 2³², a third of the
  cost of `md5(chunk_id::text)` on a real corpus.
- **Kept, not recomputed per request** (`B-156`). The route once recomputed on every read, on
  the premise that a projection is well under a second; on the live corpus it took about two.
  Profiled, a third was pgvector parsing 3,000 vectors from text in Python, so the vectors are
  now read as `real[]`, which the driver decodes in C (1.6 s to 1.2 s). The rest is the two
  queries over every passage. The answer is kept per sample and filter for ten minutes and
  refreshed behind the reader, as Gaps is (`api/cache.py`); a warm read takes milliseconds.
  The corpus moves over hours, and `as_of` says when the picture was taken, so the lag is
  stated, not hidden.

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

## In the web app

The arithmetic behind the Map's screens lives in `web/src/lib/areas.ts`, `corpusmap.ts` and
`topicweb.ts`, apart from the SVG and WebGL so it is tested without a browser.

### Circles do not overlap; lines cross them

Reported as overlapping fields (`B-157`): measured on the live build at 1440 and 390 px, every
pair of field circles keeps the 14 px gap `placeAreas` gives it. What reads as overlap is the
links drawn across the circles' translucent fills and the faint globe outline behind them.
Drawing links behind opaque fills, or stopping them at the circles they pass, is a design
question left open.

### Circle sizes

A circle's *area* is proportional to the passages collected (radius ∝ √passages), the
operator's decision: size means passages, with a key. The largest circle on screen gets the
maximum radius, and the size key is drawn at the same scale, so it stays true.

### Semantic zoom

A finer level is laid out inside the coarser one's circles, so the two share coordinates and
zooming in simply reveals it: the semantic zoom of a map, where detail appears as there is room
for it. `ZOOM_FOR_DEPTH` gives the magnification at which each level below the root takes over.

### Research shading

The "research" shade is the share of a field's passages from peer-reviewed sources alone.
Government is not counted with it: the corpus is mostly government pages, so a
government-or-research share shades almost every field alike, while the research share alone
runs from none to about a quarter.

### Topic colours

The corpus map gives each topic a colour by its *name*, never its size or rank. Each name
hashes to a preferred slot among the eight series colours and keeps it unless an alphabetically
earlier name holds it, in which case it takes the next free one; a ninth topic is "Other". The
first map assigned slots by alphabetical position, which is rank by another name: a new topic
sorting first moved every colour after it, and a reader who learned "blue is that topic" found
it orange the next morning. Hashed, a newcomer can displace at most the topics it collides
with. Every topic any point carries gets a slot, not only primaries, so a topic that is only
ever a second label keeps the same colour on the day it becomes somebody's first. Hiding a
topic in the legend does not reassign, so the others keep theirs.

The legend and table give two numbers per topic, because a passage can be about several
topics (`P2-21`) and is drawn in one, its primary. Counting only the colour would say a topic
that is every other passage's second subject is barely present; counting every label would make
the rows sum past the passages drawn. `count` sums to the total; `carrying` answers "how much
of this picture is about X".

### The topics ring

The Topics view (`B-72`) places topic circles on a ring, largest at twelve o'clock and clockwise
from there, area proportional to sources with a floor so a topic holding one source is still
something a finger can hit. A ring rather than a force layout: it is stable between loads, and
with a handful of topics every pair is a visible chord. Labels sit outside the ring where there
is width, so the chords, which all run inside it, never cross a name; on a narrow screen there
is no room at the sides, and each name goes under its circle.

### The passage cloud

The passage cloud (`P6-26`, `P6-29`) places every sampled passage by its embedding, in three
dimensions by default and two on request. It is for seeing the corpus as the vector arm sees
it: whether topics separate, where one source has piled up, what a crawl wandered into. It is
not the embedding space itself, so the caption says how much of the space the axes carry, and
the picture cannot pass for more than a shadow of it. The flat view names two shares, not
three, because it draws two; quoting the third would claim structure the picture does not
contain. Points are drawn square, not stretched to the box, since stretching one axis would
make distances along it look larger. The hover target is larger than a dot, and the last-drawn
point wins a tie because it is on top. A topic with null labels and one with an empty list look
the same on the canvas and are opposite answers ("not yet examined" against "no topic"), so the
hover card says which.

The flat view (`P6-26`) is kept beside the 3D one rather than replaced by it: it needs no
WebGL, it holds still, and a flat picture is the one a reader can compare with last week's
screenshot. The 3D view draws every dot in one call, a single `Points` with per-vertex colour
and visibility: a mesh per point would be thousands of draw calls and would not hold 60 fps
past a few hundred, while one `Points` holds eight thousand without trying. Dots are round
with a soft edge, cut in the fragment shader, and fade with depth, the one cue a rotating
picture on a flat screen needs to read as a volume. It turns slowly until the reader touches
it, then holds still for good: a picture that kept moving would make every comparison a
chase. The camera frames the visible points about their centroid, not the origin, since with a
topic hidden what remains can sit well off-centre.

Its arithmetic (`explore/map/geometry.ts`) is computed from plain arrays so it is testable in
jsdom, which has no WebGL. Picking is in screen space rather than by casting a ray at a
world-space threshold: a ray threshold is in scene units, so it is generous zoomed in and
impossible zoomed out, while a radius in pixels is the unit a hand actually misses by. Of the
points inside it the nearest the cursor wins, then the one nearer the camera, the one drawn in
front. An unreadable colour token is null rather than black, because a dot drawn black on a
near-black canvas silently vanishes. Colours are read from the tokens at draw time, so a theme
switch reaches the dots on the next read and `tokens.test.ts` needs no exemption.

### The areas screen

The Map's default view (`P6-34`) shows the corpus as nested areas, one level at a time (fields,
then the subfields inside one field, then its themes), so the picture stays readable whatever
the corpus holds. On screen they are fields, subfields and themes; the code, API and URL keep
the older word "area", so old links still open. An amber outline marks an area that is weak (few
independent sources) or stale (nothing new stored for months), with the reasons in words on
hover and in the panel. Lines are bridges: solid where a claim in the graph is cited across the
two, dashed where the nearest passages are merely similar. **A field is a cluster, not a
topic**, and the screen says so where it lists them: the server names each cluster for the
field of work its passages read as, and topics are a filter over the clusters, not their bounds.

Each level's circles together hold the same passages as the level above, so at a scale share
of 1 they cover the same area; the per-level spacing lets them spread into the room the
coarser level left.


**Ways on from a field** (`B-186`), from walking the Map as a reader:

- **Terms are searches.** A theme's distinctive terms were inert chips; each now opens Find on
  that term, the way into its passages across the corpus.
- **A child is read by its term inside a parent named alike.** Names told apart by a term
  (`B-172`) repeat their parent's base: under "Field (one)" the list read "Field (two)",
  "Field (three)" and so on, eight times. Inside that parent the list and the circles show
  "two", "three" (`nameWithin`); the breadcrumb, the hover card and
  the row's title keep the full name.
- **The jump box finds concepts too.** Typing a term found fields only, though a concept node of
  that name might be what was wanted; the first concept it names now heads the list and opens
  its node. The placeholder ends in ↵, since nothing happens until Enter.
- **The hover card sits below the level controls** and is not drawn for the area the side
  panel already shows; at the deepest level it covered the Fields/Subfields/Themes switch.

**The open theme is in the link, and the menu opens on touch** (`B-200`). `?pick=<area>` names
the theme in the side panel, replaced rather than pushed (opening a panel is not a step of Back),
and a link carrying it opens on that theme once its level arrives. The right-click menu, the only
way to "Research something new here", also opens on a touch held for 550 ms: iOS sends no
`contextmenu`, so a phone had no way in. A quick tap is still a tap, and the touch that opened the
menu does not also open the circle.
### Steering from the map

Right-click an area for more, less, make a topic, or watch; right-click empty canvas to suggest
something new to search for (`P6-35`). Each writes through steering that already exists (a
topic boost with an expiry, a search on the queue, a new topic, a saved view), so each is
reversible where those are and lands in the steering log. The menu reads first what an action
would move, and says so: "more" of an area names the topic it will boost, and "less" of an area
no topic holds is shown disabled with the reason.

### The topics readout

The Topics view's readout lists exact combinations as bars rather than drawing a Venn diagram:
past three sets a Venn cannot be drawn with honest areas, and a bar per combination reads the
same at two topics as at six.
