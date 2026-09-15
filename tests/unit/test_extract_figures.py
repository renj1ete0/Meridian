"""Figure captions (task P1-10, spec §6.6).

§6.6: **"Start with captions, not vision. Figure captions are text, usually
extractable, and often the most information-dense sentence about the figure."**
So the subject here is what counts as a caption, and — more importantly — what
does not. A figures table that fills with logos and navigation icons makes every
figure count larger and every figure list longer while making nothing findable.
"""

from __future__ import annotations

import pytest

from worker.extract.base import ExtractedFigure, Page
from worker.extract.figures import MAX_FIGURES, figures_from_html, figures_from_pages

URL = "https://example.test/reports/annual"


def page(number: int, text: str) -> Page:
    return Page(number=number, text=text)


# --------------------------------------------------------------------------
# HTML: the markup says what is a figure
# --------------------------------------------------------------------------


def test_a_marked_up_figure_yields_its_caption_and_alt_text() -> None:
    html = (
        "<figure><img src='/charts/modal.png' alt='Stacked bar chart'>"
        "<figcaption>Figure 1: modal share by year, 2015 to 2025</figcaption></figure>"
    )

    figures = figures_from_html(html, URL)

    assert len(figures) == 1
    assert figures[0].caption == "Figure 1: modal share by year, 2015 to 2025"
    assert figures[0].alt_text == "Stacked bar chart"


def test_the_image_url_is_absolute() -> None:
    """A relative `src` is meaningless once the row is in a database. This is
    the only handle on the picture until something downloads it — `file_path` is
    a local path and nothing writes one."""
    html = (
        "<figure><img src='../charts/a.png' alt='x'>"
        "<figcaption>A caption here</figcaption></figure>"
    )

    assert figures_from_html(html, URL)[0].image_url == "https://example.test/charts/a.png"


def test_an_image_that_describes_itself_counts_even_without_a_figure_tag() -> None:
    """Most real pages do not use `<figure>`. An `alt` attribute is a
    description someone wrote for exactly this purpose, and discarding it would
    mean extracting figures from almost nothing."""
    figures = figures_from_html("<img src='/d.png' alt='Network diagram of the rail system'>", URL)

    assert len(figures) == 1
    assert figures[0].alt_text == "Network diagram of the rail system"
    assert figures[0].caption is None


def test_an_image_inside_a_figure_is_not_counted_twice() -> None:
    """It already contributed its alt text beside a real caption. A second row
    for the same picture is strictly worse evidence about it."""
    html = (
        "<figure><img src='/a.png' alt='A chart'><figcaption>Figure 2: the caption</figcaption>"
        "</figure>"
    )

    figures = figures_from_html(html, URL)

    assert len(figures) == 1


def test_an_image_with_nothing_to_say_is_not_a_figure() -> None:
    """The rejection that matters. A spacer, a logo and an icon all have a
    `src`; none is evidence, and a corpus that indexes them has a figures table
    that is mostly furniture."""
    figures = figures_from_html("<img src='/spacer.gif'><img src='/logo.png' alt=''>", URL)

    assert figures == ()


def test_one_page_cannot_contribute_unbounded_figures() -> None:
    """A gallery or a thumbnail index carries hundreds of described images and
    no findings."""
    html = "".join(f"<img src='/i{n}.png' alt='Image number {n}'>" for n in range(MAX_FIGURES * 3))

    assert len(figures_from_html(html, URL)) == MAX_FIGURES


def test_marked_up_figures_survive_the_cap_before_bare_images_do() -> None:
    """When the cap truncates, it must drop the weaker evidence. A `<figure>` is
    an author saying "this is a figure"; a bare `alt` may be describing a logo.
    """
    html = "".join(f"<img src='/b{n}.png' alt='Bare image {n}'>" for n in range(MAX_FIGURES))
    html += "<figure><img src='/real.png'><figcaption>Figure 9: the real one</figcaption></figure>"

    figures = figures_from_html(html, URL)

    assert any(f.caption == "Figure 9: the real one" for f in figures)


def test_unparseable_markup_yields_nothing_rather_than_raising() -> None:
    """Extraction runs on pages this crawler did not write. A malformed document
    is a Tuesday, not an incident."""
    assert figures_from_html(b"\x00\x01 not html at all", URL) == ()


# --------------------------------------------------------------------------
# PDF: only convention says what is a caption
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Figure 1: modal share by corridor",
        "Fig. 2 — ridership before and after",
        "FIGURE 3. network coverage by district",
        "Table 4: journey times, weekday peak",
        "Chart 5 - emissions by mode since 2010",
        "Exhibit 6: the proposed alignment",
    ],
)
def test_the_conventional_caption_forms_are_recognised(line: str) -> None:
    """Near-universal in the document types this corpus collects, and the only
    signal a text layer carries."""
    figures = figures_from_pages([page(7, line)])

    assert len(figures) == 1
    assert figures[0].page == 7


def test_a_caption_carries_its_page_and_no_bounding_box() -> None:
    """The page is known exactly; the position on it is not known at all. A
    fabricated bbox would put false precision on a citation someone follows."""
    figure = figures_from_pages([page(12, "Figure 1: the caption text here")])[0]

    assert figure.page == 12
    assert figure.image_url is None


def test_a_label_with_no_caption_is_not_a_figure() -> None:
    """`Figure 4.` alone is the label, its caption having wrapped to the next
    line or not existing. Storing it produces a row that is findable and says
    nothing."""
    assert figures_from_pages([page(1, "Figure 4.")]) == ()


def test_prose_beginning_with_the_word_figure_is_not_a_caption() -> None:
    """The number is required precisely for this. "Figure out the cost" and
    "Figures suggest a decline" both start with the word."""
    text = "Figure out the total cost before committing.\nFigures suggest a decline."

    assert figures_from_pages([page(1, text)]) == ()


def test_pages_are_scanned_in_order_and_capped() -> None:
    pages = [page(n, f"Figure {n}: a caption on page {n}") for n in range(1, MAX_FIGURES + 20)]

    figures = figures_from_pages(pages)

    assert len(figures) == MAX_FIGURES
    assert figures[0].page == 1


# --------------------------------------------------------------------------
# The shape
# --------------------------------------------------------------------------


def test_a_figure_with_no_text_at_all_is_not_useful() -> None:
    assert ExtractedFigure(image_url="https://example.test/a.png").is_useful is False
    assert ExtractedFigure(caption="A caption").is_useful is True
    assert ExtractedFigure(alt_text="Alt text").is_useful is True
