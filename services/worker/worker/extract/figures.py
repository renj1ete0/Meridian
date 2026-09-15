"""Figure captions (task P1-10, spec §6.6).

§6.6 is unambiguous about where to start: **"Start with captions, not vision.
Figure captions are text, usually extractable, and often the most
information-dense sentence about the figure."** So this extracts captions and
alt text at ingestion and nothing else — no image bytes, no bounding boxes, no
model.

That is not a stub. A caption plus a page number is already a citable claim
about what a figure shows, and it is searchable through the ordinary chunk path
the moment it is stored. Vision (`P7-07`) and the figures panel (`P6-14`) build
on top of it; neither is a prerequisite for the value.

**What it does keep is the way back to the image.** `figures.file_path` is a
local raw path and nothing downloads figure images, so without the source URL a
row would describe a picture nobody could ever look at. `image_url` is that
handle, and it is why `P1-10` adds a column rather than only a module.

Two inputs, and they are genuinely different problems:

**HTML has semantics.** `<figure>`/`<figcaption>` says "this is a figure and
this is its caption" outright, and `<img alt>` is a description someone wrote
for exactly this purpose. Both are reliable when present.

**A PDF has none.** `pdftotext` yields a page of text in which a caption is
distinguishable only by convention — a line beginning "Figure 3:" or "Table 2.".
That convention is near-universal in the document types this corpus collects,
and matching it is worth doing precisely because the alternative is nothing.
It is also why the PDF path carries a page and no bbox: the page is known
exactly, the position on it is not known at all, and inventing a bbox would put
a false precision on a citation.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from lxml import etree
from lxml import html as lxml_html

from meridian_core.logging import get_logger

from .base import ExtractedFigure

log = get_logger(__name__)

#: A caption has to say something. A one-word "Figure" is the label without the
#: caption, and storing it produces a row that is findable and worthless.
MIN_CAPTION_CHARS = 12

#: Captions run long in academic work, but a "caption" of several thousand
#: characters is a paragraph that happened to start with the word.
MAX_CAPTION_CHARS = 2000

#: How many figures to take from one document. A gallery page can carry
#: hundreds of thumbnails, each with alt text, and none of them is a finding.
MAX_FIGURES = 50

#: §6.6's convention, as it actually appears. `Figure 1.`, `Fig 2:`, `FIGURE 3 —`,
#: `Table 4.`, `Chart 5:`. The number is required: without it this matches any
#: sentence that happens to begin with the word "figure".
CAPTION_RE = re.compile(
    r"^\s*(?:figure|fig\.?|table|chart|exhibit|plate)\s*"
    r"(?P<number>\d+[a-z]?|[ivxlc]+)\s*[.:—–-]\s*(?P<caption>\S.*)$",
    re.IGNORECASE,
)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_CAPTION_CHARS:
        collapsed = collapsed[:MAX_CAPTION_CHARS].rstrip()
    return collapsed or None


def figures_from_html(content: bytes | str, url: str) -> tuple[ExtractedFigure, ...]:
    """Figures a page marked up as figures, plus images that describe themselves.

    Two passes, in that order, because they are different evidence. A
    `<figure>` is an author saying "this is a figure"; a bare `<img alt>` is an
    author describing an image that may be a diagram or may be a logo. Both are
    kept, the marked-up ones first, and `MAX_FIGURES` therefore truncates the
    weaker evidence rather than the stronger.
    """
    try:
        tree = lxml_html.fromstring(content)
    except (etree.ParserError, ValueError):
        return ()

    found: list[ExtractedFigure] = []
    seen_images: set[str] = set()

    for element in tree.xpath("//figure"):
        caption = _clean(" ".join(element.xpath(".//figcaption//text()")))
        images = element.xpath(".//img")
        image = images[0] if images else None
        src = image.get("src") if image is not None else None
        absolute = urljoin(url, src) if src else None
        if absolute:
            seen_images.add(absolute)
        figure = ExtractedFigure(
            caption=caption,
            alt_text=_clean(image.get("alt")) if image is not None else None,
            image_url=absolute,
        )
        if figure.is_useful:
            found.append(figure)

    for image in tree.xpath("//img[@alt]"):
        src = image.get("src")
        absolute = urljoin(url, src) if src else None
        # Not twice. An `<img>` inside a `<figure>` already contributed its alt
        # text next to a real caption, and the second row would be strictly
        # worse evidence for the same picture.
        if absolute and absolute in seen_images:
            continue
        figure = ExtractedFigure(alt_text=_clean(image.get("alt")), image_url=absolute)
        if figure.is_useful:
            if absolute:
                seen_images.add(absolute)
            found.append(figure)

    return tuple(found[:MAX_FIGURES])


def figures_from_pages(pages) -> tuple[ExtractedFigure, ...]:
    """Caption lines from a paginated document's text layer.

    No image, no bbox, and a page number that is exact. §6.6 wants the caption
    indexed; a reader following the citation opens the page and sees the figure
    themselves, which is the same thing a page-accurate chunk citation already
    asks of them.
    """
    found: list[ExtractedFigure] = []
    for page in pages:
        for line in page.text.splitlines():
            match = CAPTION_RE.match(line)
            if match is None:
                continue
            caption = _clean(match.group("caption"))
            if caption is None or len(caption) < MIN_CAPTION_CHARS:
                # The label wrapped and its caption is on the next line, or
                # there is no caption at all. Either way there is nothing here
                # worth making searchable.
                continue
            found.append(ExtractedFigure(caption=caption, page=page.number))
            if len(found) >= MAX_FIGURES:
                return tuple(found)
    return tuple(found)
