"""Tables and hard cuts in the chunker (`B-191`).

Measured on the live corpus (2026-10-10): 4.6% of passages sat at exactly the 2,000-character
cap, and 80% of the passages cut from converted spreadsheets. A pipe table has no blank lines,
so the chunker read it as one paragraph; no sentence boundary fires on a table row, so the last
resort cut at the cap, mid-row and mid-number ("| 1389(").

What must hold, for any table: every passage is at most the cap and still a slice of the text,
a table longer than the cap is cut only between rows, and no hard cut lands inside a word or a
number when there is whitespace to cut at.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from worker.extract.chunk import MAX_CHARS, TARGET_CHARS, chunk_text

HEADER = "| Region | Year | Trips | Share |\n|---|---|---|---|"


def table(rows: list[tuple[str, int, int, float]]) -> str:
    body = "\n".join(f"| {r} | {y} | {t} | {s:.2f} |" for r, y, t, s in rows)
    return f"{HEADER}\n{body}"


def assert_slices(text: str, chunks) -> None:
    for chunk in chunks:
        assert text[chunk.offset : chunk.end] == chunk.text


row = st.tuples(
    st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=40
    ),
    st.integers(1900, 2100),
    st.integers(0, 10**9),
    st.floats(0, 1, allow_nan=False),
)


@settings(max_examples=60, deadline=None)
@given(st.lists(row, min_size=60, max_size=400), st.text(min_size=0, max_size=300))
def test_a_long_table_is_cut_only_between_rows(rows, before) -> None:
    prose = before.replace("|", " ")
    text = f"{prose}\n\n{table(rows)}" if prose.strip() else table(rows)
    start = text.index(HEADER)
    chunks = chunk_text(text)

    assert_slices(text, chunks)
    assert all(len(c) <= MAX_CHARS for c in chunks)
    for chunk in chunks:
        # A passage inside the table begins at the start of a row.
        if chunk.offset > start:
            assert text[chunk.offset - 1] == "\n", repr(text[chunk.offset - 5 : chunk.offset + 20])
            assert chunk.text.startswith("|")
        # And ends at the end of one.
        if start < chunk.end < len(text):
            assert text[chunk.end] == "\n" or text[chunk.end :].strip() == ""


@settings(max_examples=60, deadline=None)
@given(st.lists(st.text(alphabet="abcdefghij0123456789", min_size=1, max_size=30), min_size=150))
def test_a_hard_cut_backs_off_to_whitespace(words) -> None:
    """A run with no sentence boundary at all: the cut falls between words, not inside one."""
    text = " ".join(words)
    chunks = chunk_text(text)
    assert_slices(text, chunks)
    assert all(len(c) <= MAX_CHARS for c in chunks)
    for chunk in chunks[:-1]:
        assert text[chunk.end].isspace() or text[chunk.end - 1].isspace()


def test_a_single_row_longer_than_the_cap_is_still_cut_at_the_cap() -> None:
    """A spreadsheet row of a thousand cells: nothing is emitted over the cap, ever."""
    text = f"{HEADER}\n| " + " | ".join(str(n) for n in range(1500)) + " |"
    chunks = chunk_text(text)
    assert_slices(text, chunks)
    assert all(len(c) <= MAX_CHARS for c in chunks)


def test_a_short_table_stays_with_its_prose() -> None:
    """Under the target a table is packed like any paragraph: splitting it would only
    separate a caption from its numbers."""
    text = "Table 3. Trips by region.\n\n" + table(
        [("North", 2020, 120, 0.4), ("South", 2020, 90, 0.3)]
    )
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert len(chunks[0]) < TARGET_CHARS


def test_prose_with_pipes_is_not_mistaken_for_a_table() -> None:
    """A sentence that happens to contain a pipe is split as prose, at sentences."""
    sentence = "Speeds fell (a | b notation is used here). "
    text = sentence * 80
    chunks = chunk_text(text)
    assert_slices(text, chunks)
    assert all(c.text.rstrip().endswith(".") for c in chunks[:-1])


def test_rows_of_nothing_are_left_out_and_the_rest_stays_a_slice() -> None:
    """`B-195`: converted spreadsheets pad with rows of `NaN`; thousands of passages held runs
    of them, which embed as noise and cite nothing."""
    filled = [f"| r{i} | {i} | {i * 2} |" for i in range(80)]
    padding = ["| NaN | NaN | nan |", "|  |  |  |"] * 60
    text = "| a | b | c |\n|---|---|---|\n" + "\n".join(filled[:40] + padding + filled[40:])
    chunks = chunk_text(text)
    assert_slices(text, chunks)
    assert not any("NaN" in c.text or "nan |" in c.text for c in chunks)
    assert all(len(c) <= MAX_CHARS for c in chunks)
    # Nothing that held a value was lost.
    assert all(any(row in c.text for c in chunks) for row in filled)


def test_a_continued_table_carries_its_header_into_the_embedding_view_only() -> None:
    from meridian_core.embedtext import continues_table, table_head, with_table_head

    first = "| Region | Trips |\n|---|---|\n| North | 12 |"
    rest = "| South | 9 |\n| East | 4 |"
    assert table_head(first) == "| Region | Trips |"
    assert table_head(rest) is None
    assert continues_table(rest) and not continues_table(first)
    assert not continues_table("Prose, then | a pipe.")
    assert with_table_head(rest, "| Region | Trips |").startswith("| Region | Trips |\n| South")
    assert with_table_head(rest, None) == rest
