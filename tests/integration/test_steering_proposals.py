"""Steering proposals against a real Postgres (task P6-38, spec §10, §10.1, §10.2).

The claims, each as a rejection where one is possible:

- The pass measures what the database holds, and proposes for the starved and
  the over-served topic and nobody else.
- Nothing applies before its window; everything that applies lands in
  `steering_log` through `steering`, with the reason the operator was shown.
- A rejected proposal never applies. A superseded one never applies. A topic
  somebody steered recently gets no proposal at all.
- The table refuses what the model promises it refuses — checked with raw
  SQL, because the ORM's own validation would pass whether or not the
  constraint exists (handover, "Alembic does not see CHECK constraints").

Every test pauses the topics it did not create, inside its own transaction, so
the shares are the test's and the rollback puts them back.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from api.main import create_app
from meridian_core import steering
from meridian_core import steering_proposals as sp
from meridian_core.db import dispose_engines
from meridian_core.models import (
    FetchAttempt,
    FetchPolicy,
    Notification,
    QueueTask,
    Source,
    SteeringLog,
    SteeringProposal,
    TopicConfig,
)
from meridian_core.policy import resolve_policy

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.UTC)


def marker() -> str:
    return f"zz-prop-{uuid.uuid4().hex[:8]}"


async def isolate(sess) -> None:
    """Only this test's topics draw. Rolled back with everything else."""
    await sess.execute(
        update(TopicConfig)
        .where(TopicConfig.status == "active", ~TopicConfig.topic.like("zz-prop-%"))
        .values(status="paused")
    )
    await sess.flush()


async def add_topic(sess, topic: str, weight: float, *, floor: float = 0.05) -> None:
    sess.add(TopicConfig(topic=topic, weight=weight, floor=floor, ceiling=0.9, status="active"))
    await sess.flush()


async def activity(sess, topic: str, *, fetches: int, sources: int, at: dt.datetime) -> None:
    """``fetches`` attempts for queue tasks of ``topic``, and ``sources`` new
    sources labelled with it, all at ``at``."""
    task = QueueTask(url_or_query=f"https://{uuid.uuid4().hex[:10]}.test/", topic=topic)
    sess.add(task)
    await sess.flush()
    for i in range(fetches):
        sess.add(
            FetchAttempt(
                task_id=task.task_id,
                domain="example.test",
                url=f"https://example.test/{topic}/{i}",
                attempted_at=at,
                outcome="success",
            )
        )
    for _ in range(sources):
        sess.add(
            Source(
                url=f"https://{uuid.uuid4().hex[:10]}.test/{topic}",
                source_tier="institutional",
                retention_tier="primary",
                checksum=f"sha256:{uuid.uuid4().hex}",
                text_available=True,
                topic_labels=[topic],
                created_at=at,
            )
        )
    await sess.flush()


async def scenario(sess):
    """Three topics at a third each: one starved, one over-served, one on par."""
    await isolate(sess)
    starved, over, par = marker(), marker(), marker()
    for topic in (starved, over, par):
        await add_topic(sess, topic, 1 / 3)
    at = NOW - dt.timedelta(hours=2)
    await activity(sess, starved, fetches=60, sources=2, at=at)
    await activity(sess, over, fetches=160, sources=40, at=at)
    await activity(sess, par, fetches=80, sources=18, at=at)
    return starved, over, par


async def proposals_for(sess, topic: str) -> list[SteeringProposal]:
    return list(
        await sess.scalars(
            select(SteeringProposal)
            .where(SteeringProposal.topic == topic)
            .order_by(SteeringProposal.proposal_id)
        )
    )


async def log_for(sess, topic: str) -> list[SteeringLog]:
    return list(
        await sess.scalars(
            select(SteeringLog).where(SteeringLog.topic == topic).order_by(SteeringLog.log_id)
        )
    )


# -- measuring ---------------------------------------------------------------------


async def test_the_pass_counts_what_the_database_holds(session_for):
    sess = await session_for("rw")
    starved, over, par = await scenario(sess)
    # Outside the lookback, and a duplicate: neither counts.
    await activity(sess, starved, fetches=50, sources=50, at=NOW - dt.timedelta(hours=30))
    dup_of = await sess.scalar(
        select(Source.source_id).where(Source.topic_labels.any(over)).limit(1)
    )
    sess.add(
        Source(
            url=f"https://{uuid.uuid4().hex[:10]}.test/dup",
            source_tier="institutional",
            retention_tier="primary",
            checksum=f"sha256:{uuid.uuid4().hex}",
            text_available=True,
            topic_labels=[over],
            created_at=NOW,
            duplicate_of=dup_of,
            duplicate_reason="copy",
        )
    )
    await sess.flush()

    measured = {m.topic: m for m in await sp.measure(sess, now=NOW)}
    assert set(measured) == {starved, over, par}
    assert (measured[starved].fetches, measured[starved].new_sources) == (60, 2)
    assert (measured[over].fetches, measured[over].new_sources) == (160, 40)
    assert abs(sum(m.share for m in measured.values()) - 1.0) < 1e-6
    # Gaps' thin finding rides along: two labelled sources in the window,
    # fifty-two in all, so the starved topic is not thin.
    assert measured[starved].thin_sources is None


# -- end to end ----------------------------------------------------------------------


async def test_end_to_end_proposes_waits_then_applies_through_steering(session_for):
    sess = await session_for("rw")
    starved, over, par = await scenario(sess)

    report = await sp.run_pass(sess, now=NOW)
    (boost,) = await proposals_for(sess, starved)
    (lower,) = await proposals_for(sess, over)
    assert await proposals_for(sess, par) == []
    assert (boost.kind, boost.status, lower.kind, lower.status) == (
        "boost",
        "pending",
        "weight",
        "pending",
    )
    assert set(report.created) == {boost.proposal_id, lower.proposal_id}
    assert boost.apply_after == NOW + dt.timedelta(hours=sp.DEFAULT_WINDOW_HOURS)
    assert boost.evidence["new_sources"] == 2 and boost.evidence["fetches"] == 60
    assert lower.proposed_value < lower.current_value

    notes = list(
        await sess.scalars(
            select(Notification).where(
                Notification.notification_type == "steering_proposal",
                Notification.payload["topic"].astext.in_([starved, over]),
            )
        )
    )
    assert len(notes) == 2 and all("Applies by itself at" in n.body for n in notes)

    # An hour before the window ends: still waiting, and not re-proposed.
    early = await sp.run_pass(sess, now=boost.apply_after - dt.timedelta(minutes=1))
    assert boost.status == "pending" and lower.status == "pending"
    assert set(early.kept) == {boost.proposal_id, lower.proposal_id} and not early.created
    assert await log_for(sess, starved) == []

    later = boost.apply_after + dt.timedelta(minutes=5)
    await sp.run_pass(sess, now=later)
    await sess.refresh(boost)
    await sess.refresh(lower)
    assert (boost.status, lower.status) == ("applied", "applied")
    row = await sess.get(TopicConfig, starved)
    assert row.boost_factor == sp.BOOST_FACTOR
    assert row.boost_expires_at == later + dt.timedelta(hours=sp.BOOST_HOURS)
    assert boost.expires_at == row.boost_expires_at
    assert abs((await sess.get(TopicConfig, over)).weight - lower.proposed_value) < 1e-6

    logged = await log_for(sess, starved)
    assert {r.field for r in logged} >= {"boost_factor", "boost_expires_at"}
    assert all(r.actor == sp.ACTOR for r in logged)
    # The weight proposal on the other topic also moved this one's weight (a
    # renormalisation), so pick the boost's own row rather than the first.
    (factor_row,) = [r for r in logged if r.field == "boost_factor"]
    assert factor_row.reason == (f"auto-applied after 12h with no objection: {boost.reason}")
    assert all(r.reason.startswith("auto-applied after 12h with no objection: ") for r in logged)


async def test_a_window_set_in_the_policy_row_is_the_one_used(session_for):
    sess = await session_for("rw")
    glob = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == "*"))
    glob.settings = {**(glob.settings or {}), sp.WINDOW_KEY: 3}
    await sess.flush()
    starved, _, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (boost,) = await proposals_for(sess, starved)
    assert boost.apply_after == NOW + dt.timedelta(hours=3)
    # And it is not a fetch setting.
    assert sp.WINDOW_KEY not in (await resolve_policy(sess, "example.test")).model_dump()


# -- what never applies ----------------------------------------------------------------


async def test_a_rejected_proposal_never_applies_and_is_not_re_proposed(session_for):
    sess = await session_for("rw")
    starved, _, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (boost,) = await proposals_for(sess, starved)

    await sp.reject(sess, boost.proposal_id, actor="user", note="  not  now ", now=NOW)
    assert (boost.status, boost.decided_by, boost.note) == ("rejected", "user", "not now")
    (logged,) = await log_for(sess, starved)
    assert (logged.actor, logged.field) == ("user", sp.LOG_FIELD)
    assert "not now" in logged.reason

    await sp.run_pass(sess, now=boost.apply_after + dt.timedelta(hours=1))
    row = await sess.get(TopicConfig, starved)
    assert row.boost_factor is None
    assert [p.status for p in await proposals_for(sess, starved)] == ["rejected"]

    with pytest.raises(sp.NotPending):
        await sp.accept(sess, boost.proposal_id, actor="user", now=NOW)


async def test_a_topic_changed_by_hand_after_the_proposal_is_superseded(session_for):
    sess = await session_for("rw")
    starved, over, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (lower,) = await proposals_for(sess, over)

    await steering.set_bounds(
        sess, over, floor=0.1, actor="user", reason="by hand", now=NOW + dt.timedelta(hours=1)
    )
    await sp.run_pass(sess, now=lower.apply_after + dt.timedelta(minutes=1))
    await sess.refresh(lower)
    assert lower.status == "superseded"
    assert "changed by user" in lower.note
    assert not any(r.actor == sp.ACTOR for r in await log_for(sess, over))


@pytest.mark.parametrize("change", ["paused", "archived", "pinned"])
async def test_a_topic_that_left_the_pool_or_was_pinned_is_superseded(session_for, change):
    sess = await session_for("rw")
    starved, _, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (boost,) = await proposals_for(sess, starved)
    row = await sess.get(TopicConfig, starved)
    # Written directly, not through steering: the basis check must hold even
    # when the change left no log row to find.
    if change == "pinned":
        row.pinned = True
    else:
        row.status = change
    await sess.flush()

    report = await sp.apply_due(sess, now=boost.apply_after + dt.timedelta(minutes=1))
    assert boost.proposal_id in report.superseded
    assert boost.status == "superseded" and row.boost_factor is None


async def test_a_pending_proposal_whose_signal_is_gone_is_superseded(session_for):
    sess = await session_for("rw")
    starved, _, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (boost,) = await proposals_for(sess, starved)
    # The topic catches up within the hour.
    await activity(sess, starved, fetches=0, sources=40, at=NOW + dt.timedelta(minutes=30))
    await sp.run_pass(sess, now=NOW + dt.timedelta(hours=1))
    assert boost.status == "superseded" and "no longer holds" in boost.note
    await sp.run_pass(sess, now=boost.apply_after + dt.timedelta(minutes=1))
    assert (await sess.get(TopicConfig, starved)).boost_factor is None


async def test_no_proposal_for_a_topic_steered_by_hand_recently(session_for):
    sess = await session_for("rw")
    starved, _, _ = await scenario(sess)
    await steering.set_description(
        sess, starved, "words", actor="user", reason="by hand", now=NOW - dt.timedelta(hours=5)
    )
    await sp.run_pass(sess, now=NOW)
    assert await proposals_for(sess, starved) == []

    # A day later, the evidence postdates the change and it may be proposed.
    later = NOW + dt.timedelta(hours=20)
    await activity(sess, marker(), fetches=0, sources=0, at=later)
    await sess.execute(
        update(FetchAttempt)
        .where(FetchAttempt.attempted_at == NOW - dt.timedelta(hours=2))
        .values(attempted_at=later)
    )
    await sess.execute(
        update(Source)
        .where(Source.created_at == NOW - dt.timedelta(hours=2))
        .values(created_at=later)
    )
    await sp.run_pass(sess, now=later + dt.timedelta(hours=1))
    assert [p.kind for p in await proposals_for(sess, starved)] == ["boost"]


async def test_a_pinned_topic_gets_no_proposal(session_for):
    sess = await session_for("rw")
    starved, over, _ = await scenario(sess)
    for topic in (starved, over):
        (await sess.get(TopicConfig, topic)).pinned = True
    await sess.flush()
    await sp.run_pass(sess, now=NOW)
    assert await proposals_for(sess, starved) == [] == await proposals_for(sess, over)


# -- accepting -------------------------------------------------------------------------


async def test_accepting_a_weight_proposal_applies_it_now_as_the_operator(session_for):
    sess = await session_for("rw")
    _, over, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (lower,) = await proposals_for(sess, over)
    await sp.accept(sess, lower.proposal_id, actor="user", now=NOW + dt.timedelta(hours=1))
    assert (lower.status, lower.decided_by) == ("applied", "user")
    assert abs((await sess.get(TopicConfig, over)).weight - lower.proposed_value) < 1e-6
    weight_rows = [r for r in await log_for(sess, over) if r.field == "weight"]
    assert weight_rows and weight_rows[-1].actor == "user"
    assert weight_rows[-1].reason.startswith(f"accepted proposal #{lower.proposal_id}")


async def test_accepting_for_a_paused_topic_is_refused_and_changes_nothing(session_for):
    sess = await session_for("rw")
    starved, _, _ = await scenario(sess)
    await sp.run_pass(sess, now=NOW)
    (boost,) = await proposals_for(sess, starved)
    (await sess.get(TopicConfig, starved)).status = "paused"
    await sess.flush()
    with pytest.raises(ValueError, match="paused"):
        await sp.accept(sess, boost.proposal_id, actor="user", now=NOW)
    assert boost.status == "pending"


# -- what the table refuses (raw SQL) ----------------------------------------------------


async def _insert(sess, topic: str, **over) -> None:
    values = {
        "actor": "t",
        "topic": topic,
        "kind": "boost",
        "current_value": 1.0,
        "proposed_value": 1.5,
        "expires_at": NOW,
        "reason": "r",
        "apply_after": NOW,
        "status": "pending",
        "applied_at": None,
        **over,
    }
    await sess.execute(
        text(
            "INSERT INTO steering_proposals (actor, topic, kind, current_value, proposed_value,"
            " expires_at, reason, apply_after, status, applied_at) VALUES (:actor, :topic,"
            " :kind, :current_value, :proposed_value, :expires_at, :reason, :apply_after,"
            " :status, :applied_at)"
        ),
        values,
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "nudge"},
        {"status": "accepted"},
        {"kind": "boost", "expires_at": None},
        {"kind": "boost", "proposed_value": 0.0},
        {"kind": "weight", "expires_at": None, "proposed_value": 1.5},
        {"kind": "weight", "expires_at": None, "proposed_value": -0.1},
        {"status": "applied", "applied_at": None},
        {"topic": "zz-prop-no-such-topic"},
    ],
    ids=lambda b: ",".join(f"{k}={v}" for k, v in b.items()),
)
async def test_the_table_refuses_what_the_model_promises(session_for, bad):
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic, 0.2)
    await _insert(sess, topic)  # the baseline row is accepted
    await sess.execute(
        text("UPDATE steering_proposals SET status='rejected' WHERE topic=:t"), {"t": topic}
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await _insert(sess, bad.pop("topic", topic), **bad)


async def test_two_pending_proposals_for_one_topic_and_kind_are_refused(session_for):
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic, 0.2)
    await _insert(sess, topic)
    await _insert(sess, topic, kind="weight", expires_at=None, proposed_value=0.1)  # other kind
    with pytest.raises(IntegrityError):
        await _insert(sess, topic)


async def test_the_notification_type_is_allowed_by_the_database(session_for):
    sess = await session_for("rw")
    await sess.execute(
        text(
            "INSERT INTO notifications (notification_type, title) VALUES ('steering_proposal', 't')"
        )
    )
    with pytest.raises(IntegrityError):
        await sess.execute(
            text("INSERT INTO notifications (notification_type, title) VALUES ('steer', 't')")
        )


# -- the API ------------------------------------------------------------------------------


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed(session_for):
    """A committed topic with two committed pending boost proposals' worth of
    room: one proposal now, the app sees it. Removed with everything it left."""
    sess = await session_for("rw")
    await sess.rollback()
    topic = marker()
    await add_topic(sess, topic, 0.0)
    proposal = SteeringProposal(
        created_at=NOW,
        actor=sp.PROPOSER,
        topic=topic,
        kind="boost",
        current_value=1.0,
        proposed_value=sp.BOOST_FACTOR,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=2),
        reason=f"{topic} is starved.",
        evidence={"boost_hours": sp.BOOST_HOURS},
        apply_after=dt.datetime.now(dt.UTC) + dt.timedelta(hours=12),
    )
    sess.add(proposal)
    await sess.commit()
    yield topic, proposal.proposal_id
    await sess.rollback()
    await sess.execute(delete(SteeringLog).where(SteeringLog.topic == topic))
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic == topic))
    await sess.commit()


async def test_the_list_shows_pending_with_the_window(client, open_admin, committed):
    topic, proposal_id = committed
    body = (await client.get("/api/admin/proposals")).json()
    assert body["window_hours"] > 0
    (row,) = [p for p in body["pending"] if p["topic"] == topic]
    assert row["proposal_id"] == proposal_id and row["reason"] == f"{topic} is starved."


async def test_accept_applies_now_and_a_second_decision_conflicts(
    client, open_admin, committed, session_for
):
    topic, proposal_id = committed
    response = await client.post(f"/api/admin/proposals/{proposal_id}/accept")
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["decided_by"]) == ("applied", "user")
    sess = await session_for("rw")
    await sess.rollback()
    assert (await sess.get(TopicConfig, topic)).boost_factor == sp.BOOST_FACTOR

    again = await client.post(f"/api/admin/proposals/{proposal_id}/reject", json={})
    assert again.status_code == 409


async def test_reject_with_a_reason_is_logged_and_nothing_changes(
    client, open_admin, committed, session_for
):
    topic, proposal_id = committed
    response = await client.post(
        f"/api/admin/proposals/{proposal_id}/reject", json={"reason": "watching it myself"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["note"] == "watching it myself"
    sess = await session_for("rw")
    await sess.rollback()
    assert (await sess.get(TopicConfig, topic)).boost_factor is None
    (logged,) = await log_for(sess, topic)
    assert "watching it myself" in logged.reason
    recent = (await client.get("/api/admin/proposals")).json()["recent"]
    assert any(p["proposal_id"] == proposal_id and p["status"] == "rejected" for p in recent)


@pytest.mark.parametrize(
    ("path", "payload", "status"),
    [
        ("/api/admin/proposals/999999999/accept", None, 404),
        ("/api/admin/proposals/999999999/reject", {}, 404),
        ("/api/admin/proposals/{id}/reject", {"reason": "x" * 501}, 422),
    ],
)
async def test_decisions_are_refused_cleanly(client, open_admin, committed, path, payload, status):
    _, proposal_id = committed
    response = await client.post(path.format(id=proposal_id), json=payload)
    assert response.status_code == status, response.text


async def test_the_admin_gate_applies(client, monkeypatch, committed):
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    _, proposal_id = committed
    response = await client.post(f"/api/admin/proposals/{proposal_id}/accept")
    assert response.status_code == 503


# -- the worker entry point ---------------------------------------------------------------


async def test_the_report_mode_writes_nothing(session_for):
    """`--report` runs the whole pass and rolls it back: a dry run that wrote
    would be a pass nobody scheduled."""
    from worker.steerproposals import run_once, summary

    sess = await session_for("rw")
    await sess.rollback()
    before = await sess.scalar(text("SELECT count(*) FROM steering_proposals"))
    report = await run_once(write=False)
    await dispose_engines()
    after = await sess.scalar(text("SELECT count(*) FROM steering_proposals"))
    assert before == after
    assert summary(report).startswith("proposed ")
    assert f"window {report.window_hours:g}h" in summary(report)
