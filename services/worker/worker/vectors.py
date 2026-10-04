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
import os
import time
from collections.abc import Sequence
from typing import Protocol

from meridian_core.embedder import (
    MAX_TEXTS,
    EmbedderMismatch,
    EmbeddingUnavailable,
    RemoteEmbedder,
)
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
        remote_only: bool = False,
    ) -> None:
        self._remote = remote
        #: Never load the model here (`P3-12`): the sidecar is on another machine
        #: because this one cannot spare the memory. A failed call is a failed
        #: batch, retried next pass, not a 2.3 GB load beside Postgres.
        self._remote_only = remote_only
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

    async def _ask_remote(self, texts: Sequence[str]) -> list[list[float]]:
        """The sidecar's vectors, asked for at most ``MAX_TEXTS`` at a time (`B-130`).

        The backfill's batch is ``MERIDIAN_EMBED_CHUNK_BATCH`` and the sidecar takes
        ``MAX_TEXTS`` per request. Asked for more in one call, the client raised
        ValueError, which reads here as "the sidecar is unusable" — so raising the
        batch for a GPU quietly moved embedding onto this process's CPU, or with
        remote-only failed every batch.
        """
        assert self._remote is not None
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_TEXTS):
            vectors.extend(await self._remote.embed(texts[start : start + MAX_TEXTS]))
        return vectors

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        if self._remote is not None and self._remote_only:
            try:
                return await self._ask_remote(texts)
            except (EmbeddingUnavailable, ValueError) as exc:
                # EmbeddingError, which the backfill counts and steps past.
                raise EmbeddingError(f"remote-only embedder unavailable: {exc}") from exc

        if self._remote is not None:
            try:
                return await self._ask_remote(texts)
            except EmbedderMismatch as exc:
                # Distinct from "down", and worth its own line: somebody has
                # pointed this at the wrong service, and the corpus is one
                # successful batch away from being unsearchable in a way nothing
                # reports.
                self._give_up_on_remote(f"model mismatch — {exc}")
            except (EmbeddingUnavailable, ValueError) as exc:
                self._give_up_on_remote(str(exc))

        return await self._fallback().embed(texts)


#: How long to keep asking a configured sidecar before loading the model here
#: (`B-76`). A joint restart brings both up at once, and the sidecar takes most
#: of a minute to load its weights: asking once, the backfill gave up in seconds
#: and loaded a second copy that then competed with the sidecar for the CPU.
SIDECAR_WAIT_S = 180.0
SIDECAR_POLL_S = 5.0


def remote_only_from_env() -> bool:
    """Whether ``MERIDIAN_EMBED_REMOTE_ONLY`` is set (`P3-12`).

    The model is on another machine and must never be loaded in this one.
    """
    return os.environ.get("MERIDIAN_EMBED_REMOTE_ONLY", "").strip().lower() in {"1", "true", "yes"}


async def build_embedder(
    remote: RemoteEmbedder | None = None,
    *,
    wait_s: float | None = None,
    poll_s: float = SIDECAR_POLL_S,
) -> PreferRemote:
    """The backfill's embedder, with a probe to say which path it took.

    The probe is for the log, not for correctness — `embed` falls back on its
    own. It is here because "which model is this pass using" is the first
    question asked of an unexpectedly slow or unexpectedly hungry backfill, and
    without the line the answer is a guess.

    A configured sidecar is asked until it answers or ``wait_s`` passes
    (``MERIDIAN_EMBEDDER_WAIT_S``, default :data:`SIDECAR_WAIT_S`).
    """
    candidate = remote if remote is not None else RemoteEmbedder.from_env()
    remote_only = remote_only_from_env()
    if candidate is None and remote_only:
        raise EmbeddingError(
            "MERIDIAN_EMBED_REMOTE_ONLY is set but MERIDIAN_EMBEDDER_URL is not: "
            "nothing to embed with, and loading the model here is what was ruled out"
        )
    if candidate is None:
        log.info("no embedding sidecar configured; the model loads in this process")
        return PreferRemote(None)

    if wait_s is None:
        wait_s = float(os.environ.get("MERIDIAN_EMBEDDER_WAIT_S") or SIDECAR_WAIT_S)
    deadline = time.monotonic() + max(wait_s, 0.0)
    described = await candidate.describe()
    while described is None and time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        described = await candidate.describe()
    if described is None and remote_only:
        # Keep asking the sidecar, batch by batch; never load the model here.
        log.warning(
            "embedding sidecar did not answer; remote-only, so batches wait for it",
            extra={"url": candidate.base_url},
        )
        return PreferRemote(candidate, remote_only=True)
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
    return PreferRemote(candidate, remote_only=remote_only)


__all__ = [
    "AsyncEmbedder",
    "EmbeddingError",
    "LocalEmbedder",
    "PreferRemote",
    "build_embedder",
]
