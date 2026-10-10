"""Tables on PDF pages kept as rows (task `B-214`).

Reading order writes a table column by column, so no value stays beside its label. The
rules held here: a table becomes a pipe table where its cells were; prose around it is
untouched; two-column prose is never mistaken for a table; and a table that cannot be
placed cleanly is left alone rather than stored twice or put over something else.
"""

from __future__ import annotations

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_core.embedtext import table_head
from worker.extract.chunk import _is_table
from worker.extract.pdftables import pipe_rows, pipe_table, table_blocks, with_tables

PROSE = "Ridership on the line rose over the period against a network average of four per cent."

ROWS = [
    ("Measure", "Before", "After"),
    ("Morning trips", "1204", "1388"),
    ("Evening trips", "986", "1101"),
    ("Weekend trips", "412", "530"),
    ("Total trips", "2602", "3019"),
]


def layout_of(rows, *, widths=(47, 25)) -> str:
    """Rows as `pdftotext -layout` sets them: cells padded into aligned columns."""
    out = []
    for row in rows:
        line = ""
        for cell, width in zip(row, widths, strict=False):
            line += cell.ljust(width)
        line += row[-1]
        out.append(line)
    return "\n".join(out)


def reading_of(rows) -> str:
    """Rows as reading order sets them: each column a list of its own."""
    return "\n\n".join("\n".join(row[c] for row in rows) for c in range(len(rows[0])))


def page(table_layout: str, table_reading: str) -> tuple[str, str]:
    reading = f"{PROSE}\n{PROSE}\n\n{table_reading}\n\n{PROSE}"
    layout = f"{PROSE}\n{PROSE}\n\n\n{table_layout}\n\n\n{PROSE}"
    return reading, layout


def words(text: str) -> Counter[str]:
    return Counter(t for t in text.split() if t not in ("|", "---"))


def test_a_table_read_by_column_comes_back_as_rows_where_it_was() -> None:
    reading, layout = page(layout_of(ROWS), reading_of(ROWS))

    text, placed = with_tables(reading, layout)

    assert placed == 1
    assert "| Morning trips | 1204 | 1388 |" in text
    assert "| Total trips | 2602 | 3019 |" in text
    # The prose before and after reads as it did, in its place.
    assert text.startswith(f"{PROSE}\n{PROSE}\n\n| Measure | Before | After |")
    assert text.endswith(f"\n\n{PROSE}")
    # Nothing lost, nothing doubled.
    assert words(text) == words(reading)


def test_the_table_is_one_the_chunker_and_the_embedder_recognise() -> None:
    reading, layout = page(layout_of(ROWS), reading_of(ROWS))
    text, _ = with_tables(reading, layout)

    paragraph = next(p for p in text.split("\n\n") if p.startswith("|"))
    assert _is_table(paragraph, 0, len(paragraph))
    assert table_head(paragraph) == "| Measure | Before | After |"


def test_two_column_prose_is_not_a_table() -> None:
    left = "the scheme was introduced across the city in"
    right = "stages over several years and measured each"
    layout = "\n".join(f"{left}        {right}" for _ in range(12))
    reading = "\n".join([left] * 12 + [right] * 12)

    assert table_blocks(layout) == []
    assert with_tables(reading, layout) == (reading, 0)


def test_an_empty_cell_leaves_the_row_in_its_columns() -> None:
    rows = [("Item", "2023", "2024"), ("Alpha", "12", "15"), ("Beta", "", "9"), ("Gamma", "7", "8")]
    found = pipe_rows(layout_of(rows).split("\n"))
    assert found[2] == ["Beta", "", "9"]


def test_a_crowded_row_is_still_cut_between_its_values() -> None:
    # The last two columns sit one space apart on one row only.
    block = [
        "Label        First value    Second     Third",
        "Row one      (1.0 to 2.0)   (3 to 4)   (5 to 6)",
        "Row two      (1.5 to 2.5)   (3 to 4)   (5 to 6)",
        "Row three    (1.2 to 2.2)   (3 to 4)   (5 to 6)",
        "Row four     (1.1 to 2.1)   (3.1 to 4) (5 to 6)",
    ]
    rows = pipe_rows(block)
    assert rows[4] == ["Row four", "(1.1 to 2.1)", "(3.1 to 4)", "(5 to 6)"]


def test_a_label_wrapped_onto_a_second_line_joins_its_row() -> None:
    block = [
        "Measure               2023      2024",
        "Trips per person      12.1      13.4",
        "Share of trips made   41%       44%",
        "   on foot",
        "Distance walked       1.2       1.3",
    ]
    rows = pipe_rows(block)
    assert ["Share of trips made on foot", "41%", "44%"] in rows


def test_a_pipe_inside_a_cell_is_escaped() -> None:
    assert pipe_table([["a|b", "1"], ["c", "2"]]).split("\n")[0] == "| a\\|b | 1 |"


def test_a_table_only_partly_found_is_left_alone() -> None:
    # Reading order split the table's columns around a paragraph, so the run that holds
    # most of its cells holds only some of them. Placed, the rest would be stored twice.
    reading = (
        f"{PROSE}\n\nMeasure\nMorning trips\nEvening trips\nWeekend trips\nTotal trips\n\n"
        f"{PROSE}\n\nBefore\n1204\n986\n412\n2602\n\nAfter\n1388\n1101\n530\n3019"
    )
    layout = f"{PROSE}\n\n{layout_of(ROWS)}\n\n{PROSE}"

    assert with_tables(reading, layout) == (reading, 0)


def test_a_table_is_not_put_over_another_tables_cells() -> None:
    other = [
        ("Mode", "Share", "Count"),
        ("Bus", "41%", "300"),
        ("Rail", "35%", "210"),
        ("Walk", "24%", "90"),
    ]
    # Only the other table's cells are in reading order; this one's never made it.
    reading = f"{PROSE}\n\n{reading_of(other)}\n\n{PROSE}"
    layout = f"{PROSE}\n\n{layout_of(ROWS)}\n\n\n{layout_of(other)}\n\n{PROSE}"

    text, placed = with_tables(reading, layout)

    assert placed == 1
    assert "| Bus | 41% | 300 |" in text
    assert "Morning trips" not in text


def test_a_page_with_no_table_is_returned_as_it_was() -> None:
    reading = f"{PROSE}\n{PROSE}"
    assert with_tables(reading, reading) == (reading, 0)


LABEL = st.text(alphabet="abcdefghij", min_size=3, max_size=10).map(str.capitalize)
VALUE = st.integers(min_value=0, max_value=99_999).map(str)


@settings(max_examples=200, deadline=None)
@given(
    labels=st.lists(LABEL, min_size=3, max_size=12, unique=True),
    columns=st.integers(min_value=2, max_value=5),
    data=st.data(),
)
def test_any_table_comes_back_as_its_own_rows(labels, columns, data) -> None:
    """Whatever the grid, the rows recovered are exactly the rows drawn."""
    head = ("Name", *(f"Y{c}" for c in range(columns)))
    rows = [head] + [(label, *(data.draw(VALUE) for _ in range(columns))) for label in labels]
    reading, layout = page(layout_of(rows, widths=(14,) + (9,) * (columns - 1)), reading_of(rows))

    text, placed = with_tables(reading, layout)

    # A clean grid of labels and numbers is always found and always placed.
    assert placed == 1
    table = next(p for p in text.split("\n\n") if p.startswith("|"))
    lines = [line for line in table.split("\n") if not line.startswith("| ---")]
    assert [line.strip("| ").split(" | ") for line in lines] == [list(r) for r in rows]
    assert words(text) == words(reading)
