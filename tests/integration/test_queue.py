"""The queue's claim, against Postgres (task B-112).

Busy hosts are matched in SQL from the row's URL, so only the database can say
whether a subdomain is caught and a lookalike is not.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("require_db")
# -- skipping busy hosts (B-112) -------------------------------------------------


async def test_a_claim_skips_pages_on_busy_hosts_but_not_lookalikes_or_queries(session_for) -> None:
    import uuid

    from meridian_core.models import QueueTask
    from meridian_core.queueing import claim_next

    sess = await session_for("rw")
    await sess.rollback()
    tag = uuid.uuid4().hex[:8]
    busy = f"busy{tag}.test"
    rows = {
        "apex": QueueTask(
            url_or_query=f"https://{busy}/a", task_type="url", priority=10_000, topic=tag
        ),
        "sub": QueueTask(
            url_or_query=f"https://www.api.{busy}/b", task_type="url", priority=9_999, topic=tag
        ),
        "lookalike": QueueTask(
            url_or_query=f"https://not{busy}/c", task_type="url", priority=9_998, topic=tag
        ),
        "query": QueueTask(
            url_or_query=f"{busy} study", task_type="query", priority=9_997, topic=tag
        ),
    }
    sess.add_all(rows.values())
    await sess.flush()

    claimed = []
    for _ in range(3):
        task = await claim_next(sess, worker_id="t", topics=[tag], skip_domains={busy})
        claimed.append(task.url_or_query if task else None)

    assert claimed == [rows["lookalike"].url_or_query, rows["query"].url_or_query, None]
    await sess.rollback()
