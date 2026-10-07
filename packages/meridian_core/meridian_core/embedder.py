"""A remote embedder: the client for the sidecar (task P2-17, spec §4, §12.5).

A query vector must come from the model the corpus was embedded with, so the API asks
the sidecar rather than loading a model or accepting a caller's vector. No
`MERIDIAN_EMBEDDER_URL` is a supported state (lexical-only search, reported as
`degraded`). See docs/features/embedding.md#the-service.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import httpx

from .logging import get_logger

log = get_logger(__name__)

#: How long to wait on a *query* — one short string, on the read path, with a
#: reader watching. Deliberately impatient: a search that hangs for a minute is
#: worse than one that reports a degraded arm.
DEFAULT_TIMEOUT_S = 30.0

#: How long one text may take, used to size a batch's timeout (`B-27`). Generous
#: on purpose; see docs/features/embedding.md#timeouts.
SECONDS_PER_TEXT = 2.0

#: A query is one short string. This cap is about batch calls from a backfill,
#: and exists so a caller cannot ask one HTTP request to hold a corpus.
MAX_TEXTS = 256


class EmbeddingUnavailable(RuntimeError):
    """The embedder is configured and did not answer.

    Distinct from *absent* (never configured, lexical search): this is an outage.
    """


class EmbedderMismatch(EmbeddingUnavailable):
    """The sidecar answered, with a different model than this corpus was built on.

    A subclass of "unavailable": callers degrade or fall back the same way. See
    docs/features/embedding.md#design-choices.
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
        expect_model: str | None = None,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        #: Sent as a bearer token when the sidecar asks for one (`P3-12`).
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout_s
        self._client = client
        self._owns_client = client is None
        #: Which model this corpus was embedded with. When set, a sidecar naming a
        #: different one is refused rather than used.
        self.expect_model = expect_model

    @classmethod
    def from_env(cls) -> RemoteEmbedder | None:
        """Build from ``MERIDIAN_EMBEDDER_URL``, or return None.

        None rather than raising: a deployment without an embedder is degraded,
        not broken, and should still answer lexical searches.
        """
        url = os.environ.get("MERIDIAN_EMBEDDER_URL", "").strip()
        if not url:
            return None
        return cls(
            url,
            timeout_s=_float_env("MERIDIAN_EMBEDDER_TIMEOUT_S", DEFAULT_TIMEOUT_S),
            # The same variable the worker builds its own model from, so the two
            # sides of the boundary read one setting rather than two that can
            # disagree silently.
            expect_model=os.environ.get("MERIDIAN_EMBED_MODEL", "").strip() or None,
            token=os.environ.get("MERIDIAN_EMBEDDER_TOKEN", "").strip() or None,
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthy(self) -> bool:
        return await self.describe() is not None

    async def describe(self) -> dict | None:
        """What the sidecar says it is, or None when it cannot be reached.

        Returned rather than reduced to a boolean because "up" is not the
        question that matters: a sidecar running a different model is up, and
        using it is worse than having none.
        """
        try:
            response = await self._http().get(f"{self.base_url}/health", timeout=5.0)
            if response.status_code != 200:
                return None
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return body if isinstance(body, dict) else None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for ``texts``, in order.

        Raises :class:`EmbeddingUnavailable` rather than returning None or a short
        list, which a caller could misalign without noticing.
        """
        if not texts:
            return []
        if len(texts) > MAX_TEXTS:
            raise ValueError(f"{len(texts)} texts exceeds the {MAX_TEXTS} cap for one request")

        try:
            response = await self._http().post(
                f"{self.base_url}/embed",
                json={"texts": list(texts)},
                headers=self._headers,
                # Sized to the request: a batch is N times the work of one text.
                timeout=self._batch_timeout(len(texts)),
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"{self.base_url}: {exc}") from exc
        except ValueError as exc:
            raise EmbeddingUnavailable(f"{self.base_url}: unreadable response") from exc

        named = body.get("model")
        if self.expect_model and named and named != self.expect_model:
            # Every response, not once at startup: a sidecar can be restarted with a
            # different model under a running client.
            raise EmbedderMismatch(
                f"{self.base_url} is serving {named!r}; this corpus is embedded "
                f"with {self.expect_model!r}. Mixed vectors rank nonsense."
            )

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

    def _batch_timeout(self, count: int) -> float:
        """The configured timeout, or what this many texts plausibly need.

        A floor rather than a replacement, so setting `MERIDIAN_EMBEDDER_TIMEOUT_S`
        higher for a slow box still raises it for every call.
        """
        return max(self._timeout, count * SECONDS_PER_TEXT)

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
