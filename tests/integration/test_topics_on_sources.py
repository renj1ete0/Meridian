"""Topics on sources (task P2-14, spec §12.5, §12.3).

Against Postgres, because the filter is array overlap and the interesting
questions are all about what SQL does with `NULL` and `{}` — which no double can
answer.

**The distinction the whole task turns on.** `NULL` means no pass has examined
the source; `{}` means one has, and it matched nothing. They look the same from
a filter and mean opposite things to the backfill: without it, the backfill
either re-reads the whole corpus on every run or silently claims that everything
it could not match has no topic.

**A label accumulates, it is never replaced.** A source reached under two topics
belongs to both, and overwriting would make its label depend on which crawl ran
last — so the same corpus would filter differently depending on the order pages
happened to be fetched.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Source
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source
from meridian_core.topiclabels import decide, is_offtopic

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def scope() -> str:
    """A language code unique to this run — the dev database holds a real crawl,
    so an unscoped assertion about result counts is an assertion about it."""
    return f"zz-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def term() -> str:
    return f"qxz{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def clean(session_for, scope: str):
    sess = await session_for("rw")
    yield sess
    await sess.rollback()
    await sess.execute(delete(Source).where(Source.language == scope))
    await sess.commit()


async def a_source(sess, scope: str, text: str, topic_labels=None, **kwargs) -> Source:
    source, _ = await upsert_source(
        sess,
        f"https://t{uuid.uuid4().hex[:12]}.test/a",
        checksum=f"sha256:{uuid.uuid4().hex}",
        language=scope,
        **kwargs,
    )
    # Content labels are the labeller's to write (`P2-21`); set directly here,
    # because this file is about what the column means to its readers.
    if topic_labels is not None:
        source.topic_labels = topic_labels
    await replace_chunks(sess, source.source_id, [ChunkWrite(text=text, chunk_index=0)])
    await sess.flush()
    return source


def only(scope: str, **kwargs) -> SearchFilters:
    return SearchFilters(languages=[scope], **kwargs)


# --------------------------------------------------------------------------
# NULL, empty, and populated are three states
# --------------------------------------------------------------------------


async def test_a_source_written_without_topics_records_null(clean, scope, term) -> None:
    # Not `{}`. Nothing examined it, and saying "examined, matched nothing"
    # would make the backfill skip exactly the rows it exists for.
    source = await a_source(clean, scope, f"A {term} report.")

    assert source.topic_labels is None


async def test_an_empty_list_is_stored_as_examined_rather_than_unknown(clean, scope, term) -> None:
    source = await a_source(clean, scope, f"A {term} report.", topic_labels=[])

    assert source.topic_labels == []


async def test_crawled_for_accumulates_across_crawls(clean, scope, term) -> None:
    # Provenance (`P2-21`): a source reached under two topics was crawled for
    # both, and overwriting would make the record depend on which crawl ran last.
    source = await a_source(clean, scope, f"A {term} report.", crawled_for=["walkability"])

    await upsert_source(clean, source.url, crawled_for=["on-demand-bus"])

    assert source.crawled_for == ["on-demand-bus", "walkability"]
    # And it is not a content label: nothing has read the text.
    assert source.topic_labels is None


async def test_a_repeated_crawl_topic_is_not_duplicated(clean, scope, term) -> None:
    source = await a_source(clean, scope, f"A {term} report.", crawled_for=["walkability"])

    await upsert_source(clean, source.url, crawled_for=["walkability"])

    assert source.crawled_for == ["walkability"]


# --------------------------------------------------------------------------
# The filter (§12.5)
# --------------------------------------------------------------------------


async def test_a_topic_filter_keeps_only_matching_sources(clean, scope, term) -> None:
    await a_source(clean, scope, f"A {term} walking report.", topic_labels=["walkability"])
    await a_source(clean, scope, f"A {term} bus report.", topic_labels=["on-demand-bus"])

    result = await search(clean, term, filters=only(scope, topics=["walkability"]))

    assert [h.topic_labels for h in result.hits] == [["walkability"]]


async def test_naming_two_topics_means_either(clean, scope, term) -> None:
    # Overlap, not containment. An AND across topics would return almost
    # nothing, since a document rarely sits squarely in two — and a reader
    # naming two is widening their search, not narrowing it twice.
    await a_source(clean, scope, f"A {term} walking report.", topic_labels=["walkability"])
    await a_source(clean, scope, f"A {term} bus report.", topic_labels=["on-demand-bus"])

    result = await search(clean, term, filters=only(scope, topics=["walkability", "on-demand-bus"]))

    assert len(result.hits) == 2


async def test_a_source_with_several_topics_matches_any_of_them(clean, scope, term) -> None:
    await a_source(clean, scope, f"A {term} report.", topic_labels=["walkability", "on-demand-bus"])

    result = await search(clean, term, filters=only(scope, topics=["on-demand-bus"]))

    assert len(result.hits) == 1


async def test_an_unexamined_source_is_not_claimed_by_a_topic(clean, scope, term) -> None:
    # NULL cannot be claimed for a topic: no pass has established that it
    # belongs to one, and including it would assert something nothing checked.
    await a_source(clean, scope, f"A {term} report.")

    result = await search(clean, term, filters=only(scope, topics=["walkability"]))

    assert result.hits == []


async def test_an_unexamined_source_is_still_found_without_a_topic_filter(
    clean, scope, term
) -> None:
    # The converse, and the reason NULL is not simply excluded everywhere: a
    # corpus crawled before topics existed must stay searchable.
    await a_source(clean, scope, f"A {term} report.")

    result = await search(clean, term, filters=only(scope))

    assert len(result.hits) == 1


async def test_an_examined_but_unmatched_source_is_not_claimed_either(clean, scope, term) -> None:
    await a_source(clean, scope, f"A {term} report.", topic_labels=[])

    result = await search(clean, term, filters=only(scope, topics=["walkability"]))

    assert result.hits == []


# --------------------------------------------------------------------------
# The decision (`P2-21`) — pure, so the thresholds are exact
# --------------------------------------------------------------------------


def test_several_topics_within_the_margin_are_all_labels_best_first() -> None:
    assert decide({"a": 0.60, "b": 0.58, "c": 0.30}, floor=0.45, margin=0.04) == ["a", "b"]


def test_a_topic_outside_the_margin_is_not_a_label_even_above_the_floor() -> None:
    assert decide({"a": 0.70, "b": 0.60}, floor=0.45, margin=0.04) == ["a"]


def test_nothing_above_the_floor_is_an_empty_list_not_none() -> None:
    # `{}` — examined, about none of them — which is what stops the queue.
    assert decide({"a": 0.44, "b": 0.43}, floor=0.45, margin=0.04) == []


def test_unknown_scores_are_never_off_topic() -> None:
    assert not is_offtopic(None)
    assert not is_offtopic({})
    assert is_offtopic({"a": 0.10}, floor=0.30)
    assert not is_offtopic({"a": 0.30}, floor=0.30)


# --------------------------------------------------------------------------
# What the filter control is offered (task P6-24)
# --------------------------------------------------------------------------


async def test_the_stats_carry_every_configured_topic(clean) -> None:
    # From `topic_config` rather than from the labels present on sources: the
    # second needs `DISTINCT unnest(topic_labels)` over the whole corpus, which
    # no GIN index answers, and it would make the landing page's cost grow with
    # the crawl.
    from meridian_core.stats import corpus_stats

    stats = await corpus_stats(clean)

    assert stats.topics
    assert stats.topics == sorted(set(stats.topics), key=stats.topics.index)


async def test_the_stats_count_sources_nobody_examined(clean, scope, term) -> None:
    # What lets the filter tell a reader that narrowing may be hiding material.
    # A reader who narrows and sees three results cannot otherwise know the
    # corpus holds three hundred documents that were never looked at.
    from meridian_core.stats import corpus_stats

    before = (await corpus_stats(clean)).sources_without_topics
    await a_source(clean, scope, f"A {term} report.")

    assert (await corpus_stats(clean)).sources_without_topics == before + 1


async def test_an_examined_source_is_not_counted_as_unexamined(clean, scope, term) -> None:
    from meridian_core.stats import corpus_stats

    before = (await corpus_stats(clean)).sources_without_topics
    await a_source(clean, scope, f"A {term} report.", topic_labels=[])

    assert (await corpus_stats(clean)).sources_without_topics == before


async def test_a_merged_node_or_a_note_is_not_a_concept(session_for) -> None:
    """The landing counted every node row, so a node merged into another counted twice and a
    reader's note counted as a concept; Growth did not, and the two pages disagreed by
    exactly those rows (found on a live corpus). Both count with `live_entities` now."""
    import uuid

    from meridian_core.models import Entity
    from meridian_core.stats import corpus_stats

    sess = await session_for("rw")
    before = (await corpus_stats(sess)).entities

    def node(**over) -> Entity:
        return Entity(
            canonical_name=f"counted {uuid.uuid4().hex[:6]}",
            node_type=over.pop("node_type", "organisation"),
            supporting_chunk_ids=[900],
            **over,
        )

    kept = node()
    sess.add(kept)
    await sess.flush()
    sess.add_all([node(redirects_to=kept.entity_id), node(node_type="annotation")])
    await sess.flush()

    assert (await corpus_stats(sess)).entities == before + 1
    await sess.rollback()
