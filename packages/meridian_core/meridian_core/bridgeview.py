"""Reading bridges (task P6-31): what connects two areas of the newest build.

The three kinds stay apart here as they are in the table: ``claims`` are
edges a source states, ``similar`` are passage pairs near in meaning, and
``shared_terms`` are words both areas are distinctive for. Nothing merges
them into one score, because "a source says these connect" and "these read
alike" are different findings and a reader has to be able to tell which one
they are looking at.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .areaview import AreaNotFound, area_name, latest_build
from .corpusmap import SNIPPET_CHARS
from .models import Area, AreaBridge, Chunk, Edge, Entity, Source
from .schemas.areas import (
    AreaCrumb,
    BridgeClaimRead,
    BridgePairRead,
    BridgePassageRead,
    BridgeRead,
)

#: Claims shown on one bridge; the count says how many there are in all.
CLAIMS_SHOWN = 20


async def bridge(sess: AsyncSession, a: int, b: int) -> BridgeRead:
    """The bridge between two areas of the newest build, in full."""
    if a == b:
        raise ValueError("A bridge joins two different areas.")
    build = await latest_build(sess)
    if build is None:
        raise AreaNotFound("No areas have been built yet.")
    areas = {
        area.area_id: area
        for area in await sess.scalars(
            select(Area).where(Area.area_id.in_([a, b]), Area.build_id == build.build_id)
        )
    }
    missing = [x for x in (a, b) if x not in areas]
    if missing:
        raise AreaNotFound(
            f"Area {missing[0]} is not in the current map; it may belong to an earlier build."
        )
    low, high = sorted((a, b))
    row = await sess.scalar(
        select(AreaBridge).where(AreaBridge.area_a == low, AreaBridge.area_b == high)
    )

    def crumb(area: Area) -> AreaCrumb:
        return AreaCrumb(area_id=area.area_id, level=area.level, name=area_name(area.terms))

    first, second = areas[a], areas[b]
    if row is None:
        # Two areas nothing was recorded between: an answer, not an error.
        return BridgeRead(
            a=crumb(first),
            b=crumb(second),
            claims=[],
            cited_sources=0,
            similar=[],
            shared_terms=[],
            similarity=round(
                float(sum(x * y for x, y in zip(first.centroid, second.centroid, strict=True))), 4
            ),
        )

    claims: list[BridgeClaimRead] = []
    edge_ids = list(row.cited_edge_ids or ())[:CLAIMS_SHOWN]
    if edge_ids:
        source_entity = Entity.__table__.alias("from_entity")
        target_entity = Entity.__table__.alias("to_entity")
        edges = (
            await sess.execute(
                select(
                    Edge.edge_id,
                    Edge.from_node,
                    source_entity.c.canonical_name,
                    Edge.relation_type,
                    Edge.to_node,
                    target_entity.c.canonical_name,
                    Edge.supporting_chunk_ids,
                )
                .join(source_entity, source_entity.c.entity_id == Edge.from_node)
                .join(target_entity, target_entity.c.entity_id == Edge.to_node)
                .where(Edge.edge_id.in_(edge_ids))
                .order_by(Edge.edge_id)
            )
        ).all()
        cited = {c for *_, chunks in edges for c in chunks or ()}
        tier_of: dict[int, tuple[int, str]] = {}
        if cited:
            for chunk_id, source_id, tier in await sess.execute(
                select(Chunk.chunk_id, Chunk.source_id, Source.source_tier)
                .join(Source, Source.source_id == Chunk.source_id)
                .where(Chunk.chunk_id.in_(cited))
            ):
                tier_of[chunk_id] = (source_id, tier)
        for edge_id, from_node, from_name, relation, to_node, to_name, chunks in edges:
            found = [tier_of[c] for c in chunks or () if c in tier_of]
            claims.append(
                BridgeClaimRead(
                    edge_id=edge_id,
                    from_node=from_node,
                    from_name=from_name,
                    relation_type=relation,
                    to_node=to_node,
                    to_name=to_name,
                    sources=len({source for source, _ in found}),
                    citations=len(chunks or ()),
                    tiers=sorted({tier for _, tier in found}),
                )
            )

    pairs = list(row.similar_pairs or ())
    wanted = {p["chunk_a"] for p in pairs} | {p["chunk_b"] for p in pairs}
    passages: dict[int, BridgePassageRead] = {}
    if wanted:
        for chunk_id, source_id, title, tier, snippet in await sess.execute(
            select(
                Chunk.chunk_id,
                Chunk.source_id,
                Source.title,
                Source.source_tier,
                func.left(Chunk.text, SNIPPET_CHARS * 2),
            )
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id.in_(wanted))
        ):
            passages[chunk_id] = BridgePassageRead(
                chunk_id=chunk_id,
                source_id=source_id,
                title=title,
                source_tier=tier,
                snippet=snippet,
            )
    # Stored low-id side first; turned round when the caller asked b → a.
    flip = a != row.area_a
    similar = [
        BridgePairRead(
            score=p["score"],
            a=passages[p["chunk_b"] if flip else p["chunk_a"]],
            b=passages[p["chunk_a"] if flip else p["chunk_b"]],
        )
        for p in pairs
        if p["chunk_a"] in passages and p["chunk_b"] in passages
    ]
    return BridgeRead(
        a=crumb(first),
        b=crumb(second),
        claims=claims,
        cited_sources=row.cited_sources,
        similar=similar,
        shared_terms=list(row.shared_terms or ()),
        similarity=row.similarity,
    )
