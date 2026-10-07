"""bge-m3 embeddings (task P2-01, spec §4, §6.1).

No model is loaded until something asks for a vector, so importing this module stays
free. The dimension is asserted and vectors are always normalised. See
docs/features/embedding.md#the-model for why bge-m3, batching and precision.
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

#: How many chunks go through the model at once when the memory cannot be read.
#: The batch when nothing else sizes it (memory unknown, no override).
DEFAULT_BATCH_SIZE = 8

#: Below bge-m3's 8192 so that anything other than a chunk (a query, a node
#: description) is truncated deterministically.
DEFAULT_MAX_TOKENS = 1024

# -- sizing the batch at startup (`MERIDIAN_EMBED_BATCH_SIZE` still wins) --------

#: On a CPU, one passage at a time: measured, every larger batch was slower.
#: See docs/features/embedding.md#the-model.
CPU_BATCH_SIZE = 1

#: The precision the model computes in: bfloat16 where the CPU has bf16
#: instructions, else float32. See docs/features/embedding.md#the-model.
DTYPES = ("float32", "bfloat16")
CPUINFO = "/proc/cpuinfo"
BF16_FLAGS = frozenset({"avx512_bf16", "amx_bf16", "bf16"})


def cpu_has_bf16(path: str | None = None) -> bool:
    """Whether this CPU computes bfloat16 in hardware, from its flags; False when unreadable."""
    try:
        with open(path or CPUINFO) as fh:
            for line in fh:
                key, _, value = line.partition(":")
                if key.strip() in {"flags", "Features"}:
                    return not BF16_FLAGS.isdisjoint(value.split())
    except OSError:
        pass
    return False


#: On an accelerator the batch *is* the speed, so it is sized to memory. Resident
#: size of the loaded model, which a batch must leave room for ...
MODEL_BYTES = int(2.5 * 2**30)
#: ... peak memory one passage of ``DEFAULT_MAX_TOKENS`` adds (measured on CPU at
#: about 50 MB; doubled, since an accelerator's allocator is less forgiving) ...
BYTES_PER_ITEM = 100 * 2**20
#: ... the share of what is left after the model that batches may use ...
MEMORY_SHARE = 0.25
#: ... and a cap, not yet measured on an accelerator this project has run.
MAX_AUTO_BATCH = 32


#: Where a container's memory limit is, cgroup v2 then v1, and the machine's total.
CGROUP_LIMITS = ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
MEMINFO = "/proc/meminfo"


def visible_memory() -> int | None:
    """Bytes this process may use: a container's limit if it has one, else the machine's.

    The lower of cgroup v2's ``memory.max``, cgroup v1's limit and ``MemTotal``, since
    a container sees the host's ``/proc/meminfo``. None when nothing could be read.
    """
    found: list[int] = []
    for path in CGROUP_LIMITS:
        try:
            raw = open(path).read().strip()  # noqa: SIM115
        except OSError:
            continue
        # "max", or v1's "no limit" spelled as a number near 2**63.
        if raw.isdigit() and int(raw) < 2**60:
            found.append(int(raw))
    try:
        with open(MEMINFO) as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    found.append(int(line.split()[1]) * 1024)
                    break
    except (OSError, ValueError, IndexError):
        pass
    return min(found) if found else None


def on_accelerator(device: str | None) -> bool:
    """Whether the model will run on something other than the CPU.

    An explicit device says so; left to the library, it is whatever
    sentence-transformers would pick, which is CUDA or MPS when present. Without
    torch installed nothing embeds here at all, and a CPU answer is harmless.
    """
    if device is not None:
        return device.split(":", 1)[0].lower() != "cpu"
    try:
        import torch
    except ImportError:
        return False
    mps = getattr(torch.backends, "mps", None)
    return bool(torch.cuda.is_available() or (mps is not None and mps.is_available()))


def device_memory(device: str | None) -> int | None:
    """Bytes on the CUDA device the model will run on; None for anything else.

    A GPU's batch is bounded by its own memory, not the container's (`B-129`). MPS, a
    CPU and an unreadable CUDA device fall back to :func:`visible_memory`.
    """
    kind, _, index = (device or "cuda").partition(":")
    if kind.lower() != "cuda":
        return None
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        _free, total = torch.cuda.mem_get_info(int(index) if index else None)
        return int(total)
    except Exception:  # noqa: BLE001 — any failure here means "not known", never "fail to start"
        return None


def auto_batch_size(memory: int | None, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> int:
    """On an accelerator, the largest power-of-two batch that fits, capped.

    Attention memory grows with the square of the sequence, so a lower token cap
    fits more passages. Unknown memory gets `DEFAULT_BATCH_SIZE` rather than a guess.
    """
    if memory is None:
        return DEFAULT_BATCH_SIZE
    per_item = BYTES_PER_ITEM * max(max_tokens / DEFAULT_MAX_TOKENS, 1 / 16) ** 2
    fits = int(max(memory - MODEL_BYTES, 0) * MEMORY_SHARE // per_item)
    if fits < 1:
        return 1
    return min(1 << (fits.bit_length() - 1), MAX_AUTO_BATCH)


class EmbeddingError(RuntimeError):
    """The model could not be loaded or could not produce vectors."""


class Embedder(Protocol):
    """What the rest of the system needs from an embedding model.

    A protocol, so callers and tests run without the model and another runtime is a
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
    #: One of :data:`DTYPES`. float32 unless the environment or the hardware says otherwise.
    dtype: str = "float32"

    @classmethod
    def from_env(cls) -> EmbedderSettings:
        max_tokens = _int_env("MERIDIAN_EMBED_MAX_TOKENS", DEFAULT_MAX_TOKENS)
        device = os.environ.get("MERIDIAN_EMBED_DEVICE") or None
        explicit = _int_env("MERIDIAN_EMBED_BATCH_SIZE", 0)
        if explicit:
            batch_size = explicit
        elif not on_accelerator(device):
            batch_size = CPU_BATCH_SIZE
            log.info("embedding batch sized for a CPU", extra={"batch_size": batch_size})
        else:
            on_card = device_memory(device)
            memory = on_card if on_card is not None else visible_memory()
            batch_size = auto_batch_size(memory, max_tokens=max_tokens)
            log.info(
                "embedding batch sized from memory",
                extra={
                    "batch_size": batch_size,
                    "memory_gib": None if memory is None else round(memory / 2**30, 1),
                    "memory_of": "device" if on_card is not None else "host",
                },
            )
        dtype = os.environ.get("MERIDIAN_EMBED_DTYPE", "").strip().lower()
        if dtype and dtype not in DTYPES:
            raise RuntimeError(
                f"MERIDIAN_EMBED_DTYPE must be one of {', '.join(DTYPES)}, got {dtype!r}"
            )
        if not dtype:
            # Only a CPU was measured; an accelerator keeps float32 until one is.
            dtype = "bfloat16" if not on_accelerator(device) and cpu_has_bf16() else "float32"
        return cls(
            model_name=os.environ.get("MERIDIAN_EMBED_MODEL") or DEFAULT_MODEL,
            batch_size=batch_size,
            max_tokens=max_tokens,
            cache_dir=os.environ.get("MERIDIAN_EMBED_CACHE") or None,
            device=device,
            dtype=dtype,
        )


class BGEEmbedder:
    """bge-m3 through sentence-transformers.

    Import-safe: `sentence_transformers` is imported inside :meth:`_model`, so a
    worker built without the `embed` extra can still import this module.
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
            # An empty text embeds to padding, which matches only other empty
            # things; `replace_chunks` drops these, so one here is a caller's bug.
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

        out = [_unit([float(value) for value in vector]) for vector in vectors]
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
                    "batch_size": self._settings.batch_size,
                    "dtype": self._settings.dtype,
                    "cache_dir": self._settings.cache_dir,
                },
            )
            extra: dict[str, object] = {}
            if self._settings.dtype != "float32":
                import torch

                extra["model_kwargs"] = {"torch_dtype": getattr(torch, self._settings.dtype)}
            try:
                model = SentenceTransformer(
                    self._settings.model_name,
                    device=self._settings.device,
                    cache_folder=self._settings.cache_dir,
                    **extra,
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

    Not a mock: a real unit vector derived from the text, so identical text embeds
    identically and cosine similarity behaves.
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


def _unit(vector: list[float]) -> list[float]:
    """``vector`` rescaled to unit length in float64.

    In bfloat16 the model's own normalising leaves a norm off by a few parts in a
    thousand, enough that a dot product stops being a cosine.
    """
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector] if norm > 0 else vector


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
