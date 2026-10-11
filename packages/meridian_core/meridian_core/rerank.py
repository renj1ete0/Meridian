"""Re-ordering the top of a search by a cross-encoder (task `B-167`).

Only the first :data:`RERANK_DEPTH` hits are re-ordered; the rest keep the fused order
after them. Reranking that cannot run leaves the order as it was and says so, since a
search that still answers is better than one that fails. See
docs/features/search.md#reranking.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .embedder import EmbeddingUnavailable, RerankingOff
from .logging import get_logger

log = get_logger(__name__)

#: How many of the fused hits are re-ordered. Measured on 2026-10-11: the top 30 kept 8 of
#: the top ten reranking all 50 found, at three fifths of the time.
RERANK_DEPTH = 30


class Scorer(Protocol):
    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]: ...


class HasText(Protocol):
    @property
    def text(self) -> str: ...


def reorder[T](hits: Sequence[T], scores: Sequence[float]) -> list[T]:
    """``hits[:len(scores)]`` by score, highest first, ties in their old order; the rest after.

    Refuses scores that do not cover a prefix of the hits: a misaligned score would move
    a passage silently.
    """
    if len(scores) > len(hits):
        raise ValueError(f"{len(scores)} scores for {len(hits)} hits")
    head = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return [hits[i] for i in head] + list(hits[len(scores) :])


async def rerank_hits[T: HasText](
    scorer: Scorer | None, query: str, hits: Sequence[T], *, depth: int = RERANK_DEPTH
) -> tuple[list[T], bool]:
    """The hits with their top ``depth`` re-ordered, and whether that happened.

    False, with the order unchanged, when there is no scorer, reranking is off, or the
    sidecar failed (logged as an outage).
    """
    head = list(hits[:depth])
    if scorer is None or len(head) < 2:
        return list(hits), False
    try:
        scores = await scorer.rerank(query, [hit.text for hit in head])
    except RerankingOff:
        return list(hits), False
    except EmbeddingUnavailable as exc:
        log.warning("reranker unavailable; fused order kept", extra={"reason": str(exc)})
        return list(hits), False
    return reorder(hits, scores), True
