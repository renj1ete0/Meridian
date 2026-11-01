"""The similarity floor on the vector arm, and how a response reports it (task `B-235`).

The floor's effect on a real search is in tests/integration/test_search.py; here are the
pure rule and the plumbing from `SearchResult` to the HTTP response, where a field the
core computes and the route forgets is the failure that would go unseen.
"""

from __future__ import annotations

import pytest

from api import search_service
from meridian_core.search import VECTOR_FLOOR, SearchFilters, SearchResult, above_floor


def test_the_floor_keeps_order_and_counts_what_it_set_aside() -> None:
    neighbours = [(7, 0.81), (3, 0.62), (9, 0.55), (4, 0.549), (1, 0.31)]
    kept, below = above_floor(neighbours, 0.55)
    assert kept == [7, 3, 9]
    assert below == 2


def test_no_floor_keeps_every_neighbour() -> None:
    neighbours = [(7, 0.81), (1, 0.01)]
    assert above_floor(neighbours, None) == ([7, 1], 0)


def test_a_floor_above_everything_keeps_nothing_and_counts_everything() -> None:
    assert above_floor([(7, 0.5), (1, 0.4)], 0.99) == ([], 2)
    assert above_floor([], VECTOR_FLOOR) == ([], 0)


@pytest.fixture
def asked(monkeypatch) -> dict:
    """`paged_search` with the core search and the embedder replaced, recording the call."""
    calls: dict = {}

    async def fake_search(sess, query, **kwargs):
        calls.update(kwargs)
        return SearchResult(
            [], frozenset({"lexical", "vector"}), 0, 0, below_floor=37, nearest=0.4613, floor=0.55
        )

    async def fake_embed(query):
        return [0.0] * 4

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(search_service, "embed_query", fake_embed)
    return calls


async def test_the_response_carries_what_the_floor_set_aside(asked) -> None:
    response = await search_service.paged_search(
        None, "xqzv blorp", filters=SearchFilters(), limit=20, offset=0
    )
    assert asked["vector_floor"] == VECTOR_FLOOR
    assert response.hits == []
    assert response.below_floor == 37
    assert response.nearest_similarity == pytest.approx(0.4613)
    assert response.similarity_floor == 0.55


async def test_a_caller_can_turn_the_floor_off(asked) -> None:
    await search_service.paged_search(
        None, "xqzv blorp", filters=SearchFilters(), limit=20, offset=0, floor=False
    )
    assert asked["vector_floor"] is None


async def test_the_answer_page_takes_the_floor_and_reports_it(asked, monkeypatch) -> None:
    async def no_vocabulary(sess):
        # An empty gazetteer, not None: `load_vocabulary` always builds one, and since B-241
        # the answer reads the question's places with it.
        from meridian_core.places import Vocabulary

        return Vocabulary.build([])

    monkeypatch.setattr(search_service, "load_vocabulary", no_vocabulary)
    answer = await search_service.answer_search(
        None, "xqzv blorp", filters=SearchFilters(), candidates=100, top=5, floor=False
    )
    assert asked["vector_floor"] is None
    assert answer.below_floor == 37
