"""A term's neighbourhood: what a passage states, and what merely reads alike (task P6-33).

The rules are pure functions at the top of this module; `neighbourhood()` at
the bottom runs the queries, and `/api/explore/neighbourhood` serves it on the
read-only role.

Two rings, and they are different kinds of evidence:

- **Inner ring — cited.** Entities a passage *states* a link to: the other end
  of an edge (or an observation's place), each carrying the passages behind it.
- **Outer ring — similar.** Entities whose name vectors sit near the term's.
  Nothing says they are connected; they only read alike.

**The rings never mix.** An entity that is both cited and similar is shown in
the inner ring only, because "a source says so" is the stronger and different
claim, and listing it twice would let resemblance pass as corroboration.

**The anchor is chosen by name, not by meaning.** A term becomes a node only
when it equals a node's name or alias once case, spacing and a regular plural
are set aside. Picking the nearest node by embedding instead would put a stranger's
stated links in the inner ring under the reader's term — the one mixing of
cited and similar this module exists to prevent. Near-miss names are returned
as candidates for the reader to choose.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import graphview
from .models import Chunk, Edge, Entity, Observation
from .schemas.neighbourhood import (
    CitedTermRead,
    RelationRead,
    SimilarBasis,
    SimilarPassageRead,
    SimilarTermRead,
    TermNeighbourhoodRead,
    TermRead,
)
from .search import EF_SEARCH_FACTOR, SearchFilters, _arm
from .vectorindex import indexed_distance

#: Cosine floor for the outer ring. Measured on a live graph's name vectors:
#: pairs at or above 0.70 were near-synonyms or narrower forms of one another;
#: around 0.58–0.67 pairs were unrelated names sharing a word or a shape.
SIMILAR_FLOOR = 0.70

#: The same floor for a name against a passage, which sits on a different
#: scale: a short name against a paragraph scores lower however relevant the
#: paragraph. Measured on the same graph, passages that were plainly about the
#: name scored 0.58–0.69.
PASSAGE_FLOOR = 0.55

#: Ring sizes. The panel sits beside a result list; more than this is a list
#: to scroll, not a neighbourhood to take in at a glance.
MAX_CITED = 12
MAX_SIMILAR = 10
MAX_PASSAGES = 5
#: Chunks read per passage shown, so one-per-source still fills the list.
PASSAGE_POOL_FACTOR = 6

#: How many nearest names the vector query examines. More than the ring holds,
#: because cited entities and the anchor are removed afterwards; the outer
#: ring's total is a count among these, not over the whole graph.
SIMILAR_CANDIDATES = 60

#: The caller's embedder: text in, a vector out, or None when it has none. The
#: API passes the sidecar client; `meridian_core` never loads a model itself.
Embed = Callable[[str], Awaitable[Sequence[float] | None]]


def normalise_name(name: str) -> str:
    """Case, punctuation-as-space and a regular plural set aside.

    Only on words longer than three letters: short acronyms keep their final
    letter, since stripping it would equate unrelated abbreviations.
    """
    words = re.sub(r"[^\w]+", " ", name.casefold()).split()
    return " ".join(_singular(w) for w in words)


def _singular(word: str) -> str:
    """`buses` → `bus`, `canopies` → `canopy`, `vehicles` → `vehicle`.

    `access` and `ods` are left alone.
    """
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss"):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]
    return word[:-1]


#: Words a question is made of that name nothing: asked for one at a time,
#: each would offer every node with "does" or "with" in its name.
QUESTION_WORDS = frozenset(
    [
        "about",
        "after",
        "also",
        "among",
        "and",
        "are",
        "because",
        "been",
        "being",
        "between",
        "both",
        "could",
        "does",
        "doing",
        "during",
        "each",
        "from",
        "have",
        "having",
        "into",
        "more",
        "most",
        "much",
        "other",
        "over",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "within",
        "without",
        "would",
        "affect",
        "affects",
        "effect",
        "effects",
        "impact",
        "impacts",
        "relate",
        "relates",
        "related",
        "relationship",
        "role",
        "all",
        "and",
        "any",
        "are",
        "but",
        "can",
        "did",
        "does",
        "for",
        "had",
        "has",
        "her",
        "his",
        "how",
        "its",
        "may",
        "not",
        "our",
        "she",
        "the",
        "too",
        "two",
        "use",
        "was",
        "way",
        "who",
        "why",
        "yet",
        "you",
    ]
)

#: How many of a question's words are looked up, and how many nodes offered.
MAX_QUERY_WORDS = 6
MAX_CANDIDATES = 10


def query_words(term: str) -> list[str]:
    """The words of a query worth looking up as node names, in order, once each.

    Three letters or more — "bus" and "car" are subjects — and not a
    question's own vocabulary, which would match inside too many names.
    """
    words = re.findall(r"[^\W_][\w-]*", term.lower())
    return [w for w in dict.fromkeys(words) if len(w) >= 3 and w not in QUESTION_WORDS]


@dataclasses.dataclass(frozen=True)
class NameCandidate:
    entity_id: int
    canonical_name: str
    aliases: tuple[str, ...] = ()


def choose_anchor(term: str, candidates: Iterable[NameCandidate]) -> NameCandidate | None:
    """The node this term names, or None when no node's name or alias equals it.

    Canonical name beats alias; within each, the lowest id wins so the choice is
    stable across requests.
    """
    wanted = normalise_name(term)
    if not wanted:
        return None
    ordered = sorted(candidates, key=lambda c: c.entity_id)
    for candidate in ordered:
        if normalise_name(candidate.canonical_name) == wanted:
            return candidate
    for candidate in ordered:
        if any(normalise_name(alias) == wanted for alias in candidate.aliases):
            return candidate
    return None


@dataclasses.dataclass(frozen=True)
class Cited:
    entity_id: int
    support: int  # distinct passages behind the link


@dataclasses.dataclass(frozen=True)
class Similar:
    entity_id: int
    similarity: float


@dataclasses.dataclass(frozen=True)
class Rings:
    cited: list[Cited]
    similar: list[Similar]
    #: Cited links before the cap, so a panel can say "12 of 31".
    cited_total: int
    similar_total: int


def split_rings(
    anchor_id: int | None,
    cited: Sequence[Cited],
    similar: Sequence[Similar],
    *,
    floor: float = SIMILAR_FLOOR,
    max_cited: int = MAX_CITED,
    max_similar: int = MAX_SIMILAR,
) -> Rings:
    """Rank, cap and separate the two rings.

    The anchor never appears in either ring. Anything cited is removed from the
    similar ring. Similar entries below `floor` are dropped rather than shown
    faintly: a number on screen reads as a finding however it is styled.
    """
    cited_ids = {c.entity_id for c in cited} - {anchor_id}
    inner = sorted(
        (c for c in cited if c.entity_id in cited_ids),
        key=lambda c: (-c.support, c.entity_id),
    )
    outer = sorted(
        (
            s
            for s in similar
            if s.entity_id != anchor_id and s.entity_id not in cited_ids and s.similarity >= floor
        ),
        key=lambda s: (-s.similarity, s.entity_id),
    )
    return Rings(
        cited=inner[:max_cited],
        similar=outer[:max_similar],
        cited_total=len(inner),
        similar_total=len(outer),
    )


# --------------------------------------------------------------------------
# The queries
# --------------------------------------------------------------------------


def _prefilter(term: str) -> str | None:
    """An ILIKE pattern every name `choose_anchor` could accept must match.

    The longest word, less the letters a plural can change. Loose on purpose —
    it only narrows what is read; `choose_anchor` decides.
    """
    words = normalise_name(term).split()
    if not words:
        return None
    longest = max(words, key=len)
    stem = longest[: max(3, len(longest) - 2)] if len(longest) > 3 else longest
    return f"%{graphview._like(stem)}%"


async def find_anchor(sess: AsyncSession, term: str) -> Entity | None:
    """The live node the term names, by name or alias; None when none does."""
    pattern = _prefilter(term)
    if pattern is None:
        return None
    # Aliases joined with a unit separator so one ILIKE covers them all
    # without matching across two aliases' boundary in a way that matters:
    # the prefilter is loose by design and `choose_anchor` re-checks.
    rows = (
        await sess.scalars(
            select(Entity).where(
                Entity.redirects_to.is_(None),
                Entity.is_annotation.is_(False),
                or_(
                    Entity.canonical_name.ilike(pattern, escape="\\"),
                    func.array_to_string(Entity.aliases, "\x1f").ilike(pattern, escape="\\"),
                ),
            )
        )
    ).all()
    by_id = {e.entity_id: e for e in rows}
    chosen = choose_anchor(
        term,
        (NameCandidate(e.entity_id, e.canonical_name, tuple(e.aliases or ())) for e in rows),
    )
    return by_id[chosen.entity_id] if chosen else None


@dataclasses.dataclass
class _Link:
    chunks: set[int] = dataclasses.field(default_factory=set)
    relations: set[tuple[str, bool]] = dataclasses.field(default_factory=set)
    contested: bool = False


async def stated_links(sess: AsyncSession, anchor_id: int) -> dict[int, _Link]:
    """Every node a passage links to the anchor, with the passages behind it.

    Edges in both directions, and observations measured *in* a place: "this
    figure holds in that place" is a stated link between the two nodes, with a
    passage behind it, as much as an edge is. Self-links are not neighbours.
    """
    out: dict[int, _Link] = {}

    def note(other: int, chunks: Iterable[int], relation: str, outgoing: bool, contested: bool):
        link = out.setdefault(other, _Link())
        link.chunks.update(chunks)
        link.relations.add((relation, outgoing))
        link.contested = link.contested or contested

    edges = (
        await sess.scalars(
            select(Edge)
            .where(or_(Edge.from_node == anchor_id, Edge.to_node == anchor_id))
            .where(Edge.from_node != Edge.to_node)
        )
    ).all()
    for edge in edges:
        outgoing = edge.from_node == anchor_id
        note(
            edge.to_node if outgoing else edge.from_node,
            edge.supporting_chunk_ids or (),
            edge.relation_type,
            outgoing,
            graphview.is_contested(edge),
        )

    observations = (
        await sess.scalars(
            select(Observation).where(
                Observation.geography_entity_id.is_not(None),
                Observation.subject_entity_id != Observation.geography_entity_id,
                or_(
                    Observation.subject_entity_id == anchor_id,
                    Observation.geography_entity_id == anchor_id,
                ),
            )
        )
    ).all()
    for obs in observations:
        outgoing = obs.subject_entity_id == anchor_id
        note(
            obs.geography_entity_id if outgoing else obs.subject_entity_id,
            obs.supporting_chunk_ids or (),
            f"measured in: {obs.metric}" if outgoing else f"place of: {obs.metric}",
            outgoing,
            bool(obs.contested_with),
        )
    # A link with no passage behind it is not assertable (§2 principle 3);
    # the columns are NOT NULL, but an empty array is still possible.
    return {other: link for other, link in out.items() if link.chunks}


async def similar_names(
    sess: AsyncSession, vector: Sequence[float], *, limit: int = SIMILAR_CANDIDATES
) -> list[Similar]:
    """The live nodes whose name vectors are nearest, with their cosine.

    Annotations are left out: a note's vector is the reader's words, and a note
    in the outer ring would present the reader's own thinking as the corpus.
    """
    distance = Entity.embedding.cosine_distance(list(vector))
    rows = await sess.execute(
        select(Entity.entity_id, distance)
        .where(
            Entity.embedding.is_not(None),
            Entity.redirects_to.is_(None),
            Entity.is_annotation.is_(False),
        )
        .order_by(distance, Entity.entity_id)
        .limit(limit)
    )
    return [Similar(entity_id, 1.0 - float(d)) for entity_id, d in rows]


async def similar_passages(
    sess: AsyncSession,
    vector: Sequence[float],
    *,
    limit: int = MAX_PASSAGES,
    floor: float = PASSAGE_FLOOR,
) -> list[tuple[int, float]]:
    """Chunk ids nearest the vector, one per source, under search's own filters.

    `search._arm` rather than a second copy of the filter: superseded chunks,
    duplicates and junk are out here for the same reasons they are out of
    search, and two filter sites is how one of them drifts.

    One per source because five neighbouring chunks of one long document are
    one voice, and a short list of them reads as five.
    """
    pool = limit * PASSAGE_POOL_FACTOR
    await sess.execute(
        select(func.set_config("hnsw.ef_search", str(max(pool * EF_SEARCH_FACTOR, 40)), True))
    )
    distance = indexed_distance(Chunk.embedding, vector)
    stmt = (
        _arm(SearchFilters())
        .add_columns(Chunk.source_id, distance)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance, Chunk.chunk_id)
        .limit(pool)
    )
    out: list[tuple[int, float]] = []
    seen: set[int] = set()
    for chunk_id, source_id, d in await sess.execute(stmt):
        similarity = 1.0 - float(d)
        if similarity < floor or source_id in seen:
            continue
        seen.add(source_id)
        out.append((chunk_id, similarity))
        if len(out) == limit:
            break
    return out


def _term(entity: Entity) -> TermRead:
    return TermRead(
        entity_id=entity.entity_id,
        canonical_name=entity.canonical_name,
        node_type=entity.node_type,
    )


async def neighbourhood(
    sess: AsyncSession,
    term: str,
    *,
    entity_id: int | None = None,
    embed: Embed | None = None,
) -> TermNeighbourhoodRead:
    """Both rings for a term, or for a node the reader picked (task P6-33).

    ``embed`` turns the term into a vector, and is called only when there is
    no anchor or the anchor has no vector: the anchor's name vector is the
    better basis, since it is what the other names were embedded as, and it
    costs no call to the embedding service. When neither yields a vector the
    outer ring is not computed and ``similar_basis`` is ``none``.
    """
    if entity_id is not None:
        anchor = await sess.get(Entity, entity_id)
        if anchor is None:
            raise graphview.NodeNotFound(entity_id)
        # A merged node is a redirect (§5.5): follow it once.
        if anchor.redirects_to is not None:
            anchor = await sess.get(Entity, anchor.redirects_to) or anchor
    else:
        anchor = await find_anchor(sess, term)
    anchor_id = anchor.entity_id if anchor else None

    links = await stated_links(sess, anchor_id) if anchor_id is not None else {}

    vector: Sequence[float] | None = None
    basis: SimilarBasis = "none"
    if anchor is not None and anchor.embedding is not None:
        vector, basis = list(anchor.embedding), "node"
    elif embed is not None and term.strip():
        embedded = await embed(term.strip())
        if embedded is not None:
            vector, basis = list(embedded), "term"

    nearest = await similar_names(sess, vector) if vector is not None else []
    passages = await similar_passages(sess, vector) if vector is not None else []

    # Merged-away and annotation nodes are not ring members; drop them before
    # ranking so the totals count only what could be shown.
    wanted = set(links) | {s.entity_id for s in nearest}
    entities = (
        {
            e.entity_id: e
            for e in (
                await sess.scalars(select(Entity).where(Entity.entity_id.in_(sorted(wanted))))
            ).all()
            if e.redirects_to is None
        }
        if wanted
        else {}
    )

    rings = split_rings(
        anchor_id,
        [Cited(other, len(link.chunks)) for other, link in links.items() if other in entities],
        [s for s in nearest if s.entity_id in entities],
    )

    hits = await graphview.hydrate_chunks(sess, [chunk_id for chunk_id, _ in passages])

    candidates: list[TermRead] = []
    if anchor is None and term.strip():
        matches = list((await graphview.search_nodes(sess, term)).matches)
        if not matches:
            # A question names no node as a whole, but its words may (`B-94`):
            # "how does X affect Y" offers X and Y rather than nothing.
            for word in query_words(term)[:MAX_QUERY_WORDS]:
                matches.extend((await graphview.search_nodes(sess, word, limit=4)).matches)
        seen: set[int] = set()
        for m in matches:
            if m.is_annotation or m.entity_id in seen:
                continue
            seen.add(m.entity_id)
            candidates.append(
                TermRead(
                    entity_id=m.entity_id, canonical_name=m.canonical_name, node_type=m.node_type
                )
            )
        candidates = candidates[:MAX_CANDIDATES]

    return TermNeighbourhoodRead(
        term=term,
        anchor=_term(anchor) if anchor else None,
        candidates=candidates,
        cited=[
            CitedTermRead(
                **_term(entities[c.entity_id]).model_dump(),
                support=c.support,
                relations=[
                    RelationRead(relation_type=r, outgoing=o)
                    for r, o in sorted(links[c.entity_id].relations)
                ],
                contested=links[c.entity_id].contested,
            )
            for c in rings.cited
        ],
        cited_total=rings.cited_total,
        similar=[
            SimilarTermRead(**_term(entities[s.entity_id]).model_dump(), similarity=s.similarity)
            for s in rings.similar
        ],
        similar_total=rings.similar_total,
        passages=[
            SimilarPassageRead(hit=hits[chunk_id], similarity=sim)
            for chunk_id, sim in passages
            if chunk_id in hits
        ],
        similar_basis=basis,
        similar_floor=SIMILAR_FLOOR,
        passage_floor=PASSAGE_FLOOR,
    )
