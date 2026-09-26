"""Areas against a real Postgres (task P6-30).

A build is derived, global and replaced wholesale, so what matters is that it
is *consistent*: every searchable passage in exactly one leaf, each level the
sum of the one below, parents that nest, names that come from the passages,
positions that survive a rebuild, and old builds pruned. Each test builds over
its own passages by a topic label unique to the run, so a dev database with a
real corpus in it does not change the answers.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import httpx
import pytest
from area_doubles import SOURCES, SUBJECTS, a_topic, seed, shrink_levels
from sqlalchemy import delete, func, select

from api.main import create_app
from meridian_core import areabuild
from meridian_core.areabuild import KEEP_BUILDS, build_areas
from meridian_core.areaview import area_detail, areas_level, jump
from meridian_core.db import dispose_engines
from meridian_core.models import Area, AreaBuild, AreaMember, ChunkTopics, ScheduledJob, Source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture(autouse=True)
def small_levels(monkeypatch):
    shrink_levels(monkeypatch)


@pytest.fixture
def topic() -> str:
    return a_topic()


async def test_a_build_puts_every_passage_in_one_leaf_and_levels_nest(session_for, topic):
    sess = await session_for("rw")
    chunks = await seed(sess, topic)

    report = await build_areas(sess, topics=[topic])

    assert report.build_id is not None
    # Each level is cut in proportion to its share, so rounding can add one.
    assert report.regions == 2 and report.regions < report.areas <= report.leaves
    assert report.passages == len(chunks)

    areas = list(await sess.scalars(select(Area).where(Area.build_id == report.build_id)))
    by_id = {a.area_id: a for a in areas}
    members = dict(
        (
            await sess.execute(
                select(AreaMember.chunk_id, AreaMember.area_id).where(
                    AreaMember.build_id == report.build_id
                )
            )
        ).all()
    )
    assert set(members) == set(chunks), "every passage, and only these, in exactly one leaf"
    assert all(by_id[a].level == 3 for a in members.values())

    for area in areas:
        if area.level == 1:
            assert area.parent_id is None
        else:
            assert by_id[area.parent_id].level == area.level - 1
        children = [c for c in areas if c.parent_id == area.area_id]
        if area.level < 3:
            assert children, "no empty region or area"
            assert area.passages == sum(c.passages for c in children)
        assert sum(area.tier_mix.values()) == area.passages
        assert area.sources <= SOURCES and area.newest_at is not None
        assert -1.0001 <= area.x <= 1.0001 and -1.0001 <= area.y <= 1.0001

    # A leaf holds one subject: the clusters follow the vectors.
    for leaf in (a for a in areas if a.level == 3):
        subjects = {chunks[c] for c, a in members.items() if a == leaf.area_id}
        assert len(subjects) == 1


async def test_areas_are_named_by_their_own_passages(session_for, topic):
    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])

    members = (
        await sess.execute(
            select(AreaMember.chunk_id, Area.terms)
            .join(Area, Area.area_id == AreaMember.area_id)
            .where(AreaMember.build_id == report.build_id)
        )
    ).all()
    for chunk_id, terms in members:
        vocabulary = set(SUBJECTS[chunks[chunk_id]].split())
        assert terms, "a leaf with passages from several sources has a name"
        assert set(terms[0].split()) <= vocabulary, (terms, vocabulary)


async def test_too_few_passages_write_nothing(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic, sources=1, per_source=2)  # six passages
    before = await sess.scalar(select(func.count()).select_from(AreaBuild))

    report = await build_areas(sess, topics=[topic])

    assert report.build_id is None and report.passages == 6
    assert await sess.scalar(select(func.count()).select_from(AreaBuild)) == before


async def test_a_rebuild_keeps_positions_and_prunes_old_builds(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)

    builds = [await build_areas(sess, topics=[topic]) for _ in range(KEEP_BUILDS + 1)]

    def placed(build_id):
        return select(Area.level, Area.passages, Area.x, Area.y).where(Area.build_id == build_id)

    first = sorted((await sess.execute(placed(builds[-2].build_id))).all())
    second = sorted((await sess.execute(placed(builds[-1].build_id))).all())
    assert first == second, "an unchanged corpus draws the same map"
    assert builds[-1].inherited == builds[-1].regions + builds[-1].areas + builds[-1].leaves

    remaining = set(await sess.scalars(select(AreaBuild.build_id)))
    assert builds[0].build_id not in remaining
    assert {b.build_id for b in builds[1:]} <= remaining
    assert not await sess.scalar(
        select(func.count()).select_from(Area).where(Area.build_id == builds[0].build_id)
    ), "an old build's areas go with it"


async def test_reading_levels_details_and_jumps(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])

    top = await areas_level(sess)
    assert top.build is not None and top.build.build_id == report.build_id
    assert top.level == 1 and len(top.areas) == 2
    assert [a.passages for a in top.areas] == sorted((a.passages for a in top.areas), reverse=True)

    region = top.areas[0]
    inside = await areas_level(sess, parent_id=region.area_id)
    assert inside.level == 2 and inside.parent.area_id == region.area_id
    assert [c.area_id for c in inside.path] == [region.area_id]
    assert sum(a.passages for a in inside.areas) == region.passages
    assert region.children == len(inside.areas)

    leaf = (await areas_level(sess, parent_id=inside.areas[0].area_id)).areas[0]
    detail = await area_detail(sess, leaf.area_id)
    assert [c.level for c in detail.path] == [1, 2, 3]
    assert 0 < len(detail.passages) <= 5
    assert all(p.snippet for p in detail.passages)

    found = await jump(sess, "enzyme")
    assert found.hits and found.hits[0].match == "name"
    assert "enzyme" in " ".join(found.hits[0].area.terms)
    assert not (await jump(sess, "   ")).hits


async def test_old_build_and_unknown_areas_are_refused_with_a_reason(session_for, topic):
    from meridian_core.areaview import AreaNotFound

    sess = await session_for("rw")
    await seed(sess, topic)
    old = await build_areas(sess, topics=[topic])
    await build_areas(sess, topics=[topic])
    old_area = await sess.scalar(select(Area.area_id).where(Area.build_id == old.build_id))

    with pytest.raises(AreaNotFound, match="earlier build"):
        await area_detail(sess, old_area)
    with pytest.raises(AreaNotFound, match="No area"):
        await areas_level(sess, parent_id=10**12)


async def test_no_build_is_an_answer_not_an_error(session_for):
    sess = await session_for("rw")
    await sess.execute(delete(AreaBuild))  # rolled back by the fixture
    empty = await areas_level(sess)
    assert empty.build is None and empty.areas == []
    assert empty.passages_needed == areabuild.MIN_PASSAGES


async def test_weak_and_stale_carry_their_reasons(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)
    await build_areas(sess, topics=[topic])
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=400)

    fresh = await areas_level(sess)
    stale = await areas_level(sess, now=later)
    assert not any(a.stale for a in fresh.areas)
    assert all(a.stale and any("days" in r for r in a.reasons) for a in stale.areas)


async def test_the_timetable_runs_the_build():
    """The migration inserts the row; `seed.py` is insert-only (as `B-43`)."""
    from meridian_core.db import session

    async with session() as sess:
        job = await sess.scalar(select(ScheduledJob).where(ScheduledJob.name == "areas"))
    await dispose_engines()
    assert job is not None and job.module == "worker.areas" and job.args == ["--once"]


# ---------------------------------------------------------------------------
# Over HTTP, on the read-only role


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed(session_for, topic):
    sess = await session_for("rw")
    await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])
    await sess.commit()
    yield report
    await sess.execute(delete(AreaBuild).where(AreaBuild.build_id == report.build_id))
    await sess.execute(delete(Source).where(Source.url.like(f"https://{topic}.test/%")))
    await sess.commit()


async def test_the_api_serves_the_newest_build(client, committed):
    top = (await client.get("/api/explore/areas")).json()
    assert top["build"]["build_id"] == committed.build_id
    assert top["level"] == 1 and len(top["areas"]) == 2
    assert {"weak", "stale", "reasons", "tier_mix", "x", "y", "name"} <= set(top["areas"][0])

    region = top["areas"][0]["area_id"]
    inside = (await client.get("/api/explore/areas", params={"parent": region})).json()
    assert inside["level"] == 2 and inside["path"][0]["area_id"] == region

    detail = await client.get(f"/api/explore/areas/{inside['areas'][0]['area_id']}")
    assert detail.status_code == 200 and detail.json()["path"][-1]["level"] == 2

    hits = (await client.get("/api/explore/areas/jump", params={"q": "tariff"})).json()["hits"]
    assert hits and "tariff" in " ".join(hits[0]["area"]["terms"])


async def test_the_api_refuses_what_it_cannot_answer(client, committed):
    assert (await client.get("/api/explore/areas/999999999999")).status_code == 404
    assert (
        await client.get("/api/explore/areas", params={"parent": 999999999999})
    ).status_code == 404
    assert (await client.get("/api/explore/areas", params={"parent": 0})).status_code == 422
    assert (await client.get("/api/explore/areas/jump", params={"q": ""})).status_code == 422


async def test_the_read_only_role_cannot_write_areas(session_for):
    from sqlalchemy.exc import DBAPIError

    sess = await session_for("ro")
    with pytest.raises(DBAPIError):
        await sess.execute(delete(AreaBuild))


async def _label_passages(sess, chunks: dict[int, int], topic: str) -> dict[int, list[str] | None]:
    """Decide topics for most passages: subject 0 on ``topic`` and on a second
    topic, subject 1 examined and about none, the rest never examined."""
    other = f"{topic}-b"
    labels: dict[int, list[str] | None] = {}
    for chunk_id, subject in chunks.items():
        if subject == 0:
            labels[chunk_id] = [topic, other]
        elif subject == 1:
            labels[chunk_id] = []
        else:
            labels[chunk_id] = None
            continue
        sess.add(
            ChunkTopics(
                chunk_id=chunk_id,
                topic_labels=labels[chunk_id],
                topic_scores={},
                topic_basis="test",
            )
        )
    await sess.flush()
    return labels


async def test_a_build_counts_passages_on_a_topic_and_levels_sum(session_for, topic):
    """`P6-42`: a field's on-topic share is measured, never guessed. A passage
    never examined is counted as neither on nor off topic."""
    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    labels = await _label_passages(sess, chunks, topic)

    report = await build_areas(sess, topics=[topic])
    areas = list(await sess.scalars(select(Area).where(Area.build_id == report.build_id)))
    members = dict(
        (
            await sess.execute(
                select(AreaMember.chunk_id, AreaMember.area_id).where(
                    AreaMember.build_id == report.build_id
                )
            )
        ).all()
    )

    for leaf in (a for a in areas if a.level == 3):
        inside = [labels[c] for c, a in members.items() if a == leaf.area_id]
        assert leaf.examined == sum(1 for x in inside if x is not None)
        assert leaf.on_topic == sum(1 for x in inside if x)
        assert leaf.examined <= leaf.passages
    for area in (a for a in areas if a.level < 3):
        children = [c for c in areas if c.parent_id == area.area_id]
        assert area.examined == sum(c.examined for c in children)
        assert area.on_topic == sum(c.on_topic for c in children)
        for key, n in area.topic_mix.items():
            assert n == sum(c.topic_mix.get(key, 0) for c in children)

    top = [a for a in areas if a.level == 1]
    on = sum(1 for x in labels.values() if x)
    assert sum(a.on_topic for a in top) == on
    assert sum(a.examined for a in top) == sum(1 for x in labels.values() if x is not None)
    # A passage on two topics counts under both, and on_topic counts it once.
    assert sum(a.topic_mix.get(topic, 0) for a in top) == on
    assert sum(a.topic_mix.get(f"{topic}-b", 0) for a in top) == on


async def test_the_migration_backfill_counts_as_the_build_does(session_for, topic):
    """Drift: the migration measures builds made before the build counted, with
    SQL of its own. It must reach the same numbers the build writes."""
    import importlib.util
    from pathlib import Path

    from sqlalchemy import null, text, update

    path = next(
        Path(__file__)
        .resolve()
        .parents[2]
        .glob("migrations/versions/*_areas_count_passages_on_a_topic.py")
    )
    spec = importlib.util.spec_from_file_location("backfill", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    await _label_passages(sess, chunks, topic)
    report = await build_areas(sess, topics=[topic])
    mine = Area.build_id == report.build_id
    built = {
        a.area_id: (a.examined, a.on_topic, a.topic_mix)
        for a in await sess.scalars(select(Area).where(mine))
    }

    await sess.execute(
        update(Area).where(mine).values(examined=None, on_topic=None, topic_mix=null())
    )
    await sess.execute(text(migration.LEAF_SQL))
    for _ in range(migration.ROLLUP_PASSES):
        await sess.execute(text(migration.ROLLUP_SQL))
    sess.expire_all()
    backfilled = {
        a.area_id: (a.examined, a.on_topic, a.topic_mix)
        for a in await sess.scalars(select(Area).where(mine))
    }
    assert backfilled == built


async def test_the_api_serves_on_topic_counts(client, committed):
    body = (await client.get("/api/explore/areas")).json()
    for area in body["areas"]:
        assert set(area) >= {"examined", "on_topic", "topic_mix"}
        if area["examined"] is not None:
            assert 0 <= area["on_topic"] <= area["examined"] <= area["passages"]
            counts = list(area["topic_mix"].values())
            assert counts == sorted(counts, reverse=True), "most first"


async def test_gaps_lists_on_topic_fields_that_rest_on_few_sources(session_for, topic, monkeypatch):
    """`P6-42` through Gaps: a real build, measured, read by the registered
    source. Subject 0 is on the topic and rests on the seed's few sources;
    subject 1 was examined and is off every topic, so it is no gap however
    thin; the rest were never examined, so nothing is claimed about them."""
    from meridian_core import gaps

    monkeypatch.setattr(gaps, "FIELD_MIN_PASSAGES", 1)
    sess = await session_for("rw")
    chunks = await seed(sess, topic)
    labels = await _label_passages(sess, chunks, topic)
    report = await build_areas(sess, topics=[topic])

    found = await gaps.SOURCES["areas"](sess)

    deepest = max(
        a.level for a in await sess.scalars(select(Area).where(Area.build_id == report.build_id))
    )
    leaves = {
        a.area_id: a
        for a in await sess.scalars(
            select(Area).where(Area.build_id == report.build_id, Area.level == deepest)
        )
    }
    members = dict(
        (
            await sess.execute(
                select(AreaMember.chunk_id, AreaMember.area_id).where(
                    AreaMember.build_id == report.build_id
                )
            )
        ).all()
    )
    on_topic_leaves = {a for c, a in members.items() if labels[c]}
    assert found, "the on-topic fields rest on fewer sources than the threshold"
    assert {g.evidence["area_id"] for g in found} == on_topic_leaves
    for gap in found:
        assert gap.kind == "field_thin"
        assert (
            gap.evidence["sources"] == leaves[gap.evidence["area_id"]].sources < gaps.THIN_SOURCES
        )
        seed_action = next(a for a in gap.actions if a.kind == "seed_query")
        assert seed_action.topic in {topic, f"{topic}-b"}


async def test_gaps_says_fields_are_unavailable_before_a_measured_build(session_for):

    from meridian_core import gaps

    sess = await session_for("rw")
    await sess.execute(delete(AreaBuild))  # rolled back by the fixture
    with pytest.raises(gaps.SourceUnavailable, match="not been built"):
        await gaps.SOURCES["areas"](sess)
