"""``EdgeCreate`` must reject at the API boundary what
``ck_edges_comparison_states_its_limits`` rejects in the database
(schemas/graph.py, mirrors models/graph.py ``Edge``; §7.2).

§7.2 is unambiguous: "Every comparison edge stores both the dimension of
similarity and the disanalogy." Without this mirrored validator, a bare
``comparable_to`` edge from a model would surface as an opaque integrity
error (a 500) instead of a 422 naming the missing field (AGENTS.md Testing:
"Rejection tests ... that valid input works is the weaker half").
"""

from __future__ import annotations

import pytest
from meridian_core.models.graph import COMPARISON_RELATION
from meridian_core.schemas import EdgeCreate
from pydantic import ValidationError


def _base(**overrides: object) -> dict:
    payload = {
        "from_node": 1,
        "to_node": 2,
        "relation_type": "relates_to",
        "supporting_chunk_ids": [1],
    }
    payload.update(overrides)
    return payload


def test_edge_create_accepts_an_ordinary_edge_without_limits() -> None:
    """The CHECK, and this validator, are scoped to comparable_to only."""
    edge = EdgeCreate(**_base())
    assert edge.similarity_dimension is None
    assert edge.disanalogy is None


def test_edge_create_rejects_bare_comparison() -> None:
    """A comparable_to edge with neither field set is exactly the shallow
    inference §7.2 exists to block."""
    with pytest.raises(ValidationError, match="similarity_dimension and disanalogy"):
        EdgeCreate(**_base(relation_type=COMPARISON_RELATION))


@pytest.mark.parametrize(
    "overrides",
    [
        {"similarity_dimension": "climate"},
        {"disanalogy": "governance capacity differs sharply"},
    ],
    ids=["missing_disanalogy", "missing_similarity_dimension"],
)
def test_edge_create_rejects_a_half_qualified_comparison(overrides: dict) -> None:
    """Both fields are required together — one alone is not a stated limit."""
    with pytest.raises(ValidationError, match="similarity_dimension and disanalogy"):
        EdgeCreate(**_base(relation_type=COMPARISON_RELATION, **overrides))


def test_edge_create_accepts_a_qualified_comparison() -> None:
    edge = EdgeCreate(
        **_base(
            relation_type=COMPARISON_RELATION,
            similarity_dimension="climate",
            disanalogy="governance capacity differs sharply",
        )
    )
    assert edge.similarity_dimension == "climate"
    assert edge.disanalogy == "governance capacity differs sharply"
