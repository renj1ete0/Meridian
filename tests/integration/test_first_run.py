"""The first run, steered from the interface (task `B-07`, §15 phase 0, §16).

§16 calls cold-start seed quality a real risk, "worth spending an evening on".
Until now that evening had to be spent editing `config/seed_sources.yaml`
*before* the first boot, because the file is read once and never again (§13.1)
— and somebody installing Meridian to find out what it does has no idea what to
put there yet.

The tests are about the window this opens and its edges: a seed is editable
while it is pending, and not after, because a task that has already been
reached has produced evidence that would be left unexplained.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from api.routes.admin import add_seed, drop_seed, read_first_run
from fastapi import HTTPException
from meridian_core.models import QueueTask, Source
from meridian_core.schemas.admin import SeedCreate

pytestmark = pytest.mark.usefixtures("require_db")

URL = "https://first-run.test/root"


@pytest.fixture
async def clean(session_for):
    sess = await session_for("rw")
    await sess.execute(delete(QueueTask).where(QueueTask.url_or_query.like("%first-run.test%")))
    await sess.execute(delete(Source).where(Source.url.like("%first-run.test%")))
    await sess.flush()
    return sess


async def a_seed(sess, *, url=URL, status="pending") -> QueueTask:
    task = QueueTask(
        url_or_query=url, task_type="url", status=status, seed_source="user", priority=100
    )
    sess.add(task)
    await sess.flush()
    return task


# --------------------------------------------------------------------------
# Is this a first run


async def test_an_empty_corpus_is_a_first_run(clean) -> None:
    """ "No sources yet" is the test, not "no seeds yet" — seeds are queued by
    `make seed` at first boot, so a fresh install always has them. What makes a
    run *first* is that nothing has come back."""
    await clean.execute(delete(Source))
    await clean.flush()

    view = await read_first_run(None, clean)

    assert view.is_first_run is True
    assert view.sources == 0


async def test_one_crawled_document_ends_it(clean) -> None:
    clean.add(Source(url=URL, source_tier="informal", retention_tier="background"))
    await clean.flush()

    view = await read_first_run(None, clean)

    assert view.is_first_run is False
    assert view.sources >= 1


async def test_pending_seeds_are_listed_and_in_flight_ones_are_counted(clean) -> None:
    """Both, because a crawl that has started is a fact. A screen showing only
    what is still editable would invite somebody to remove a seed that has
    already been fetched and wonder why it came back."""
    await a_seed(clean, url=f"{URL}/a")
    await a_seed(clean, url=f"{URL}/b", status="fetched")

    view = await read_first_run(None, clean)

    listed = {row.url_or_query for row in view.pending_seeds}
    assert f"{URL}/a" in listed
    assert f"{URL}/b" not in listed
    assert view.seeds_in_flight >= 1


# --------------------------------------------------------------------------
# Adding


async def test_a_typed_seed_is_queued_as_the_operators_own(clean) -> None:
    """`seed_source="user"` — somebody typing a URL is consent, and `P4-12`
    allows the domain immediately on that basis."""
    created = await add_seed(SeedCreate(url_or_query=URL, topic="walkability"), None, clean)

    assert created.seed_source == "user"
    assert created.topic == "walkability"
    assert created.priority == 100, "cold-start seeds run first"


async def test_a_dangerous_url_is_refused_even_when_typed(clean) -> None:
    """The checks that matter here are about the URL. A private address is no
    safer for having been typed by the operator than proposed by a model —
    §11.8's attack path ends at the crawler being used as a proxy, and it does
    not care who asked."""
    with pytest.raises(HTTPException) as raised:
        await add_seed(SeedCreate(url_or_query="http://169.254.169.254/latest/"), None, clean)

    assert raised.value.status_code == 422


async def test_a_file_scheme_is_refused(clean) -> None:
    with pytest.raises(HTTPException) as raised:
        await add_seed(SeedCreate(url_or_query="file:///etc/passwd"), None, clean)

    assert raised.value.status_code == 422


async def test_a_query_seed_skips_the_url_checks(clean) -> None:
    """A search query is not an address and must not be validated as one —
    `check_seed_allowed` would reject every query for having no scheme."""
    # The query carries the fixture's marker, like the URLs do. A realistic
    # phrase collides with the query seeds `config/seed_sources.yaml` really
    # ships, and the collision reads as a broken handler rather than a test
    # sharing a database with the thing it is testing.
    created = await add_seed(
        SeedCreate(url_or_query="first-run.test comfort study", task_type="query"), None, clean
    )

    assert created.task_type == "query"


async def test_queueing_the_same_seed_twice_is_refused(clean) -> None:
    await add_seed(SeedCreate(url_or_query=URL), None, clean)

    with pytest.raises(HTTPException) as raised:
        await add_seed(SeedCreate(url_or_query=URL), None, clean)

    assert raised.value.status_code == 409


# --------------------------------------------------------------------------
# Removing — and the edge that matters


async def test_a_pending_seed_can_be_removed(clean) -> None:
    task = await a_seed(clean)

    await drop_seed(task.task_id, None, clean)

    assert (
        await clean.scalar(select(QueueTask.task_id).where(QueueTask.task_id == task.task_id))
        is None
    )


@pytest.mark.parametrize("status", ["fetched", "failed", "done"])
async def test_a_seed_that_has_been_reached_cannot_be_removed(clean, status: str) -> None:
    """It has already produced a fetch attempt and possibly a source, and
    deleting the queue row would leave that evidence with nothing explaining
    where it came from. The refusal says which, because "I removed that seed"
    and "that seed had already run" are different things to believe."""
    task = await a_seed(clean, status=status)

    with pytest.raises(HTTPException) as raised:
        await drop_seed(task.task_id, None, clean)

    assert raised.value.status_code == 409
    assert status in str(raised.value.detail)


async def test_a_seed_being_fetched_right_now_cannot_be_removed(clean) -> None:
    """The case somebody is most likely to hit: they watch the crawl start and
    reach for the seed they did not mean to include.

    **Claiming is a lease, not a status** (`P1-01`), so this row is still
    `pending` — a handler checking only the status would delete it out from
    under the worker mid-fetch.
    """
    task = await a_seed(clean)
    task.claimed_by = "worker-1"
    task.claimed_at = dt.datetime.now(dt.UTC)
    await clean.flush()

    with pytest.raises(HTTPException) as raised:
        await drop_seed(task.task_id, None, clean)

    assert raised.value.status_code == 409
    assert "right now" in str(raised.value.detail)


async def test_removing_something_that_is_not_there_is_a_404(clean) -> None:
    with pytest.raises(HTTPException) as raised:
        await drop_seed(-1, None, clean)

    assert raised.value.status_code == 404
