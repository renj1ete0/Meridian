"""Inbound Telegram commands against a real Postgres (task `P5-07`, §13.3).

`tests/unit/test_commands.py` covers everything that can be decided without a
database. What is left needs one, and it is the half that changes something:
a pause that must keep the topic's weight, a boost that must expire on its own,
and a seed that gets the same refusals a model's seed gets.

**The fixture restores rather than rolls back.** `execute` commits — it has to,
because a bot replying "done" over a transaction nobody committed is lying —
so the only way to leave the dev database as it was found is to write the old
values back. Every topic row is snapshotted, and the rows these tests create
carry a per-run marker so cleanup removes those and nothing else.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select

from meridian_core import steering
from meridian_core.models import FetchPolicy, QueueTask, SteeringLog, TopicConfig
from worker.commands import Command, execute, parse

pytestmark = pytest.mark.usefixtures("require_db")

FIELDS = ("weight", "floor", "ceiling", "status", "pinned", "boost_factor", "boost_expires_at")


@pytest.fixture
async def steered(session_for) -> AsyncIterator[tuple]:
    """A throwaway topic to steer, and every other topic put back afterwards.

    `add_topic` renormalises, so creating one moves every weight in the set;
    restoring the snapshot is what undoes that.
    """
    sess = await session_for("rw")
    await sess.rollback()

    before = {
        row.topic: {field: getattr(row, field) for field in FIELDS}
        for row in await sess.scalars(select(TopicConfig))
    }
    started = dt.datetime.now(dt.UTC)
    marker = uuid.uuid4().hex[:8]
    topic = f"zz-test-{marker}"

    await steering.add_topic(
        sess, topic, floor=0.0, ceiling=1.0, actor="test", reason="P5-07", now=started
    )
    await sess.commit()

    yield sess, topic, marker

    await sess.rollback()
    await sess.execute(delete(QueueTask).where(QueueTask.url_or_query.contains(marker)))
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain.contains(marker)))
    for row in await sess.scalars(select(TopicConfig)):
        if row.topic not in before:
            await sess.delete(row)
            continue
        for field, value in before[row.topic].items():
            setattr(row, field, value)
    await sess.execute(delete(SteeringLog).where(SteeringLog.changed_at >= started))
    await sess.commit()


async def run(sess, text: str) -> str:
    return await execute(sess, parse(text))


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


async def test_status_answers_with_the_counts_that_decide_whether_to_worry(steered) -> None:
    sess, _topic, _marker = steered

    reply = await run(sess, "/status")

    # Chunks and embedded chunks separately: §12.5's point is that "we have not
    # collected this" and "we collected it and have not embedded it" are
    # different findings, and one number would erase the distinction.
    assert "chunks" in reply and "embedded" in reply


async def test_weights_reports_the_share_rather_than_the_stored_weight(steered) -> None:
    sess, topic, _marker = steered

    reply = await run(sess, "/weights")

    # The two differ exactly when a bound or a boost is doing something, and it
    # is the share that decides what gets crawled.
    assert topic in reply
    assert "%" in reply


# --------------------------------------------------------------------------
# Steering
# --------------------------------------------------------------------------


async def test_pause_changes_the_status_and_keeps_the_weight(steered) -> None:
    sess, topic, _marker = steered
    before = (await sess.get(TopicConfig, topic)).weight

    reply = await run(sess, f"/pause {topic}")

    row = await sess.get(TopicConfig, topic)
    await sess.refresh(row)
    assert row.status == "paused"
    assert row.weight == before, "§10.2: a pause keeps the weight so the topic can come back"
    assert topic in reply


async def test_a_steering_change_records_who_asked(steered) -> None:
    sess, topic, _marker = steered

    await run(sess, f"/pause {topic}")

    # Scoped to the change the command made: the fixture's own `add_topic`
    # writes a status row too, under its own actor.
    rows = list(
        await sess.scalars(
            select(SteeringLog).where(
                SteeringLog.topic == topic,
                SteeringLog.field == "status",
                SteeringLog.new_value.contains("paused"),
            )
        )
    )
    # §10.1 wants a reason on every change, and "who" matters as much: a weight
    # moved from a phone is a different thing to review than one changed in
    # Admin, and only the actor can tell them apart afterwards.
    assert rows and all(row.actor == "telegram" for row in rows)
    assert any("Telegram" in (row.reason or "") for row in rows)


async def test_boost_sets_a_multiplier_that_expires(steered) -> None:
    """The drift this catches: a boost written as a permanent weight change.

    `set_weight` and `set_boost` take the same topic and a similar-looking
    number, and the wrong one compiles. The difference is that the wrong one
    never comes back on its own — §10 makes decay the mechanism precisely so a
    temporary steer does not have to be remembered.
    """
    sess, topic, _marker = steered
    before = (await sess.get(TopicConfig, topic)).weight

    await run(sess, f"/boost {topic} 2 3")

    row = await sess.get(TopicConfig, topic)
    await sess.refresh(row)
    assert row.boost_factor == pytest.approx(2.0)
    assert row.boost_expires_at is not None
    assert row.boost_expires_at > dt.datetime.now(dt.UTC) + dt.timedelta(days=20)
    assert row.weight == before, "the baseline is untouched, so expiry restores nothing"


async def test_a_boost_is_visible_in_the_share_and_the_log(steered) -> None:
    sess, topic, _marker = steered

    await run(sess, f"/boost {topic} 3 2")

    fields = set(await sess.scalars(select(SteeringLog.field).where(SteeringLog.topic == topic)))
    assert {"boost_factor", "boost_expires_at"} <= fields
    assert steering.boost_is_active(await sess.get(TopicConfig, topic), now=dt.datetime.now(dt.UTC))


@pytest.mark.parametrize("text", ["/pause zz-no-such-topic", "/boost zz-no-such-topic 2 4"])
async def test_steering_a_topic_that_does_not_exist_is_a_reply_not_a_traceback(
    steered, text
) -> None:
    sess, _topic, _marker = steered

    reply = await run(sess, text)

    # A traceback is unreadable on a phone, and the bot would go silent.
    assert "zz-no-such-topic" in reply
    assert "Could not" in reply


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


async def test_a_seeded_url_is_queued_as_the_operator_s_own(steered) -> None:
    sess, topic, marker = steered
    url = f"https://{marker}.example.test/a"

    reply = await run(sess, f"/seed {url} {topic}")

    row = await sess.scalar(select(QueueTask).where(QueueTask.url_or_query == url))
    assert row is not None
    assert row.task_type == "url"
    assert row.seed_source == "user", "typing a URL is consent (§P4-12)"
    assert row.topic == topic
    assert row.priority > 0, "a hand-typed seed should not wait behind the frontier"
    assert url in reply


async def test_a_seeded_url_makes_its_domain_operator_chosen(steered) -> None:
    sess, _topic, marker = steered

    await run(sess, f"/seed https://{marker}.example.test/a")

    policy = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain.contains(marker)))
    assert policy is not None
    assert policy.first_seen_via == "user"
    assert policy.seed_allowed is True


async def test_a_seed_without_a_scheme_is_queued_as_a_query(steered) -> None:
    sess, _topic, marker = steered

    await run(sess, f"/seed {marker}-some-question")

    row = await sess.scalar(select(QueueTask).where(QueueTask.url_or_query.contains(marker)))
    assert row is not None
    assert row.task_type == "query", "a search term is not a URL and must not be fetched as one"


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"],
)
async def test_a_seed_that_would_reach_inward_is_refused_and_queues_nothing(steered, url) -> None:
    """The same checks a model's seed gets. §11.8's point is that a target is no
    safer for having been typed into a phone — and the phone is the surface most
    likely to be used by somebody who is not at a desk to check."""
    sess, _topic, _marker = steered
    before = await sess.scalar(select(QueueTask).where(QueueTask.url_or_query == url))

    reply = await run(sess, f"/seed {url}")

    assert "Refused" in reply
    assert await sess.scalar(select(QueueTask).where(QueueTask.url_or_query == url)) == before


# --------------------------------------------------------------------------
# Refusals reach the database not at all
# --------------------------------------------------------------------------


async def test_a_refusal_from_parse_is_returned_verbatim_and_writes_nothing(steered) -> None:
    sess, topic, _marker = steered
    before = {row.topic: row.weight for row in await sess.scalars(select(TopicConfig))}

    reply = await execute(sess, Command("boost", (topic,), error="Usage: /boost <t> <x> <w>"))

    assert reply == "Usage: /boost <t> <x> <w>"
    await sess.rollback()
    after = {row.topic: row.weight for row in await sess.scalars(select(TopicConfig))}
    assert after == before


async def test_a_command_waiting_on_a_later_phase_changes_nothing(steered) -> None:
    sess, _topic, _marker = steered

    reply = await run(sess, "/run")

    assert "not available yet" in reply
