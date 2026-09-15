"""The acronym harvest against a real corpus (task P5-02, spec §5.6).

Against Postgres because every claim here is about rows: which documents are in
the queue, what a second pass sees, whether a term already in the table is
duplicated or corroborated, and what happens when two documents disagree. None
of that is visible from the regex, which is tested separately and in full in
``tests/unit/test_gazetteer.py``.

The property that matters most is the quiet one: **the harvest may add to the
table but may not decide anything.** It writes unapproved rows, and the only
route from unapproved to approved is a person or several independent documents
saying the same thing. An approved row loads into the ``EntityRuler`` and
overrides statistical NER, so a regex allowed to approve its own findings would
be a regex with the final say over entity extraction.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select, update

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.gazetteer import compile_patterns
from meridian_core.models import GazetteerTerm, Source
from meridian_core.sources import upsert_source
from worker.harvest import (
    APPROVAL_THRESHOLD,
    HARVEST_ENTITY_TYPE,
    HarvestStats,
    document_text,
    harvest_batch,
    sources_awaiting_harvest,
)

pytestmark = pytest.mark.usefixtures("require_db")

#: An expansion no seeded term uses, so the assertions are about this test's
#: writes and not about whatever the dev database already holds.
EXPANSION = "Provisional Coordination Bureau"
ACRONYM = "PCB"


@pytest.fixture
def prefix() -> str:
    return f"https://harvest-{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def clean(session_for, prefix):
    sess = await session_for("rw")
    # Take every document the dev database already holds out of the queue, inside
    # this test's transaction. Without it the assertions about *the queue* are
    # assertions about whatever was last crawled, and a second pass in one test
    # reads fifteen unrelated documents.
    await sess.execute(
        update(Source)
        .where(Source.acronyms_harvested_at.is_(None))
        .values(acronyms_harvested_at=dt.datetime.now(dt.UTC))
    )
    yield sess
    await sess.rollback()
    await sess.execute(delete(Source).where(Source.url.like(f"{prefix}%")))
    await sess.execute(delete(GazetteerTerm).where(GazetteerTerm.canonical.like("Provisional %")))
    await sess.execute(delete(GazetteerTerm).where(GazetteerTerm.canonical.like("Peripheral %")))
    await sess.commit()


async def a_document(sess, url: str, texts: list[str], *, text_available: bool = True) -> Source:
    """A source with text, in the harvest's queue."""
    source, _ = await upsert_source(
        sess, url, checksum=f"sha256:{uuid.uuid4().hex}", text_available=text_available
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=text, chunk_index=i) for i, text in enumerate(texts)],
    )
    return source


async def terms_for(sess, canonical: str) -> list[GazetteerTerm]:
    rows = await sess.scalars(select(GazetteerTerm).where(GazetteerTerm.canonical == canonical))
    return list(rows)


async def harvest(sess, source_ids: list[int]) -> HarvestStats:
    stats = HarvestStats()
    await harvest_batch(sess, source_ids, stats)
    await sess.flush()
    return stats


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


async def test_a_document_with_text_is_queued(clean, prefix) -> None:
    source = await a_document(clean, f"{prefix}/a", ["Some ordinary prose."])

    assert source.source_id in await sources_awaiting_harvest(clean, 500)


async def test_a_metadata_only_document_is_not_queued(clean, prefix) -> None:
    # A scan awaiting OCR has no text to read. Leaving it in the queue means
    # re-skipping it on every pass forever, and the partial index the query
    # relies on would not even contain it.
    source = await a_document(clean, f"{prefix}/b", [], text_available=False)

    assert source.source_id not in await sources_awaiting_harvest(clean, 500)


async def test_a_harvested_document_leaves_the_queue(clean, prefix) -> None:
    source = await a_document(clean, f"{prefix}/c", [f"The {EXPANSION} ({ACRONYM}) said."])

    await harvest(clean, [source.source_id])

    assert source.source_id not in await sources_awaiting_harvest(clean, 500)


async def test_a_second_pass_does_not_count_the_same_document_twice(clean, prefix) -> None:
    # The corroboration threshold is the only thing standing between a regex and
    # an approved term. If re-reading a document counted again, one document read
    # three times would auto-approve, and the threshold would measure how often
    # the pass ran rather than how many documents agreed.
    source = await a_document(clean, f"{prefix}/d", [f"The {EXPANSION} ({ACRONYM}) said."])

    await harvest(clean, [source.source_id])
    second = await harvest(clean, await sources_awaiting_harvest(clean, 500))

    assert second.definitions == 0
    assert (await terms_for(clean, EXPANSION))[0].occurrence_count == 1


# --------------------------------------------------------------------------
# Reading a document
# --------------------------------------------------------------------------


async def test_chunks_are_rejoined_in_order(clean, prefix) -> None:
    source = await a_document(clean, f"{prefix}/e", ["first", "second", "third"])

    assert await document_text(clean, source.source_id) == "first\nsecond\nthird"


async def test_a_definition_split_across_a_chunk_boundary_is_found(clean, prefix) -> None:
    # The reason the document is rejoined rather than scanned chunk by chunk.
    # Each half alone reads as text containing no definition, so a chunk-wise
    # harvest loses exactly the definitions that fall near a boundary — a fixed
    # fraction of every long document, invisibly.
    source = await a_document(
        clean,
        f"{prefix}/f",
        ["… as set out by the Provisional", f"Coordination Bureau ({ACRONYM}) …"],
    )

    await harvest(clean, [source.source_id])

    assert await terms_for(clean, EXPANSION)


# --------------------------------------------------------------------------
# What a harvested term is
# --------------------------------------------------------------------------


async def test_a_new_term_lands_unapproved_with_the_acronym_as_an_alias(clean, prefix) -> None:
    source = await a_document(clean, f"{prefix}/g", [f"The {EXPANSION} ({ACRONYM}) published."])

    await harvest(clean, [source.source_id])
    term = (await terms_for(clean, EXPANSION))[0]

    assert term.approved is False
    assert term.aliases == [ACRONYM]
    assert term.source == "auto_acronym"
    assert term.entity_type == HARVEST_ENTITY_TYPE
    assert term.jurisdiction is None


async def test_an_unapproved_term_loads_no_patterns(clean, prefix) -> None:
    # The whole point of landing unapproved, stated as the property that makes
    # it matter: until somebody agrees, this term has no say over the model.
    source = await a_document(clean, f"{prefix}/h", [f"The {EXPANSION} ({ACRONYM}) published."])

    await harvest(clean, [source.source_id])
    term = (await terms_for(clean, EXPANSION))[0]

    assert compile_patterns([term]).patterns == ()


async def test_one_document_repeating_itself_counts_once(clean, prefix) -> None:
    # A report that defines a term in its glossary and again in each of forty
    # sections has said one thing forty times. Counting hits would auto-approve
    # on the strength of a single author's typo.
    sentence = f"The {EXPANSION} ({ACRONYM}) said. "
    source = await a_document(clean, f"{prefix}/i", [sentence * 10])

    await harvest(clean, [source.source_id])

    assert (await terms_for(clean, EXPANSION))[0].occurrence_count == 1


async def test_enough_documents_agreeing_auto_approve_it(clean, prefix) -> None:
    ids = []
    for i in range(APPROVAL_THRESHOLD):
        source = await a_document(clean, f"{prefix}/j{i}", [f"The {EXPANSION} ({ACRONYM}) said."])
        ids.append(source.source_id)

    await harvest(clean, ids)
    term = (await terms_for(clean, EXPANSION))[0]

    assert term.occurrence_count == APPROVAL_THRESHOLD
    assert term.approved is True


async def test_one_document_short_of_the_threshold_does_not(clean, prefix) -> None:
    # The converse, and what makes the test above mean anything: if approval did
    # not depend on the count, the threshold would be decoration.
    ids = []
    for i in range(APPROVAL_THRESHOLD - 1):
        source = await a_document(clean, f"{prefix}/k{i}", [f"The {EXPANSION} ({ACRONYM}) said."])
        ids.append(source.source_id)

    await harvest(clean, ids)

    assert (await terms_for(clean, EXPANSION))[0].approved is False


# --------------------------------------------------------------------------
# Disagreement
# --------------------------------------------------------------------------


async def test_two_expansions_of_one_acronym_flag_both_ambiguous(clean, prefix) -> None:
    other = "Peripheral Component Board"
    first = await a_document(clean, f"{prefix}/l", [f"The {EXPANSION} ({ACRONYM}) said."])
    second = await a_document(clean, f"{prefix}/m", [f"A {other} ({ACRONYM}) was fitted."])

    await harvest(clean, [first.source_id, second.source_id])

    assert (await terms_for(clean, EXPANSION))[0].ambiguous is True
    assert (await terms_for(clean, other))[0].ambiguous is True


async def test_an_ambiguous_terms_acronym_is_withheld_but_its_expansion_is_not(
    clean, prefix
) -> None:
    # The two mechanisms have to compose, and they compose on *surfaces*. Several
    # documents agreeing that PCB expands one way says nothing about the
    # documents where it expands the other — so the acronym is undecidable while
    # the expansion it was found beside is a perfectly ordinary term. Approval
    # means "this term is real"; ambiguity means "the short form does not
    # identify it".
    other = "Peripheral Component Board"
    ids = []
    for i in range(APPROVAL_THRESHOLD):
        source = await a_document(clean, f"{prefix}/n{i}", [f"The {EXPANSION} ({ACRONYM}) said."])
        ids.append(source.source_id)
    clash = await a_document(clean, f"{prefix}/o", [f"A {other} ({ACRONYM}) was fitted."])
    ids.append(clash.source_id)

    await harvest(clean, ids)
    term = (await terms_for(clean, EXPANSION))[0]

    assert term.approved is True
    assert term.ambiguous is True
    compiled = compile_patterns([term])
    assert [w.surface for w in compiled.withheld] == [ACRONYM]
    assert len(compiled.patterns) == 1


async def test_a_single_expansion_is_not_flagged(clean, prefix) -> None:
    source = await a_document(clean, f"{prefix}/p", [f"The {EXPANSION} ({ACRONYM}) said."])

    await harvest(clean, [source.source_id])

    assert (await terms_for(clean, EXPANSION))[0].ambiguous is False


# --------------------------------------------------------------------------
# Curated rows
# --------------------------------------------------------------------------


async def test_an_existing_term_is_corroborated_not_duplicated(clean, prefix) -> None:
    # A second row differing only in how it was written is the fragmentation
    # §5.5 exists to prevent, arriving through the table meant to fix it.
    clean.add(
        GazetteerTerm(
            canonical=EXPANSION, aliases=[ACRONYM], entity_type="agency", approved=True
        )
    )
    await clean.flush()
    source = await a_document(clean, f"{prefix}/q", [f"The {EXPANSION.lower()} ({ACRONYM}) said."])

    stats = await harvest(clean, [source.source_id])
    rows = await terms_for(clean, EXPANSION)

    assert stats.created == 0
    assert len(rows) == 1
    assert rows[0].occurrence_count == 1


async def test_the_harvest_does_not_edit_an_approved_rows_aliases(clean, prefix) -> None:
    # An approved row is already loaded into the ruler, so an alias appended here
    # would take effect with nobody having agreed to it — the approval queue
    # bypassed by the one mechanism it exists to hold back. A document that
    # mis-defines a curated term must not be able to teach it a wrong surface
    # form.
    clean.add(
        GazetteerTerm(
            canonical=EXPANSION, aliases=["PROV"], entity_type="agency", approved=True
        )
    )
    await clean.flush()
    source = await a_document(clean, f"{prefix}/r", [f"The {EXPANSION} ({ACRONYM}) said."])

    await harvest(clean, [source.source_id])
    term = (await terms_for(clean, EXPANSION))[0]

    assert term.aliases == ["PROV"]
    assert term.occurrence_count == 1


async def test_an_unapproved_row_does_learn_a_new_alias(clean, prefix) -> None:
    # The converse: a row still in the approval queue is the harvest's own, and
    # withholding evidence from it would leave a curator approving a term with
    # less information than the pass had.
    clean.add(
        GazetteerTerm(
            canonical=EXPANSION, aliases=["PROV"], entity_type="concept", source="auto_acronym"
        )
    )
    await clean.flush()
    source = await a_document(clean, f"{prefix}/s", [f"The {EXPANSION} ({ACRONYM}) said."])

    await harvest(clean, [source.source_id])

    assert (await terms_for(clean, EXPANSION))[0].aliases == ["PROV", ACRONYM]


async def test_a_curated_row_is_never_auto_approved_by_the_harvest(clean, prefix) -> None:
    # `approved=false` on a manual row is a curator's decision to keep something
    # out. Corroboration from documents must not overturn it: the auto-approval
    # rule applies to the harvest's own findings only.
    clean.add(
        GazetteerTerm(
            canonical=EXPANSION, aliases=[ACRONYM], entity_type="agency", source="manual"
        )
    )
    await clean.flush()
    ids = []
    for i in range(APPROVAL_THRESHOLD + 2):
        source = await a_document(clean, f"{prefix}/t{i}", [f"The {EXPANSION} ({ACRONYM}) said."])
        ids.append(source.source_id)

    await harvest(clean, ids)

    assert (await terms_for(clean, EXPANSION))[0].approved is False
