"""Integration tests for edge validity (``valid_from``/``valid_to``) and
comparison edges (``similarity_dimension``/``disanalogy``) — models/graph.py
``Edge``, §7.2, §9.

CHECK constraints are asserted with raw SQL against a live Postgres: the point
is that the constraint exists in the database, not merely in the model.
``ck_edges_valid_period_ordered`` and ``ck_edges_comparison_states_its_limits``
were both declared on the ``Edge`` model and briefly missing from the actual
database — Alembic autogenerate does not detect a ``CheckConstraint`` added to
an existing table, and ``alembic check`` uses that same comparison, so neither
caught it. Only a live INSERT (here) or a live ``pg_constraint`` query (see
``test_every_model_check_constraint_exists_in_the_database`` in
``test_schema_and_roles.py``) can.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text

from meridian_core.models import Edge, Entity

pytestmark = pytest.mark.usefixtures("require_db")


async def _insert_entity(sess, name: str, node_type: str = "concept") -> int:
    row = await sess.execute(
        text(
            "INSERT INTO entities (canonical_name, node_type, schema_version, created_at) "
            "VALUES (:name, :node_type, 1, now()) RETURNING entity_id"
        ),
        {"name": name, "node_type": node_type},
    )
    return row.scalar_one()


# --------------------------------------------------------------------------
# valid_from / valid_to
# --------------------------------------------------------------------------


async def test_edge_with_inverted_valid_period_is_rejected(session_for) -> None:
    """``ck_edges_valid_period_ordered``: an AV pilot cannot end before it starts."""
    sess = await session_for("rw")
    a = await _insert_entity(sess, "ck_edges_valid_period a")
    b = await _insert_entity(sess, "ck_edges_valid_period b")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO edges (from_node, to_node, relation_type, "
                "supporting_chunk_ids, valid_from, valid_to, schema_version, created_at) "
                "VALUES (:a, :b, 'relates_to', ARRAY[1]::bigint[], "
                "'2021-01-01', '2019-01-01', 1, now())"
            ),
            {"a": a, "b": b},
        )
    message = str(exc.value).lower()
    assert "check constraint" in message and "ck_edges_valid_period_ordered" in message, (
        f"expected ck_edges_valid_period_ordered to fire, got: {exc.value}"
    )


async def test_edge_valid_period_is_independent_of_recording_timestamps(session_for) -> None:
    """valid_from/valid_to describe when the fact held, not when the edge was
    written (created_at) or derived (produced_at). Conflating "when the pilot
    ran" with "when we recorded it" made a 2019-2021 pilot indistinguishable
    from one still running — exactly the bug these columns fix (model
    docstring on ``Edge.valid_from``).
    """
    sess = await session_for("rw")
    a = Entity(canonical_name="edge validity subject", node_type="intervention")
    b = Entity(canonical_name="edge validity place", node_type="place")
    sess.add_all([a, b])
    await sess.flush()

    produced_at = dt.datetime(2026, 1, 15, tzinfo=dt.UTC)
    edge = Edge(
        from_node=a.entity_id,
        to_node=b.entity_id,
        relation_type="piloted_in",
        supporting_chunk_ids=[1],
        valid_from=dt.date(2019, 1, 1),
        valid_to=dt.date(2021, 6, 30),
        produced_at=produced_at,
    )
    sess.add(edge)
    await sess.flush()
    await sess.refresh(edge)

    assert edge.valid_from == dt.date(2019, 1, 1)
    assert edge.valid_to == dt.date(2021, 6, 30)
    assert edge.created_at.date() != edge.valid_from, (
        "created_at (when we wrote the edge) must not be conflated with valid_from "
        "(when the pilot started)"
    )
    assert edge.produced_at.date() != edge.valid_to, (
        "produced_at (when a model derived the edge) must not be conflated with "
        "valid_to (when the pilot ended)"
    )


async def test_edge_valid_period_defaults_to_open_ended(session_for) -> None:
    """Null means open-ended or unknown, not invalid — an ongoing pilot has no
    end date yet."""
    sess = await session_for("rw")
    a = Entity(canonical_name="open ended edge a", node_type="intervention")
    b = Entity(canonical_name="open ended edge b", node_type="place")
    sess.add_all([a, b])
    await sess.flush()

    edge = Edge(
        from_node=a.entity_id,
        to_node=b.entity_id,
        relation_type="piloted_in",
        supporting_chunk_ids=[1],
        valid_from=dt.date(2024, 1, 1),
    )
    sess.add(edge)
    await sess.flush()
    await sess.refresh(edge)

    assert edge.valid_from == dt.date(2024, 1, 1)
    assert edge.valid_to is None, "an ongoing pilot must be representable without an end date"


# --------------------------------------------------------------------------
# Comparison edges must state their limits (§7.2)
# --------------------------------------------------------------------------


async def test_bare_comparison_edge_is_rejected(session_for) -> None:
    """``ck_edges_comparison_states_its_limits``: "Singapore is equatorial,
    therefore Jakarta's findings apply" is the shallow inference this guards
    against. A ``comparable_to`` edge with no stated axis or limit must not
    reach the database, however the prompt that produced it was worded.
    """
    sess = await session_for("rw")
    a = await _insert_entity(sess, "bare comparison a")
    b = await _insert_entity(sess, "bare comparison b")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO edges (from_node, to_node, relation_type, "
                "supporting_chunk_ids, schema_version, created_at) "
                "VALUES (:a, :b, 'comparable_to', ARRAY[1]::bigint[], 1, now())"
            ),
            {"a": a, "b": b},
        )
    message = str(exc.value).lower()
    assert "check constraint" in message and "ck_edges_comparison_states_its_limits" in message, (
        f"expected ck_edges_comparison_states_its_limits to fire, got: {exc.value}"
    )


async def test_qualified_comparison_edge_is_accepted(session_for) -> None:
    """The positive case: a comparison that names its axis and its limit is a
    normal, insertable edge."""
    sess = await session_for("rw")
    a = await _insert_entity(sess, "qualified comparison a", "place")
    b = await _insert_entity(sess, "qualified comparison b", "place")
    edge_id = await sess.scalar(
        text(
            "INSERT INTO edges (from_node, to_node, relation_type, supporting_chunk_ids, "
            "similarity_dimension, disanalogy, schema_version, created_at) "
            "VALUES (:a, :b, 'comparable_to', ARRAY[1]::bigint[], "
            "'climate', 'governance capacity differs sharply', 1, now()) "
            "RETURNING edge_id"
        ),
        {"a": a, "b": b},
    )
    assert edge_id is not None


async def test_non_comparison_edge_does_not_require_limits(session_for) -> None:
    """The CHECK is scoped to ``comparable_to`` — an ordinary edge must not be
    forced to carry a similarity_dimension it has no use for."""
    sess = await session_for("rw")
    a = await _insert_entity(sess, "ordinary edge a")
    b = await _insert_entity(sess, "ordinary edge b")
    edge_id = await sess.scalar(
        text(
            "INSERT INTO edges (from_node, to_node, relation_type, supporting_chunk_ids, "
            "schema_version, created_at) "
            "VALUES (:a, :b, 'relates_to', ARRAY[1]::bigint[], 1, now()) RETURNING edge_id"
        ),
        {"a": a, "b": b},
    )
    assert edge_id is not None
