"""Diversity seeds read off the graph (task `P5-05`, spec §7.4).

`searchseeds` writes §7.4's seeds per *topic*. Two of the five mechanisms need
the graph instead, because what triggers them is a property of a node:

- **Mechanism 2, tier imbalance.** A node whose evidence comes entirely, or
  overwhelmingly, from one source tier gets queries phrased to reach the tiers
  it lacks. A node backed only by government pages is asked for research and
  for reporting; one backed only by papers is asked for official and press
  material. Tier is assigned mechanically at ingestion (§5.2), so this is
  arithmetic over provenance and never a judgement.
- **Mechanism 4, random distant walks.** A few nodes far from where the crawl
  has been — one connected to nothing, or one whose vector is far from the
  centroid of the most recently embedded chunks — are searched by name, so
  the crawl occasionally steps outside its own neighbourhood.

**Mechanism 1 at node level is not here, on purpose.** §7.4 triggers it when
every *source* on a node argues the same direction, and the position a source
argues is a model's output (§8) that no pass extracts yet. `edges.stance` is
not that: it is one model's reading of one relation, not what each source
argues, and counting it as the sources' stance would make a counter-seed out
of an extraction artefact. :func:`stance_counter` is the hook — it applies the
rule when a node carries per-source stances, and :func:`graph_inputs` passes
none until a stance field exists to read. Topic-level counter-phrasings
(`searchseeds.COUNTER`) run meanwhile.

**No model.** Everything below is SQL over provenance columns and string
templates; the worker that runs it never calls an LLM (§2.1).

Evidence for a node is every chunk that justifies something about it: the
edges at either end, its attribute values, its observations, and — for a
hand-written note — its own chunks. A node's tier mix is counted over the
*distinct sources* of those chunks, excluding sources marked as copies of
another: ten chunks of one report are one voice, and so is a mirror of it.
"""

from __future__ import annotations

import dataclasses
import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models.source import SOURCE_TIER
from .searchseeds import COUNTER, NEWS_BANG, topic_words

#: Every tier a source can have, from the column's own CHECK.
TIERS: tuple[str, ...] = tuple(SOURCE_TIER.enums)

#: The tiers a counter-seed may aim at, and how a query reaches each. The
#: phrasings are the words those tiers' own documents use about themselves,
#: which is what a search engine matches. `!science` and `!news` are SearXNG's
#: category bangs, sending the query to the scholarly and news engines only.
#:
#: `informal` is never a target. It is what an unaimed crawl already finds most
#: of, and a node lacking blogs is not a node whose evidence is lopsided in any
#: way that matters to a written argument.
TIER_PHRASINGS: dict[str, tuple[str, ...]] = {
    "peer_reviewed": ("!science {}", "{} peer-reviewed study"),
    "government": ("{} government report", "{} official evaluation"),
    "press": (f"{NEWS_BANG} {{}}", "{} investigation"),
    "institutional": ("{} think tank report", "{} NGO report"),
}
TARGET_TIERS: tuple[str, ...] = tuple(TIER_PHRASINGS)

#: Fewer distinct sources than this and a node's tier mix says nothing. One
#: source is single-tier by construction, and two sharing a tier is what two
#: draws from a five-tier crawl often do. Three all from one tier is the first
#: count at which uniformity is a finding rather than a coincidence. It also
#: keeps the mechanism aimed: most nodes of a young graph rest on one source,
#: and firing on them would mean "search every node", not "correct an
#: imbalance".
MIN_SOURCES = 3

#: One tier holding at least this share of a node's sources is "overwhelming".
#: At three or four sources that demands unanimity (2/3 and 3/4 fall short);
#: from five, a single dissenting source does not balance a node.
DOMINANT_SHARE = 0.8

#: Node types whose names are not subjects to search. A place is a whole
#: jurisdiction ("research on <a city>" is everything), a `source` node is a
#: document already held, and an annotation is a person's note.
EXCLUDED_NODE_TYPES = frozenset({"place", "source", "annotation"})

#: Names shorter than this are mostly acronyms, which search engines read as
#: something else; longer than this many words, a name is a sentence.
MIN_NAME = 4
MAX_NAME_WORDS = 8
MIN_ALIAS = 5

#: Per-run caps. Bounded in absolute numbers so the graph's share of §7.4's
#: reserved budget cannot grow with the graph: at the six-hourly cadence this
#: is at most 32 graph queries a day, whatever the node count.
TIER_PER_RUN = 6
#: No single node takes a run's whole tier budget.
PER_NODE = 2
WALKS_PER_RUN = 2

#: A node with at most this many edges counts as far by edge count. Zero —
#: connected to nothing — rather than one: a young graph is mostly single
#: edges from one extraction pass, and at "one edge or fewer" four nodes in
#: five were "far", which made the walk a uniform draw over the graph.
LOW_DEGREE = 0
#: The share of nodes, by distance from the recent crawl's centroid, that
#: counts as far.
FAR_FRACTION = 0.25
#: "The recent crawl": the newest this many embedded chunks.
RECENT_CHUNKS = 5000

_SPACE = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class NodeEvidence:
    entity_id: int
    name: str
    node_type: str
    aliases: tuple[str, ...] = ()
    #: The active topic a query about this node is filed under, or None.
    topic: str | None = None
    #: Distinct, non-duplicate sources behind the node, per tier.
    tiers: Mapping[str, int] = dataclasses.field(default_factory=dict)
    #: Edges at either end.
    degree: int = 0
    #: Cosine distance from the recent crawl's centroid; None if either is missing.
    distance: float | None = None
    #: Sources per argued position, once a stance field exists (see module
    #: docstring). None means "not known", never "balanced".
    stances: Mapping[str, int] | None = None

    @property
    def sources(self) -> int:
        return sum(self.tiers.values())


@dataclasses.dataclass(frozen=True)
class GraphQuery:
    topic: str
    text: str
    #: A `queue.seed_mechanism` value.
    mechanism: str
    entity_id: int
    node: str
    #: Why, in words, for the report.
    reason: str

    @property
    def kind(self) -> str:
        return self.mechanism


@dataclasses.dataclass(frozen=True)
class TierGap:
    dominant: str
    share: float
    missing: tuple[str, ...]


def _clean(value: str) -> str:
    return _SPACE.sub(" ", value).strip()


def searchable(name: str) -> bool:
    name = _clean(name)
    return len(name) >= MIN_NAME and len(name.split()) <= MAX_NAME_WORDS


def eligible(node: NodeEvidence) -> bool:
    """Whether a node may seed a query at all, whatever its evidence."""
    return (
        node.topic is not None
        and node.node_type not in EXCLUDED_NODE_TYPES
        and searchable(node.name)
    )


def name_forms(node: NodeEvidence) -> list[str]:
    """The canonical name, then usable aliases: the words a query is built from."""
    forms = [_clean(node.name)]
    for alias in node.aliases:
        alias = _clean(alias)
        if (
            len(alias) >= MIN_ALIAS
            and searchable(alias)
            and alias.lower() not in {f.lower() for f in forms}
        ):
            forms.append(alias)
    return forms


def tier_gap(node: NodeEvidence) -> TierGap | None:
    """The tier imbalance behind a node, or None if it is balanced or too thin."""
    total = node.sources
    if total < MIN_SOURCES:
        return None
    dominant, count = max(sorted(node.tiers.items()), key=lambda kv: kv[1])
    share = count / total
    if share < DOMINANT_SHARE:
        return None
    missing = tuple(t for t in TARGET_TIERS if t != dominant and not node.tiers.get(t))
    if not missing:
        return None
    return TierGap(dominant, share, missing)


def tier_queries(node: NodeEvidence, gap: TierGap) -> dict[str, list[GraphQuery]]:
    """Every counter-seed a node's gap could produce, grouped by the tier it aims at."""
    assert node.topic is not None
    out: dict[str, list[GraphQuery]] = {}
    for tier in gap.missing:
        reason = f"{node.sources} sources, {gap.share:.0%} {gap.dominant}, none {tier}"
        out[tier] = [
            GraphQuery(
                node.topic,
                _clean(p.format(form)),
                "tier_imbalance",
                node.entity_id,
                node.name,
                reason,
            )
            for form in name_forms(node)
            for p in TIER_PHRASINGS[tier]
        ]
    return out


#: Directions a source can argue that make a node one-sided when they are all
#: the same (§8's stance values; `mixed`, `neutral` and `unclear` are not a side).
SIDES = ("supports", "opposes")


def stance_counter(node: NodeEvidence) -> list[GraphQuery]:
    """§7.4 mechanism 1 at node level: the hook, waiting on a stance field.

    Every source on a node arguing one direction gets counter-phrasings. Returns
    nothing while ``node.stances`` is None, which it always is today:
    :func:`graph_inputs` has no per-source stance to read (module docstring).
    """
    if node.stances is None or node.topic is None or not eligible(node):
        return []
    sided = {s: n for s, n in node.stances.items() if s in SIDES and n}
    if sum(sided.values()) < MIN_SOURCES or len(sided) != 1:
        return []
    (side,) = sided
    reason = f"all {sum(sided.values())} sources that take a side {side}"
    return [
        GraphQuery(
            node.topic, _clean(p.format(form)), "counter_seed", node.entity_id, node.name, reason
        )
        for form in name_forms(node)
        for p in COUNTER
    ]


def far_nodes(nodes: Sequence[NodeEvidence]) -> list[tuple[NodeEvidence, str]]:
    """Nodes far from current focus, each with why (§7.4 mechanism 4).

    Far is either of the spec's two measures: few edges (at most
    :data:`LOW_DEGREE` edges), or in the :data:`FAR_FRACTION` of nodes most
    distant from the recent crawl's centroid. A union, so a graph with no
    vectors yet still has isolated nodes, and a dense graph still has outliers.
    """
    distances = sorted((n.distance for n in nodes if n.distance is not None), reverse=True)
    cutoff = None
    if distances:
        cutoff = distances[max(0, math.ceil(len(distances) * FAR_FRACTION) - 1)]
    out: list[tuple[NodeEvidence, str]] = []
    for node in nodes:
        why = []
        if node.degree <= LOW_DEGREE:
            why.append(f"{node.degree} edge{'s' if node.degree != 1 else ''}")
        if cutoff is not None and node.distance is not None and node.distance >= cutoff:
            why.append(f"distance {node.distance:.2f} from the recent crawl")
        if why:
            out.append((node, ", ".join(why)))
    return out


def walk_queries(node: NodeEvidence, why: str) -> list[GraphQuery]:
    assert node.topic is not None
    name = _clean(node.name)
    texts = [name]
    words = topic_words(node.topic)
    if words.lower() not in name.lower():
        texts.append(f"{name} {words}")
    texts.extend(name_forms(node)[1:])
    return [
        GraphQuery(node.topic, t, "distant_walk", node.entity_id, node.name, f"far: {why}")
        for t in texts
    ]


def plan(
    nodes: Sequence[NodeEvidence],
    *,
    already: Iterable[str],
    seed: int,
    tier_cap: int = TIER_PER_RUN,
    per_node: int = PER_NODE,
    walks: int = WALKS_PER_RUN,
) -> list[GraphQuery]:
    """This run's graph seeds: tier counter-seeds first, then distant walks.

    Deterministic for a given ``seed`` and input. Never repeats a query in
    ``already`` (compared case-insensitively, as `searchseeds.plan` does) or
    within the run. Nodes with the most sources go first — the more evidence
    behind an imbalance, the less likely it is chance — and ties are broken
    by the seeded shuffle, so successive runs reach different nodes.
    """
    if tier_cap < 0 or per_node < 1 or walks < 0:
        raise ValueError("caps must not be negative, and per_node at least 1")
    seen = {_clean(q).lower() for q in already}
    rng = random.Random(seed)
    pool = sorted((n for n in nodes if eligible(n)), key=lambda n: n.entity_id)
    chosen: list[GraphQuery] = []

    def take(query: GraphQuery) -> None:
        seen.add(query.text.lower())
        chosen.append(query)

    ordered = list(pool)
    rng.shuffle(ordered)
    ordered.sort(key=lambda n: -n.sources)
    used: set[int] = set()
    tier_taken = 0
    for node in ordered:
        if tier_taken >= tier_cap:
            break
        # Alternatives, one pick per group: the stance hook's phrasings are one
        # group, each missing tier another. The node-level budget is shared.
        groups: list[list[GraphQuery]] = []
        if countered := stance_counter(node):
            groups.append(countered)
        if (gap := tier_gap(node)) is not None:
            groups.extend(tier_queries(node, gap).values())
        taken_here = 0
        for group in groups:
            if taken_here >= per_node or tier_taken >= tier_cap:
                break
            fresh = next((q for q in group if q.text.lower() not in seen), None)
            if fresh is not None:
                take(fresh)
                taken_here += 1
                tier_taken += 1
        if taken_here:
            used.add(node.entity_id)

    far = [(n, why) for n, why in far_nodes(pool) if n.entity_id not in used]
    rng.shuffle(far)
    walked = 0
    for node, why in far:
        if walked >= walks:
            break
        fresh = next((q for q in walk_queries(node, why) if q.text.lower() not in seen), None)
        if fresh is not None:
            take(fresh)
            walked += 1
    return chosen


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------

_NODES = text(
    """
    SELECT entity_id, canonical_name, node_type, aliases, topic_labels
    FROM entities
    WHERE redirects_to IS NULL AND NOT is_annotation
    """
)

# Every chunk that justifies something about a node, resolved to its source.
# DISTINCT per (node, source): the unit of evidence is a source, not a chunk.
_EVIDENCE = text(
    """
    WITH ev AS (
        SELECT from_node AS entity_id, unnest(supporting_chunk_ids) AS chunk_id FROM edges
        UNION ALL SELECT to_node, unnest(supporting_chunk_ids) FROM edges
        UNION ALL SELECT entity_id, unnest(supporting_chunk_ids) FROM attribute_values
        UNION ALL SELECT subject_entity_id, unnest(supporting_chunk_ids) FROM observations
        UNION ALL SELECT entity_id, unnest(supporting_chunk_ids) FROM entities
    )
    SELECT DISTINCT ev.entity_id, s.source_id, s.source_tier, s.topic_labels
    FROM ev
    JOIN chunks c ON c.chunk_id = ev.chunk_id
    JOIN sources s ON s.source_id = c.source_id
    WHERE s.duplicate_of IS NULL
    """
)

_DEGREE = text(
    """
    SELECT node, count(*) FROM (
        SELECT from_node AS node FROM edges UNION ALL SELECT to_node FROM edges
    ) e GROUP BY node
    """
)

# The centroid of the newest embedded chunks, and each node's distance from it.
# `<=>` is cosine distance, the operator the chunk index already uses.
_DISTANCE = text(
    """
    WITH recent AS (
        SELECT embedding FROM chunks
        WHERE embedding IS NOT NULL AND superseded_at IS NULL
        ORDER BY chunk_id DESC LIMIT :recent
    ), centre AS (SELECT avg(embedding) AS v FROM recent)
    SELECT e.entity_id, e.embedding <=> centre.v
    FROM entities e CROSS JOIN centre
    WHERE e.embedding IS NOT NULL AND centre.v IS NOT NULL
      AND e.redirects_to IS NULL AND NOT e.is_annotation
    """
)


def file_under(
    own: Iterable[str] | None, evidence: Iterable[str], active: Iterable[str]
) -> str | None:
    """The active topic a node's queries belong to.

    The node's own label if it has an active one; otherwise the active topic
    most of its evidence sources are labelled with (content labels, `P2-21`),
    ties to the alphabetically first so the choice is stable. None if neither
    says: a query filed under a guessed topic would inherit that topic's
    weight and mislabel what it finds.
    """
    live = set(active)
    mine = sorted(t for t in own or () if t in live)
    if mine:
        return mine[0]
    counts = Counter(t for t in evidence if t in live)
    if not counts:
        return None
    return min(counts, key=lambda t: (-counts[t], t))


async def graph_inputs(
    sess: AsyncSession, *, active: Sequence[str], recent: int = RECENT_CHUNKS
) -> list[NodeEvidence]:
    """Every live node, with its tier mix, degree, distance and topic.

    ``stances`` is left None on every node: there is no per-source stance to
    read yet (module docstring). When one exists, this is where it is counted.
    """
    tiers: dict[int, Counter[str]] = defaultdict(Counter)
    labels: dict[int, list[str]] = defaultdict(list)
    for entity_id, _source_id, tier, topic_labels in await sess.execute(_EVIDENCE):
        tiers[entity_id][tier] += 1
        labels[entity_id].extend(topic_labels or ())
    degree = {int(n): int(c) for n, c in await sess.execute(_DEGREE)}
    distance = {
        int(n): float(d)
        for n, d in await sess.execute(_DISTANCE, {"recent": recent})
        if d is not None and not math.isnan(d)
    }
    return [
        NodeEvidence(
            entity_id=entity_id,
            name=name,
            node_type=node_type,
            aliases=tuple(aliases or ()),
            topic=file_under(own, labels.get(entity_id, ()), active),
            tiers=dict(tiers.get(entity_id, {})),
            degree=degree.get(entity_id, 0),
            distance=distance.get(entity_id),
        )
        for entity_id, name, node_type, aliases, own in await sess.execute(_NODES)
    ]


@dataclasses.dataclass(frozen=True)
class GraphSummary:
    """What the graph looked like to this run, for the report."""

    nodes: int
    #: Nodes a query could be built from (type, name, topic).
    eligible: int
    #: Nodes with no active topic to file a query under.
    no_topic: int
    #: Eligible nodes with at least :data:`MIN_SOURCES` sources.
    enough_evidence: int
    #: Of those, the ones with a tier gap.
    imbalanced: int
    #: Eligible nodes that count as far.
    far: int


def summarise(nodes: Sequence[NodeEvidence]) -> GraphSummary:
    ok = [n for n in nodes if eligible(n)]
    return GraphSummary(
        nodes=len(nodes),
        eligible=len(ok),
        no_topic=sum(1 for n in nodes if n.topic is None),
        enough_evidence=sum(1 for n in ok if n.sources >= MIN_SOURCES),
        imbalanced=sum(1 for n in ok if tier_gap(n) is not None),
        far=len(far_nodes(ok)),
    )
