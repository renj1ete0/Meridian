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
4. Name each area from `config/fields.yaml` (OpenAlex fields and subfields): regions take
   the nearest field, deeper levels the nearest subfield, followed by the area's best
   distinctive phrase ("Transportation: road safety").
5. Compute bridges and write the whole build in one transaction. Older builds are removed,
   except the previous one, which the next build reads for positions.

**Weak and stale** are measured, not judged: weak means fewer than 3 independent sources;
stale means nothing new in 180 days. Each comes with its reason in words.

**Bridges** between sibling areas come in three kinds that are never merged:

- **cited**: a graph edge whose evidence spans both areas. This is the only kind that says
  a source states the connection.
- **similar**: the most similar pair of passages across the two.
- **shared terms**: distinctive terms both areas carry.

**The 3D corpus map** projects a sample of passages onto three PCA components and reports the
share of variance each axis carries, so a reader can see how thin a shadow of the space it is.

**Steering from an area** (`P6-35`). "More of this" boosts the area's dominant topic (when at
least half its passages are about one topic) and queues a search for its own terms. "Less"
is refused for an area about no topic, with that reason. Every action goes through the
ordinary steering machinery and is logged with the area it came from. See
[steering.md](steering.md).

## Design choices

- **An area is a cluster, not a topic.** Topics are a filter and a shade over areas, never
  their boundaries.
- **Named from a fixed list** (`B-74`). Names built from passage words let licence strings,
  publisher badges and repository furniture through, and no list of words to refuse would
  ever be complete.
- **PCA, not UMAP or t-SNE.** It is linear and deterministic: distances mean something, the
  same corpus draws the same map, and there is no heavy dependency.
- **Deterministic and bounded.** Seeded clustering on a bounded sample means the same corpus
  fits the same way, and memory does not grow with the corpus.
- **Levels share coordinates** (`B-93`): each finer level is laid out inside its parent's
  circle, so zooming changes level without jumping.

## Operating it

- Job: `areas` (daily). `python -m worker.areas --report` prints a build without writing;
  `--name-only` renames the newest build.
- A missed run only means the map shows yesterday's build. An area id from an older build
  gets a 404 that says the map was rebuilt.

## Tests

`tests/unit/test_areas_core.py`, `test_bridges_core.py`, `test_corpusmap_projection.py`,
`test_fields.py`, `test_area_names.py`; `tests/integration/test_areas.py`,
`test_bridges.py`, `test_area_fields.py`, `test_corpusmap.py`, `test_map_steering.py`.
