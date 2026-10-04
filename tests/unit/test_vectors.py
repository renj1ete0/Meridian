"""Where the backfill's vectors come from (task P2-19, spec §4, §12.5).

`P2-17` put one copy of the model resident in a sidecar for the query path, and
the backfill went on constructing its own — so a stack running both held two
copies of 2.3GB of weights on a machine chosen for being small.

The properties worth testing are all about the fallback, because falling back is
the behaviour that has to be both automatic and *visible*. A backfill that
quietly loads a second model when the sidecar hiccups looks exactly like one
using the sidecar, and the only symptom is memory pressure with nothing in the
log to explain it.
"""

from __future__ import annotations

import logging

import pytest

from meridian_core.embedder import EmbedderMismatch, EmbeddingUnavailable
from worker.vectors import LocalEmbedder, PreferRemote, build_embedder


class FakeLocal:
    """Stands in for the in-process model, and counts whether it was built."""

    built = 0

    def __init__(self) -> None:
        FakeLocal.built += 1
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class FakeRemote:
    """A sidecar that answers, or fails in one of the two ways that matter."""

    def __init__(self, *, fail: Exception | None = None, described: dict | None = None) -> None:
        self.base_url = "http://embedder.test"
        self.fail = fail
        self.described = described
        self.calls = 0

    async def describe(self):
        return self.described

    async def embed(self, texts):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return [[0.0, 1.0] for _ in texts]


@pytest.fixture(autouse=True)
def reset() -> None:
    FakeLocal.built = 0


# --------------------------------------------------------------------------
# The sidecar is used when it works
# --------------------------------------------------------------------------


async def test_a_working_sidecar_keeps_the_model_out_of_this_process() -> None:
    # The whole point. Constructing the local embedder at all is the cost being
    # avoided, so "it answered from the sidecar" is not enough — nothing may
    # have built the fallback.
    remote = FakeRemote()
    embedder = PreferRemote(remote, local_factory=FakeLocal)

    vectors = await embedder.embed(["a", "b"])

    assert vectors == [[0.0, 1.0], [0.0, 1.0]]
    assert remote.calls == 1
    assert FakeLocal.built == 0


async def test_no_sidecar_means_the_local_model() -> None:
    embedder = PreferRemote(None, local_factory=FakeLocal)

    vectors = await embedder.embed(["a"])

    assert vectors == [[1.0, 0.0]]
    assert FakeLocal.built == 1


async def test_an_empty_batch_builds_nothing_and_calls_nothing() -> None:
    remote = FakeRemote()
    embedder = PreferRemote(remote, local_factory=FakeLocal)

    assert await embedder.embed([]) == []
    assert remote.calls == 0
    assert FakeLocal.built == 0


# --------------------------------------------------------------------------
# Falling back
# --------------------------------------------------------------------------


async def test_a_sidecar_that_stops_answering_falls_back_within_the_batch() -> None:
    # The batch is not lost. A backfill can afford to load a model; what it
    # cannot afford is to drop the rows it was holding when the sidecar went
    # away, since the cursor has already moved past them.
    embedder = PreferRemote(
        FakeRemote(fail=EmbeddingUnavailable("connection refused")), local_factory=FakeLocal
    )

    vectors = await embedder.embed(["a"])

    assert vectors == [[1.0, 0.0]]


async def test_the_switch_is_one_way_within_a_process() -> None:
    # Once the local model is loaded the memory is already spent, so going back
    # would buy nothing and cost a reload's worth of uncertainty about which
    # model produced what.
    remote = FakeRemote(fail=EmbeddingUnavailable("down"))
    embedder = PreferRemote(remote, local_factory=FakeLocal)

    await embedder.embed(["a"])
    remote.fail = None
    await embedder.embed(["b"])

    assert remote.calls == 1, "it went back to the sidecar after falling back"
    assert FakeLocal.built == 1


async def test_only_one_local_model_is_ever_built() -> None:
    embedder = PreferRemote(None, local_factory=FakeLocal)

    for _ in range(5):
        await embedder.embed(["a"])

    assert FakeLocal.built == 1


async def test_a_mismatched_model_falls_back_rather_than_mixing_vectors() -> None:
    # The failure that cannot be detected afterwards: `<=>` accepts any two
    # vectors of the right width and returns a number, so a column holding two
    # models' vectors ranks confident nonsense forever. The local model is the
    # right one, so falling back is correct — but it has to be reported as a
    # mismatch and not as an outage.
    embedder = PreferRemote(
        FakeRemote(fail=EmbedderMismatch("serving 'other-model'")), local_factory=FakeLocal
    )

    vectors = await embedder.embed(["a"])

    assert vectors == [[1.0, 0.0]]
    assert embedder.using_remote is False


async def test_falling_back_says_why() -> None:
    # The reason this is asserted rather than assumed: a backfill that quietly
    # loads a second model looks exactly like one using the sidecar, and the
    # only symptom is memory pressure with nothing in the log to explain it.
    #
    # A handler rather than `caplog`: this suite runs with `-p no:logging`,
    # because pytest's plugin interleaves plain-text records with the JSON ones
    # `meridian_core.logging` emits.
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("worker.vectors")
    handler = Collect()
    logger.addHandler(handler)
    try:
        await PreferRemote(
            FakeRemote(fail=EmbeddingUnavailable("connection refused")), local_factory=FakeLocal
        ).embed(["a"])
    finally:
        logger.removeHandler(handler)

    reasons = [getattr(r, "reason", "") for r in records]
    assert any("connection refused" in reason for reason in reasons)


async def test_a_mismatch_is_reported_as_a_mismatch_not_an_outage() -> None:
    # Different fixes: a sidecar that is down comes back, and one serving the
    # wrong model needs somebody to change a URL.
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("worker.vectors")
    handler = Collect()
    logger.addHandler(handler)
    try:
        await PreferRemote(
            FakeRemote(fail=EmbedderMismatch("serving 'other-model'")), local_factory=FakeLocal
        ).embed(["a"])
    finally:
        logger.removeHandler(handler)

    assert any("mismatch" in getattr(r, "reason", "") for r in records)


# --------------------------------------------------------------------------
# Choosing at startup
# --------------------------------------------------------------------------


async def test_an_unreachable_sidecar_is_not_chosen() -> None:
    embedder = await build_embedder(FakeRemote(described=None), wait_s=0)

    assert embedder.using_remote is False


class SlowToStart(FakeRemote):
    """Answers on the nth probe, as a sidecar still loading its weights does."""

    def __init__(self, answers_on: int) -> None:
        super().__init__(described={"model": "bge-m3", "loaded": True})
        self.answers_on = answers_on
        self.probes = 0

    async def describe(self):
        self.probes += 1
        return self.described if self.probes >= self.answers_on else None


async def test_a_sidecar_still_starting_is_waited_for() -> None:
    """`B-76`: a joint restart must not load a second copy of the model."""
    remote = SlowToStart(answers_on=3)
    embedder = await build_embedder(remote, wait_s=5, poll_s=0.01)

    assert embedder.using_remote is True and remote.probes == 3


async def test_the_wait_ends(monkeypatch) -> None:
    remote = SlowToStart(answers_on=10_000)
    embedder = await build_embedder(remote, wait_s=0.05, poll_s=0.01)

    assert embedder.using_remote is False
    assert 2 <= remote.probes < 50


async def test_the_wait_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBEDDER_WAIT_S", "0")
    remote = SlowToStart(answers_on=2)
    embedder = await build_embedder(remote, poll_s=0.01)

    assert embedder.using_remote is False and remote.probes == 1


async def test_a_reachable_sidecar_is_chosen() -> None:
    embedder = await build_embedder(FakeRemote(described={"model": "bge-m3", "loaded": True}))

    assert embedder.using_remote is True


# --------------------------------------------------------------------------
# The local adapter
# --------------------------------------------------------------------------


async def test_the_local_adapter_passes_texts_through_unchanged() -> None:
    class Sync:
        def embed(self, texts):
            return [[float(len(t))] for t in texts]

    assert await LocalEmbedder(Sync()).embed(["ab", "c"]) == [[2.0], [1.0]]


async def test_the_local_adapter_short_circuits_an_empty_batch() -> None:
    class Exploding:
        def embed(self, texts):
            raise AssertionError("should not have been called")

    assert await LocalEmbedder(Exploding()).embed([]) == []


# --------------------------------------------------------------------------
# Remote only: the model is on another machine (P3-12)
# --------------------------------------------------------------------------


async def test_remote_only_never_builds_the_local_model_when_the_sidecar_is_down(
    monkeypatch,
) -> None:
    """On the stack's board, loading 2.3 GB beside Postgres is the failure."""
    from worker.embeddings import EmbeddingError

    monkeypatch.setenv("MERIDIAN_EMBED_REMOTE_ONLY", "1")
    remote = FakeRemote(described=None, fail=EmbeddingUnavailable("connection refused"))
    embedder = await build_embedder(remote, wait_s=0)
    embedder._local_factory = FakeLocal

    assert embedder.using_remote is True, "a down sidecar still is the only embedder"
    with pytest.raises(EmbeddingError):
        await embedder.embed(["a"])
    assert FakeLocal.built == 0


@pytest.mark.parametrize("failure", [EmbeddingUnavailable("down"), EmbedderMismatch("other model")])
async def test_remote_only_fails_the_batch_instead_of_falling_back(failure) -> None:
    """A failed batch is retried next pass; a local load is not undone."""
    from worker.embeddings import EmbeddingError

    embedder = PreferRemote(FakeRemote(fail=failure), local_factory=FakeLocal, remote_only=True)
    with pytest.raises(EmbeddingError):
        await embedder.embed(["a"])
    assert FakeLocal.built == 0
    assert embedder.using_remote is True, "the next batch asks the sidecar again"


async def test_remote_only_with_no_sidecar_configured_is_refused(monkeypatch) -> None:
    from worker.embeddings import EmbeddingError

    monkeypatch.setenv("MERIDIAN_EMBED_REMOTE_ONLY", "1")
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)
    with pytest.raises(EmbeddingError, match="MERIDIAN_EMBEDDER_URL"):
        await build_embedder()


async def test_without_remote_only_the_fallback_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("MERIDIAN_EMBED_REMOTE_ONLY", raising=False)
    embedder = await build_embedder(FakeRemote(described=None), wait_s=0)
    assert embedder.using_remote is False


# --------------------------------------------------------------------------
# A backfill batch bigger than one request (`B-130`)
# --------------------------------------------------------------------------


class CappedRemote(FakeRemote):
    """Refuses more than one request's worth, as `RemoteEmbedder` does, and numbers its vectors."""

    def __init__(self) -> None:
        super().__init__()
        self.sizes: list[int] = []

    async def embed(self, texts):
        from meridian_core.embedder import MAX_TEXTS

        if len(texts) > MAX_TEXTS:
            raise ValueError(f"{len(texts)} texts exceeds the {MAX_TEXTS} cap for one request")
        self.sizes.append(len(texts))
        return [[float(t), 0.0] for t in texts]


@pytest.mark.parametrize("remote_only", [False, True])
async def test_a_batch_over_the_request_cap_stays_on_the_sidecar(remote_only) -> None:
    from meridian_core.embedder import MAX_TEXTS

    remote = CappedRemote()
    embedder = PreferRemote(remote, local_factory=FakeLocal, remote_only=remote_only)
    texts = [str(i) for i in range(2 * MAX_TEXTS + 7)]

    vectors = await embedder.embed(texts)

    assert remote.sizes == [MAX_TEXTS, MAX_TEXTS, 7]
    assert [v[0] for v in vectors] == [float(t) for t in texts], "order kept across requests"
    assert FakeLocal.built == 0
    assert embedder.using_remote is True


async def test_the_real_client_still_refuses_an_oversized_request() -> None:
    """The cap this splits for is real; if it went, the split would hide nothing."""
    from meridian_core.embedder import MAX_TEXTS, RemoteEmbedder

    with pytest.raises(ValueError, match="cap"):
        await RemoteEmbedder("http://embedder.test").embed(["x"] * (MAX_TEXTS + 1))


def test_the_client_and_server_caps_agree() -> None:
    from meridian_core.embedder import MAX_TEXTS as CLIENT_CAP
    from worker.embedserver import MAX_TEXTS as SERVER_CAP

    assert CLIENT_CAP == SERVER_CAP


async def test_a_failure_part_way_through_a_split_batch_fails_the_whole_batch() -> None:
    """Half a batch of vectors would pair the rest of the chunks with nobody's."""
    from meridian_core.embedder import MAX_TEXTS
    from worker.embeddings import EmbeddingError

    class FailsSecond(CappedRemote):
        async def embed(self, texts):
            if self.sizes:
                raise EmbeddingUnavailable("went away")
            return await super().embed(texts)

    embedder = PreferRemote(FailsSecond(), local_factory=FakeLocal, remote_only=True)
    with pytest.raises(EmbeddingError):
        await embedder.embed([str(i) for i in range(MAX_TEXTS + 1)])
