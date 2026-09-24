"""Demoting site furniture already in the corpus (task `B-42`).

Against a real Postgres, scoped to a host unique to the run, because the sweep
writes `retention_tier` and the dev database holds a real crawl.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, Entity, Source
from meridian_core.sources import upsert_source
from worker import furniture

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def site(session_for):
    host = f"f{uuid.uuid4().hex[:10]}.test"
    sess = await session_for("rw")
    made = {}
    for name, path in {
        "privacy": "/privacy-policy",
        "terms": "/legal/terms-of-use.html",
        "article": "/research/privacy-policy-effects-on-location-sharing",
        "cited": "/about/history-of-the-programme",
    }.items():
        source, _ = await upsert_source(
            sess, f"https://{host}{path}", checksum=f"sha256:{uuid.uuid4().hex}"
        )
        await replace_chunks(
            sess, source.source_id, [ChunkWrite(text=f"{name} text", chunk_index=0)]
        )
        made[name] = source.source_id
    cited_chunk = await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == made["cited"]))
    entity = Entity(
        canonical_name=f"{host} programme", node_type="concept", supporting_chunk_ids=[cited_chunk]
    )
    sess.add(entity)
    await sess.commit()
    await dispose_engines()

    yield host, made

    await sess.rollback()
    await sess.execute(delete(Entity).where(Entity.entity_id == entity.entity_id))
    await sess.execute(delete(Source).where(Source.url.like(f"https://{host}/%")))
    await sess.commit()
    await dispose_engines()


async def tiers(session_for, made):
    sess = await session_for("rw")
    await sess.rollback()
    rows = await sess.execute(
        select(Source.source_id, Source.retention_tier).where(Source.source_id.in_(made.values()))
    )
    by_id = dict(rows.all())
    return {name: by_id[source_id] for name, source_id in made.items()}


async def test_a_report_writes_nothing(site, session_for) -> None:
    host, made = site
    before = await tiers(session_for, made)

    stats = await furniture.run_pass(apply=False, domain=host)

    assert stats.furniture == 2
    assert await tiers(session_for, made) == before


async def test_furniture_is_demoted_and_everything_else_is_left_alone(site, session_for) -> None:
    host, made = site

    stats = await furniture.run_pass(apply=True, domain=host)

    after = await tiers(session_for, made)
    assert after["privacy"] == "junk" and after["terms"] == "junk"
    assert after["article"] != "junk", "a slug that merely starts with a furniture phrase"
    assert stats.furniture == 2


async def test_a_cited_page_is_kept_whatever_its_address(site, session_for) -> None:
    """`/about/...` is furniture by address. A page the graph cites is evidence,
    and evidence outranks the URL — the operator's point that furniture words
    and real content can share a page, taken to its strict form."""
    host, made = site

    stats = await furniture.run_pass(apply=True, domain=host)

    assert (await tiers(session_for, made))["cited"] != "junk"
    assert stats.kept_as_evidence == 1


async def test_the_domain_option_does_not_reach_other_sites(site, session_for) -> None:
    host, made = site
    stats = await furniture.run_pass(apply=False, domain=f"other-{host}")
    assert stats.examined == 0


async def test_a_stored_page_titled_as_missing_is_demoted_and_a_statute_is_not(
    session_for,
) -> None:
    """`B-45`: the title decides, one segment at a time."""
    host = f"e{uuid.uuid4().hex[:10]}.test"
    sess = await session_for("rw")
    missing, _ = await upsert_source(
        sess, f"https://{host}/gone", checksum="sha256:m", title="Page not found – A Faculty"
    )
    statute, _ = await upsert_source(
        sess, f"https://{host}/rule", checksum="sha256:s", title="Rule 9005. Harmless Error"
    )
    ids = {"missing": missing.source_id, "statute": statute.source_id}
    await sess.commit()
    await dispose_engines()
    try:
        await furniture.run_pass(apply=True, domain=host)
        after = await tiers(session_for, ids)
        assert after["missing"] == "junk"
        assert after["statute"] != "junk"
    finally:
        sess = await session_for("rw")
        await sess.execute(delete(Source).where(Source.url.like(f"https://{host}/%")))
        await sess.commit()
        await dispose_engines()
