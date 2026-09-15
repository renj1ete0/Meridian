"""A remote embedder (task P2-17, spec §4, §12.5).

`P2-07` left the API unable to embed a query, and the two obvious fixes were
both wrong. Depending on `sentence-transformers` puts 2.3GB of weights and a
cold start inside an HTTP request path. Accepting a vector from the caller is
worse than it sounds: it is not mainly a security problem — pgvector's `<=>`
takes a vector, not SQL — it is that **a vector from a different model is
meaningless against this corpus**. Gemini's embedding of a phrase and bge-m3's
are points in unrelated spaces, and comparing them computes without erroring.
The result is plausible, ranked, confident nonsense, which is far worse than a
refusal.

So the vector has to be produced by the same model the corpus was embedded
with, on this side of the boundary. That is a sidecar, and it is the pattern
`docs/connectors.md` §4 already describes: a container with no credentials, a
client that returns None when it is absent, and a word on the health line.

**This module holds only the client.** The model runs in the worker's image —
the one place in this system that already carries it — started with a different
command. `meridian_core` is imported by the API and the orchestrator, and
neither should acquire a model dependency because a search function wanted one.

**Absent is a supported state, not a failure.** The same shape as
`Crawl4aiClient.from_env()`: no `MERIDIAN_EMBEDDER_URL` means lexical-only
retrieval, reported honestly by `SearchResult.degraded`, rather than an
exception at startup or a search that raises.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import httpx

from .logging import get_logger

log = get_logger(__name__)

DEFAULT_TIMEOUT_S = 30.0

#: A query is one short string. This cap is about batch calls from a backfill,
#: and exists so a caller cannot ask one HTTP request to hold a corpus.
MAX_TEXTS = 256


class EmbeddingUnavailable(RuntimeError):
    """The embedder is configured and did not answer.

    Distinct from *absent* on purpose, and the distinction decides what a caller
    should do. Absent is a deployment that never had one and degrades to lexical
    search; unavailable is one that has an embedder which is down, and reporting
    that as "no embedder configured" would hide an outage behind a feature flag.
    """


class RemoteEmbedder:
    """Vectors from the embedding sidecar.

    Async, unlike `worker.embeddings.Embedder`, because the callers that need it
    are already inside an event loop and the sidecar is a network hop rather
    than CPU work to push into a thread.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> RemoteEmbedder | None:
        """Build from ``MERIDIAN_EMBEDDER_URL``, or return None.

        None rather than raising: a deployment without an embedder is degraded,
        not broken, and should still answer lexical searches.
        """
        url = os.environ.get("MERIDIAN_EMBEDDER_URL", "").strip()
        if not url:
            return None
        return cls(url, timeout_s=_float_env("MERIDIAN_EMBEDDER_TIMEOUT_S", DEFAULT_TIMEOUT_S))

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthy(self) -> bool:
        try:
            response = await self._http().get(f"{self.base_url}/health", timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for ``texts``, in order.

        Raises :class:`EmbeddingUnavailable` rather than returning None or an
        empty list. A caller that got fewer vectors than texts and did not
        notice would pair the wrong vector with the wrong chunk, and nothing
        downstream can detect that — the numbers are all valid.
        """
        if not texts:
            return []
        if len(texts) > MAX_TEXTS:
            raise ValueError(f"{len(texts)} texts exceeds the {MAX_TEXTS} cap for one request")

        try:
            response = await self._http().post(
                f"{self.base_url}/embed", json={"texts": list(texts)}
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"{self.base_url}: {exc}") from exc
        except ValueError as exc:
            raise EmbeddingUnavailable(f"{self.base_url}: unreadable response") from exc

        vectors = body.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            # The alignment check. Off-by-one here attaches each vector to the
            # wrong text, and every value involved is a perfectly valid float —
            # there is no later point at which this becomes visible.
            raise EmbeddingUnavailable(
                f"{self.base_url}: asked for {len(texts)} vectors, got "
                f"{len(vectors) if isinstance(vectors, list) else 'none'}"
            )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        """One vector, for the query path."""
        return (await self.embed([text]))[0]


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("ignoring unreadable value", extra={"var": name, "value": raw})
        return default
