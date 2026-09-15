"""The retention sweep (task P1-31, spec §5.4).

Against a real Postgres and a real temporary directory, because the subject is
the relationship between two of them: rows that name files and files that may or
may not be there. A double for either half would only be able to confirm the
agreement this code exists to check.

Deletion is the reason most of these are rejection tests. It is the only
operation in the system that destroys something a re-crawl cannot reproduce —
the web moves on, so a page fetched last month is not re-fetchable, only
re-visitable — and §5.4 makes link rot the binding reason raw retention exists
at all.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, Edge, Entity, Source
from meridian_core.retention import (
    PROTECTED_TIER,
    DropCandidate,
    RetentionPlan,
    apply_sweep,
    cited_source_ids,
    plan_sweep,
)
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    """Scopes every row this file writes, so the dev corpus is not in the way."""
    return f"sweep{uuid.uuid4().hex[:10]}"


@pytest.fixture
def root(tmp_path) -> Path:
    store = tmp_path / "raw"
    store.mkdir()
    return store


@pytest.fixture
async def cleanup(session_for, marker):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.url.like(f"%{marker}%")))
    await sess.execute(delete(Entity).where(Entity.canonical_name.like(f"%{marker}%")))
    await sess.commit()


async def a_source(sess, marker: str, root: Path, tier: str, *, write: bool = True) -> Source:
    """A source, its retention tier, and optionally the file it claims."""
    relative = f"{marker}.test/ab/{uuid.uuid4().hex}.html"
    source, _ = await upsert_source(
        sess,
        f"https://{marker}.test/{uuid.uuid4().hex[:8]}",
        checksum=f"sha256:{uuid.uuid4().hex}",
        raw_file_path=relative,
        retention_tier=tier,
    )
    if write:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 64)
    await sess.flush()
    return source


# --------------------------------------------------------------------------
# What must never be deleted
# --------------------------------------------------------------------------


async def test_a_primary_file_is_never_droppable(session_for, marker, root, cleanup) -> None:
    """§5.4's binding reason for raw retention is link rot: government URLs
    reorganise constantly, so a primary file is frequently the only remaining
    copy of what a citation points at. Re-crawling does not bring it back."""
    sess = await session_for("rw")
    await a_source(sess, marker, root, PROTECTED_TIER)

    plan = await plan_sweep(sess, root)

    assert plan.droppable == ()
    assert plan.orphaned == ()
    assert plan.protected == 1


async def test_apply_refuses_a_primary_candidate_it_is_handed(root) -> None:
    """Defence in depth. A plan is data: it can be constructed, filtered or
    replayed by a caller that did not build it, so `apply_sweep` re-checks
    rather than trusting that `plan_sweep` was the one who decided."""
    (root / "x.html").write_bytes(b"keep me")
    forged = RetentionPlan(
        droppable=(DropCandidate("x.html", 7, f"retention_tier={PROTECTED_TIER}"),)
    )

    with pytest.raises(ValueError, match=PROTECTED_TIER):
        apply_sweep(forged, root, dry_run=False)

    assert (root / "x.html").exists(), "a refused sweep must not have deleted first"


async def test_a_dry_run_deletes_nothing(session_for, marker, root, cleanup) -> None:
    """The default, and it has to be, because the alternative default is
    irreversible and the flag to disable it would be the one nobody passes."""
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, "background")
    plan = await plan_sweep(sess, root)
    assert len(plan.droppable) == 1

    reclaimed = apply_sweep(plan, root, dry_run=True)

    assert reclaimed == 64, "a dry run should still report what it would reclaim"
    assert (root / plan.droppable[0].path).exists()
    assert await sess.get(Source, source.source_id) is not None


async def test_a_dangling_row_is_reported_and_left_alone(
    session_for, marker, root, cleanup
) -> None:
    """The row is the only record that the fetch happened, and the text
    extracted from it is still in the corpus. Deleting it to tidy up the report
    would destroy more than the missing file did."""
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, PROTECTED_TIER, write=False)

    plan = await plan_sweep(sess, root)

    # `in`, not `==`. Dangling is reported relative to *the root being swept*,
    # so every source whose file lives under a different one is listed too —
    # which is the whole of `P1-45`: `raw_file_path` records no root, so
    # "the file is missing" and "you are looking in the wrong place" are the
    # same observation. The report says so; this assertion must not pretend
    # otherwise by demanding the list hold nothing else.
    assert source.source_id in [d.source_id for d in plan.dangling]
    assert plan.droppable == () and plan.orphaned == ()
    assert await sess.get(Source, source.source_id) is not None


# --------------------------------------------------------------------------
# What it does delete
# --------------------------------------------------------------------------


async def test_a_file_no_row_refers_to_is_an_orphan(session_for, marker, root, cleanup) -> None:
    """Real, not theoretical: `path_for` includes the media type's extension, so
    a URL served as HTML and later as a PDF lands at a different path and leaves
    the old file behind with nothing pointing at it."""
    sess = await session_for("rw")
    await a_source(sess, marker, root, PROTECTED_TIER)
    stray = root / "nobody.test" / "ff" / "stray.pdf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"orphan")

    plan = await plan_sweep(sess, root)

    assert [c.path for c in plan.orphaned] == ["nobody.test/ff/stray.pdf"]

    apply_sweep(plan, root, dry_run=False)
    assert not stray.exists()


async def test_applying_twice_is_not_an_error(session_for, marker, root, cleanup) -> None:
    """The intended end state is that the file is absent, and after the first
    run it is. A sweep that raised on its own success would make the obvious
    recovery — run it again — the thing that breaks."""
    sess = await session_for("rw")
    await a_source(sess, marker, root, "background")
    plan = await plan_sweep(sess, root)

    apply_sweep(plan, root, dry_run=False)
    assert apply_sweep(plan, root, dry_run=False) == 0


async def test_a_missing_raw_store_is_not_an_error(session_for, tmp_path) -> None:
    """A worker that never stored anything, or a root that moved. Nothing to
    sweep is an answer."""
    sess = await session_for("rw")

    plan = await plan_sweep(sess, tmp_path / "absent")

    assert plan.files_seen == 0 and plan.is_empty


# --------------------------------------------------------------------------
# §5.4: background keeps a snapshot *if cited*
# --------------------------------------------------------------------------


async def test_a_cited_background_source_keeps_its_file(
    session_for, marker, root, cleanup
) -> None:
    """The clause that turns this from a tier lookup into a graph question.

    Empty until edges exist, which is why the query is written now rather than
    deferred: the day the first edges land is the day a sweep without this
    starts deleting the evidence under them, and nobody would connect that to a
    retention pass.
    """
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, "background")
    await replace_chunks(sess, source.source_id, [ChunkWrite(text="cited text", chunk_index=0)])
    chunk = (
        await sess.execute(select(Chunk).where(Chunk.source_id == source.source_id))
    ).scalar_one()

    assert (await plan_sweep(sess, root)).droppable, "uncited, it should be droppable"

    a = Entity(canonical_name=f"{marker} subject", node_type="concept")
    b = Entity(canonical_name=f"{marker} object", node_type="place")
    sess.add_all([a, b])
    await sess.flush()
    sess.add(
        Edge(
            from_node=a.entity_id,
            to_node=b.entity_id,
            relation_type="piloted_in",
            supporting_chunk_ids=[chunk.chunk_id],
        )
    )
    await sess.flush()

    assert source.source_id in await cited_source_ids(sess)
    plan = await plan_sweep(sess, root)
    assert plan.droppable == (), "a cited source lost its snapshot"
    assert plan.protected == 1


async def test_citing_one_source_does_not_protect_another(
    session_for, marker, root, cleanup
) -> None:
    """The converse, because a protection rule that is too broad silently turns
    the sweep off and looks exactly like one that works."""
    sess = await session_for("rw")
    cited = await a_source(sess, marker, root, "background")
    other = await a_source(sess, marker, root, "background")
    await replace_chunks(sess, cited.source_id, [ChunkWrite(text="cited", chunk_index=0)])
    chunk = (
        await sess.execute(select(Chunk).where(Chunk.source_id == cited.source_id))
    ).scalar_one()

    a = Entity(canonical_name=f"{marker} one", node_type="concept")
    b = Entity(canonical_name=f"{marker} two", node_type="place")
    sess.add_all([a, b])
    await sess.flush()
    sess.add(
        Edge(
            from_node=a.entity_id,
            to_node=b.entity_id,
            relation_type="piloted_in",
            supporting_chunk_ids=[chunk.chunk_id],
        )
    )
    await sess.flush()

    plan = await plan_sweep(sess, root)

    assert [c.source_id for c in plan.droppable] == [other.source_id]


# --------------------------------------------------------------------------
# Which store the file went into (task P1-45)
# --------------------------------------------------------------------------


async def test_a_file_under_another_root_is_elsewhere_not_dangling(
    session_for, marker, root, cleanup
) -> None:
    """The distinction this column exists to make.

    A row naming a different store is not missing its file — it is a file this
    sweep is not looking at. Folded together, a corpus written partly natively
    and partly by a container produces a dangling list long enough that a real
    loss inside it would never be noticed.
    """
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, PROTECTED_TIER, write=False)
    source.raw_root = "/somewhere/else/raw"
    await sess.flush()

    plan = await plan_sweep(sess, root)

    assert source.source_id in [d.source_id for d in plan.elsewhere]
    assert source.source_id not in [d.source_id for d in plan.dangling]


async def test_a_file_missing_from_its_own_root_is_dangling(
    session_for, marker, root, cleanup
) -> None:
    """The converse. A row that names *this* store and has no file here is the
    real thing, and must not be excused by the same mechanism."""
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, PROTECTED_TIER, write=False)
    source.raw_root = str(root)
    await sess.flush()

    plan = await plan_sweep(sess, root)

    assert source.source_id in [d.source_id for d in plan.dangling]
    assert source.source_id not in [d.source_id for d in plan.elsewhere]


async def test_a_row_with_no_recorded_root_stays_dangling(
    session_for, marker, root, cleanup
) -> None:
    """Rows written before `P1-45` record no root and genuinely cannot be told
    apart from a loss. Guessing one — assuming it must be the root being swept,
    or assuming it must be elsewhere — would turn "unknown" into a confident
    wrong answer for exactly the rows the column exists to explain."""
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, PROTECTED_TIER, write=False)
    assert source.raw_root is None

    plan = await plan_sweep(sess, root)

    assert source.source_id in [d.source_id for d in plan.dangling]


async def test_nothing_under_another_root_is_ever_deleted(
    session_for, marker, root, cleanup
) -> None:
    """Belt and braces: `elsewhere` is a report, and must not have become a
    deletion list by being adjacent to two of them."""
    sess = await session_for("rw")
    source = await a_source(sess, marker, root, "background", write=False)
    source.raw_root = "/somewhere/else/raw"
    await sess.flush()

    plan = await plan_sweep(sess, root)
    apply_sweep(plan, root, dry_run=False)

    assert await sess.get(Source, source.source_id) is not None
    assert all(c.source_id != source.source_id for c in plan.droppable)
