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
from worker.retopic import labels_for, run_pass, unexamined
from worker.topicmatch import TopicVocabulary

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


async def a_source(sess, scope: str, text: str, **kwargs) -> Source:
    source, _ = await upsert_source(
        sess,
        f"https://t{uuid.uuid4().hex[:12]}.test/a",
        checksum=f"sha256:{uuid.uuid4().hex}",
        language=scope,
        **kwargs,
    )
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


async def test_an_empty_list_is_stored_as_examined_rather_than_unknown(
    clean, scope, term
) -> None:
    source = await a_source(clean, scope, f"A {term} report.", topic_labels=[])

    assert source.topic_labels == []


async def test_labels_accumulate_across_crawls(clean, scope, term) -> None:
    # A source reached under two topics belongs to both. Overwriting would make
    # the label depend on which crawl ran last, so the same corpus would filter
    # differently depending on the order pages happened to be fetched.
    source = await a_source(clean, scope, f"A {term} report.", topic_labels=["walkability"])

    await upsert_source(clean, source.url, topic_labels=["on-demand-bus"])

    assert source.topic_labels == ["on-demand-bus", "walkability"]


async def test_a_repeated_label_is_not_duplicated(clean, scope, term) -> None:
    source = await a_source(clean, scope, f"A {term} report.", topic_labels=["walkability"])

    await upsert_source(clean, source.url, topic_labels=["walkability"])

    assert source.topic_labels == ["walkability"]


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

    result = await search(
        clean, term, filters=only(scope, topics=["walkability", "on-demand-bus"])
    )

    assert len(result.hits) == 2


async def test_a_source_with_several_topics_matches_any_of_them(clean, scope, term) -> None:
    await a_source(
        clean, scope, f"A {term} report.", topic_labels=["walkability", "on-demand-bus"]
    )

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


async def test_an_examined_but_unmatched_source_is_not_claimed_either(
    clean, scope, term
) -> None:
    await a_source(clean, scope, f"A {term} report.", topic_labels=[])

    result = await search(clean, term, filters=only(scope, topics=["walkability"]))

    assert result.hits == []


# --------------------------------------------------------------------------
# The backfill (`python -m worker.retopic`)
# --------------------------------------------------------------------------


#: Built from phrases directly rather than through `from_terms`, which reads
#: gazetteer rows — this file is about what happens to a source once a topic is
#: known, not about how the vocabulary is assembled.
VOCAB = TopicVocabulary(phrases={"walking": ("walkability",), "footpath": ("walkability",)})


async def test_the_backfill_queue_is_the_null_rows(clean, scope, term) -> None:
    unknown = await a_source(clean, scope, f"A {term} report.")
    known = await a_source(clean, scope, f"A {term} report.", topic_labels=[])
    await clean.flush()

    queued = {s.source_id for s in await unexamined(clean, 5000)}

    assert unknown.source_id in queued
    assert known.source_id not in queued


def test_the_backfill_reads_the_served_url_not_the_requested_one() -> None:
    # A redirect to `/transport/walking/...` says something about the document;
    # the address that was asked for says only what was guessed.
    source = Source(url="https://example.test/go", extra={"final_url": "https://x.test/walking/a"})

    assert labels_for(VOCAB, source) == ["walkability"]


def test_the_backfill_falls_back_to_the_requested_url() -> None:
    source = Source(url="https://example.test/walking/a", extra=None)

    assert labels_for(VOCAB, source) == ["walkability"]


def test_an_unmatched_url_backfills_to_an_empty_list_not_null() -> None:
    # `{}` is the answer that stops the next run reading the same rows again.
    source = Source(url="https://example.test/nothing-relevant", extra=None)

    assert labels_for(VOCAB, source) == []


async def test_an_empty_vocabulary_writes_nothing(clean, scope, term) -> None:
    # It would stamp `{}` on every source in the corpus — a pass recording
    # "examined, matched nothing" about a question it never asked, and not
    # repeatable afterwards because the NULLs are gone.
    await a_source(clean, scope, f"A {term} report.")
    await clean.commit()

    stats = await run_pass(apply=True, vocabulary=TopicVocabulary())

    assert stats.examined == 0
