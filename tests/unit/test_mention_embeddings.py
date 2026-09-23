"""Embedding mention names for resolution (`B-40`).

The contract worth pinning is the failure behaviour: resolution must keep
working, on names alone, whenever the embedder is absent or down — a synthesis
run that stopped because the sidecar restarted would be the embedding signal
costing more than it adds.
"""

from __future__ import annotations

import pytest

from meridian_core.embedder import MAX_TEXTS, EmbeddingUnavailable
from meridian_core.mentions import embed_missing_entities, embed_names


class Recording:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise EmbeddingUnavailable("sidecar down")
        return [[float(len(text))] for text in texts]


async def test_no_embedder_means_names_alone() -> None:
    assert await embed_names(None, ["a", "b"]) == {}


async def test_an_unreachable_embedder_degrades_rather_than_raising() -> None:
    assert await embed_names(Recording(fail=True), ["a name"]) == {}


async def test_each_distinct_name_is_embedded_once_and_keyed_as_written() -> None:
    embedder = Recording()

    vectors = await embed_names(embedder, ["  shuttle ", "shuttle", "bus", "", "   "])

    assert embedder.calls == [["bus", "shuttle"]]
    assert set(vectors) == {"bus", "shuttle"}


async def test_more_names_than_one_request_allows_are_split() -> None:
    embedder = Recording()
    names = [f"name {i}" for i in range(MAX_TEXTS + 5)]

    vectors = await embed_names(embedder, names)

    assert [len(call) for call in embedder.calls] == [MAX_TEXTS, 5]
    assert len(vectors) == len(names)


@pytest.mark.parametrize("embedder", [None])
async def test_backfill_without_an_embedder_touches_nothing(embedder) -> None:
    assert await embed_missing_entities(object(), embedder) == 0
