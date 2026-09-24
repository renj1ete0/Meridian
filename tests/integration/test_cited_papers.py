"""Cited papers ranked by the page that cited them (task B-58).

Against a real Postgres: the parent column's foreign key, the queueing helper
that raises a DOI a better page cites, and the backlog pass that finds citing
pages in `sources.extra` for rows queued before the parent was recorded.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from meridian_core.citedpapers import FLOOR_PRIORITY, UNJUDGED_PRIORITY, on_topic_priority
from meridian_core.hostscores import MIN_EXAMINED
from meridian_core.models import HostScore, QueueTask, Source
from meridian_core.policy import source_tier_map
from meridian_core.queueing import enqueue_dois
from meridian_core.sources import upsert_source
from worker.requeue_dois import run_pass

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


def host() -> str:
    return f"h{uuid.uuid4().hex[:10]}.test"


def doi() -> str:
    return f"10.5555/cited-{uuid.uuid4().hex[:10]}"


async def page(sess, h: str, *, labels=None, cites: tuple[str, ...] = ()) -> Source:
    """One stored page, with a reference list the way `_bibliography` writes it."""
    extra = {"citations": [{"kind": "doi", "value": c} for c in cites]} if cites else None
    source, _ = await upsert_source(
        sess,
        f"https://{h}/p-{uuid.uuid4().hex[:6]}",
        checksum=f"sha256:{uuid.uuid4().hex}",
        **({"extra": extra} if extra else {}),
    )
    source.topic_labels = labels
    await sess.flush()
    return source


async def judge(sess, h: str, *, on_topic: int) -> None:
    sess.add(HostScore(host=h, examined=MIN_EXAMINED, on_topic=on_topic, pending=0))
    await sess.flush()


async def queued_doi(sess, value: str, *, priority: int = FLOOR_PRIORITY, **fields) -> QueueTask:
    fields.setdefault("seed_source", "citation")
    task = QueueTask(url_or_query=value, task_type="doi", priority=priority, **fields)
    sess.add(task)
    await sess.flush()
    return task


# --------------------------------------------------------------------------
# The column
# --------------------------------------------------------------------------


async def test_a_parent_that_does_not_exist_is_refused(sess) -> None:
    """A foreign key, not a number somebody hoped was a source id."""
    sess.add(QueueTask(url_or_query=doi(), task_type="doi", parent_source_id=-424242, priority=3))
    with pytest.raises(IntegrityError):
        await sess.flush()


async def test_deleting_the_citing_page_keeps_the_doi_and_forgets_the_parent(sess) -> None:
    """A ranking input, not provenance anything depends on: the row survives."""
    source = await page(sess, host(), labels=["t"])
    task = await queued_doi(sess, doi(), parent_source_id=source.source_id)

    await sess.execute(delete(Source).where(Source.source_id == source.source_id))
    await sess.refresh(task)

    assert task.parent_source_id is None
    assert task.status == "pending"


async def test_the_column_is_what_the_model_declares(sess) -> None:
    """Drift between the model and the migration: type, nullability, FK action."""
    column = QueueTask.__table__.c.parent_source_id
    row = (
        await sess.execute(
            sa.text(
                # pg_catalog, not information_schema: the latter hides
                # constraints from a role that does not own the table.
                """
                SELECT NOT a.attnotnull AS nullable,
                       format_type(a.atttypid, a.atttypmod) AS type,
                       c.confdeltype::text AS on_delete,
                       c.confrelid::regclass::text AS target
                  FROM pg_constraint c
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                 WHERE c.conrelid = 'queue'::regclass AND c.contype = 'f'
                   AND a.attname = 'parent_source_id'
                """
            )
        )
    ).one()
    fk = next(iter(column.foreign_keys))
    # pg_constraint.confdeltype: n = SET NULL, c = CASCADE, a = NO ACTION.
    actions = {"SET NULL": "n", "CASCADE": "c", "RESTRICT": "r", "NO ACTION": "a"}
    assert row.nullable is column.nullable
    assert row.type == "bigint"
    assert row.on_delete == actions[fk.ondelete.upper()]
    assert row.target == fk.column.table.name


async def test_the_timetable_row_matches_the_config(sess) -> None:
    """The migration inserts the row an existing deployment gets; the YAML seeds
    a new one. Two sources of truth for one row, compared."""
    from pathlib import Path

    jobs = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "schedule.yaml").read_text()
    )["jobs"]
    wanted = next(j for j in jobs if j["name"] == "requeue_dois")
    row = (
        await sess.execute(
            sa.text(
                "SELECT module, args, interval_seconds, enabled FROM scheduled_jobs"
                " WHERE name = 'requeue_dois'"
            )
        )
    ).one()
    assert row.module == wanted["module"]
    assert list(row.args) == wanted["args"]
    assert row.interval_seconds == wanted["interval_seconds"]
    assert row.enabled is wanted["enabled"]


# --------------------------------------------------------------------------
# enqueue_dois
# --------------------------------------------------------------------------


async def test_a_better_page_raises_a_pending_doi_and_becomes_its_parent(sess) -> None:
    value = doi()
    weak = await page(sess, host())
    strong = await page(sess, host(), labels=["t"])
    first = await enqueue_dois(
        sess,
        [value],
        topic="t",
        seed_source="citation",
        priority=UNJUDGED_PRIORITY,
        parent_source_id=weak.source_id,
    )
    again = await enqueue_dois(
        sess,
        [value],
        topic="t",
        seed_source="citation",
        priority=60,
        parent_source_id=strong.source_id,
    )

    rows = list(await sess.scalars(select(QueueTask).where(QueueTask.url_or_query == value)))
    assert (first, again) == (1, 0)
    assert len(rows) == 1
    await sess.refresh(rows[0])
    assert (rows[0].priority, rows[0].parent_source_id) == (60, strong.source_id)


async def test_a_weaker_page_does_not_lower_a_doi(sess) -> None:
    value = doi()
    strong = await page(sess, host(), labels=["t"])
    weak = await page(sess, host())
    await enqueue_dois(
        sess,
        [value],
        topic="t",
        seed_source="citation",
        priority=60,
        parent_source_id=strong.source_id,
    )
    await enqueue_dois(
        sess,
        [value],
        topic="t",
        seed_source="citation",
        priority=FLOOR_PRIORITY,
        parent_source_id=weak.source_id,
    )
    task = await sess.scalar(select(QueueTask).where(QueueTask.url_or_query == value))
    await sess.refresh(task)
    assert (task.priority, task.parent_source_id) == (60, strong.source_id)


async def test_an_answered_doi_is_not_reranked(sess) -> None:
    value = doi()
    task = await queued_doi(sess, value, status="done")
    source = await page(sess, host(), labels=["t"])

    await enqueue_dois(
        sess,
        [value],
        topic="t",
        seed_source="citation",
        priority=60,
        parent_source_id=source.source_id,
    )
    await sess.refresh(task)
    assert (task.priority, task.parent_source_id) == (FLOOR_PRIORITY, None)


# --------------------------------------------------------------------------
# The backlog pass
# --------------------------------------------------------------------------


async def test_the_backlog_finds_an_on_topic_citing_page_and_raises_its_doi(sess) -> None:
    """No parent recorded; found in the reference list, spelled differently."""
    value = doi()
    citing = await page(sess, host(), labels=["t"], cites=(f"https://doi.org/{value.upper()}",))
    task = await queued_doi(sess, value)

    stats = await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority == on_topic_priority(await source_tier_map(sess))
    assert task.priority > FLOOR_PRIORITY
    assert task.parent_source_id == citing.source_id
    assert stats.raised >= 1


async def test_an_off_topic_host_leaves_its_doi_at_the_floor(sess) -> None:
    """Labelled on-topic, but on a host judged off-topic: `B-48` wins."""
    h = host()
    await judge(sess, h, on_topic=0)
    value = doi()
    await page(sess, h, labels=["t"], cites=(value,))
    task = await queued_doi(sess, value)

    await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority == FLOOR_PRIORITY


async def test_a_page_about_nothing_lowers_a_provisionally_ranked_doi(sess) -> None:
    """The other direction: queued unjudged, the page is labelled off-topic."""
    source = await page(sess, host(), labels=[])
    task = await queued_doi(
        sess, doi(), priority=UNJUDGED_PRIORITY, parent_source_id=source.source_id
    )

    stats = await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority == FLOOR_PRIORITY
    assert stats.lowered >= 1


async def test_an_unlabelled_citing_page_gives_the_unjudged_rank(sess) -> None:
    value = doi()
    await page(sess, host(), labels=None, cites=(value,))
    task = await queued_doi(sess, value)

    await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority == UNJUDGED_PRIORITY


async def test_the_best_citing_page_decides(sess) -> None:
    """An off-topic page citing a paper does not undo an on-topic one citing it."""
    h = host()
    await judge(sess, h, on_topic=0)
    value = doi()
    await page(sess, h, labels=["t"], cites=(value,))
    good = await page(sess, host(), labels=["t"], cites=(value,))
    task = await queued_doi(sess, value)

    await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority == on_topic_priority(await source_tier_map(sess))
    assert task.parent_source_id == good.source_id


async def test_a_doi_nobody_is_found_citing_keeps_its_place(sess) -> None:
    task = await queued_doi(sess, doi(), priority=7)

    await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert (task.priority, task.parent_source_id) == (7, None)


async def test_a_duplicate_doi_is_ranked_once(sess) -> None:
    """The frontier queued the case it saw beside the citation's normal form."""
    value = doi()
    await page(sess, host(), labels=["t"], cites=(value,))
    shouted = await queued_doi(sess, value.upper(), seed_source="frontier")
    normal = await queued_doi(sess, value)

    stats = await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(shouted)
    await sess.refresh(normal)
    assert normal.priority > FLOOR_PRIORITY
    assert shouted.priority == FLOOR_PRIORITY
    assert stats.duplicates >= 1


async def test_a_malformed_citations_field_does_not_stop_the_pass(sess) -> None:
    """`jsonb_array_elements` raises on an object; one odd row must not end it."""
    value = doi()
    await upsert_source(
        sess, f"https://{host()}/odd", checksum="sha256:odd", extra={"citations": {"x": 1}}
    )
    await page(sess, host(), labels=["t"], cites=(value,))
    task = await queued_doi(sess, value)

    await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority > FLOOR_PRIORITY


async def test_an_unparseable_queued_doi_is_left_alone(sess) -> None:
    task = await queued_doi(sess, "not a doi at all")

    stats = await run_pass(apply=True, session_factory=factory(sess))

    await sess.refresh(task)
    assert task.priority == FLOOR_PRIORITY
    assert stats.unparseable >= 1


async def test_a_report_writes_nothing(sess) -> None:
    value = doi()
    await page(sess, host(), labels=["t"], cites=(value,))
    task = await queued_doi(sess, value)

    stats = await run_pass(apply=False, session_factory=factory(sess))

    await sess.refresh(task)
    assert (task.priority, task.parent_source_id) == (FLOOR_PRIORITY, None)
    assert stats.raised >= 1


async def test_the_pass_deletes_nothing_and_touches_only_pending_dois(sess) -> None:
    h = host()
    await judge(sess, h, on_topic=0)
    value = doi()
    await page(sess, h, labels=[], cites=(value,))
    pending = await queued_doi(sess, value, priority=UNJUDGED_PRIORITY)
    other = doi()
    await page(sess, host(), labels=["t"], cites=(other,))
    done = await queued_doi(sess, other, status="done")
    link = QueueTask(url_or_query=f"https://{host()}/x", priority=11)
    sess.add(link)
    await sess.flush()

    await run_pass(apply=True, session_factory=factory(sess))

    for row in (pending, done, link):
        await sess.refresh(row)
    assert pending.priority == FLOOR_PRIORITY
    assert done.priority == FLOOR_PRIORITY
    assert link.priority == 11
