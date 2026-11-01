"""The headline filter on watched sources: embeddings, or the vocabulary when they fail (`B-251`).

The basis (topic prototypes, passage centroids, the off-topic centroid) is built once and
cached for :data:`BASIS_TTL_S`; each read embeds only its new headlines. When there is no
embedder, or it fails, :meth:`HeadlineFilter.topics_for` answers None and the caller falls
back to the vocabulary rule: never everything queued, never a failed read. See
docs/features/discovery.md#headline-filter.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.embedder import MAX_TEXTS
from meridian_core.headlinetopics import HeadlineBasis, offtopic_centroid, topic_centroids
from meridian_core.logging import get_logger
from meridian_core.topiclabels import REFERENCE_TEXTS, Basis, basis_fingerprint, load_prototypes

log = get_logger(__name__)

#: How long a built basis is used before it is built again: topics, their vocabulary and
#: their passages' labels change over days, and a rebuild costs one small embedding call
#: and a few sampled queries.
BASIS_TTL_S = 3600.0

#: After a failed build, how long reads use the vocabulary before trying again, so an
#: embedder that is down is not asked to build on every read.
RETRY_AFTER_FAILURE_S = 300.0


class Embeds(Protocol):
    """What the filter needs from an embedder."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class HeadlineFilter:
    """Topics for watched headlines by embedding, with a cached basis."""

    def __init__(
        self,
        embedder: Embeds | None,
        *,
        session_factory: SessionFactory,
        ttl_s: float = BASIS_TTL_S,
        retry_s: float = RETRY_AFTER_FAILURE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._embedder = embedder
        self._session_factory = session_factory
        self._ttl_s = ttl_s
        self._retry_s = retry_s
        self._clock = clock
        self._basis: HeadlineBasis | None = None
        self._built_at = 0.0
        self._failed_at: float | None = None

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        assert self._embedder is not None
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_TEXTS):
            vectors.extend(await self._embedder.embed(texts[start : start + MAX_TEXTS]))
        if len(vectors) != len(texts):
            raise ValueError(f"asked for {len(texts)} vectors and got {len(vectors)}")
        return vectors

    async def _build(self) -> HeadlineBasis | None:
        async with self._session_factory() as sess:
            prototypes = await load_prototypes(sess)
            if not prototypes:
                return None
            topics = [p.topic for p in prototypes]
            centroids = await topic_centroids(sess, topics)
            offtopic = await offtopic_centroid(sess)
        texts = [p.text for p in prototypes] + list(REFERENCE_TEXTS)
        vectors = await self._embed(texts)
        basis = Basis.build(
            dict(zip(topics, vectors[: len(prototypes)], strict=True)),
            vectors[len(prototypes) :],
            basis_fingerprint(prototypes, None),
        )
        log.info(
            "headline basis built",
            extra={
                "topics": len(topics),
                "centroids": len(centroids),
                "offtopic_centroid": offtopic is not None,
            },
        )
        return HeadlineBasis.build(basis, centroids, offtopic)

    async def basis(self) -> HeadlineBasis | None:
        """The cached basis, rebuilt when stale; None when it cannot be built now."""
        now = self._clock()
        if self._basis is not None and now - self._built_at < self._ttl_s:
            return self._basis
        if self._failed_at is not None and now - self._failed_at < self._retry_s:
            # A stale basis still beats the vocabulary while the embedder is away.
            return self._basis
        try:
            basis = await self._build()
        except Exception as exc:  # any failure here means "use the vocabulary"
            self._failed_at = now
            log.warning("headline basis not built", extra={"error": f"{type(exc).__name__}: {exc}"})
            return self._basis
        if basis is None:
            self._failed_at = now
            return None
        self._basis, self._built_at, self._failed_at = basis, now, None
        return basis

    async def topics_for(self, headlines: Sequence[str]) -> list[tuple[str, ...]] | None:
        """Each headline's topics, best first; None when embedding is unavailable."""
        if self._embedder is None:
            return None
        if not headlines:
            return []
        basis = await self.basis()
        if basis is None:
            return None
        try:
            vectors = await self._embed(list(headlines))
        except Exception as exc:  # a failed read is worse than the vocabulary
            log.warning("headlines not embedded", extra={"error": f"{type(exc).__name__}: {exc}"})
            return None
        return [basis.topics_for(vector) for vector in vectors]


__all__ = ["BASIS_TTL_S", "RETRY_AFTER_FAILURE_S", "Embeds", "HeadlineFilter"]
