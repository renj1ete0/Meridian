"""Reading areas (task P6-30): the newest build, one level at a time.

Read-only, for `/api/explore/areas*`. An area id from an earlier build is answered with
a 404 that says the map was rebuilt. Weak is fewer than :data:`WEAK_BELOW_SOURCES`
independent sources, stale nothing new in :data:`STALE_AFTER_DAYS`; each carries its reason.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import Select, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from .areabuild import MIN_PASSAGES
from .areas import FURNITURE
from .bridges import exact_distance
from .corpusmap import SNIPPET_CHARS
from .models import Area, AreaBridge, AreaBuild, AreaMember, Chunk, Source
from .schemas.areas import (
    AreaBuildRead,
    AreaCrumb,
    AreaDetailRead,
    AreaJumpHit,
    AreaJumpRead,
    AreaLinkRead,
    AreaPassage,
    AreaRead,
    AreasRead,
)

WEAK_BELOW_SOURCES = 3
STALE_AFTER_DAYS = 180
LEVELS = 3
NAME_TERMS = 3
TERMS_SHOWN = 8
PASSAGES_SHOWN = 5
JUMP_LIMIT = 10


class AreaNotFound(LookupError):
    pass


def usable_terms(terms: list[str]) -> list[str]:
    """Stored terms without document furniture (`B-71`).

    Applied on read as well as at build time, so a map built before the
    furniture list existed is named properly without waiting for a rebuild.
    """
    return [t for t in terms if not any(word in FURNITURE for word in t.split())]


def area_name(terms: list[str], field: str | None = None) -> str:
    """One short name, not a list of keywords (`B-71`, `B-74`).

    The area's field of work when the build found one; without one, the best two-word
    phrase among the top few terms, else the top term. See docs/features/map.md#naming.
    """
    if field:
        return field
    terms = usable_terms(terms)
    phrase = next((t for t in terms[:NAME_TERMS] if " " in t), terms[0] if terms else None)
    if not phrase:
        return "(no distinctive terms)"
    return phrase[:1].upper() + phrase[1:]


def flags(area: Area, *, now: dt.datetime) -> tuple[bool, bool, list[str]]:
    """``(weak, stale, reasons)`` for one area."""
    reasons: list[str] = []
    weak = area.sources < WEAK_BELOW_SOURCES
    if weak:
        reasons.append(
            f"{area.sources} source{'s' if area.sources != 1 else ''}; "
            f"fewer than {WEAK_BELOW_SOURCES} independent sources"
        )
    stale = area.newest_at is None or (now - area.newest_at).days > STALE_AFTER_DAYS
    if stale:
        reasons.append(f"nothing new stored in {STALE_AFTER_DAYS} days")
    return weak, stale, reasons


def to_read(area: Area, children: int, *, now: dt.datetime) -> AreaRead:
    weak, stale, reasons = flags(area, now=now)
    return AreaRead(
        area_id=area.area_id,
        level=area.level,
        parent_id=area.parent_id,
        name=area_name(area.terms, area.field),
        terms=usable_terms(list(area.terms))[:TERMS_SHOWN],
        passages=area.passages,
        sources=area.sources,
        tier_mix=dict(area.tier_mix),
        examined=area.examined,
        on_topic=area.on_topic,
        topic_mix=dict(sorted((area.topic_mix or {}).items(), key=lambda kv: (-kv[1], kv[0]))),
        newest_at=area.newest_at,
        x=area.x,
        y=area.y,
        children=children,
        weak=weak,
        stale=stale,
        reasons=reasons,
    )


async def latest_build(sess: AsyncSession) -> AreaBuild | None:
    return await sess.scalar(select(AreaBuild).order_by(AreaBuild.build_id.desc()).limit(1))


def build_read(build: AreaBuild) -> AreaBuildRead:
    return AreaBuildRead(
        build_id=build.build_id,
        computed_at=build.computed_at,
        passages=build.passages,
        regions=int(build.params.get("regions", 0)),
        areas=int(build.params.get("areas", 0)),
        leaves=int(build.params.get("leaves", 0)),
    )


def _with_children(build_id: int) -> Select:
    child = Area.__table__.alias("child")
    count = (
        select(func.count())
        .select_from(child)
        .where(child.c.parent_id == Area.area_id)
        .scalar_subquery()
    )
    return select(Area, count).where(Area.build_id == build_id)


async def path_to(sess: AsyncSession, area: Area) -> list[AreaCrumb]:
    """Ancestors, root first, ending with the area itself."""
    chain = [area]
    while chain[-1].parent_id is not None:
        parent = await sess.get(Area, chain[-1].parent_id)
        if parent is None:  # pragma: no cover - the FK forbids it
            break
        chain.append(parent)
    return [
        AreaCrumb(area_id=a.area_id, level=a.level, name=area_name(a.terms, a.field))
        for a in reversed(chain)
    ]


async def _area_in(sess: AsyncSession, build: AreaBuild | None, area_id: int) -> tuple[Area, int]:
    if build is None:
        raise AreaNotFound("No areas have been built yet.")
    row = (
        await sess.execute(_with_children(build.build_id).where(Area.area_id == area_id))
    ).first()
    if row is None:
        exists = await sess.scalar(select(Area.build_id).where(Area.area_id == area_id))
        if exists is not None:
            raise AreaNotFound(
                f"Area {area_id} belongs to an earlier build; the map has been rebuilt since."
            )
        raise AreaNotFound(f"No area {area_id}.")
    return row[0], int(row[1])


async def areas_level(
    sess: AsyncSession, *, parent_id: int | None = None, now: dt.datetime | None = None
) -> AreasRead:
    """The regions, or the children of one area, largest first."""
    now = now or dt.datetime.now(dt.UTC)
    build = await latest_build(sess)
    common = {
        "levels": LEVELS,
        "passages_needed": MIN_PASSAGES,
        "stale_after_days": STALE_AFTER_DAYS,
        "weak_below_sources": WEAK_BELOW_SOURCES,
    }
    if build is None:
        return AreasRead(build=None, level=1, parent=None, path=[], areas=[], links=[], **common)

    parent_read = None
    path: list[AreaCrumb] = []
    if parent_id is None:
        statement = _with_children(build.build_id).where(Area.level == 1)
        level = 1
    else:
        parent, children = await _area_in(sess, build, parent_id)
        parent_read = to_read(parent, children, now=now)
        path = await path_to(sess, parent)
        statement = _with_children(build.build_id).where(Area.parent_id == parent_id)
        level = parent.level + 1

    rows = (await sess.execute(statement.order_by(Area.passages.desc(), Area.area_id))).all()
    return AreasRead(
        build=build_read(build),
        level=level,
        parent=parent_read,
        path=path,
        areas=[to_read(area, int(children), now=now) for area, children in rows],
        links=await links_between(sess, [area.area_id for area, _ in rows]),
        **common,
    )


#: Shared terms carried on a map line; the bridge itself has them all.
LINK_TERMS = 8


def link_read(bridge: AreaBridge) -> AreaLinkRead:
    return AreaLinkRead(
        area_a=bridge.area_a,
        area_b=bridge.area_b,
        cited_claims=len(bridge.cited_edge_ids or ()),
        cited_sources=bridge.cited_sources,
        similar_pairs=len(bridge.similar_pairs or ()),
        similarity=bridge.similarity,
        shared_terms=list(bridge.shared_terms or ())[:LINK_TERMS],
    )


async def links_between(sess: AsyncSession, area_ids: list[int]) -> list[AreaLinkRead]:
    """Every recorded bridge whose two ends are both among ``area_ids`` (P6-31)."""
    if len(area_ids) < 2:
        return []
    rows = await sess.scalars(
        select(AreaBridge)
        .where(AreaBridge.area_a.in_(area_ids), AreaBridge.area_b.in_(area_ids))
        .order_by(AreaBridge.area_a, AreaBridge.area_b)
    )
    return [link_read(row) for row in rows]


async def area_detail(
    sess: AsyncSession, area_id: int, *, now: dt.datetime | None = None
) -> AreaDetailRead:
    """One area, where it sits, and the passages nearest its centre."""
    now = now or dt.datetime.now(dt.UTC)
    build = await latest_build(sess)
    area, children = await _area_in(sess, build, area_id)
    leaves = _leaves_under(area)
    rows = (
        await sess.execute(
            select(
                Chunk.chunk_id,
                Chunk.source_id,
                Source.title,
                Source.url,
                Source.source_tier,
                func.left(Chunk.text, SNIPPET_CHARS),
            )
            .join(AreaMember, AreaMember.chunk_id == Chunk.chunk_id)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(AreaMember.build_id == area.build_id, AreaMember.area_id.in_(leaves))
            .order_by(exact_distance(Chunk.embedding, area.centroid), Chunk.chunk_id)
            .limit(PASSAGES_SHOWN)
        )
    ).all()
    return AreaDetailRead(
        area=to_read(area, children, now=now),
        path=await path_to(sess, area),
        passages=[
            AreaPassage(chunk_id=c, source_id=s, title=t, url=u, source_tier=tier, snippet=snippet)
            for c, s, t, u, tier, snippet in rows
        ],
    )


def _leaves_under(area: Area) -> Select:
    """Leaf area ids at or below ``area``: members are recorded against leaves."""
    level2 = Area.__table__.alias("l2")
    level3 = Area.__table__.alias("l3")
    if area.level == LEVELS:
        return select(level3.c.area_id).where(level3.c.area_id == area.area_id)
    if area.level == LEVELS - 1:
        return select(level3.c.area_id).where(level3.c.parent_id == area.area_id)
    return (
        select(level3.c.area_id)
        .join(level2, level3.c.parent_id == level2.c.area_id)
        .where(level2.c.parent_id == area.area_id)
    )


@dataclasses.dataclass(frozen=True)
class _Hit:
    area_id: int
    match: str
    hits: int


async def jump(sess: AsyncSession, query: str, *, now: dt.datetime | None = None) -> AreaJumpRead:
    """Areas for "jump to an area or a term".

    Two kinds of answer, kept apart: areas whose own distinctive terms contain
    the query (``name``), then the sub-areas holding the most passages that
    match it as text (``passages``). The first is what the map is named by;
    the second finds a term too small to name anything.
    """
    now = now or dt.datetime.now(dt.UTC)
    text = " ".join(query.split())
    build = await latest_build(sess)
    if build is None or not text:
        return AreaJumpRead(query=text, hits=[])

    # The terms as one line each, so a pattern cannot match across two.
    joined = func.array_to_string(Area.terms, "\n")
    candidates = (
        await sess.execute(
            select(Area.area_id, Area.level, Area.passages, Area.terms).where(
                Area.build_id == build.build_id,
                joined.ilike(f"%{_escape(text)}%", escape="\\"),
            )
        )
    ).all()
    # Where the term ranks in an area's own list matters more than how big
    # the area is: an area whose first term it is, is the area named by it.
    wanted = text.lower()
    named = [
        area_id
        for area_id, *_ in sorted(
            candidates,
            key=lambda row: (
                next((i for i, term in enumerate(row[3]) if wanted in term.lower()), len(row[3])),
                row[1],
                -row[2],
                row[0],
            ),
        )[:JUMP_LIMIT]
    ]
    hits = [_Hit(area_id, "name", 0) for area_id in named]

    tsquery = func.websearch_to_tsquery(literal_column("'english'::regconfig"), text)
    by_passages = (
        await sess.execute(
            select(AreaMember.area_id, func.count())
            .join(Chunk, Chunk.chunk_id == AreaMember.chunk_id)
            .where(AreaMember.build_id == build.build_id, Chunk.search_vector.op("@@")(tsquery))
            .group_by(AreaMember.area_id)
            .order_by(func.count().desc(), AreaMember.area_id)
            .limit(JUMP_LIMIT)
        )
    ).all()
    seen = {hit.area_id for hit in hits}
    hits += [_Hit(a, "passages", int(n)) for a, n in by_passages if a not in seen]

    out: list[AreaJumpHit] = []
    for hit in hits[:JUMP_LIMIT]:
        area, children = await _area_in(sess, build, hit.area_id)
        out.append(
            AreaJumpHit(
                area=to_read(area, children, now=now),
                path=await path_to(sess, area),
                match=hit.match,  # type: ignore[arg-type]
                hits=hit.hits,
            )
        )
    return AreaJumpRead(query=text, hits=out)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
