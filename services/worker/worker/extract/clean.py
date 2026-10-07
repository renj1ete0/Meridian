"""Line-level cleaning of extracted text before it is chunked (task `B-43`).

Every rule removes whole lines and returns spans rather than a cleaned copy, so each
chunk stays a verbatim slice. Conservative: if cleaning would remove more than
:data:`MAX_REMOVED_SHARE`, the document is kept whole. The per-site rule applies the
host's line hashes, which `worker.boilerplate` builds from :func:`page_line_hashes`. See
docs/features/extraction.md#cleaning for the rules and the measurements behind them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence

from .base import Page

# --- The thresholds, and what each was measured against ---------------------
#
# Measured over a real crawl; see docs/features/extraction.md#cleaning before changing one.

#: A document losing more than this share of its visible text to cleaning is kept
#: whole: such a page is junk for `worker.furniture`, not text for the chunker.
MAX_REMOVED_SHARE = 0.8

#: A run of short lines is a menu only when it is at least this long.
MENU_MIN_LINES = 5

#: "Short" for a menu item: at most this many visible words.
MENU_MAX_WORDS = 4

#: Share of a run's items that must be links, leaving room for a label or two.
MENU_MIN_LINK_SHARE = 0.7

#: A PDF line counts as a running header or footer when it sits at a page edge
#: on at least this many pages...
PDF_MIN_REPEATS = 4

#: ...and on at least this share of the document's pages.
PDF_MIN_SHARE = 0.5

#: How deep into a page's top and bottom a running header or footer may sit.
#: pdftotext puts a two-line running head first and a page number last; three
#: leaves one line of slack without reaching into the body.
PDF_EDGE_LINES = 3

#: Lines considered by the per-site repetition rule, by visible length: neither a
#: label nor a paragraph.
REPEAT_MIN_CHARS = 12
REPEAT_MAX_CHARS = 200

#: And by words. A one-word line that recurs on every page of a journal is a
#: section heading ("Introduction", "Acknowledgments") — structure the chunk
#: needs to make sense, not furniture.
REPEAT_MIN_WORDS = 2


@dataclasses.dataclass(frozen=True)
class Removal:
    """One removed line: a half-open span into the text, and the rule that took it.

    The span covers the line's characters and not its newline, so removed spans
    never overlap and the text between two of them is exactly what survives.
    """

    start: int
    end: int
    reason: str

    def __len__(self) -> int:
        return self.end - self.start


@dataclasses.dataclass(frozen=True)
class Cleaning:
    """What cleaning one text decided.

    ``removals`` is empty either when there was nothing to remove or when the
    guard kept the original; ``kept_original`` says which.
    """

    removals: tuple[Removal, ...] = ()
    kept_original: bool = False
    #: Non-whitespace characters the rules *would* have removed, and out of how
    #: many. Reported even when the guard kept the original, because that is
    #: the case somebody needs the numbers for.
    removed_chars: int = 0
    total_chars: int = 0

    @property
    def spans(self) -> list[tuple[int, int]]:
        return [(r.start, r.end) for r in self.removals]

    @property
    def removed_share(self) -> float:
        return self.removed_chars / self.total_chars if self.total_chars else 0.0


# --- Reading a markdown line -------------------------------------------------

#: List bullets, ordered-list numbers, heading hashes and quote markers, any
#: number of them, at the start of a line.
_MARKERS = re.compile(r"^\s*(?:(?:[-*+•·]|\d{1,3}[.)])\s+|#{1,6}\s+|>\s*)*")

#: An image, `![alt](src "title")`. Replaced by its alt text first, so that an
#: image inside a link (`[ ![](logo.png) ](/home)`) leaves a link to read.
_IMAGE = re.compile(r"!\[((?:\\.|[^\]\\])*)\]\(\s*[^)\s]*(?:\s+\"[^\"]*\")?\s*\)")

#: A link, `[text](target "title")`, with escaped brackets allowed in the text.
_LINK = re.compile(r"\[((?:\\.|[^\]\\])*)\]\(\s*([^)\s]*)(?:\s+\"[^\"]*\")?\s*\)")

#: What may sit between links on a link-only line: separators and emphasis.
_FILLER = re.compile(r"^[\s|·•,/*_\-–—()\[\]]*$")

#: Something a citation resolver or a reader follows: a DOI, an arXiv id, a
#: URL written out. A line carrying one is never navigation, whatever its shape
#: — an index page of identifiers is exactly the text that must survive.
_IDENTIFIER = re.compile(
    r"\b10\.\d{4,9}/\S+|\barxiv:\s*\d{4}\.\d{4,5}|https?://|\bwww\.\w", re.IGNORECASE
)

_DOI = re.compile(r"\b10\.\d{4,9}/")

#: A navigation affordance, matched against a line's whole visible text.
_AFFORDANCE = re.compile(
    r"""^(?:
        (?:skip|jump)\b.{0,60}\b(?:content|navigation|nav|main|menu|breadcrumbs?|footer|search)
      | (?:back|return|go)\s+to\s+(?:the\s+)?top
      | (?:toggle|open|close|expand)\s+(?:the\s+)?(?:navigation|menu|search)
    )\W*$
    | ^\{?\s*top\s*\}?$""",
    re.IGNORECASE | re.VERBOSE,
)

#: "(opens in new tab)" — the link-text suffix screen readers are given. On a
#: line that is nothing but that link it is chrome; inside a sentence (an
#: accessibility statement's own links) the sentence is kept.
_NEW_TAB = re.compile(r"opens\s+in\s+(?:a\s+)?new\s+(?:tab|window)", re.IGNORECASE)

#: Ends like a sentence or a label. A menu item does neither.
_TERMINAL = re.compile(r"[.!?;:]\s*$")


@dataclasses.dataclass(frozen=True)
class _Line:
    start: int
    end: int
    raw: str
    #: The line as a reader sees it: markers, link syntax and emphasis removed.
    visible: str
    #: At least one link or image, and nothing else but separators.
    link_only: bool
    targets: tuple[str, ...]
    table_row: bool

    @property
    def words(self) -> int:
        return len(self.visible.split())

    @property
    def blank(self) -> bool:
        return not self.raw.strip()

    @property
    def has_identifier(self) -> bool:
        """Names a work: an identifier in the text, or a link *to* a DOI.

        The second half is a reference list's "[Crossref]" link, whose visible
        text says nothing and whose target is the one thing the reference needs.
        """
        return bool(_IDENTIFIER.search(self.visible) or any(_DOI.search(t) for t in self.targets))


def _read(text: str, start: int, end: int) -> _Line:
    raw = text[start:end]
    body = _MARKERS.sub("", raw).strip()
    targets: list[str] = []
    images = 0

    def image(match: re.Match[str]) -> str:
        nonlocal images
        images += 1
        return match.group(1)

    def link(match: re.Match[str]) -> str:
        targets.append(match.group(2))
        return match.group(1)

    unimaged = _IMAGE.sub(image, body)
    visible = _LINK.sub(link, unimaged)
    leftover = _LINK.sub("", _IMAGE.sub("", body))
    visible = re.sub(r"\\([\[\]()*_`\\])", r"\1", visible)
    visible = " ".join(re.sub(r"[*_`]+", " ", visible).split())
    return _Line(
        start=start,
        end=end,
        raw=raw,
        visible=visible,
        link_only=bool(targets or images) and bool(_FILLER.match(leftover)),
        targets=tuple(targets),
        table_row=body.startswith("|"),
    )


def _lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    cursor = 0
    for piece in text.split("\n"):
        out.append(_read(text, cursor, cursor + len(piece)))
        cursor += len(piece) + 1
    return out


# --- The rules ---------------------------------------------------------------


def navigation_lines(text: str) -> list[Removal]:
    """Lines that exist to move a reader around the page, not to say anything.

    Affordances ("Skip to content") that are the whole line, lone "opens in new tab"
    links, and links with nothing to read. Deliberately *not* every link-only line;
    see docs/features/extraction.md#cleaning.
    """
    out: list[Removal] = []
    for line in _lines(text):
        if line.blank or line.table_row:
            continue
        reason = _navigation_reason(line)
        if reason:
            out.append(Removal(line.start, line.end, reason))
    return out


def _navigation_reason(line: _Line) -> str | None:
    if line.has_identifier:
        return None
    if line.words <= 8 and _AFFORDANCE.match(line.visible):
        return "navigation"
    if not line.link_only:
        return None
    if _NEW_TAB.search(line.visible):
        return "navigation"
    if not line.visible.strip(" |·•,/-–—()[]"):
        return "empty-link"
    if line.words <= 3 and line.targets and all("#" in target for target in line.targets):
        return "anchor"
    return None


#: Unicode categories a reader never sees: format characters (soft hyphen,
#: zero-width joiners), controls, private-use and unassigned code points.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cn"})

#: A debris line keeps fewer visible characters than this once the invisible
#: ones are gone.
DEBRIS_MAX_VISIBLE = 3


def debris_lines(text: str) -> list[Removal]:
    """Lines that are extraction debris: invisible characters and a stray glyph.

    Not blank to ``str.strip``, so they would reach the chunker. The line must
    *contain* an invisible character, so a rule or lone page number is untouched.
    """
    out: list[Removal] = []
    for line in _lines(text):
        raw = line.raw
        invisible = [
            c for c in raw if unicodedata.category(c) in _INVISIBLE_CATEGORIES and c not in "\t\r"
        ]
        if not invisible:
            continue
        visible = [
            c
            for c in raw
            if not c.isspace() and unicodedata.category(c) not in _INVISIBLE_CATEGORIES
        ]
        if len(visible) < DEBRIS_MAX_VISIBLE:
            out.append(Removal(line.start, line.end, "debris"))
    return out


def menu_blocks(text: str) -> list[Removal]:
    """Runs of short, mostly-linked lines: a navigation menu the extractor kept.

    A run is consecutive non-blank lines (blank lines between them allowed,
    since some extractors put every item in its own paragraph) where each line
    is short (:data:`MENU_MAX_WORDS`), does not end like a sentence or a label,
    and carries no identifier. It is a menu when it has at least
    :data:`MENU_MIN_LINES` such lines and at least :data:`MENU_MIN_LINK_SHARE`
    of them are link-only. Lines with no letters in them ("-", "…", "2") ride
    along inside a run without counting either way — a pager is digits and
    dashes between links.

    A content list, a table, a sentence containing a link and a heading above prose
    all survive by construction.
    """
    out: list[Removal] = []
    run: list[_Line] = []

    def flush() -> None:
        items = [line for line in run if _has_letters(line)]
        if len(items) >= MENU_MIN_LINES:
            linked = sum(1 for line in items if line.link_only)
            if linked / len(items) >= MENU_MIN_LINK_SHARE:
                # From the first link to the last. A label *between* links is
                # part of the menu; one at either end may be the page's own
                # title sitting just below the menu, and is left alone.
                ends = [i for i, line in enumerate(run) if line.link_only]
                first, last = ends[0], ends[-1]
                out.extend(Removal(line.start, line.end, "menu") for line in run[first : last + 1])
        run.clear()

    for line in _lines(text):
        if line.blank:
            continue
        if _menu_item(line) or (run and not _has_letters(line) and not line.table_row):
            run.append(line)
            continue
        flush()
    flush()
    return out


def _has_letters(line: _Line) -> bool:
    return any(ch.isalpha() for ch in line.visible)


def _menu_item(line: _Line) -> bool:
    return (
        not line.table_row
        # A heading that is not a link is the page's own structure — often its
        # title, directly under the menu — and ends a run rather than joining it.
        and (line.link_only or not line.raw.lstrip().startswith("#"))
        and _has_letters(line)
        and line.words <= MENU_MAX_WORDS
        and not _TERMINAL.search(line.visible)
        and not line.has_identifier
    )


def repeated_lines(text: str, boilerplate: Collection[int]) -> list[Removal]:
    """Lines this page's site repeats on page after page.

    ``boilerplate`` is the host's set of line hashes (`meridian_core.boilerplate`).
    Matching is by :func:`line_hash`, so whitespace and casing do not matter.
    """
    if not boilerplate:
        return []
    return [
        Removal(line.start, line.end, "repeated")
        for line in _lines(text)
        if not line.blank and (digest := _hash_of(line)) is not None and digest in boilerplate
    ]


def running_headers(pages: Sequence[Page]) -> dict[int, list[Removal]]:
    """A PDF's running heads, running feet and page numbers, per page.

    Looked for only at a page's edges (:data:`PDF_EDGE_LINES` from the top and
    from the bottom), and counted only within one document: a line is a running
    header when it recurs at an edge on :data:`PDF_MIN_REPEATS` pages and at
    least :data:`PDF_MIN_SHARE` of them. Digits are folded before comparing, so
    "Page 3 of 20" and "Page 4 of 20" are one line — and so is a bare page
    number, which is the commonest footer there is.

    Mid-page occurrences are never touched.
    """
    if len(pages) < PDF_MIN_REPEATS:
        return {}
    edges: dict[int, list[tuple[str, _Line]]] = {}
    seen: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        filled = [line for line in _lines(page.text) if not line.blank]
        chosen = filled[:PDF_EDGE_LINES] + filled[PDF_EDGE_LINES:][-PDF_EDGE_LINES:]
        keyed = [(_folded(line.raw), line) for line in chosen]
        edges[page.number] = keyed
        for key, _ in keyed:
            if key:
                seen[key].add(page.number)

    needed = max(PDF_MIN_REPEATS, math.ceil(PDF_MIN_SHARE * len(pages)))
    running = {key for key, numbers in seen.items() if len(numbers) >= needed}
    out: dict[int, list[Removal]] = {}
    for number, keyed in edges.items():
        found = [
            Removal(line.start, line.end, "running-header") for key, line in keyed if key in running
        ]
        if found:
            out[number] = sorted(found, key=lambda r: r.start)
    return out


def _folded(raw: str) -> str:
    return re.sub(r"\d+", "#", " ".join(raw.split())).casefold()


# --- Repetition across a site: the hash both sides agree on -----------------


def normalise_line(line: str) -> str | None:
    """The form a line is compared in across pages, or None if it is not a candidate.

    Visible text only, so a banner linking to a per-page URL is still one line;
    NFKC and casefolded, whitespace collapsed. Outside the length and word
    bounds (:data:`REPEAT_MIN_CHARS`…) a line is never a candidate at all.
    """
    return _normalised(_read(line, 0, len(line)))


def _normalised(line: _Line) -> str | None:
    visible = unicodedata.normalize("NFKC", line.visible).casefold()
    visible = " ".join(visible.split())
    if not REPEAT_MIN_CHARS <= len(visible) <= REPEAT_MAX_CHARS:
        return None
    if len(visible.split()) < REPEAT_MIN_WORDS:
        return None
    return visible


def line_hash(normalised: str) -> int:
    """A stable signed 64-bit hash, the shape Postgres stores as `bigint`.

    Stable across processes (Python's own ``hash`` is salted per run), and
    signed because `bigint` is.
    """
    digest = hashlib.blake2b(normalised.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _hash_of(line: _Line) -> int | None:
    normalised = _normalised(line)
    return line_hash(normalised) if normalised is not None else None


def page_line_hashes(texts: Sequence[str]) -> list[int]:
    """The distinct candidate-line hashes of one page, sorted.

    Computed from the text *before* cleaning, always, or the rule would switch itself
    off by working.
    """
    found: set[int] = set()
    for text in texts:
        for line in _lines(text):
            if not line.blank and (digest := _hash_of(line)) is not None:
                found.add(digest)
    return sorted(found)


# --- Putting it together -----------------------------------------------------


def clean_text(text: str, *, boilerplate: Collection[int] = ()) -> Cleaning:
    """Every rule over one unpaginated text, with the guard applied."""
    found = _merge(
        navigation_lines(text)
        + menu_blocks(text)
        + repeated_lines(text, boilerplate)
        + debris_lines(text)
    )
    removed, total = _readable_in(text, found), _readable(text)
    if _over_guard(removed, total):
        return Cleaning(kept_original=True, removed_chars=removed, total_chars=total)
    return Cleaning(removals=tuple(found), removed_chars=removed, total_chars=total)


def clean_pages(pages: Sequence[Page], *, boilerplate: Collection[int] = ()) -> dict[int, Cleaning]:
    """Every rule over a paginated document, per page.

    The guard is applied to the document as a whole.

    One guard for the document rather than per page, so a bare cover page is not
    kept whole while the rest is cleaned.
    """
    headers = running_headers(pages)
    found: dict[int, list[Removal]] = {}
    removed: dict[int, int] = {}
    total: dict[int, int] = {}
    for page in pages:
        found[page.number] = _merge(
            headers.get(page.number, [])
            + navigation_lines(page.text)
            + menu_blocks(page.text)
            + repeated_lines(page.text, boilerplate)
            + debris_lines(page.text)
        )
        removed[page.number] = _readable_in(page.text, found[page.number])
        total[page.number] = _readable(page.text)

    keep = _over_guard(sum(removed.values()), sum(total.values()))
    return {
        number: Cleaning(
            removals=() if keep else tuple(found[number]),
            kept_original=keep,
            removed_chars=removed[number],
            total_chars=total[number],
        )
        for number in found
    }


def document_share(cleanings: Mapping[int, Cleaning]) -> tuple[int, int, bool]:
    """(removed, total, kept_original) summed over a paginated document."""
    removed = sum(c.removed_chars for c in cleanings.values())
    total = sum(c.total_chars for c in cleanings.values())
    kept = any(c.kept_original for c in cleanings.values())
    return removed, total, kept


def _merge(found: list[Removal]) -> list[Removal]:
    """One removal per line, the first rule to claim it named, in text order."""
    by_start: dict[int, Removal] = {}
    for removal in found:
        by_start.setdefault(removal.start, removal)
    return [by_start[start] for start in sorted(by_start)]


def _over_guard(removed: int, total: int) -> bool:
    return bool(total) and removed / total > MAX_REMOVED_SHARE


def _readable(text: str) -> int:
    """How much a reader would read: visible, non-whitespace characters.

    Visible rather than raw: link targets are most of a linked line's characters and
    none of what it says.
    """
    return sum(_solid(line.visible) for line in _lines(text))


def _readable_in(text: str, removals: Sequence[Removal]) -> int:
    return sum(_solid(_read(text, r.start, r.end).visible) for r in removals)


def _solid(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())
