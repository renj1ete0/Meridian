"""Deciding whether two mentions are the same thing (task `P4-02`, §5.5).

§5.5's own example is the test set: "LTA", "Land Transport Authority", "the
Authority" and "LTA Singapore" must not become four nodes. But the more
important tests here are the ones that refuse to merge, because §16 is explicit
that a bad merge is worse than a duplicate — conflation is invisible once done,
and a duplicate is at least visible.

`block` needs a real Postgres: it is a query, and the thing worth proving about
it is what it *excludes*.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from meridian_core.models import Entity, GazetteerTerm
from meridian_core.resolution import (
    AUTO_MERGE,
    SEPARATE_BELOW,
    block,
    context_overlap,
    decide,
    embedding_similarity,
    expansions_from_gazetteer,
    normalise,
    score,
    string_similarity,
)

pytestmark = pytest.mark.usefixtures("require_db")

MARK = "resolution-test"


@pytest.fixture
async def clean(session_for):
    sess = await session_for("rw")
    await sess.execute(delete(Entity).where(Entity.description == MARK))
    await sess.execute(delete(GazetteerTerm).where(GazetteerTerm.canonical.like("%.test")))
    await sess.flush()
    return sess


async def an_entity(sess, name, node_type="organisation", **over) -> Entity:
    row = Entity(
        canonical_name=name,
        node_type=node_type,
        description=MARK,
        supporting_chunk_ids=over.pop("chunks", []),
        **over,
    )
    sess.add(row)
    await sess.flush()
    return row


def as_entity(name, node_type="organisation", chunks=None, embedding=None) -> Entity:
    return Entity(
        canonical_name=name,
        node_type=node_type,
        supporting_chunk_ids=chunks or [],
        embedding=embedding,
    )


# --------------------------------------------------------------------------
# Normalise


def test_the_fragmentation_case_from_the_spec_collapses() -> None:
    """ "The Authority" and "Authority, Ltd." are the same mention."""
    assert normalise("The Authority") == normalise("Authority, Ltd.")


def test_accents_do_not_split_an_entity() -> None:
    """A crawl reaches both spellings of the same place routinely, and treating
    them as different entities fragments on exactly the material it sees."""
    assert normalise("Zürich") == normalise("Zurich")


def test_an_abbreviation_expands_per_token() -> None:
    """So "LTA Singapore" resolves without anybody having curated that exact
    phrase."""
    out = normalise("LTA Singapore", expansions={"lta": "land transport authority"})

    assert out == "land transport authority singapore"


def test_a_name_that_is_entirely_noise_survives() -> None:
    """An empty normalisation matches every other empty one, which is the worst
    possible outcome for a resolver."""
    assert normalise("The") != ""


async def test_only_approved_gazetteer_terms_expand(clean) -> None:
    """A proposed term is a suggestion nobody has checked. Letting it rewrite
    names would let an unreviewed suggestion silently merge two entities."""
    clean.add(
        GazetteerTerm(
            canonical="approved.test", aliases=["ok"], entity_type="agency", approved=True
        )
    )
    clean.add(
        GazetteerTerm(
            canonical="pending.test", aliases=["no"], entity_type="agency", approved=False
        )
    )
    await clean.flush()

    table = await expansions_from_gazetteer(clean)

    assert table.get("ok") == "approved.test"
    assert "no" not in table


# --------------------------------------------------------------------------
# The individual signals


def test_word_order_does_not_matter_to_the_string_signal() -> None:
    """Entity names differ by word rather than by character, which is why
    token-set leads."""
    assert string_similarity("land transport authority", "authority land transport") > 0.85


def test_an_absent_embedding_is_unknown_rather_than_dissimilar() -> None:
    """None, not 0.0. Scoring it as maximally dissimilar would make every
    entity written before the backfill unmergeable."""
    assert embedding_similarity(None, [1.0, 0.0]) is None
    assert embedding_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_context_is_measured_as_overlap_not_count() -> None:
    """An entity cited by two hundred chunks would otherwise overlap with
    everything."""
    assert context_overlap([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert context_overlap([1, 2], [3, 4]) == 0.0
    assert context_overlap([], [1]) is None


# --------------------------------------------------------------------------
# Scoring, and the refusals


def test_the_same_organisation_under_two_names_merges() -> None:
    left = as_entity("Land Transport Authority", chunks=[1, 2, 3])
    right = as_entity("Authority, Land Transport", chunks=[1, 2, 3])

    verdict = score(left, right)

    assert verdict.band == "merge"


def test_the_same_name_in_different_neighbourhoods_does_not() -> None:
    """§5.5's reason for weighting context heaviest: "Cambridge" the city and
    "Cambridge" the university share every character and nothing else."""
    left = as_entity("Cambridge", chunks=[1, 2, 3])
    right = as_entity("Cambridge", chunks=[90, 91, 92])

    verdict = score(left, right)

    assert verdict.band != "merge"
    assert verdict.signals["context"] == 0.0


def test_missing_signals_are_dropped_not_counted_as_zero() -> None:
    """An entity with no embedding and no chunks would otherwise score at most
    0.3 however exactly its name matched, and a corpus that has not finished
    embedding could resolve nothing."""
    identical = score(as_entity("Land Transport Authority"), as_entity("Land Transport Authority"))

    assert identical.signals == {"string": pytest.approx(1.0)}
    assert identical.band == "merge"


def test_the_signals_are_reported_separately() -> None:
    """A single number cannot be argued with. "string 0.9, context 0.1" is a
    merge somebody should look at."""
    verdict = score(as_entity("A B", chunks=[1]), as_entity("A B", chunks=[2]))

    assert set(verdict.signals) == {"string", "context"}


@pytest.mark.parametrize(
    "value,expected",
    [
        (1.0, "merge"),
        (AUTO_MERGE, "merge"),
        (0.7, "adjudicate"),
        (SEPARATE_BELOW - 0.01, "separate"),
    ],
)
def test_the_three_bands(value: float, expected: str) -> None:
    assert decide(value) == expected


def test_the_middle_band_is_narrow() -> None:
    """§5.5 wants it "small, cheap, and the only place a model adds value".
    A wide band is a queue nobody reads."""
    assert AUTO_MERGE - SEPARATE_BELOW <= 0.4


# --------------------------------------------------------------------------
# Blocking — what it refuses to consider


async def test_blocking_never_crosses_node_types(clean) -> None:
    """§5.5's first cheap win, enforced here so a caller cannot forget it. An
    organisation and a place that share a name are two things, always, and no
    score should be able to overturn that."""
    await an_entity(clean, "Cambridge", node_type="place")
    await an_entity(clean, "Cambridge", node_type="organisation")

    candidates = await block(clean, "Cambridge", "place")

    assert candidates
    assert {c.entity.node_type for c in candidates} == {"place"}


async def test_blocking_skips_entities_that_already_redirect(clean) -> None:
    """They are not destinations. Merging into one builds a chain somebody has
    to follow."""
    target = await an_entity(clean, "Land Transport Authority")
    await an_entity(clean, "Land Transport Authority Old", redirects_to=target.entity_id)

    candidates = await block(clean, "Land Transport Authority", "organisation")

    assert all(c.entity.redirects_to is None for c in candidates)


async def test_blocking_finds_a_shared_token(clean) -> None:
    await an_entity(clean, "Land Transport Authority")

    candidates = await block(clean, "Transport Authority of Somewhere", "organisation")

    assert any(c.entity.canonical_name == "Land Transport Authority" for c in candidates)
    assert all(c.via == "name" for c in candidates)
