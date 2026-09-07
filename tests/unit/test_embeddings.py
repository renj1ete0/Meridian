"""The embedding wrapper (task P2-01, spec §4, §6.1).

Almost everything here runs against `FakeEmbedder`, and that is the design
rather than a compromise. bge-m3 is 2.3GB, and the things worth testing about
this module — that the dimension is checked, that batching preserves order,
that an empty string is refused, that nothing loads until something asks — are
properties of the wrapper, not of the weights. A suite that needed the model
would be a suite nobody runs.

The model itself gets one test, skipped unless it is already cached, and its job
is the "arm64 sanity check" `P2-01` asks for: that the configured model really
does produce `EMBEDDING_DIM` unit vectors on this machine.
"""

from __future__ import annotations

import math
import os
import threading

import pytest

from meridian_core.models.source import EMBEDDING_DIM
from worker.embeddings import (
    DEFAULT_MODEL,
    BGEEmbedder,
    EmbedderSettings,
    EmbeddingError,
    FakeEmbedder,
)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


# --------------------------------------------------------------------------
# The contract every embedder has to satisfy
# --------------------------------------------------------------------------


def test_vectors_match_the_column_width() -> None:
    """`chunks.embedding` is `Vector(1024)`.

    A model returning 768 produces a pgvector error a thousand chunks later,
    pointing at the insert rather than at the misconfiguration.
    """
    assert FakeEmbedder().dimensions == EMBEDDING_DIM
    assert len(FakeEmbedder().embed(["anything"])[0]) == EMBEDDING_DIM


def test_vectors_are_normalised() -> None:
    """`P2-03`'s novelty gate compares cosine against a fixed 0.95 threshold.

    A threshold means nothing if the vectors behind it are sometimes unit
    length and sometimes not.
    """
    for vector in FakeEmbedder().embed(["transit", "a much longer piece of text " * 20]):
        assert norm(vector) == pytest.approx(1.0, abs=1e-9)


def test_the_same_text_always_embeds_identically() -> None:
    """Otherwise the novelty gate would reject a page against itself, or not."""
    first, second = FakeEmbedder().embed(["identical text", "identical text"])

    assert first == second
    assert cosine(first, second) == pytest.approx(1.0, abs=1e-9)


def test_different_text_embeds_differently() -> None:
    """A fake that returned a constant would make every downstream test vacuous."""
    a, b = FakeEmbedder().embed(["alpha in dense cities", "autonomous vehicle regulation"])

    assert a != b
    assert cosine(a, b) < 0.9


def test_order_is_preserved_across_a_batch() -> None:
    """The caller zips vectors back onto chunk ids by position.

    A reordering here misaligns every chunk in the batch with somebody else's
    meaning, and nothing downstream would ever notice.
    """
    texts = [f"chunk number {i} about transit planning" for i in range(20)]
    embedder = FakeEmbedder()

    batched = embedder.embed(texts)
    one_at_a_time = [embedder.embed([text])[0] for text in texts]

    assert batched == one_at_a_time


def test_an_empty_batch_costs_nothing() -> None:
    """A backfill that finds no work must not pay 2.3GB to discover that."""
    embedder = BGEEmbedder()

    assert embedder.embed([]) == []
    assert not embedder.loaded, "an empty batch loaded the model"


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_an_empty_string_is_refused(text: str) -> None:
    """It would embed to whatever the model does with padding — a vector that
    matches other empty things and nothing meaningful.

    `replace_chunks` already drops these; a caller that got one past it should
    hear about it rather than store noise.
    """
    with pytest.raises(ValueError, match="empty"):
        FakeEmbedder().embed(["fine", text])


# --------------------------------------------------------------------------
# Loading — the part that costs 2.3GB
# --------------------------------------------------------------------------


def test_nothing_loads_at_construction() -> None:
    """`worker.main` imports the package tree and never embeds.

    A model loaded at import or at construction would sit in the crawler's
    memory budget for work it does not do.
    """
    assert not BGEEmbedder().loaded


def test_importing_the_module_does_not_import_the_runtime() -> None:
    """The worker image is built without the `embed` extra, deliberately.

    `sentence_transformers` is imported inside the loader for that reason: a
    worker that cannot embed must still start, crawl, and say why it cannot
    embed — rather than failing at startup with an ImportError.
    """
    import inspect

    import worker.embeddings as module

    source = inspect.getsource(module)
    top_level = source.split("class BGEEmbedder")[0]
    assert "import sentence_transformers" not in top_level
    assert "from sentence_transformers" not in top_level


def test_a_missing_runtime_is_named_rather_than_traced(monkeypatch) -> None:
    """The message has to say what to install and where."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("no module named sentence_transformers")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(EmbeddingError, match="embed"):
        BGEEmbedder().embed(["text"])


def fake_sentence_transformers(monkeypatch, *, dimensions=EMBEDDING_DIM, on_load=None):
    """Install a stand-in `sentence_transformers` so the real loader runs.

    Monkeypatching `BGEEmbedder._model` would test the test. What needs
    exercising is the loading path itself — the dimension check, the double
    checked lock, the max_seq_length assignment — so the module it imports is
    what gets replaced, not the method that imports it.
    """
    import sys
    import types

    loads: list[str] = []

    class StubModel:
        max_seq_length = 512

        def __init__(self, name, device=None, cache_folder=None):
            loads.append(name)
            if on_load is not None:
                on_load()

        def get_sentence_embedding_dimension(self):
            return dimensions

        def encode(self, texts, **kwargs):
            return [[1.0 / (dimensions**0.5)] * dimensions for _ in texts]

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = StubModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return loads


def test_a_model_of_the_wrong_width_is_refused_at_load(monkeypatch) -> None:
    """Caught at load rather than after a batch.

    The alternative is a pgvector error a thousand chunks in, pointing at the
    insert instead of at the configuration that caused it — so the real loader
    is what runs here, with only the library replaced.
    """
    fake_sentence_transformers(monkeypatch, dimensions=384)

    with pytest.raises(EmbeddingError, match="384"):
        BGEEmbedder().embed(["text"])


def test_a_model_of_the_right_width_loads(monkeypatch) -> None:
    """The other half: the check must not reject a correct model."""
    fake_sentence_transformers(monkeypatch)
    embedder = BGEEmbedder()

    vectors = embedder.embed(["text"])

    assert embedder.loaded
    assert len(vectors[0]) == EMBEDDING_DIM


def test_the_max_sequence_length_is_applied(monkeypatch) -> None:
    """A caller embedding a query or a node description should be truncated
    deterministically rather than by whatever the tokeniser happens to do."""
    fake_sentence_transformers(monkeypatch)
    embedder = BGEEmbedder(EmbedderSettings(max_tokens=777))

    embedder.embed(["text"])

    assert embedder._model().max_seq_length == 777


def test_the_model_loads_once_under_concurrent_callers(monkeypatch) -> None:
    """The backfill embeds in a worker thread.

    Two batches arriving together must not each start a 2.3GB load, so this
    drives the real double-checked lock with a load slow enough for six threads
    to pile up behind it.
    """
    import time

    loads = fake_sentence_transformers(monkeypatch, on_load=lambda: time.sleep(0.05))
    embedder = BGEEmbedder()

    threads = [threading.Thread(target=embedder._model) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(loads) == 1, f"the model was loaded {len(loads)} times"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_the_default_model_is_the_one_the_spec_chose() -> None:
    """§4 picks bge-m3 because serious literature for the comparison set is
    substantially non-English.

    An English-only model would bias the corpus toward Western sources while
    every measurement of it looked fine, which is why this is not a knob to
    turn for speed without deciding that trade explicitly.
    """
    assert EmbedderSettings().model_name == DEFAULT_MODEL == "BAAI/bge-m3"
    assert EmbedderSettings().dimensions == EMBEDDING_DIM


def test_settings_read_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBED_MODEL", "some/other-model")
    monkeypatch.setenv("MERIDIAN_EMBED_BATCH_SIZE", "32")
    monkeypatch.setenv("MERIDIAN_EMBED_DEVICE", "cuda")
    monkeypatch.setenv("MERIDIAN_EMBED_CACHE", "/models")

    settings = EmbedderSettings.from_env()

    assert settings.model_name == "some/other-model"
    assert settings.batch_size == 32
    assert settings.device == "cuda"
    assert settings.cache_dir == "/models"


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_a_misconfigured_batch_size_fails_loudly(monkeypatch, value: str) -> None:
    """A batch size of zero is an embedder that silently does nothing."""
    monkeypatch.setenv("MERIDIAN_EMBED_BATCH_SIZE", value)

    with pytest.raises(RuntimeError, match="MERIDIAN_EMBED_BATCH_SIZE"):
        EmbedderSettings.from_env()


def test_the_batch_size_default_is_sized_for_the_pi() -> None:
    """§3's node is an Orange Pi 5 Plus with 16GB *shared with other services*.

    A batch that swaps is far slower than two batches that do not, so this is
    deliberately small — not a value to raise without measuring on the board.
    """
    assert 1 <= EmbedderSettings().batch_size <= 16


# --------------------------------------------------------------------------
# The real model — `P2-01`'s "arm64 sanity check"
# --------------------------------------------------------------------------


def _model_is_cached() -> bool:
    cache = os.environ.get("MERIDIAN_EMBED_CACHE") or ""
    if not cache or not os.path.isdir(cache):
        return False
    return any("bge-m3" in name for name in os.listdir(cache))


@pytest.mark.skipif(
    not _model_is_cached(),
    reason="bge-m3 is not cached; set MERIDIAN_EMBED_CACHE to run the real-model check",
)
def test_the_real_model_produces_the_width_the_schema_expects() -> None:
    """The sanity check `P2-01` asks for, on whatever machine runs the suite.

    Everything above tests the wrapper. This tests that the wrapper and the
    weights agree — the one thing a fake cannot tell you, and the thing that
    differs between x86 development and the arm64 board.
    """
    embedder = BGEEmbedder(
        EmbedderSettings(cache_dir=os.environ["MERIDIAN_EMBED_CACHE"], device="cpu")
    )

    vectors = embedder.embed(
        [
            "Ridership on the Downtown Line rose eleven per cent.",
            "地下鉄の乗客数は11パーセント増加した。",
        ]
    )

    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    assert all(norm(v) == pytest.approx(1.0, abs=1e-5) for v in vectors)
    # The multilingual claim §4 chose this model for: the same statement in
    # Japanese must land nearer than an unrelated English sentence.
    unrelated = embedder.embed(["The cat sat on the mat."])[0]
    assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], unrelated)
