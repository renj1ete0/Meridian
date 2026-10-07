"""Helpers for fixtures that commit, so a test leaves the database as it found it (`B-148`).

Queueing anything records its domain in `fetch_policy` (`trust.record_discovery`), and adding
a topic renormalises the others, so a fixture that commits either leaves rows or weights behind
unless it puts them back. Rows are removed by what appeared during the test, never by name, so
the developer's own domains and topics are never touched. `make leak-check` finds what is missed.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.models import FetchPolicy, SteeringLog, TopicConfig


async def policy_domains(sess: AsyncSession) -> set[str]:
    """Every domain with a `fetch_policy` row, as committed."""
    return set(await sess.scalars(select(FetchPolicy.domain)))


async def forget_new_policies(sess: AsyncSession, before: set[str]) -> None:
    """Delete the `fetch_policy` rows that were not there in ``before``. Does not commit."""
    new = await policy_domains(sess) - before
    if new:
        await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain.in_(new)))


TOPIC_FIELDS = (
    "weight",
    "floor",
    "ceiling",
    "status",
    "pinned",
    "boost_factor",
    "boost_expires_at",
)


@dataclasses.dataclass(frozen=True)
class Topics:
    """Every topic's steering fields and the newest log row, as committed."""

    rows: dict[str, dict[str, object]]
    last_log_id: int


async def topics_now(sess: AsyncSession) -> Topics:
    """Take the snapshot `restore_topics` puts back."""
    rows = {
        row.topic: {field: getattr(row, field) for field in TOPIC_FIELDS}
        for row in await sess.scalars(select(TopicConfig))
    }
    last = await sess.scalar(select(func.coalesce(func.max(SteeringLog.log_id), 0)))
    return Topics(rows=rows, last_log_id=int(last or 0))


async def restore_topics(sess: AsyncSession, before: Topics) -> None:
    """Put every topic back, drop topics added since, and the log rows written since.

    `steering.add_topic` renormalises, so adding one topic can move every other weight and log
    each move. Written as statements, not attribute sets, so a stale session copy cannot turn
    the restore into a no-op. Does not commit.
    """
    await sess.execute(delete(SteeringLog).where(SteeringLog.log_id > before.last_log_id))
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic.notin_(before.rows or {""})))
    for topic, fields in before.rows.items():
        await sess.execute(update(TopicConfig).where(TopicConfig.topic == topic).values(**fields))
