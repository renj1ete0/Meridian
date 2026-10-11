"""The cross-encoder that re-orders search results (task `B-167`).

Off unless `MERIDIAN_RERANK_MODEL` names one. It reads the question and a passage
together, which a vector made from each alone cannot. Loaded lazily, like the embedder,
and served by the same sidecar. See docs/features/search.md#reranking.
"""

from __future__ import annotations

import dataclasses
import os
import threading
from collections.abc import Sequence

from meridian_core.logging import get_logger

from .embeddings import EmbeddingError, cpu_has_bf16, on_accelerator

log = get_logger(__name__)

#: The model the 2026-10-11 grading chose: as good as the LLM-based reranker it was
#: measured against, a third of its time, and the fewest off-topic passages.
RECOMMENDED_MODEL = "BAAI/bge-reranker-v2-m3"

#: Tokens of question and passage together. A passage is at most 2,000 characters;
#: measured, cutting to 512 changed almost nothing because most are shorter.
DEFAULT_MAX_TOKENS = 1024

DEFAULT_BATCH_SIZE = 8


@dataclasses.dataclass(frozen=True)
class RerankerSettings:
    """The reranker as deployment rather than code. ``model_name`` None means off."""

    model_name: str | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    batch_size: int = DEFAULT_BATCH_SIZE
    cache_dir: str | None = None
    device: str | None = None
    dtype: str = "float32"

    @property
    def enabled(self) -> bool:
        return self.model_name is not None

    @classmethod
    def from_env(cls) -> RerankerSettings:
        """Shares the embedder's cache and, unless told otherwise, its device."""
        device = (
            os.environ.get("MERIDIAN_RERANK_DEVICE") or os.environ.get("MERIDIAN_EMBED_DEVICE")
        ) or None
        # float16 on a card (measured on ROCm and fine for ranking), bfloat16 on a CPU
        # that has it. See docs/features/search.md#reranking.
        dtype = "float16" if on_accelerator(device) else "bfloat16" if cpu_has_bf16() else "float32"
        return cls(
            model_name=os.environ.get("MERIDIAN_RERANK_MODEL", "").strip() or None,
            cache_dir=os.environ.get("MERIDIAN_EMBED_CACHE") or None,
            device=device,
            dtype=dtype,
        )


class CrossEncoderReranker:
    """A sentence-transformers cross-encoder, loaded on first use."""

    def __init__(self, settings: RerankerSettings) -> None:
        if not settings.enabled:
            raise ValueError("no reranker model is configured")
        self._settings = settings
        self._loaded: object | None = None
        self._lock = threading.Lock()

    @property
    def settings(self) -> RerankerSettings:
        return self._settings

    @property
    def loaded(self) -> bool:
        return self._loaded is not None

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """One relevance score per text, in order; higher is more relevant."""
        if not texts:
            return []
        if not query.strip():
            raise ValueError("cannot rerank for an empty query")
        model = self._model()
        try:
            scores = model.predict(  # type: ignore[attr-defined]
                [(query, text) for text in texts],
                batch_size=self._settings.batch_size,
                show_progress_bar=False,
            )
        except Exception as exc:  # pragma: no cover - depends on the runtime
            raise EmbeddingError(f"{type(exc).__name__}: {exc}") from exc
        return [float(s) for s in scores]

    def _model(self) -> object:
        if self._loaded is not None:
            return self._loaded
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            try:
                import torch
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed (the `embed` extra)"
                ) from exc
            log.info(
                "loading reranker",
                extra={
                    "model": self._settings.model_name,
                    "device": self._settings.device or "auto",
                    "dtype": self._settings.dtype,
                },
            )
            try:
                model = CrossEncoder(
                    self._settings.model_name,
                    device=self._settings.device,
                    max_length=self._settings.max_tokens,
                    cache_folder=self._settings.cache_dir,
                    model_kwargs={"torch_dtype": getattr(torch, self._settings.dtype)},
                )
            except Exception as exc:
                raise EmbeddingError(
                    f"could not load {self._settings.model_name}: {type(exc).__name__}: {exc}"
                ) from exc
            self._loaded = model
            return model


class FakeReranker:
    """Scores by how many of the query's words a text holds; for tests, no download."""

    loaded = True

    def __init__(self, model_name: str = "fake-reranker") -> None:
        self.settings = RerankerSettings(model_name=model_name)

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        words = set(query.lower().split())
        return [float(len(words & set(text.lower().split()))) for text in texts]
