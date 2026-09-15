"""Where the backfill's vectors come from (task P2-19, spec §4, §12.5).

`P2-17` put one copy of the model resident in a sidecar for the query path, and
`worker.embed` went on constructing a `BGEEmbedder` of its own — so a stack
running both held two copies of 2.3GB of weights on a machine chosen for being
small. That is the whole of this module: ask the sidecar first, and load locally
only when there is no sidecar to ask.

**Falling back is right, and falling back silently is not.** A backfill can
afford to wait for a model to load; what it cannot afford is to appear to be
using the sidecar while quietly loading a second copy, because the symptom is
memory pressure with no line in the log that explains it. So the switch is
logged once, loudly, with the reason.

**A sidecar running a different model is refused, not used.** It is the one
failure in this area that cannot be detected afterwards: `<=>` accepts any two
vectors of the right width and returns a number, so a column holding two models'
vectors ranks confident nonsense forever. The local model is the right one, so a
mismatch falls back rather than failing — but it is reported as what it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from meridian_core.embedder import EmbedderMismatch, EmbeddingUnavailable, RemoteEmbedder
from meridian_core.logging import get_logger

from .embeddings import BGEEmbedder, Embedder, EmbedderSettings, EmbeddingError

log = get_logger(__name__)


class AsyncEmbedder(Protocol):
    """What the backfill needs. Async, because one of the two is a network call."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """The in-process model, driven off the event loop.

    The thread is not an optimisation. The model is synchronous and CPU-bound,
    and running it on the loop would block the signal handler that stops the
    pass — so a `SIGTERM` during a batch would be honoured whenever the batch
    happened to finish, which on a Pi is not a short wait.
    """

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder or BGEEmbedder(EmbedderSettings.from_env())

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embedder.embed, list(texts))


class PreferRemote:
    """The sidecar while it answers; the local model once it does not.

    **The switch is one-way within a process.** Once the local model is loaded
    the memory is already spent, so going back to the sidecar mid-pass would buy
    nothing and cost a reload's worth of uncertainty about which produced what.
    The next pass starts by asking the sidecar again.
    """

    def __init__(
        self,
        remote: RemoteEmbedder | None,
        *,
        local: AsyncEmbedder | None = None,
        local_factory=None,
    ) -> None:
        self._remote = remote
        self._local = local
        # A factory rather than an instance, because constructing the local
        # embedder is what this class exists to avoid doing unnecessarily —
        # `BGEEmbedder` is cheap to make and expensive on first use, and a test
        # that passed one in eagerly would not be testing the avoidance.
        self._local_factory = local_factory or LocalEmbedder

    @property
    def using_remote(self) -> bool:
        return self._remote is not None

    def _fallback(self) -> AsyncEmbedder:
        if self._local is None:
            self._local = self._local_factory()
        return self._local

    def _give_up_on_remote(self, reason: str) -> None:
        self._remote = None
        log.warning(
            "embedding sidecar unusable; loading the model in this process instead",
            extra={"reason": reason},
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        if self._remote is not None:
            try:
                return await self._remote.embed(texts)
            except EmbedderMismatch as exc:
                # Distinct from "down", and worth its own line: somebody has
                # pointed this at the wrong service, and the corpus is one
                # successful batch away from being unsearchable in a way nothing
                # reports.
                self._give_up_on_remote(f"model mismatch — {exc}")
            except (EmbeddingUnavailable, ValueError) as exc:
                self._give_up_on_remote(str(exc))

        return await self._fallback().embed(texts)


async def build_embedder(remote: RemoteEmbedder | None = None) -> PreferRemote:
    """The backfill's embedder, with one probe to say which path it took.

    The probe is for the log, not for correctness — `embed` falls back on its
    own. It is here because "which model is this pass using" is the first
    question asked of an unexpectedly slow or unexpectedly hungry backfill, and
    without the line the answer is a guess.
    """
    candidate = remote if remote is not None else RemoteEmbedder.from_env()
    if candidate is None:
        log.info("no embedding sidecar configured; the model loads in this process")
        return PreferRemote(None)

    described = await candidate.describe()
    if described is None:
        log.warning(
            "embedding sidecar did not answer; the model loads in this process",
            extra={"url": candidate.base_url},
        )
        return PreferRemote(None)

    log.info(
        "embedding through the sidecar",
        extra={
            "url": candidate.base_url,
            "model": described.get("model"),
            "loaded": described.get("loaded"),
        },
    )
    return PreferRemote(candidate)


__all__ = [
    "AsyncEmbedder",
    "EmbeddingError",
    "LocalEmbedder",
    "PreferRemote",
    "build_embedder",
]
