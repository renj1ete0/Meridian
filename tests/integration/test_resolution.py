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

from meridian_core.models import Edge, Entity, GazetteerTerm, MergeLog
from meridian_core.resolution import (
    AUTO_MERGE,
    SEPARATE_BELOW,
    MergeError,
    block,
    context_overlap,
    decide,
    embedding_similarity,
    expansions_from_gazetteer,
    merge,
    normalise,
    reverse,
    score,
    string_similarity,
)

pytestmark = pytest.mark.usefixtures("require_db")

MARK = "resolution-test"


@pytest.fixture
async def clean(session_for):
    sess = await session_for("rw")
    await sess.execute(delete(MergeLog))
    await sess.execute(delete(Edge))
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


# --------------------------------------------------------------------------
# Merging, reversibly (task `P4-03`, §5.5)
# --------------------------------------------------------------------------
#
# §5.5: "Bad merges are worse than duplicates because conflation is invisible
# once done." So the tests that matter are the refusals, and the one that
# proves a reversal puts back *this* merge's rows rather than whatever happens
# to point at the target now.


async def an_edge(sess, from_id, to_id) -> Edge:
    row = Edge(
        from_node=from_id,
        to_node=to_id,
        relation_type="influences",
        supporting_chunk_ids=[1],
    )
    sess.add(row)
    await sess.flush()
    return row


async def test_a_merge_moves_the_edges_and_keeps_a_redirect(clean) -> None:
    """The source is kept, never deleted. Deleting it would break every
    citation that already named it — and make the merge exactly the invisible
    thing §5.5 warns about."""
    source = await an_entity(clean, "LTA")
    target = await an_entity(clean, "Land Transport Authority")
    other = await an_entity(clean, "Somewhere Else")
    edge = await an_edge(clean, source.entity_id, other.entity_id)

    await merge(clean, source.entity_id, target.entity_id, decided_by="auto")

    await clean.refresh(edge)
    await clean.refresh(source)
    await clean.refresh(target)
    assert edge.from_node == target.entity_id
    assert source.redirects_to == target.entity_id
    assert source.entity_id in (target.merged_from or [])


async def test_a_merge_carries_the_aliases_across(clean) -> None:
    """A merge that dropped them would lose the very spellings that caused it,
    so the next mention fragments again."""
    source = await an_entity(clean, "LTA", aliases=["the Authority"])
    target = await an_entity(clean, "Land Transport Authority")

    await merge(clean, source.entity_id, target.entity_id, decided_by="auto")

    await clean.refresh(target)
    assert "LTA" in (target.aliases or [])
    assert "the Authority" in (target.aliases or [])


@pytest.mark.parametrize(
    "setup,reason",
    [
        ("self", "self"),
        ("node_type", "node_type"),
        ("source_redirects", "chain"),
        ("target_redirects", "chain"),
    ],
)
async def test_the_four_refusals(clean, setup: str, reason: str) -> None:
    """Each is a merge somebody would regret, and none is recoverable by
    scoring harder."""
    a = await an_entity(clean, "Alpha")
    b = await an_entity(clean, "Beta")

    if setup == "self":
        pair = (a.entity_id, a.entity_id)
    elif setup == "node_type":
        place = await an_entity(clean, "Alpha", node_type="place")
        pair = (place.entity_id, a.entity_id)
    elif setup == "source_redirects":
        a.redirects_to = b.entity_id
        await clean.flush()
        pair = (a.entity_id, b.entity_id)
    else:
        b.redirects_to = a.entity_id
        await clean.flush()
        c = await an_entity(clean, "Gamma")
        pair = (c.entity_id, b.entity_id)

    with pytest.raises(MergeError) as raised:
        await merge(clean, pair[0], pair[1], decided_by="auto")

    assert raised.value.reason == reason


async def test_the_log_records_what_the_resolver_thought(clean) -> None:
    """`P7-10` samples merges, and a merge at 0.91 on string alone is a
    different decision from one at 0.91 with context agreeing."""
    source = await an_entity(clean, "LTA", chunks=[1, 2])
    target = await an_entity(clean, "Land Transport Authority", chunks=[1, 2])
    verdict = score(source, target)

    entry = await merge(
        clean, source.entity_id, target.entity_id, decided_by="auto", verdict=verdict
    )

    assert entry.score == pytest.approx(verdict.score)
    assert set(entry.signals) == set(verdict.signals)
    assert entry.decided_by == "auto"


async def test_a_reversal_puts_back_exactly_this_merges_rows(clean) -> None:
    """The reason the log records moved ids rather than only the fact of the
    move. Two merges into the same target are otherwise indistinguishable, and
    reversing the second would take the first's edges with it.
    """
    first = await an_entity(clean, "First Name")
    second = await an_entity(clean, "Second Name")
    target = await an_entity(clean, "Canonical Name")
    other = await an_entity(clean, "Elsewhere")

    first_edge = await an_edge(clean, first.entity_id, other.entity_id)
    second_edge = await an_edge(clean, second.entity_id, other.entity_id)

    await merge(clean, first.entity_id, target.entity_id, decided_by="auto")
    second_merge = await merge(clean, second.entity_id, target.entity_id, decided_by="auto")

    await reverse(clean, second_merge.merge_id, reversed_by="user")

    await clean.refresh(first_edge)
    await clean.refresh(second_edge)
    assert second_edge.from_node == second.entity_id, "its own edge came back"
    assert first_edge.from_node == target.entity_id, "the other merge's edge stayed put"


async def test_a_reversal_clears_the_redirect_and_the_provenance(clean) -> None:
    source = await an_entity(clean, "Old Name")
    target = await an_entity(clean, "New Name")
    entry = await merge(clean, source.entity_id, target.entity_id, decided_by="auto")

    await reverse(clean, entry.merge_id, reversed_by="user")

    await clean.refresh(source)
    await clean.refresh(target)
    assert source.redirects_to is None
    assert source.entity_id not in (target.merged_from or [])


async def test_the_log_row_survives_the_reversal(clean) -> None:
    """ "Merged and then reversed" is a more interesting fact than "never
    merged" — it is the signal that a threshold is wrong."""
    source = await an_entity(clean, "Old Name")
    target = await an_entity(clean, "New Name")
    entry = await merge(clean, source.entity_id, target.entity_id, decided_by="auto")

    await reverse(clean, entry.merge_id, reversed_by="user")

    assert entry.reversed_at is not None
    assert entry.reversed_by == "user"


async def test_a_merge_cannot_be_reversed_twice(clean) -> None:
    source = await an_entity(clean, "Old Name")
    target = await an_entity(clean, "New Name")
    entry = await merge(clean, source.entity_id, target.entity_id, decided_by="auto")
    await reverse(clean, entry.merge_id, reversed_by="user")

    with pytest.raises(MergeError) as raised:
        await reverse(clean, entry.merge_id, reversed_by="user")

    assert raised.value.reason == "already"
