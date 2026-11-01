"""The headline filter by embedding, and its fallback to the vocabulary (`B-251`).

Constructed vectors on named axes stand in for headlines, so each case says why it is
kept or refused. See docs/features/discovery.md#headline-filter.
"""

from __future__ import annotations

import contextlib

import numpy as np
import pytest

from meridian_core.embedder import EmbeddingUnavailable
from meridian_core.headlinetopics import (
    CENTROID_MARGIN,
    PROTOTYPE_FLOOR,
    PROTOTYPE_FLOOR_ALONE,
    HeadlineBasis,
)
from meridian_core.models import Watch
from meridian_core.topiclabels import Basis, Prototype
from worker import headlines as headlines_module
from worker.headlines import HeadlineFilter
from worker.main import watch_topic
from worker.topicmatch import TopicVocabulary

DIM = 8
TRANSIT, ROBOTS, SPORT, CELEBRITY, GENERIC = 0, 1, 2, 3, 7


def vec(**weights: float) -> np.ndarray:
    """A text's vector: what it is about, on top of what every text shares.

    Real embeddings share a large common part (the reference point removes it), so every
    vector here is 0.6 its subject plus 0.8 the GENERIC axis; no subject is pure GENERIC.
    """
    names = {"transit": TRANSIT, "robots": ROBOTS, "sport": SPORT, "celebrity": CELEBRITY}
    v = np.zeros(DIM)
    for name, w in weights.items():
        v[names[name]] = w
    norm = np.linalg.norm(v)
    topical = 0.6 * v / norm if norm else np.zeros(DIM)
    topical[GENERIC] = 0.8 if norm else 1.0
    return topical


def mix(t: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    v = t * a + (1 - t) * b
    return v / np.linalg.norm(v)


def solve(fn, target: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The mix of ``a`` and ``b`` where ``fn`` (rising toward ``a``) reaches ``target``."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if fn(mix(mid, a, b)) >= target else (mid, hi)
    return mix(hi, a, b)


def basis(*, centroids: bool = True, offtopic: bool = True) -> HeadlineBasis:
    prototypes = Basis.build({"transit": vec(transit=1), "robots": vec(robots=1)}, [vec()], "test")
    return HeadlineBasis.build(
        prototypes,
        {"transit": vec(transit=1), "robots": vec(robots=1)} if centroids else {},
        # The corpus's passages about nothing: sport and celebrity pages.
        vec(sport=1, celebrity=1) if offtopic else None,
    )


def proto(b: HeadlineBasis, topic: str = "transit"):
    return lambda v: b.prototypes.scores(v, language=None)[topic]


def margin(b: HeadlineBasis, topic: str = "transit"):
    return lambda v: float(v @ b.centroids[topic]) - float(v @ b.offtopic)


# --------------------------------------------------------------------------
# The decision rule
# --------------------------------------------------------------------------


def test_an_on_topic_headline_is_kept_under_its_topic() -> None:
    assert basis().topics_for(vec(transit=1)) == ("transit",)


@pytest.mark.parametrize(
    "vector",
    [vec(sport=1), vec(celebrity=1), vec(sport=1, celebrity=0.5), vec()],
    ids=["match-report", "star-wedding", "athlete-gossip", "filler"],
)
def test_sport_celebrity_and_filler_are_refused(vector) -> None:
    b = basis()
    assert max(b.prototypes.scores(vector, language=None).values()) < PROTOTYPE_FLOOR
    assert b.topics_for(vector) == ()


def test_a_headline_sharing_a_topics_words_but_reading_as_sport_is_refused() -> None:
    """Over the prototype floor, yet nearer the off-topic passages than the topic's: the
    case the centroid check exists for (measured: a column on a discipline that shares a
    topic's name)."""
    b = basis()
    lure = vec(transit=0.5, sport=0.8)
    assert proto(b)(lure) >= PROTOTYPE_FLOOR
    assert margin(b)(lure) < CENTROID_MARGIN
    assert b.topics_for(lure) == ()


def test_the_margin_is_a_floor() -> None:
    """Just over the margin is kept, just under is refused."""
    b = basis()
    at = solve(margin(b), CENTROID_MARGIN + 0.001, vec(transit=1), b.offtopic)
    under = solve(margin(b), CENTROID_MARGIN - 0.001, vec(transit=1), b.offtopic)
    assert proto(b)(at) >= PROTOTYPE_FLOOR and proto(b)(under) >= PROTOTYPE_FLOOR
    assert b.topics_for(at) == ("transit",)
    assert b.topics_for(under) == ()


def test_the_prototype_floor_holds_however_far_from_sport() -> None:
    """Far from the off-topic passages is not enough: the headline must be near the topic."""
    b = basis()
    weak = solve(proto(b), PROTOTYPE_FLOOR - 0.01, vec(transit=1), vec(robots=1))
    assert margin(b)(weak) > CENTROID_MARGIN
    assert "transit" not in b.topics_for(weak)


def test_a_topic_without_a_centroid_needs_the_higher_floor() -> None:
    """A new topic with no labelled passages yet is judged on its prototype alone."""
    alone = basis(centroids=False)
    between = solve(
        proto(alone), (PROTOTYPE_FLOOR + PROTOTYPE_FLOOR_ALONE) / 2, vec(transit=1), vec(sport=1)
    )
    over = solve(proto(alone), PROTOTYPE_FLOOR_ALONE + 0.005, vec(transit=1), vec(sport=1))
    assert alone.topics_for(between) == ()
    assert alone.topics_for(over) == ("transit",)


def test_without_an_offtopic_centroid_every_topic_is_judged_alone() -> None:
    b = basis(offtopic=False)
    assert b.offtopic is None
    between = solve(
        proto(b), (PROTOTYPE_FLOOR + PROTOTYPE_FLOOR_ALONE) / 2, vec(transit=1), vec(sport=1)
    )
    assert b.topics_for(between) == ()
    assert b.topics_for(vec(transit=1)) == ("transit",)


def test_two_topics_come_best_first() -> None:
    assert basis().topics_for(vec(transit=0.6, robots=0.8)) == ("robots", "transit")


def test_centroids_for_unknown_topics_are_dropped() -> None:
    prototypes = Basis.build({"transit": vec(transit=1)}, [vec()], "t")
    b = HeadlineBasis.build(prototypes, {"retired": vec(robots=1)}, None)
    assert dict(b.centroids) == {}


# --------------------------------------------------------------------------
# The filter: caching and falling back
# --------------------------------------------------------------------------


class FakeEmbedder:
    """Vectors by text; raises while ``down``."""

    def __init__(self, vectors: dict[str, np.ndarray], *, down: bool = False) -> None:
        self.vectors = vectors
        self.down = down
        self.calls: list[list[str]] = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.down:
            raise EmbeddingUnavailable("connection refused")
        return [list(self.vectors.get(t, vec())) for t in texts]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@contextlib.asynccontextmanager
async def no_session():
    yield None


HEADLINES = {
    "Driverless shuttle trial starts in the east": vec(transit=1),
    "Late winner seals the derby": vec(sport=1),
    "Actor weds in a private ceremony": vec(celebrity=1),
}


@pytest.fixture
def corpus(monkeypatch):
    """What the database would give the build: two topics and their centroids."""
    builds = []

    async def prototypes(_sess):
        builds.append(1)
        return [Prototype("robots", "robots"), Prototype("transit", "transit")]

    async def centroids(_sess, topics):
        return {"transit": vec(transit=1), "robots": vec(robots=1)}

    async def offtopic(_sess):
        return vec(sport=1, celebrity=1)

    monkeypatch.setattr(headlines_module, "load_prototypes", prototypes)
    monkeypatch.setattr(headlines_module, "topic_centroids", centroids)
    monkeypatch.setattr(headlines_module, "offtopic_centroid", offtopic)
    return builds


def embedder(**kw) -> FakeEmbedder:
    vectors = {"transit": vec(transit=1), "robots": vec(robots=1), **HEADLINES}
    return FakeEmbedder(vectors, **kw)


async def test_the_filter_keeps_the_on_topic_headline_only(corpus) -> None:
    f = HeadlineFilter(embedder(), session_factory=no_session)
    assert await f.topics_for(list(HEADLINES)) == [("transit",), (), ()]


async def test_no_embedder_means_the_vocabulary(corpus) -> None:
    f = HeadlineFilter(None, session_factory=no_session)
    assert await f.topics_for(list(HEADLINES)) is None
    assert corpus == []  # nothing was built


async def test_an_embedder_that_is_down_means_the_vocabulary_not_everything(corpus) -> None:
    fake = embedder(down=True)
    clock = Clock()
    f = HeadlineFilter(fake, session_factory=no_session, clock=clock)
    assert await f.topics_for(list(HEADLINES)) is None
    # Within the retry window the next read does not ask a machine known to be away.
    asked = len(fake.calls)
    assert await f.topics_for(list(HEADLINES)) is None
    assert len(fake.calls) == asked
    clock.now += headlines_module.RETRY_AFTER_FAILURE_S + 1
    fake.down = False
    assert await f.topics_for(list(HEADLINES)) == [("transit",), (), ()]


async def test_headlines_that_fail_to_embed_fall_back_after_the_basis_was_built(corpus) -> None:
    fake = embedder()
    f = HeadlineFilter(fake, session_factory=no_session)
    assert await f.basis() is not None
    fake.down = True
    assert await f.topics_for(list(HEADLINES)) is None


async def test_a_wrong_number_of_vectors_is_a_fallback_not_a_misfiling(corpus) -> None:
    class Short(FakeEmbedder):
        async def embed(self, texts):
            vectors = await super().embed(texts)
            return vectors if len(texts) > 3 else vectors[:-1]

    f = HeadlineFilter(Short(embedder().vectors), session_factory=no_session)
    assert await f.topics_for(list(HEADLINES)) is None


async def test_the_basis_is_cached_and_rebuilt_when_stale(corpus) -> None:
    clock = Clock()
    f = HeadlineFilter(embedder(), session_factory=no_session, clock=clock)
    await f.topics_for(list(HEADLINES))
    await f.topics_for(list(HEADLINES))
    assert len(corpus) == 1
    clock.now += headlines_module.BASIS_TTL_S + 1
    await f.topics_for(list(HEADLINES))
    assert len(corpus) == 2


async def test_a_stale_basis_is_kept_while_a_rebuild_fails(corpus) -> None:
    clock = Clock()
    fake = embedder()
    f = HeadlineFilter(fake, session_factory=no_session, clock=clock)
    first = await f.basis()
    clock.now += headlines_module.BASIS_TTL_S + 1
    fake.down = True
    assert await f.basis() is first


async def test_no_topics_means_the_vocabulary(monkeypatch) -> None:
    async def none(_sess):
        return []

    monkeypatch.setattr(headlines_module, "load_prototypes", none)
    f = HeadlineFilter(embedder(), session_factory=no_session)
    assert await f.topics_for(list(HEADLINES)) is None


async def test_no_headlines_asks_nothing(corpus) -> None:
    fake = embedder()
    f = HeadlineFilter(fake, session_factory=no_session)
    assert await f.topics_for([]) == []
    assert fake.calls == []


# --------------------------------------------------------------------------
# How an entry is filed
# --------------------------------------------------------------------------

VOCAB = TopicVocabulary(phrases={"road safety": ("transit",)})


def watch(**fields) -> Watch:
    fields.setdefault("headline_filter", True)
    return Watch(url="https://paper.test/news.xml", **fields)


def test_the_embedding_overrules_the_vocabulary() -> None:
    """A sport story under a `/road-safety/` path: the vocabulary would queue it."""
    url = "https://paper.test/road-safety/derby-report"
    assert watch_topic(watch(), url, "Late winner seals the derby", VOCAB) == "transit"
    assert watch_topic(watch(), url, "Late winner seals the derby", VOCAB, ()) is None


def test_an_embedded_topic_is_filed_and_a_watch_topic_still_restricts() -> None:
    url = "https://paper.test/a/1"
    assert watch_topic(watch(), url, "Robot couriers", VOCAB, ("robots", "transit")) == "robots"
    restricted = watch(topic="transit")
    assert watch_topic(restricted, url, "Robot couriers", VOCAB, ("robots", "transit")) == "transit"
    assert watch_topic(restricted, url, "Robot couriers", VOCAB, ("robots",)) is None


def test_a_watch_without_the_filter_ignores_embeddings() -> None:
    plain = watch(headline_filter=False, topic="transit")
    assert watch_topic(plain, "https://paper.test/a/2", "Derby", VOCAB, ()) == "transit"
