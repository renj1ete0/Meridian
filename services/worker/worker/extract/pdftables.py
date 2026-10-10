"""Tables on PDF pages, kept as rows (task `B-214`).

`pdftotext` in reading order writes a table column by column: every row label, then each
column's values as a list of their own, so no value stays beside what it measures. With
`-layout` the columns stay aligned, but two-column prose is interleaved line by line, so
layout text cannot replace a page wholesale. Instead each page's layout text is searched
for table blocks; a block found is turned into a Markdown pipe table, which the chunker
already keeps whole and cuts between rows (`B-191`), and put where the block's cells sit in
the reading-order text. Anything not found with confidence is left as it was.

Measured on 100 stored PDFs (1,912 non-blank pages): about a third of pages hold a table;
on those, reading order has a median 3.6× the lines layout has. The rule here flagged
pages at about 87% precision on 60 hand-labelled ones, with all ten two-column prose pages
left alone. See docs/features/extraction.md#pdf-tables.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

#: Layout output separates cells by runs of spaces; prose has single spaces.
_CELL_GAP = re.compile(r"\s{3,}")

#: A cell that is a value rather than a label: numbers, ranges, percentages, a dash.
_NUMERIC = re.compile(
    r"^[(\[<>~≈]?[-+−–]?[$€£¥]?\s?\d[\d.,\s]*\s?[%‰]?[)\]]?[*a-z†‡]{0,2}$"
    r"|^[-–—]$|^n/?a$|^\.\.\.?$|^\d+\s?[-–]\s?\d+$",
    re.IGNORECASE,
)

#: Running words: what a prose column has and a label rarely does.
_WORDS = re.compile(r"[a-z]{3,} [a-z]{2,}")

#: A table line: this many cells or more, short on average...
MIN_CELLS = 3
MAX_MEAN_CELL = 30
#: ...or a label of at most this length followed by one value.
MAX_PAIR_LABEL = 60

#: A block is this many table lines at least, allowing one other line inside it.
MIN_BLOCK_LINES = 3
#: And mostly values, or longer and few wordy cells (a text table).
MIN_NUMERIC_SHARE = 0.25
MIN_TEXT_BLOCK_LINES = 4
MAX_WORDY_SHARE = 0.35

#: A page this share of whose lines are two columns of running words is prose set in two
#: columns, which layout output interleaves; it is left in reading order.
TWO_COLUMN_SHARE = 0.3
TWO_COLUMN_MIN_CHARS = 20

#: The share of a block's words the reading-order text must hold in one place before
#: the block replaces them. Below it the block is not where it can be found, and the
#: page is left as it was rather than given a table twice.
MIN_FOUND_SHARE = 0.5

#: The share of a block's words that may stay elsewhere on the page in reading order.
#: More, and the table found is only part of what reading order holds of it, so placing
#: it would store those cells twice; the page is left as it was.
MAX_LEFT_SHARE = 0.2

#: The share of words that may differ between a table and the reading-order lines it
#: replaces, either way: a value read twice, a heading taken in.
MAX_MISMATCH = 0.1

#: A gutter between columns is at least this many blank character columns.
MIN_GUTTER = 2


def _cells(line: str) -> list[str]:
    return [cell for cell in _CELL_GAP.split(line.strip()) if cell]


def _is_numeric(cell: str) -> bool:
    return bool(_NUMERIC.match(cell.strip()))


def _kind(line: str) -> str:
    """`table`, `prose2` (two columns of running words) or `other`."""
    cells = _cells(line)
    if len(cells) >= MIN_CELLS:
        mean = sum(map(len, cells)) / len(cells)
        return "table" if mean <= MAX_MEAN_CELL else "other"
    if len(cells) == 2:
        label, value = cells
        if _is_numeric(value) and len(label) <= MAX_PAIR_LABEL:
            return "table"
        if (
            len(label) >= TWO_COLUMN_MIN_CHARS
            and len(value) >= TWO_COLUMN_MIN_CHARS
            and _WORDS.search(label)
            and _WORDS.search(value)
        ):
            return "prose2"
    return "other"


def table_blocks(layout: str) -> list[list[str]]:
    """The table blocks on one page of layout text, each as its lines.

    A block is a run of table lines, one other line allowed inside it (a wrapped label, a
    sub-heading), that is mostly values or a text table of short cells. Two blank lines
    end a block, since one table set under another is two tables. A page of two-column
    prose has none, whatever its lines look like.
    """
    lines: list[str] = []
    apart: list[bool] = []  # whether two or more blank lines came before this line
    blanks = 0
    for raw in layout.split("\n"):
        if not raw.strip():
            blanks += 1
            continue
        lines.append(raw.rstrip())
        apart.append(blanks >= 2)
        blanks = 0
    if not lines:
        return []
    kinds = [_kind(line) for line in lines]
    if kinds.count("prose2") >= TWO_COLUMN_SHARE * len(lines):
        return []

    runs: list[tuple[int, int]] = []
    start: int | None = None
    last = -1
    for index, kind in enumerate(kinds):
        if kind == "table":
            if start is None or index - last > 2 or any(apart[last + 1 : index + 1]):
                if start is not None:
                    runs.append((start, last + 1))
                start = index
            last = index
    if start is not None:
        runs.append((start, last + 1))

    blocks = []
    for begin, end in runs:
        rows = [lines[i] for i in range(begin, end) if kinds[i] == "table"]
        if len(rows) < MIN_BLOCK_LINES:
            continue
        cells = [cell for row in rows for cell in _cells(row)]
        numeric = sum(1 for cell in cells if _is_numeric(cell)) / len(cells)
        wordy = sum(1 for cell in cells if len(cell.split()) >= 4) / len(cells)
        if numeric >= MIN_NUMERIC_SHARE or (
            len(rows) >= MIN_TEXT_BLOCK_LINES and wordy < MAX_WORDY_SHARE
        ):
            blocks.append(lines[begin:end])
    return blocks


def _gutters(lines: Sequence[str]) -> list[int]:
    """Where to cut every line: one column inside each blank band the lines share.

    Columns are found down the whole block, not by splitting each line, so an empty cell
    (a dash, a blank) leaves its row's other values in their columns. A band may be
    crossed by one line in five, since a crowded row often has a single space where the
    others have a wide gap; the cut is made at a column blank in every line, so even that
    row is cut between words.
    """
    width = max(len(line) for line in lines)
    occupied = [sum(1 for line in lines if i < len(line) and line[i] != " ") for i in range(width)]
    allowed = len(lines) // 5
    # Leading indentation is not a gutter.
    i = next((k for k, n in enumerate(occupied) if n > allowed), width)
    cuts = []
    while i < width:
        if occupied[i] > allowed:
            i += 1
            continue
        j = i
        while j < width and occupied[j] <= allowed:
            j += 1
        clear = [k for k in range(i, j) if occupied[k] == 0]
        if j - i >= MIN_GUTTER and j < width and clear:
            cuts.append(clear[-1])
        i = j
    return cuts


def pipe_rows(block: Sequence[str]) -> list[list[str]]:
    """A block's lines as rows of cells, cut at the gutters every table line shares.

    A line that is not a table line (a wrapped label inside the block) is not used to
    find the columns, since it would close them; it becomes a row of its own text, unless
    it starts in lower case, when it is the end of the label above. A row whose first cell
    is empty and holds no value continues the label above it.
    """
    table = [line for line in block if _kind(line) == "table"]
    cuts = _gutters(table)
    if not cuts:
        return []
    rows: list[list[str]] = []
    for line in block:
        if _kind(line) != "table":
            text = line.strip()
            if rows and text[:1].islower():
                rows[-1][0] = f"{rows[-1][0]} {text}".strip()
            else:
                rows.append([text] + [""] * len(cuts))
            continue
        bounds = [0, *cuts, len(line)]
        cells = [line[a:b].strip() for a, b in zip(bounds, bounds[1:], strict=False)]
        cells += [""] * (len(cuts) + 1 - len(cells))
        if rows and not cells[0] and not any(_is_numeric(c) for c in cells if c):
            rows[-1] = [
                f"{above} {cell}".strip() for above, cell in zip(rows[-1], cells, strict=False)
            ]
            continue
        rows.append(cells)
    return [row for row in rows if any(row)]


def pipe_table(rows: Sequence[Sequence[str]]) -> str:
    """Rows as a Markdown pipe table, the first row as its head."""

    def row_line(row: Sequence[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    head, *body = rows
    separator = "| " + " | ".join("---" for _ in head) + " |"
    return "\n".join([row_line(head), separator, *(row_line(row) for row in body)])


def _tokens(text: str) -> list[str]:
    return text.split()


def _place(
    reading: list[str], words: Counter[str], others: Sequence[Counter[str]] = ()
) -> tuple[int, int] | None:
    """The run of reading-order lines that is this block's cells, as (start, end).

    A line belongs to the block when every word on it is one of the block's; blank lines
    may sit inside the run. The run holding the most of the block's words wins, and only
    when it holds enough of them and few are left outside it. A line another table on the
    page could equally hold (a dash, a repeated value) is not counted as left behind.
    """
    best: tuple[int, int, int] | None = None
    i = 0
    while i < len(reading):
        if not reading[i].strip() or not all(t in words for t in _tokens(reading[i])):
            i += 1
            continue
        j = i
        found = 0
        end = i
        while j < len(reading):
            line = reading[j]
            if not line.strip():
                j += 1
                continue
            if not all(t in words for t in _tokens(line)):
                break
            found += len(_tokens(line))
            j += 1
            end = j
        if best is None or found > best[2]:
            best = (i, end, found)
        i = max(j, i + 1)
    if best is None or best[2] < MIN_FOUND_SHARE * sum(words.values()):
        return None
    total = sum(words.values())
    # A run holding far more words than the block has is prose that happens to use them.
    if best[2] > 2 * total:
        return None
    # Cells left elsewhere on the page would be stored twice, once in each form.
    left = sum(
        len(_tokens(line))
        for k, line in enumerate(reading)
        if not best[0] <= k < best[1]
        and line.strip()
        and all(t in words for t in _tokens(line))
        and not any(all(t in other for t in _tokens(line)) for other in others)
    )
    if left > MAX_LEFT_SHARE * total:
        return None
    return best[0], best[1]


def with_tables(reading: str, layout: str) -> tuple[str, int]:
    """One page's reading-order text with its tables as pipe tables, and how many.

    Each table goes where its cells were, on a paragraph of its own; the rest of the page
    is untouched, so a caption, a heading or the prose around it reads as it did.
    """
    lines = reading.split("\n")
    placed = 0
    blocks = table_blocks(layout)
    vocab = [Counter(t for line in block for t in _tokens(line)) for block in blocks]
    for index, block in enumerate(blocks):
        rows = pipe_rows(block)
        if len(rows) < MIN_BLOCK_LINES or max(len(row) for row in rows) < 2:
            continue
        words = vocab[index]
        where = _place(lines, words, vocab[:index] + vocab[index + 1 :])
        if where is None:
            continue
        start, end = where
        table = pipe_table(rows)
        # A table replaces its own cells and nothing else: the words going out and the
        # words coming in must be the same, both ways. Words only coming in mean some of
        # its cells are still elsewhere on the page and would be stored twice; words only
        # going out mean the run was another table's, which would be lost.
        incoming = Counter(t for t in _tokens(table) if t not in ("|", "---"))
        outgoing = Counter(t for line in lines[start:end] for t in _tokens(line))
        shared = sum((incoming & outgoing).values())
        if shared < (1 - MAX_MISMATCH) * max(incoming.total(), outgoing.total()):
            continue
        lines[start:end] = ["", *table.split("\n"), ""]
        placed += 1
    if not placed:
        return reading, 0
    # Collapse the blank lines a replacement can leave doubled.
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return text, placed
