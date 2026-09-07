"""Chunking (task P2-02, spec §5.3, §6.2).

The invariant everything else rests on is that a chunk is a verbatim *slice* of
the text it came from: `source[offset:offset + len(text)] == text`. §5.3 requires
the offset to be captured at extraction time because "reconstructing it later is
painful and often impossible", and an offset that does not locate the passage is
worse than no offset — it makes a citation look checkable when it is not.

The rest is about the splits being defensible: whole paragraphs where possible,
sentences where a paragraph is too long, a hard cut only when there is no
boundary left, and no chunk so small that citing it proves nothing.
"""

from __future__ import annotations

import pytest

from worker.extract.chunk import (
    MAX_CHARS,
    MIN_CHARS,
    TARGET_CHARS,
    TextChunk,
    chunk_text,
)

SENTENCE = "Ridership on the Downtown Line rose by eleven per cent over the period. "
PARAGRAPH = SENTENCE * 4  # ~290 chars


def document(paragraphs: int) -> str:
    return "\n\n".join(f"Paragraph {i}. {PARAGRAPH}" for i in range(paragraphs))


def assert_slices(text: str, chunks: list[TextChunk]) -> None:
    """Every chunk locates itself in the text, exactly."""
    for chunk in chunks:
        assert text[chunk.offset : chunk.end] == chunk.text, (
            f"chunk {chunk.index} at offset {chunk.offset} is not a slice of the source"
        )


# --------------------------------------------------------------------------
# The slice invariant
# --------------------------------------------------------------------------


@pytest.mark.parametrize("paragraphs", [1, 2, 5, 20, 60])
def test_every_chunk_is_a_verbatim_slice(paragraphs: int) -> None:
    text = document(paragraphs)

    assert_slices(text, chunk_text(text))


def test_the_offset_is_not_recovered_by_searching_for_the_text() -> None:
    """A repeated passage must not resolve to its first occurrence.

    Boilerplate disclaimers and repeated table headers make this the normal
    case, not a corner one, and a citation that silently points at the wrong
    copy is the kind of wrong that never surfaces as an error.
    """
    repeated = f"{PARAGRAPH}\n\n" * 8
    chunks = chunk_text(repeated)

    assert_slices(repeated, chunks)
    assert len(chunks) > 1
    assert chunks[1].offset > repeated.index(chunks[1].text), (
        "the second chunk resolved to the first occurrence of its text"
    )


def test_leading_whitespace_moves_the_offset_rather_than_being_stripped_off_it() -> None:
    """Stripping without moving the offset breaks every citation by the indent."""
    text = "\n\n   " + PARAGRAPH

    chunk = chunk_text(text)[0]

    assert chunk.offset == 5
    assert text[chunk.offset : chunk.end] == chunk.text


def test_chunks_carry_no_edge_whitespace() -> None:
    text = document(10)

    for chunk in chunk_text(text):
        assert chunk.text == chunk.text.strip()


# --------------------------------------------------------------------------
# Ordering and completeness
# --------------------------------------------------------------------------


def test_chunks_are_indexed_from_zero_without_gaps() -> None:
    """`chunks.chunk_index` is uniquely constrained per source; a gap or a
    repeat is a write that fails or a document that reads out of order."""
    chunks = chunk_text(document(30))

    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_offsets_increase_and_chunks_do_not_overlap() -> None:
    """No overlap is a deliberate choice, so it is worth asserting.

    Overlap compensates for blind splitting cutting through an idea; splitting
    on paragraph boundaries fixes the same problem directly, and paying for both
    duplicates text in the table, the embedding index, and every batch the slow
    loop reads.
    """
    chunks = chunk_text(document(30))

    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier.offset < later.offset
        assert earlier.end <= later.offset


def test_no_prose_is_dropped_between_chunks() -> None:
    """Whatever falls between chunks must be separators, never content."""
    text = document(20)
    chunks = chunk_text(text)

    gaps = [text[a.end : b.offset] for a, b in zip(chunks, chunks[1:], strict=False)]
    assert all(gap.strip() == "" for gap in gaps), f"content lost between chunks: {gaps}"
    assert text[: chunks[0].offset].strip() == ""
    assert text[chunks[-1].end :].strip() == ""


# --------------------------------------------------------------------------
# Sizes
# --------------------------------------------------------------------------


def test_nothing_exceeds_the_hard_cap() -> None:
    """The cap is what keeps a chunk inside the embedding window (§4)."""
    for text in (document(40), PARAGRAPH * 30, "x" * 9000):
        for chunk in chunk_text(text):
            assert len(chunk) <= MAX_CHARS, f"{len(chunk)} > {MAX_CHARS}"


def test_short_paragraphs_are_packed_rather_than_left_alone() -> None:
    """A page of one-line paragraphs would otherwise be one chunk per line.

    A two-sentence chunk retrieves badly and cites nothing worth reading.
    """
    text = "\n\n".join("A short line about transit." for _ in range(40))

    chunks = chunk_text(text)

    assert len(chunks) < 10
    assert all(len(c) > MIN_CHARS for c in chunks[:-1])


def test_whole_paragraphs_are_preferred_over_hitting_the_target_exactly() -> None:
    """Structure beats arithmetic: the split falls on a blank line."""
    text = document(12)
    chunks = chunk_text(text)

    for chunk in chunks:
        assert chunk.text.startswith("Paragraph "), (
            "a chunk began mid-paragraph while a boundary was available"
        )


def test_an_oversized_paragraph_is_split_at_sentence_boundaries() -> None:
    """The first fallback, for a legal recital or a flattened table."""
    text = SENTENCE * 60  # one paragraph, far over the cap

    chunks = chunk_text(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("Ridership"), "a chunk began mid-sentence"
        assert len(chunk) <= MAX_CHARS
    assert_slices(text, chunks)


def test_a_single_sentence_over_the_cap_is_cut_at_the_cap() -> None:
    """The last resort. There is no boundary left to respect, and emitting it
    whole would overflow the embedding window."""
    text = "z" * (MAX_CHARS * 3 + 17)

    chunks = chunk_text(text)

    assert [len(c) for c in chunks] == [MAX_CHARS, MAX_CHARS, MAX_CHARS, 17]
    assert_slices(text, chunks)


# --------------------------------------------------------------------------
# Runts
# --------------------------------------------------------------------------


def test_a_trailing_runt_is_merged_backwards() -> None:
    """A stranded caption retrieves badly and proves nothing when cited."""
    text = document(4) + "\n\nFig 1."

    chunks = chunk_text(text)

    assert chunks[-1].text.endswith("Fig 1.")
    assert len(chunks[-1]) > MIN_CHARS


def test_a_leading_runt_is_merged_forwards() -> None:
    """A document opening with a `# Title` line is the common case.

    It has no predecessor to merge into, so the merge goes the other way — and
    the offset stays the earlier of the two, keeping offsets increasing.
    """
    text = f"# Annual Report\n\n{PARAGRAPH}"

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].offset == 0
    assert chunks[0].text.startswith("# Annual Report")


def test_a_runt_that_cannot_fit_anywhere_stands_alone() -> None:
    """Merging it would push a chunk past the cap, which is the worse failure."""
    text = "Short.\n\n" + "y" * (MAX_CHARS + 500)

    chunks = chunk_text(text)

    assert chunks[0].text == "Short."
    assert all(len(c) <= MAX_CHARS for c in chunks)


def test_merging_never_pushes_a_chunk_over_the_cap() -> None:
    for text in (document(30) + "\n\na.", "b.\n\n" + PARAGRAPH * 8):
        assert all(len(c) <= MAX_CHARS for c in chunk_text(text))


# --------------------------------------------------------------------------
# Nothing to chunk
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n\n", "\t \n"])
def test_empty_text_yields_no_chunks(text: str) -> None:
    """A source with no chunks is metadata-only (§6.5), not a failure."""
    assert chunk_text(text) == []


def test_a_single_short_line_is_still_a_chunk() -> None:
    """Short is not nothing. The alternative is silently dropping a page whose
    only content is one sentence, which is a real shape for a statistics release.
    """
    chunks = chunk_text("Ridership rose eleven per cent.")

    assert len(chunks) == 1
    assert chunks[0].offset == 0


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_the_sizes_are_ordered_sensibly() -> None:
    assert MIN_CHARS < TARGET_CHARS <= MAX_CHARS


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_chars": 3000, "max_chars": 2000},
        {"min_chars": 5000},
        {"target_chars": 0},
    ],
)
def test_incoherent_sizes_are_refused(kwargs: dict) -> None:
    """A silently-ignored bad setting produces a corpus chunked wrongly, which
    is only discoverable by re-reading it."""
    with pytest.raises(ValueError):
        chunk_text(document(3), **kwargs)


def test_the_sizes_are_actually_honoured_when_overridden() -> None:
    chunks = chunk_text(document(20), target_chars=400, max_chars=600, min_chars=50)

    assert all(len(c) <= 600 for c in chunks)
    assert len(chunks) > len(chunk_text(document(20)))


def test_a_chunk_reports_its_own_length_and_end() -> None:
    chunk = TextChunk(text="abcde", offset=10, index=0)

    assert len(chunk) == 5
    assert chunk.end == 15
