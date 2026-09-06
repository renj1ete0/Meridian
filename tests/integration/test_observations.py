"""Integration tests for the ``observations`` table (models/graph.py
``Observation``; §14.3). Edge validity (``valid_from``/``valid_to``) lives in
``test_edges.py``.

CHECK constraints are asserted with raw SQL against a live Postgres, not through
the ORM or the DTO, because the thing being tested is that the constraint
exists in the database — the same class of bug AGENTS.md calls out: "Three
Phase 0 bugs were 'the constraint exists' assumptions that turned out to be
false." A mock or SQLite would report all of them as passing.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text

from meridian_core.models import Entity, Observation
from meridian_core.schemas import ObservationRead

pytestmark = pytest.mark.usefixtures("require_db")


async def _insert_entity(sess, name: str, node_type: str = "finding") -> int:
    row = await sess.execute(
        text(
            "INSERT INTO entities (canonical_name, node_type, schema_version, created_at) "
            "VALUES (:name, :node_type, 1, now()) RETURNING entity_id"
        ),
        {"name": name, "node_type": node_type},
    )
    return row.scalar_one()


# --------------------------------------------------------------------------
# CHECK constraints must actually fire in Postgres
# --------------------------------------------------------------------------


async def test_observation_without_a_value_is_rejected(session_for) -> None:
    """``ck_observations_has_a_value``: a reading with neither value_numeric
    nor value_text is not a measurement (model docstring: "none of that fits
    ... attribute_values")."""
    sess = await session_for("rw")
    subject = await _insert_entity(sess, "ck_has_a_value subject")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO observations (subject_entity_id, metric, "
                "supporting_chunk_ids, schema_version, created_at) "
                "VALUES (:subject, 'av_share', ARRAY[1]::bigint[], 1, now())"
            ),
            {"subject": subject},
        )
    message = str(exc.value).lower()
    assert "check constraint" in message and "ck_observations_has_a_value" in message, (
        f"expected ck_observations_has_a_value to fire, got: {exc.value}"
    )


async def test_observation_with_inverted_period_is_rejected(session_for) -> None:
    """``ck_observations_period_ordered``: an interval that ends before it
    starts never happened."""
    sess = await session_for("rw")
    subject = await _insert_entity(sess, "ck_period_ordered subject")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO observations (subject_entity_id, metric, value_numeric, "
                "period_start, period_end, supporting_chunk_ids, schema_version, created_at) "
                "VALUES (:subject, 'av_share', 1.0, '2021-01-01', '2019-01-01', "
                "ARRAY[1]::bigint[], 1, now())"
            ),
            {"subject": subject},
        )
    message = str(exc.value).lower()
    assert "check constraint" in message and "ck_observations_period_ordered" in message, (
        f"expected ck_observations_period_ordered to fire, got: {exc.value}"
    )


async def test_observation_supporting_chunk_ids_cannot_be_null(session_for) -> None:
    """An observation without a justifying chunk is not assertable (spec §2
    principle 3) — the same rule ``edges`` and ``attribute_values`` enforce,
    now on the new table."""
    sess = await session_for("rw")
    subject = await _insert_entity(sess, "no provenance subject")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO observations (subject_entity_id, metric, value_numeric, "
                "supporting_chunk_ids, schema_version, created_at) "
                "VALUES (:subject, 'av_share', 1.0, NULL, 1, now())"
            ),
            {"subject": subject},
        )
    assert "null" in str(exc.value).lower()


# --------------------------------------------------------------------------
# Design intent: one subject entity, many observations (§14.3, model docstring)
# --------------------------------------------------------------------------


async def test_one_subject_entity_accumulates_many_observations(session_for) -> None:
    """A time series lives against one stable node, not one node per reading.

    Modelling each reading as its own ``finding`` entity would produce
    near-identical ``canonical_name`` values that entity resolution — which
    matches on string similarity, embedding cosine, and shared neighbours
    (§5.5) — would eventually merge, silently collapsing distinct quarters into
    one. This is the failure the ``Observation`` table exists to avoid.
    """
    sess = await session_for("rw")
    subject = Entity(canonical_name="AV share of Phoenix taxi trips", node_type="finding")
    sess.add(subject)
    await sess.flush()

    readings = [
        (dt.date(2019, 1, 1), dt.date(2019, 12, 31), 1.1),
        (dt.date(2021, 1, 1), dt.date(2021, 12, 31), 3.2),
        (dt.date(2020, 1, 1), dt.date(2020, 12, 31), 2.4),
    ]
    for start, end, value in readings:
        sess.add(
            Observation(
                subject_entity_id=subject.entity_id,
                metric="av_share_of_taxi_trips",
                value_numeric=value,
                unit="percent",
                denominator="taxi trips",
                period_start=start,
                period_end=end,
                supporting_chunk_ids=[1],
            )
        )
    await sess.flush()

    rows = (
        await sess.execute(
            text(
                "SELECT period_start, value_numeric FROM observations "
                "WHERE subject_entity_id = :subject ORDER BY period_start"
            ),
            {"subject": subject.entity_id},
        )
    ).all()
    assert [r.value_numeric for r in rows] == [1.1, 2.4, 3.2], (
        "all three readings against one subject must persist and come back in period order"
    )

    entity_count = await sess.scalar(
        text("SELECT count(*) FROM entities WHERE entity_id = :subject"),
        {"subject": subject.entity_id},
    )
    assert entity_count == 1, "three observations must not require three subject entities"


# --------------------------------------------------------------------------
# ObservationRead builds from a persisted ORM row
# --------------------------------------------------------------------------


async def test_observation_read_builds_from_a_persisted_orm_row(session_for) -> None:
    """The Read DTO must round-trip a real row, not just a hand-built dict."""
    sess = await session_for("rw")
    subject = Entity(canonical_name="observation read subject", node_type="finding")
    sess.add(subject)
    await sess.flush()

    obs = Observation(
        subject_entity_id=subject.entity_id,
        metric="av_share_of_taxi_trips",
        value_numeric=4.5,
        unit="percent",
        denominator="taxi trips",
        qualifiers={"segment": "students", "time_of_day": "peak"},
        supporting_chunk_ids=[1, 2],
    )
    sess.add(obs)
    await sess.flush()
    await sess.refresh(obs)

    read = ObservationRead.model_validate(obs)
    assert read.observation_id == obs.observation_id
    assert read.subject_entity_id == subject.entity_id
    assert read.metric == "av_share_of_taxi_trips"
    assert read.value_numeric == 4.5
    assert read.supporting_chunk_ids == [1, 2]
    assert read.qualifiers == {"segment": "students", "time_of_day": "peak"}
    assert read.created_at == obs.created_at
