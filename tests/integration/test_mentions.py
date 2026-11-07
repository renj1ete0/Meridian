"""Turning a name into a node, against a real Postgres (task `P4-16`, §5.5).

`tests/integration/test_resolution.py` covers the four steps `P4-02` built.
This covers the one thing that has to happen between them and `add_edge`:
deciding whether a name a model just read is something the graph already
holds, and writing the row when it is not.

The test that matters most is the boring one — **the same name in two
different passages is one node**. It is boring because it is what anybody
would assume, and it is here because the assumption was false: scoring a
fresh mention against an entity with the batch's chunks as its "context"
counts a non-overlap as disagreement, which drags an exact name match under
the separation threshold and founds a new node for every passage.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from meridian_core.mentions import SplitRefused, resolve_mention, split_match
from meridian_core.models import Entity, Notification, ResolutionMatch
from meridian_core.validation import ValidationError

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.UTC)
PROVENANCE = {"produced_by": "test-agent", "model": "test-model-1", "quality_tier": 3}


@pytest.fixture
def marker() -> str:
    return f"men{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def sess(session_for, marker: str):
    session = await session_for("rw")
    await session.rollback()

    yield session

    await session.rollback()
    await session.execute(delete(Entity).where(Entity.canonical_name.like(f"%{marker}%")))
    await session.execute(delete(Notification).where(Notification.title.like(f"%{marker}%")))
    await session.commit()


async def resolve(sess, name: str, marker: str, *, chunks: list[int], **kwargs):
    return await resolve_mention(
        sess,
        name=name,
        node_type=kwargs.pop("node_type", "organisation"),
        supporting_chunk_ids=chunks,
        now=NOW,
        **PROVENANCE,
        **kwargs,
    )


async def test_a_name_nobody_has_seen_becomes_a_node(sess, marker: str) -> None:
    resolved = await resolve(sess, f"{marker} Authority", marker, chunks=[1])

    assert resolved.created
    assert resolved.entity.entity_id is not None
    assert resolved.entity.supporting_chunk_ids == [1]


async def test_the_new_node_records_what_produced_it(sess, marker: str) -> None:
    """§2.3: every derived row carries its provenance, or nothing downstream
    can tell a frontier model's node from a cheap one's."""
    resolved = await resolve(sess, f"{marker} Authority", marker, chunks=[1])

    assert resolved.entity.produced_by == "test-agent"
    assert resolved.entity.model == "test-model-1"
    assert resolved.entity.quality_tier == 3


async def test_the_same_name_in_two_passages_is_one_node(sess, marker: str) -> None:
    """The fragmentation §5.5 opens with. Two mentions, different chunks, and
    nothing in common but the name — which is the strongest signal there is."""
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Authority", marker, chunks=[2])

    assert not second.created
    assert second.entity.entity_id == first.entity.entity_id


async def test_resolving_again_accumulates_the_evidence(sess, marker: str) -> None:
    """Context is §5.5's heaviest signal, and it only gets heavier if each
    mention's chunks join the ones already there."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Authority", marker, chunks=[2, 3])

    assert second.entity.supporting_chunk_ids == [1, 2, 3]


async def test_a_different_spelling_is_kept_as_an_alias(sess, marker: str) -> None:
    """The canonical name stays the one that got there first; the variant is
    recorded, because the next resolution blocks on aliases too."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2])

    assert not second.created
    assert f"{marker} Authority Pte Ltd" in (second.entity.aliases or [])


async def test_a_spelling_that_differs_only_in_case_is_not_an_alias(sess, marker: str) -> None:
    """Blocking is case-insensitive, so such an "alias" is a row that helps
    nothing and one more string for a person reading the node to weigh."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} AUTHORITY", marker, chunks=[2])

    assert not second.created
    assert not second.entity.aliases


async def test_two_node_types_sharing_a_name_are_two_nodes(sess, marker: str) -> None:
    """§5.5's first cheap win. An organisation and a place that share a name
    are two things, always, and no score may overturn it."""
    first = await resolve(sess, f"{marker} Ashford", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Ashford", marker, chunks=[1], node_type="place")

    assert second.created
    assert second.entity.entity_id != first.entity.entity_id


async def test_two_stated_jurisdictions_are_two_nodes(sess, marker: str) -> None:
    """One name routinely denotes unrelated things in two countries, and those
    are exactly the cases where every similarity signal says "the same"."""
    first = await resolve(sess, f"{marker} Transport Agency", marker, chunks=[1], jurisdiction="SG")
    second = await resolve(
        sess, f"{marker} Transport Agency", marker, chunks=[1], jurisdiction="NZ"
    )

    assert second.created
    assert second.entity.entity_id != first.entity.entity_id


async def test_an_unstated_jurisdiction_does_not_separate(sess, marker: str) -> None:
    """Most passages never say. Treating silence as disagreement would split
    every entity the moment one document happened to name a country."""
    first = await resolve(sess, f"{marker} Transport Agency", marker, chunks=[1], jurisdiction="SG")
    second = await resolve(sess, f"{marker} Transport Agency", marker, chunks=[1])

    assert second.entity.entity_id == first.entity.entity_id


async def test_the_middle_band_creates_a_node_and_asks_a_person(sess, marker: str) -> None:
    """§5.5 queues the middle band for adjudication. The disposition is a
    second node plus a notification, never a merge on the balance of
    probability: a duplicate is visible and recoverable, a conflation is
    neither."""
    first = await resolve(sess, f"{marker} Regional Transit Board", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Regional Transit Bureau", marker, chunks=[1])

    if second.band != "adjudicate":
        pytest.skip(f"these two names score in the {second.band} band, not the middle one")

    assert second.created
    assert second.entity.entity_id != first.entity.entity_id
    queued = (
        await sess.scalars(
            select(Notification).where(
                Notification.notification_type == "merge_adjudication",
                Notification.title.like(f"%{marker}%"),
            )
        )
    ).all()
    assert len(queued) == 1
    assert queued[0].payload["candidate"] == first.entity.entity_id


async def test_a_separate_name_raises_nothing_for_anybody_to_read(sess, marker: str) -> None:
    """A queue that filled with obvious non-matches would train whoever reads
    it to approve without looking."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Office of Coastal Records", marker, chunks=[1])

    queued = (
        await sess.scalars(
            select(Notification).where(
                Notification.notification_type == "merge_adjudication",
                Notification.title.like(f"%{marker}%"),
            )
        )
    ).all()
    assert queued == []


async def test_a_run_may_not_create_an_annotation(sess, marker: str) -> None:
    """An annotation is a note a person wrote (§12.5, `P6-05`) and is the only
    layer of the graph that reflects the reader's own thinking. Nothing
    downstream could tell a forged one from a real one."""
    with pytest.raises(ValidationError, match="annotation"):
        await resolve(sess, f"{marker} Note", marker, chunks=[1], node_type="annotation")


async def test_a_blank_mention_is_refused(sess, marker: str) -> None:
    with pytest.raises(ValidationError):
        await resolve(sess, "   ", marker, chunks=[1])


async def test_an_acronym_resolves_to_itself_even_when_it_has_an_expansion(
    sess, marker: str
) -> None:
    """`B-35`. With a gazetteer expansion, blocking searched only the expanded
    words — so the node literally named by the acronym was never a candidate,
    the expansion's node scored into the middle band, and every re-read of the
    acronym founded another copy of it. Found on the first live synthesis run,
    where a resumed batch created a second node for a name it had already made.
    """
    acronym = marker.upper()
    # The expansion shares no word with the acronym, as a real one does not —
    # which is exactly why searching by the expansion alone cannot find it.
    expansions = {marker: "operational design domain"}

    first = await resolve(sess, acronym, marker, chunks=[1], node_type="odd", expansions=expansions)
    await resolve(
        sess,
        f"operational design domains {marker}",
        marker,
        chunks=[2],
        node_type="odd",
        expansions=expansions,
    )
    again = await resolve(sess, acronym, marker, chunks=[3], node_type="odd", expansions=expansions)

    assert not again.created, "re-reading a name that already has a node made another one"
    assert again.entity.entity_id == first.entity.entity_id
    count = await sess.scalar(
        select(func.count()).select_from(Entity).where(Entity.canonical_name == acronym)
    )
    assert count == 1


async def test_a_name_too_short_to_tokenise_still_finds_itself(sess, marker: str) -> None:
    """Blocking drops tokens of two characters or fewer, so a two-letter name
    produced no candidates at all and was re-created on every mention."""
    name = "Q7"
    first = await resolve(sess, name, marker, chunks=[1], node_type="concept")
    try:
        again = await resolve(sess, name, marker, chunks=[2], node_type="concept")
        assert not again.created
        assert again.entity.entity_id == first.entity.entity_id
    finally:
        await sess.rollback()


async def test_two_nodes_with_the_same_name_resolve_to_the_same_one_every_time(
    sess, marker: str
) -> None:
    """`B-37`. With two exact matches — a duplicate made before `B-35` — the
    candidates came back in whatever order the table scan found them, and a
    tie went to whichever was first. Updating a row moves it in the heap, so
    the choice flipped between reads: a resumed batch resolved the name to
    the other node, the entity list in its `tag` prompt changed, and the batch
    could never complete. Ties now go to the oldest node, always.
    """
    name = f"{marker} Duplicate"
    older = Entity(canonical_name=name, node_type="concept", supporting_chunk_ids=[1])
    newer = Entity(canonical_name=name, node_type="concept", supporting_chunk_ids=[2])
    sess.add(older)
    await sess.flush()
    sess.add(newer)
    await sess.flush()
    # Rewriting the older row moves its tuple past the newer one, which is
    # exactly what happens to a node every time a mention adds its chunks.
    older.supporting_chunk_ids = [1, 3]
    await sess.flush()

    for chunk in (10, 11, 12):
        resolved = await resolve(sess, name, marker, chunks=[chunk], node_type="concept")
        assert not resolved.created
        assert resolved.entity.entity_id == older.entity_id


def _near(cos: float) -> list[float]:
    """A unit vector at a chosen cosine from the first axis."""
    import math

    from meridian_core.models.source import EMBEDDING_DIM

    vector = [0.0] * EMBEDDING_DIM
    vector[0] = cos
    vector[1] = math.sqrt(1 - cos * cos)
    return vector


async def test_two_wordings_of_one_thing_reach_a_person_once_embedded(sess, marker) -> None:
    """`B-40`. Resolution was built to weigh meaning as well as words (§5.5),
    but no mention or entity ever carried a vector, so two names for the same
    thing that share few words scored as strangers and became separate nodes
    with nobody asked. With the embedding they land in the middle band: a
    separate node, and a notice for a person — never a silent merge."""
    first = await resolve(
        sess,
        "autonomous shuttlecraft",
        marker,
        chunks=[1],
        node_type="failure_mode",
        embedding=_near(1.0),
    )
    assert first.entity.embedding is not None, "a new node keeps the vector it was resolved with"

    second = await resolve(
        sess,
        "driverless shuttlecraft",
        marker,
        chunks=[2],
        node_type="failure_mode",
        embedding=_near(0.93),
    )

    assert second.created
    assert second.band == "adjudicate"
    asked = await sess.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.title == "Possible duplicate: driverless shuttlecraft")
    )
    assert asked == 1


async def test_without_the_embedding_the_same_pair_is_judged_on_words_alone(sess, marker) -> None:
    """The contrast that makes the case above mean something: the words alone
    put this pair below the middle band."""
    await resolve(sess, "autonomous shuttlecraft", marker, chunks=[1], node_type="failure_mode")
    second = await resolve(
        sess, "driverless shuttlecraft", marker, chunks=[2], node_type="failure_mode"
    )
    assert second.created and second.band == "separate"


async def test_an_unrelated_name_with_a_distant_vector_stays_separate(sess, marker) -> None:
    await resolve(
        sess,
        "autonomous shuttlecraft",
        marker,
        chunks=[1],
        node_type="failure_mode",
        embedding=_near(1.0),
    )
    other = await resolve(
        sess,
        "rainfall intensity",
        marker,
        chunks=[2],
        node_type="failure_mode",
        embedding=_near(0.1),
    )
    assert other.created and other.band == "separate"


async def test_an_older_node_gains_a_vector_when_resolved_with_one(sess, marker) -> None:
    """Nodes made before `B-40` have none; the first embedded mention fills it."""
    old = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    assert old.entity.embedding is None

    again = await resolve(sess, f"{marker} Authority", marker, chunks=[2], embedding=_near(1.0))

    assert again.entity.entity_id == old.entity.entity_id
    assert again.entity.embedding is not None


async def test_backfill_embeds_entities_that_have_no_vector(sess, marker) -> None:
    from meridian_core.mentions import embed_missing_entities

    made = await resolve(sess, f"{marker} Registry", marker, chunks=[1])

    class Fake:
        async def embed(self, texts):
            return [_near(1.0) for _ in texts]

    done = await embed_missing_entities(sess, Fake(), limit=10_000)

    assert done >= 1
    await sess.refresh(made.entity)
    assert made.entity.embedding is not None


async def _matches(sess, marker: str) -> list[ResolutionMatch]:
    return list(
        await sess.scalars(
            select(ResolutionMatch)
            .where(ResolutionMatch.mention.like(f"%{marker}%"))
            .order_by(ResolutionMatch.match_id)
        )
    )


async def test_a_match_to_an_existing_node_is_recorded_for_the_audit(sess, marker: str) -> None:
    """`B-273`, §14.1: "conflation is invisible once done, so it must be looked for
    deliberately". The high band joins a mention to a node with no merge, so the decision is
    recorded where the graph audit can draw it: the node as the resolver saw it, before the
    mention's name and passages joined it, the score and every signal, and who named it."""
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(
        sess, f"{marker} Authority Pte Ltd", marker, chunks=[3, 2, 3], run_id=4242
    )
    assert not second.created

    [match] = await _matches(sess, marker)
    assert match.entity_id == first.entity.entity_id
    assert match.mention == f"{marker} Authority Pte Ltd"
    assert match.node_type == "organisation"
    assert match.supporting_chunk_ids == [2, 3]
    # Before, not after: the node's own name, no alias yet, and only its first passage.
    assert match.node_name == f"{marker} Authority"
    assert match.node_aliases == []
    assert match.node_chunk_ids == [1]
    assert match.score == pytest.approx(second.verdict.score)
    assert match.signals == second.verdict.signals
    assert (match.run_id, match.produced_by, match.model, match.quality_tier) == (
        4242,
        "test-agent",
        "test-model-1",
        3,
    )
    assert match.at == NOW


async def test_every_match_is_recorded_not_only_the_first(sess, marker: str) -> None:
    """Each mention is its own decision; the second sees the node the first enlarged."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2])
    await resolve(sess, f"{marker} Authority", marker, chunks=[5])

    first, second = await _matches(sess, marker)
    assert second.node_aliases == [f"{marker} Authority Pte Ltd"]
    assert second.node_chunk_ids == [1, 2]
    assert first.run_id is None  # resolved outside a run: recorded all the same


async def test_a_new_node_records_no_match(sess, marker: str) -> None:
    """Founding a node, or the middle band's second node, is not "these are one thing"; the
    middle band is already in front of a person as a possible duplicate."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Office of Coastal Records", marker, chunks=[1])
    await resolve(sess, f"{marker} Regional Transit Board", marker, chunks=[1])
    adjudicated = await resolve(sess, f"{marker} Regional Transit Bureau", marker, chunks=[1])

    assert adjudicated.created
    assert await _matches(sess, marker) == []


# --------------------------------------------------------------------------
# Splitting a wrong match back off (`B-274`)
# --------------------------------------------------------------------------

LATER = NOW + dt.timedelta(days=3)


async def test_a_split_gives_the_mention_a_node_of_its_own(sess, marker: str) -> None:
    """A match graded wrong is undone as far as it can be: the mention becomes the node it
    should have founded, and the node it joined loses the name and the passages it brought."""
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1], jurisdiction="SG")
    await resolve(
        sess, f"{marker} Authority Pte Ltd", marker, chunks=[2, 3], jurisdiction="SG", run_id=9
    )
    [match] = await _matches(sess, marker)

    done = await split_match(sess, match.match_id, split_by="user", now=LATER)

    old = await sess.get(Entity, first.entity.entity_id)
    new = await sess.get(Entity, done.split_entity_id)
    assert (done.entity_id, done.alias_removed, done.chunks_moved) == (old.entity_id, True, 2)
    assert old.aliases == [] and old.supporting_chunk_ids == [1]
    assert new.entity_id != old.entity_id
    assert (new.canonical_name, new.node_type, new.jurisdiction) == (
        f"{marker} Authority Pte Ltd",
        "organisation",
        "SG",
    )
    assert new.supporting_chunk_ids == [2, 3]
    # §2.3: the new node is the model's reading, not the person's; it carries the model's name.
    assert (new.produced_by, new.model, new.quality_tier) == ("test-agent", "test-model-1", 3)
    assert (match.split_at, match.split_by, match.split_entity_id) == (
        LATER,
        "user",
        new.entity_id,
    )


async def test_a_split_leaves_what_another_match_still_rests_on(sess, marker: str) -> None:
    """Two mentions brought the same name and passage; splitting one off must not take away
    what the other, still standing, rests on."""
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2, 4])
    await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2])
    one, _ = await _matches(sess, marker)

    done = await split_match(sess, one.match_id, split_by="user", now=LATER)

    old = await sess.get(Entity, first.entity.entity_id)
    assert done.alias_removed is False and done.chunks_moved == 1
    assert old.aliases == [f"{marker} Authority Pte Ltd"]
    assert old.supporting_chunk_ids == [1, 2]


async def test_splitting_an_exact_name_match_keeps_the_node_its_name(sess, marker: str) -> None:
    """One name for two things: the split founds a second node of the same name, and the
    first keeps its name, since it was never an alias."""
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Authority", marker, chunks=[5])
    [match] = await _matches(sess, marker)

    done = await split_match(sess, match.match_id, split_by="user", now=LATER)

    old = await sess.get(Entity, first.entity.entity_id)
    new = await sess.get(Entity, done.split_entity_id)
    assert old.canonical_name == new.canonical_name == f"{marker} Authority"
    assert old.supporting_chunk_ids == [1] and new.supporting_chunk_ids == [5]
    assert done.alias_removed is False


async def test_a_split_that_cannot_be_made_is_refused_and_changes_nothing(
    sess, marker: str
) -> None:
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2])
    [match] = await _matches(sess, marker)

    with pytest.raises(SplitRefused, match="who"):
        await split_match(sess, match.match_id, split_by="", now=LATER)
    with pytest.raises(SplitRefused, match="No match"):
        await split_match(sess, 999_999_999_999, split_by="user", now=LATER)

    done = await split_match(sess, match.match_id, split_by="user", now=LATER)
    count = await sess.scalar(
        select(func.count(Entity.entity_id)).where(Entity.canonical_name.like(f"%{marker}%"))
    )
    with pytest.raises(SplitRefused, match=f"Already split off, as node #{done.split_entity_id}"):
        await split_match(sess, match.match_id, split_by="user", now=LATER)
    assert (
        await sess.scalar(
            select(func.count(Entity.entity_id)).where(Entity.canonical_name.like(f"%{marker}%"))
        )
        == count
    ), "a second split founds no second node"

    # A node merged away since: the split names where it went instead of guessing.
    await resolve(sess, f"{marker} Board", marker, chunks=[7])
    await resolve(sess, f"{marker} Board", marker, chunks=[8])
    target = await resolve(sess, f"{marker} Office of Coastal Records", marker, chunks=[9])
    board_match = (await _matches(sess, marker))[-1]
    board = await sess.get(Entity, board_match.entity_id)
    board.redirects_to = target.entity.entity_id
    await sess.flush()
    with pytest.raises(SplitRefused, match=f"merged into #{target.entity.entity_id}"):
        await split_match(sess, board_match.match_id, split_by="user", now=LATER)
    assert board_match.split_at is None
    assert first.entity.entity_id != target.entity.entity_id


async def test_the_database_refuses_half_a_split(sess, marker: str) -> None:
    """Raw SQL, so the CHECK is what is tested."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2])
    [match] = await _matches(sess, marker)
    with pytest.raises(IntegrityError, match="match_split_together"):
        async with sess.begin_nested():
            await sess.execute(
                text("UPDATE resolution_matches SET split_at = now() WHERE match_id = :id"),
                {"id": match.match_id},
            )
