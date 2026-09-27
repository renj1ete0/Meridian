"""News queries may be asked again after a while (task B-105).

Every other query is asked once, ever; a news query's answers change, so once
it is old enough it is no longer "already asked". Against Postgres because the
rule reads the queue's own timestamps.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from meridian_core.models import QueueTask
from worker.seedsearch import NEWS_REPEAT, asked_before

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 27, 12, tzinfo=dt.UTC)


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def asked(sess, text: str, age: dt.timedelta) -> str:
    sess.add(
        QueueTask(
            url_or_query=text,
            task_type="query",
            seed_source="diversity",
            status="done",
            priority=70,
            created_at=NOW - age,
        )
    )
    await sess.flush()
    return text


async def test_an_old_news_query_may_be_asked_again_and_nothing_else_may(sess) -> None:
    w = uuid.uuid4().hex[:8]
    old_news = await asked(sess, f"!news {w} old", NEWS_REPEAT + dt.timedelta(hours=1))
    new_news = await asked(sess, f"!news {w} new", NEWS_REPEAT - dt.timedelta(hours=1))
    old_plain = await asked(sess, f"{w} evaluation", dt.timedelta(days=90))

    seen = set(await asked_before(sess, now=NOW))

    assert old_news not in seen, "an old news query is still blocked"
    assert new_news in seen, "a recent news query would be asked again at once"
    assert old_plain in seen, "an ordinary query became askable again"


async def test_asked_again_recently_blocks_it_again(sess) -> None:
    """The newest row decides: re-asked yesterday, it waits another three days."""
    w = uuid.uuid4().hex[:8]
    text = f"!news {w}"
    await asked(sess, text, dt.timedelta(days=30))
    await asked(sess, text, dt.timedelta(days=1))
    assert text in set(await asked_before(sess, now=NOW))
