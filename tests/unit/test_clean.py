"""Line-level cleaning before chunking (task `B-43`).

Inputs are shaped like what the extractors really emit — trafilatura and
browser markdown, pdftotext pages — and every rule is tested from both sides:
what it takes, and the content next to it that it must leave alone.
"""

from __future__ import annotations

import pytest

from worker.extract import clean
from worker.extract.base import Page
from worker.extract.chunk import chunk_pages, chunk_text
from worker.extract.clean import clean_text, debris_lines


def removed(text: str, removals) -> list[str]:
    return [text[r.start : r.end] for r in removals]


MENU = "\n".join(
    [
        "* [About](https://example.org/about/)",
        "* [History](https://example.org/about/history/)",
        "* [Leadership and Staff](https://example.org/about/leadership/)",
        "* [Awards](https://example.org/about/awards/)",
        "* [Our Work](https://example.org/our-work/)",
        "* [Contact Us](https://example.org/contact-us/)",
    ]
)

PROSE = (
    "The authority reviewed bus service reliability across the network in 2023 and "
    "found that headway adherence improved on most trunk routes after the change."
)


# --- navigation lines --------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "[Skip to Main Content](https://example.org/page/#main-container)",
        "[Skip Over Breadcrumbs and Secondary Navigation](https://www.example.edu#content-title)",
        "[ Back To Top ](https://example.org/careers.html#top)",
        "[Trusted websites (opens in new tab)](https://www.example.gov/trusted-sites#govsites)",
        "[Close Menu](https://example.org/page/)",
        "Skip to content",
        "[ ![](https://example.org/logo.png) ](https://example.org/)",
        "[Figure 1](https://www.example.org#f1)",
    ],
)
def test_navigation_affordances_are_removed(line: str) -> None:
    text = f"{PROSE}\n\n{line}\n\n{PROSE}"
    assert removed(text, clean.navigation_lines(text)) == [line]


@pytest.mark.parametrize(
    "line",
    [
        # A sentence that carries a link, including the screen-reader suffix.
        "This website is reviewed annually in line with [WCAG 2.1 AA  Link opens in a "
        "new tab](https://www.w3.org/TR/WCAG21/) and the results are published.",
        # The affordance words inside prose.
        "An accessibility statement explains how keyboard users can skip to content.",
        # Identifiers: an index of them is the text, whatever its shape.
        "[arXiv:2609.24611](https://arxiv.org/abs/2609.24611)",
        "[https://doi.org/10.1016/j.tranpol.2023.02.001](https://doi.org/10.1016/j.tranpol.2023.02.001)",
        # A lone content link — the commonest link-only shape, and mostly content.
        "[Title 2 - GOVERNMENTAL ORGANIZATION](https://example.org/regulations/title-2)",
        "[Uniform Commercial Code](https://example.org/ucc)",
        # "Top:" is a caption, not an affordance.
        "Top: the new clinic building at the opening ceremony",
    ],
)
def test_content_is_not_navigation(line: str) -> None:
    text = f"{PROSE}\n\n{line}\n\n{PROSE}"
    assert clean.navigation_lines(text) == []


# --- menus ---------------------------------------------------------------------


def test_a_run_of_short_links_is_a_menu() -> None:
    text = f"{MENU}\n\n# Jane Doe\n\n{PROSE}"
    got = removed(text, clean.menu_blocks(text))
    assert got == MENU.split("\n")
    assert "# Jane Doe" not in got, "the page's own title sits under the menu"


def test_menu_items_in_separate_paragraphs_are_still_a_menu() -> None:
    text = MENU.replace("\n", "\n\n")
    assert len(clean.menu_blocks(text)) == 6


def test_a_label_between_links_goes_with_the_menu_but_not_one_at_its_end() -> None:
    lines = MENU.split("\n")
    text = "\n".join(lines[:3] + ["Main Menu"] + lines[3:] + ["Sloan Institute"])
    got = removed(text, clean.menu_blocks(text))
    assert "Main Menu" in got
    assert "Sloan Institute" not in got


@pytest.mark.parametrize(
    "block",
    [
        # A content list: short items, no links. The largest class measured.
        "- Fast heartbeat\n- Headache\n- Uncontrolled shaking (tremors)\n- Nervousness\n"
        "- Restlessness\n- Difficulty in sleeping",
        # A table: rows never join a run.
        "| Route | Headway |\n|---|---|\n"
        + "\n".join(f"| [{n}](https://x.org/{n}) | {n % 7 + 5} |" for n in range(10, 17)),
        # Four short links: a "see also" pair and friends, below the run length.
        "\n".join(MENU.split("\n")[:4]),
        # Links whose text is a title of five or more words: an index, kept.
        "\n".join(
            f"[Chapter {n} - Contracts for Sale of Vehicles](https://x.org/ch-{n})"
            for n in range(8)
        ),
        # A reference list: its DOI links break any run of tool links.
        "\n".join(
            [
                "-",
                "[\\[Google Scholar\\]](https://scholar.example/lookup?t=a)",
                "-",
                "[\\[Crossref\\]](https://doi.org/10.1109/TRO.2023.32)",
            ]
            * 4
        ),
    ],
)
def test_content_shaped_like_a_menu_survives(block: str) -> None:
    text = f"{PROSE}\n\n{block}\n\n{PROSE}"
    assert clean.menu_blocks(text) == []


def test_a_short_heading_followed_by_prose_survives() -> None:
    text = f"## Findings\n\n{PROSE}\n\n## Method\n\n{PROSE}"
    assert clean.clean_text(text).removals == ()


# --- PDF running headers --------------------------------------------------------


WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
]


def pdf(
    n: int, *, head: str | None = "Journal of Transport Economics 41 (2024) 1-19"
) -> list[Page]:
    """Pages shaped like pdftotext's: a running head, a body, a page number."""
    pages = []
    for number in range(1, n + 1):
        body = [
            f"{WORDS[(number + i) % len(WORDS)]} {WORDS[(number * 3 + i) % len(WORDS)]} line "
            f"{WORDS[i]} of the body text."
            for i in range(8)
        ]
        body.insert(4, "Journal of Transport Economics is cited here in the body.")
        lines = ([head] if head else []) + body + [str(number)]
        pages.append(Page(number=number, text="\n".join(lines)))
    return pages


def test_running_heads_and_page_numbers_are_removed_at_page_edges() -> None:
    pages = pdf(6)
    found = clean.running_headers(pages)
    for page in pages:
        got = removed(page.text, found[page.number])
        assert got == ["Journal of Transport Economics 41 (2024) 1-19", str(page.number)]


def test_a_running_header_is_counted_within_one_document_and_needs_enough_pages() -> None:
    assert clean.running_headers(pdf(3)) == {}, "three pages is below the repeat floor"
    pages = pdf(8)
    # On four of eight pages: at the floor and exactly half — still a header.
    varied = [
        Page(
            p.number,
            p.text if p.number <= 4 else p.text.replace("Journal of", f"{WORDS[p.number]} of", 1),
        )
        for p in pages
    ]
    assert any(
        "Journal" in varied[0].text[r.start : r.end] for r in clean.running_headers(varied)[1]
    )
    # On three of eight: content, left alone.
    fewer = [
        Page(
            p.number,
            p.text if p.number <= 3 else p.text.replace("Journal of", f"{WORDS[p.number]} of", 1),
        )
        for p in pages
    ]
    heads = clean.running_headers(fewer)
    assert all(
        "Journal" not in page.text[r.start : r.end]
        for page in fewer
        for r in heads.get(page.number, [])
    )


def test_a_mid_page_repeat_is_never_a_header() -> None:
    pages = []
    for number in range(1, 7):
        body = [f"Opening line of page {number} with its own words."] * 3
        body += ["The same institution name appears mid-page"]
        body += [f"Closing line of page {number} with its own words."] * 3
        pages.append(Page(number, "\n".join(body)))
    for page in pages:
        assert all(
            "institution" not in page.text[r.start : r.end]
            for r in clean.running_headers(pages).get(page.number, [])
        )


# --- repetition across a site --------------------------------------------------

BANNER = "Institutions will NEVER ask you to transfer money or disclose bank details over a call."


def test_repeated_lines_match_by_normalised_text() -> None:
    known = {clean.line_hash(clean.normalise_line(BANNER))}
    text = f"{BANNER.upper()}\n\n{PROSE}\n\n  {BANNER}  "
    got = removed(text, clean.repeated_lines(text, known))
    assert len(got) == 2
    assert PROSE not in got


@pytest.mark.parametrize("line", ["Introduction", "References", "a" * 11, "word " * 60])
def test_labels_and_paragraphs_are_never_candidates(line: str) -> None:
    assert clean.normalise_line(line) is None


def test_page_hashes_are_distinct_stable_and_signed_64_bit() -> None:
    hashes = clean.page_line_hashes([f"{BANNER}\n{BANNER}\n{PROSE[:150]}"])
    assert len(hashes) == 2 and hashes == sorted(hashes)
    assert all(-(2**63) <= h < 2**63 for h in hashes)
    # Stable across processes: Python's own hash() is salted and would not be.
    assert clean.line_hash("scam banner text") == clean.line_hash("scam banner text")


# --- the guard -------------------------------------------------------------------


def test_a_page_that_is_nearly_all_furniture_is_kept_whole() -> None:
    known = {clean.line_hash(clean.normalise_line(BANNER))}
    text = f"Beware of Scam Calls\n{BANNER}\n{BANNER}"
    result = clean.clean_text(text, boilerplate=known)
    assert result.kept_original and result.removals == ()
    assert result.removed_share > clean.MAX_REMOVED_SHARE


def test_link_targets_do_not_count_as_text_for_the_guard() -> None:
    entries = "\n\n".join(
        f"[arXiv:2609.2{n:04d}](https://arxiv.org/abs/2609.2{n:04d})\n"
        f"Title: A study of item {n} under realistic operating conditions\n"
        f"[Skip to content](https://arxiv.org/list/{'x' * 200}#main)"
        for n in range(5)
    )
    result = clean.clean_text(entries)
    assert not result.kept_original, "long URLs are not a page's text"
    assert len(result.removals) == 5


# --- the chunker never cuts across a removed line --------------------------------


def test_chunks_stay_verbatim_slices_and_never_contain_a_dropped_line() -> None:
    text = (
        f"[Skip to content](https://x.org#main)\n\n{PROSE}\n{BANNER}\n{PROSE}\n\n{MENU}\n\n{PROSE}"
    )
    known = {clean.line_hash(clean.normalise_line(BANNER))}
    result = clean.clean_text(text, boilerplate=known)
    chunks = chunk_text(text, drop=result.spans)
    assert chunks
    for chunk in chunks:
        assert text[chunk.offset : chunk.end] == chunk.text
        assert BANNER not in chunk.text and "Skip to content" not in chunk.text
        assert "[Awards]" not in chunk.text
    assert sum(chunk.text.count(PROSE) for chunk in chunks) == 3, "no prose was lost"


def test_no_drop_is_the_old_chunker_exactly() -> None:
    text = "\n\n".join([PROSE] * 30)
    assert chunk_text(text) == chunk_text(text, drop=[])


def test_an_overlapping_drop_is_refused() -> None:
    with pytest.raises(ValueError):
        chunk_text(PROSE, drop=[(0, 10), (5, 20)])
    with pytest.raises(ValueError):
        chunk_text(PROSE, drop=[(0, len(PROSE) + 5)])


def test_paginated_drops_are_per_page() -> None:
    pages = pdf(6)
    cleaned = clean.clean_pages(pages)
    chunks = chunk_pages(pages, drop={n: c.spans for n, c in cleaned.items()})
    assert [c.offset for c in chunks] == [1, 2, 3, 4, 5, 6]
    assert all("41 (2024)" not in c.text for c in chunks)
    assert all("cited here in the body" in c.text for c in chunks)


# --------------------------------------------------------------------------
# Extraction debris
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        " ­",  # a soft hyphen alone
        "ª­  ",  # an ordinal sign and a soft hyphen
        "",  # private use
        "​​",  # zero-width spaces
        "\x0c",  # a form feed pdftotext left behind
    ],
)
def test_a_debris_line_is_removed(line: str) -> None:
    text = f"A real sentence before.\n{line}\nA real sentence after."
    removed = [text[r.start : r.end] for r in debris_lines(text)]
    assert removed == [line]


@pytest.mark.parametrize(
    "line",
    [
        "---",  # a horizontal rule has no invisible character
        "12",  # a page number is the running-head rule's, not this one's
        "A",  # a one-letter heading
        "An ordinary sen­tence with a soft hyphen inside it.",
        "­ Figure 3 shows the result.",  # debris beside real text
    ],
)
def test_a_line_with_readable_text_or_no_invisible_character_is_kept(line: str) -> None:
    assert debris_lines(f"Before.\n{line}\nAfter.") == []


def test_debris_is_part_of_clean_text_and_named() -> None:
    text = "Paragraph one is here.\n­\nParagraph two is here."
    cleaning = clean_text(text)
    assert [r.reason for r in cleaning.removals] == ["debris"]
