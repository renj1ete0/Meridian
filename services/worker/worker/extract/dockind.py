"""What kind of document a source is, decided mechanically (task B-59).

`source_tier` says who published a document; nothing said what the document
*is*. That matters most for one kind: a **listing** — an index, a feed of new
items, a search result or tag page, a directory. Its value is its links, and
chunked as if it were a document it produces passages that splice unrelated
summaries together with rows of author links, which are then embedded,
labelled and offered to synthesis as if they said something.

So every fetched document gets a kind from its own structure — no model, as
the worker never calls one (§2.1) — and the rule that decided is recorded so a
verdict can be audited. The rules run in a fixed order and the first to match
wins:

1. **paper** — the page names its own DOI or carries scholarly `citation_*`
   head metadata; a PDF with an Abstract heading in its front matter. First,
   because a paper's reference list is a long run of links and must never make
   it a listing.
2. **listing** — measured from the extracted text, where links survive as
   markdown (see :func:`link_shape`): most of what a reader sees is link text;
   or link rows recur evenly through the whole page, one per record; or the
   URL says list, search, tag, category or archive and the text is link-dense.
3. **legal** — a section-marked title, section-marked provisions, or a path
   under legislation, regulations or a code. After listing, so a statute's
   table of contents is a listing and a statute's text is legal.
4. **news** — a press publisher's dated article, or a dated `og:type article`
   under a news or press path.
5. **report** — a PDF or office document from a government or institutional
   publisher.
6. **profile** — an organisation's own pages: its home page, about, contact,
   people and programme pages.
7. **other** — nothing above applied.

The thresholds were calibrated against a live corpus of several thousand
sources, with pages whose URL makes them known listings on one side and
abstract pages and articles on the other; the task report has the numbers.
They favour precision: a listing missed keeps the chunks every page had
before, while a document wrongly called a listing loses its passages from
search.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import statistics
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from meridian_core.models import Source

    from .base import ExtractedDocument

#: A markdown link, allowing escaped brackets in its text (`\\[1\\]`).
_LINK = re.compile(r"\[((?:[^\[\]\\]|\\.)*)\]\(([^)\s]*)\)")
_ESCAPE = re.compile(r"\\(.)")
#: What does not count as something a reader reads: whitespace, list bullets,
#: table pipes, brackets and punctuation. Counting them would make a bulleted
#: link list look emptier than it is and a table of links look fuller.
_NOT_READ = re.compile(r"[\s\-*•|:;,.()\[\]]+")

# --- Listing thresholds -----------------------------------------------------

#: Share of visible characters inside link text above which a page is mostly
#: links, and the fewest links for that to mean anything. Articles with dense
#: inline linking stayed well below the share; a short page with two links
#: says nothing either way.
LINK_SHARE = 0.5
LINK_SHARE_MIN_LINKS = 10

#: A "link row" is a line whose visible text is at least this much link text.
LINK_ROW_SHARE = 0.7
#: Link rows closer than this many visible characters belong to one record.
RECORD_GAP = 80
#: A feed of records with a summary each: at least this many record groups,
#: present in at least this many tenths of the page, spaced evenly (the
#: coefficient of variation of the gaps at most this), with a summary's worth
#: of text between one record's links and the next (the median gap at least
#: this many visible characters). A paper's reference list is also many even
#: rows — but a line of citation apart, not a summary apart, and on a short
#: abstract page it can fill most of the page. An article's inline links are
#: not rows at all.
RECORD_GROUPS = 10
RECORD_SPREAD = 8
RECORD_GAP_CV = 0.5
RECORD_MIN_GAP = 200

#: With a listing-shaped URL, far less evidence is needed: a few links, dense.
HINTED_MIN_LINKS = 3
HINTED_LINKS_PER_1K = 5.0

#: Path segments and query keys that name a listing. Not `index` — it is a
#: default document name, and many real documents are served as one — and not
#: a `page` parameter, which a long article split across pages carries too.
_LISTING_SEGMENTS = frozenset(
    {
        "list",
        "lists",
        "listing",
        "listings",
        "search",
        "tag",
        "tags",
        "category",
        "categories",
        "browse",
        "archive",
        "archives",
        "directory",
    }
)
_LISTING_QUERY_KEYS = frozenset({"q", "query", "search", "tag", "category"})

# --- Other kinds --------------------------------------------------------------

_LEGAL_SEGMENTS = frozenset(
    {
        "statute",
        "statutes",
        "legislation",
        "regulation",
        "regulations",
        "uscode",
        "cfr",
        "act",
        "acts",
        "bills",
        "ordinances",
        "constitution",
        "gazette",
    }
)
#: A provision heading: a line opening with a section mark.
_SECTION_LINE = re.compile(r"^\s*\[?§+\s*\d", re.MULTILINE)
SECTION_LINES = 3

_NEWS_SEGMENTS = frozenset(
    {
        "news",
        "newsroom",
        "press",
        "press-release",
        "press-releases",
        "media-release",
        "media-releases",
    }
)
_PROFILE_SEGMENTS = frozenset(
    {
        "about",
        "about-us",
        "who-we-are",
        "contact",
        "contact-us",
        "people",
        "staff",
        "faculty",
        "team",
        "leadership",
        "our-people",
        "programmes",
        "programs",
        "departments",
        "careers",
    }
)
#: An Abstract heading near the start is what front matter looks like.
_ABSTRACT_HEADING = re.compile(r"^\s*abstract\b", re.IGNORECASE | re.MULTILINE)
FRONT_MATTER_CHARS = 4000

PDF_MEDIA_TYPES = frozenset({"application/pdf"})
HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})


@dataclasses.dataclass(frozen=True)
class LinkShape:
    """How much of a page's visible text is links, and how the links are laid out."""

    visible_chars: int = 0
    links: int = 0
    #: Share of visible characters that are link text.
    link_share: float = 0.0
    links_per_1k: float = 0.0
    #: Lines that are mostly link text.
    link_rows: int = 0
    #: Link rows merged into records, and how many tenths of the page have one.
    record_groups: int = 0
    record_spread: int = 0
    #: Coefficient of variation of the gaps between records, and their median
    #: in visible characters; None under 3 gaps.
    gap_cv: float | None = None
    median_gap: float | None = None

    def as_record(self) -> dict[str, object]:
        return {
            "visible_chars": self.visible_chars,
            "links": self.links,
            "link_share": round(self.link_share, 3),
            "links_per_1k": round(self.links_per_1k, 2),
            "link_rows": self.link_rows,
            "record_groups": self.record_groups,
            "record_spread": self.record_spread,
            "gap_cv": None if self.gap_cv is None else round(self.gap_cv, 2),
            "median_gap": self.median_gap,
        }


def _readable(text: str) -> int:
    return len(_NOT_READ.sub("", text))


def _line_shape(line: str) -> tuple[int, int]:
    """(visible characters, of which link text) for one line of markdown."""
    parts: list[str] = []
    link_chars = 0
    last = 0
    for match in _LINK.finditer(line):
        parts.append(line[last : match.start()])
        anchor = _ESCAPE.sub(r"\1", match.group(1))
        parts.append(anchor)
        link_chars += _readable(anchor)
        last = match.end()
    parts.append(line[last:])
    return _readable(_ESCAPE.sub(r"\1", "".join(parts))), link_chars


def link_shape(text: str) -> LinkShape:
    """Measure the links in extracted text, where they survive as markdown.

    Measured over what a reader sees — a link counts by its anchor text, not
    its URL. Measuring the raw markdown instead lets one long URL per link make
    a statute whose defined terms are all links look like an index.
    """
    visible = 0
    link_chars = 0
    rows: list[int] = []
    for line in text.split("\n"):
        seen, linked = _line_shape(line)
        if not seen:
            continue
        if linked >= LINK_ROW_SHARE * seen:
            rows.append(visible)
        visible += seen
        link_chars += linked

    groups: list[int] = []
    previous: int | None = None
    for position in rows:
        if previous is None or position - previous >= RECORD_GAP:
            groups.append(position)
        previous = position
    gaps = [b - a for a, b in zip(groups, groups[1:], strict=False)]
    measurable = len(gaps) >= 3 and statistics.mean(gaps) > 0
    gap_cv = statistics.pstdev(gaps) / statistics.mean(gaps) if measurable else None
    median_gap = float(statistics.median(gaps)) if measurable else None
    links = len(_LINK.findall(text))
    return LinkShape(
        visible_chars=visible,
        links=links,
        link_share=link_chars / visible if visible else 0.0,
        links_per_1k=1000 * links / visible if visible else 0.0,
        link_rows=len(rows),
        record_groups=len(groups),
        record_spread=len({min(9, 10 * p // visible) for p in groups}) if visible else 0,
        gap_cv=gap_cv,
        median_gap=median_gap,
    )


def _segments(url: str) -> tuple[set[str], set[str], str]:
    split = urlsplit(url)
    segments = {
        re.sub(r"\.(html?|php|aspx?|cfm)$", "", s.lower()) for s in split.path.split("/") if s
    }
    return segments, {k.lower() for k in parse_qs(split.query)}, split.path


def listing_rule(url: str, shape: LinkShape) -> str | None:
    """Which listing rule this page meets, or None."""
    if shape.links >= LINK_SHARE_MIN_LINKS and shape.link_share >= LINK_SHARE:
        return "link_share"
    if (
        shape.record_groups >= RECORD_GROUPS
        and shape.record_spread >= RECORD_SPREAD
        and shape.gap_cv is not None
        and shape.gap_cv <= RECORD_GAP_CV
        and (shape.median_gap or 0) >= RECORD_MIN_GAP
    ):
        return "record_rows"
    segments, keys, _ = _segments(url)
    hinted = bool(segments & _LISTING_SEGMENTS) or bool(keys & _LISTING_QUERY_KEYS)
    if hinted and shape.links >= HINTED_MIN_LINKS and shape.links_per_1k >= HINTED_LINKS_PER_1K:
        return "listing_url"
    return None


@dataclasses.dataclass(frozen=True)
class Evidence:
    """Everything the rules read. Built from a fresh extraction or a stored source."""

    url: str
    media_type: str | None = None
    tier: str | None = None
    text: str = ""
    title: str | None = None
    doi: str | None = None
    publication_date: dt.date | None = None
    og_type: str | None = None
    scholarly_meta: bool = False


def evidence_from(
    url: str,
    document: ExtractedDocument | None,
    *,
    media_type: str | None,
    tier: str | None,
) -> Evidence:
    """Evidence from a fresh extraction; a document that did not extract has only its URL."""
    if document is None:
        return Evidence(url=url, media_type=media_type, tier=tier)
    text = document.text or "\n\n".join(page.text for page in document.pages)
    return Evidence(
        url=url,
        media_type=media_type,
        tier=tier,
        text=text,
        title=document.title,
        doi=document.doi,
        publication_date=document.publication_date,
        og_type=document.og_type,
        scholarly_meta=document.scholarly_meta,
    )


@dataclasses.dataclass(frozen=True)
class Verdict:
    kind: str
    rule: str
    shape: LinkShape | None = None

    def as_record(self) -> dict[str, object]:
        """What goes in ``sources.extra["doc_kind"]``."""
        record: dict[str, object] = {"kind": self.kind, "rule": self.rule}
        if self.shape is not None:
            record["links"] = self.shape.as_record()
        return record


def _media(media_type: str | None) -> str:
    return (media_type or "").split(";")[0].strip().lower()


def classify(evidence: Evidence) -> Verdict:
    """The kind of one document, and the rule that decided it (see module docstring)."""
    media = _media(evidence.media_type)
    segments, _, path = _segments(evidence.url)
    is_html = media in HTML_MEDIA_TYPES or (not media and bool(evidence.text))
    is_document = bool(media) and not is_html and media != "text/plain"

    if evidence.doi:
        return Verdict("paper", "own_doi")
    if evidence.scholarly_meta:
        return Verdict("paper", "citation_meta")
    if media in PDF_MEDIA_TYPES and _ABSTRACT_HEADING.search(evidence.text[:FRONT_MATTER_CHARS]):
        return Verdict("paper", "pdf_abstract")

    shape = link_shape(evidence.text) if evidence.text else None
    if shape is not None:
        rule = listing_rule(evidence.url, shape)
        if rule:
            return Verdict("listing", rule, shape)

    if "§" in (evidence.title or ""):
        return Verdict("legal", "section_title", shape)
    if len(_SECTION_LINE.findall(evidence.text)) >= SECTION_LINES:
        return Verdict("legal", "section_lines", shape)
    if segments & _LEGAL_SEGMENTS:
        return Verdict("legal", "legal_path", shape)

    article = evidence.og_type == "article"
    dated = evidence.publication_date is not None
    if evidence.tier == "press" and (article or dated):
        return Verdict("news", "press_article", shape)
    if article and dated and segments & _NEWS_SEGMENTS:
        return Verdict("news", "news_path_article", shape)

    if is_document and evidence.tier in {"government", "institutional"}:
        return Verdict("report", "institutional_document", shape)

    if is_html and path.strip("/") == "":
        return Verdict("profile", "home_page", shape)
    if is_html and segments & _PROFILE_SEGMENTS:
        return Verdict("profile", "profile_path", shape)

    return Verdict("other", "no_rule", shape)


def record_doc_kind(source: Source, verdict: Verdict) -> None:
    """Write a verdict onto a source row: the column, and the rule in ``extra``.

    ``extra`` is replaced rather than mutated — SQLAlchemy does not see an
    in-place change to a JSONB dict, and the write would silently never land.
    """
    source.doc_kind = verdict.kind
    source.extra = {**(source.extra or {}), "doc_kind": verdict.as_record()}
