"""bge-m3 embeddings (task P2-01, spec §4, §6.1).

§4 picks bge-m3 for one reason and it is worth restating, because it is the
reason not to swap it for something smaller when the first arm64 build is slow:
serious literature for the comparison set is substantially non-English (§14.1's
language coverage note). An English-only embedding model would systematically
bias the corpus toward Western sources while every measurement of it looked
fine.

**No model is loaded until something asks for a vector.** Importing this module
must stay free: `worker.main` imports the package tree, the fetch loop never
embeds, and a 2.3GB model load at import would put it in the crawler's memory
budget for nothing. The load happens on the first `embed()` and is held after.

**Batching is not an optimisation here, it is the whole cost model.** Encoding
one chunk at a time on an RK3588 wastes almost all of the work: the model runs
the same graph either way, and the per-call overhead dominates. Batches also
bound memory, which is the constraint that actually bites on a 16GB board shared
with Postgres.

**The dimension is asserted, not assumed.** `chunks.embedding` is
`Vector(1024)`, so a model returning 768 produces a database error somewhere far
from the cause — or worse, silently succeeds against a table someone widened.
The check is cheap and it fires at the point the wrong model was configured.

**Normalised, always.** pgvector's cosine operator does not require unit vectors
but `<=>` is cheapest and the novelty gate (`P2-03`) compares raw cosine
similarity against a fixed 0.95 threshold. A threshold means nothing if the
vectors behind it are sometimes normalised and sometimes not.
"""

from __future__ import annotations

import dataclasses
import os
import threading
from collections.abc import Sequence
from typing import Protocol

from meridian_core.logging import get_logger
from meridian_core.models.source import EMBEDDING_DIM

log = get_logger(__name__)

#: §4's choice. Pinned by name rather than by revision because the weights are
#: fetched from a cache the operator controls; see `MERIDIAN_EMBED_MODEL`.
DEFAULT_MODEL = "BAAI/bge-m3"

#: How many chunks go through the model at once. Sized for the Pi rather than
#: for a GPU: bge-m3 at 8192 tokens is memory-hungry, and a batch that swaps is
#: far slower than two batches that do not.
DEFAULT_BATCH_SIZE = 8

#: bge-m3 accepts 8192 tokens, which is far more than `chunk_text` produces
#: (§P2-02 caps a chunk at 2000 characters). Set below the model's ceiling
#: anyway: a caller embedding something other than a chunk — a query, a node
#: description — should be truncated deterministically rather than by whatever
#: the tokeniser happens to do.
DEFAULT_MAX_TOKENS = 1024


class EmbeddingError(RuntimeError):
    """The model could not be loaded or could not produce vectors."""


class Embedder(Protocol):
    """What the rest of the system needs from an embedding model.

    A protocol rather than the concrete class so the novelty gate (`P2-03`),
    the search path (`P2-06`) and their tests can be built and run without a
    2.3GB download — and so a different runtime (ONNX, a remote service) is a
    substitution rather than a rewrite.
    """

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclasses.dataclass(frozen=True)
class EmbedderSettings:
    """Everything about the model that is deployment rather than code."""

    model_name: str = DEFAULT_MODEL
    batch_size: int = DEFAULT_BATCH_SIZE
    max_tokens: int = DEFAULT_MAX_TOKENS
    dimensions: int = EMBEDDING_DIM
    #: Where the weights live. Left to the library's default when unset, which
    #: is `~/.cache/huggingface` — worth setting explicitly in a container,
    #: where that is either a read-only layer or a volume nobody mounted.
    cache_dir: str | None = None
    #: `cpu`, `cuda`, `mps`. None lets sentence-transformers choose, which on
    #: the Pi means CPU and on a workstation means the GPU is used for the
    #: backfill without anyone configuring it.
    device: str | None = None

    @classmethod
    def from_env(cls) -> EmbedderSettings:
        return cls(
            model_name=os.environ.get("MERIDIAN_EMBED_MODEL") or DEFAULT_MODEL,
            batch_size=_int_env("MERIDIAN_EMBED_BATCH_SIZE", DEFAULT_BATCH_SIZE),
            max_tokens=_int_env("MERIDIAN_EMBED_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            cache_dir=os.environ.get("MERIDIAN_EMBED_CACHE") or None,
            device=os.environ.get("MERIDIAN_EMBED_DEVICE") or None,
        )


class BGEEmbedder:
    """bge-m3 through sentence-transformers.

    Import-safe: `sentence_transformers` is imported inside :meth:`_model`, not
    at module scope, so a worker built without the `embed` extra can still
    import this module and report the absence rather than failing at startup
    with an ImportError from a dependency it was never given.
    """

    def __init__(self, settings: EmbedderSettings | None = None) -> None:
        self._settings = settings or EmbedderSettings()
        self._loaded: object | None = None
        # The backfill embeds in a worker thread so it does not block the event
        # loop; two batches arriving together must not each start a 2.3GB load.
        self._lock = threading.Lock()

    @property
    def settings(self) -> EmbedderSettings:
        return self._settings

    @property
    def dimensions(self) -> int:
        return self._settings.dimensions

    @property
    def loaded(self) -> bool:
        return self._loaded is not None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for ``texts``, in order, normalised to unit length.

        Empty input returns an empty list without loading anything — a backfill
        pass that finds nothing to do should not pay 2.3GB to discover that.
        """
        if not texts:
            return []
        if any(not text or not text.strip() for text in texts):
            # An empty chunk embeds to whatever the model does with padding,
            # which is a vector that will match other empty things and nothing
            # meaningful. `replace_chunks` already drops these; a caller that
            # got one past it should hear about it.
            raise ValueError("cannot embed an empty string")

        model = self._model()
        try:
            vectors = model.encode(  # type: ignore[attr-defined]
                list(texts),
                batch_size=self._settings.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # pragma: no cover - depends on the runtime
            raise EmbeddingError(f"{type(exc).__name__}: {exc}") from exc

        out = [[float(value) for value in vector] for vector in vectors]
        for vector in out:
            if len(vector) != self._settings.dimensions:
                raise EmbeddingError(
                    f"{self._settings.model_name} returned {len(vector)} dimensions, "
                    f"but chunks.embedding is Vector({self._settings.dimensions}); "
                    "the configured model does not match the schema"
                )
        return out

    def _model(self) -> object:
        """Load on first use, once, however many threads ask at the same time."""
        if self._loaded is not None:
            return self._loaded
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed; the worker image is built "
                    "without the `embed` extra, which is deliberate — install "
                    "meridian-worker[embed] in whatever runs the backfill"
                ) from exc

            log.info(
                "loading embedding model",
                extra={
                    "model": self._settings.model_name,
                    "device": self._settings.device or "auto",
                    "cache_dir": self._settings.cache_dir,
                },
            )
            try:
                model = SentenceTransformer(
                    self._settings.model_name,
                    device=self._settings.device,
                    cache_folder=self._settings.cache_dir,
                )
            except Exception as exc:
                raise EmbeddingError(
                    f"could not load {self._settings.model_name}: {type(exc).__name__}: {exc}"
                ) from exc

            model.max_seq_length = self._settings.max_tokens
            # sentence-transformers renamed this in 6.x and kept the old name as
            # a deprecated alias. Asking for the new one first means the warning
            # stops without pinning a floor that would drop older installs.
            accessor = getattr(model, "get_embedding_dimension", None) or getattr(
                model, "get_sentence_embedding_dimension", None
            )
            reported = accessor() if accessor else None
            if reported is not None and reported != self._settings.dimensions:
                # Caught at load rather than after a batch: the alternative is a
                # pgvector error a thousand chunks later, pointing at the
                # insert rather than at the misconfiguration.
                raise EmbeddingError(
                    f"{self._settings.model_name} embeds to {reported} dimensions, "
                    f"but chunks.embedding is Vector({self._settings.dimensions})"
                )
            log.info(
                "embedding model ready",
                extra={"model": self._settings.model_name, "dimensions": reported},
            )
            self._loaded = model
            return model


class FakeEmbedder:
    """Deterministic vectors with no model, for tests and for wiring.

    Not a mock: it returns a real unit vector derived from the text, so
    identical text embeds identically, different text embeds differently, and
    cosine similarity behaves — which is what the novelty gate (`P2-03`) and the
    search path (`P2-06`) need in order to be testable at all without a 2.3GB
    download in CI.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIM) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import hashlib
        import math

        out: list[list[float]] = []
        for text in texts:
            if not text or not text.strip():
                raise ValueError("cannot embed an empty string")
            # A hash stream widened to the dimension. Deterministic, dependent
            # on the whole text, and cheap.
            raw: list[float] = []
            counter = 0
            while len(raw) < self._dimensions:
                digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
                raw.extend(byte / 255.0 - 0.5 for byte in digest)
                counter += 1
            vector = raw[: self._dimensions]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            out.append([value / norm for value in vector])
        return out


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive, got {value}")
    return value
