"""Export (task P6-15, spec §12.5).

> "Export: BibTeX (citations) and Markdown (notes) — avoid trapping material in
> a bespoke store."

The tests are mostly about what is *not* in the output. A bibliography is a file
somebody pastes into a paper, so a fabricated year is wrong somewhere it will not
be checked again, and an unescaped `&` silently breaks the document it lands in.
Neither failure is visible from here — both surface in someone else's tool.
"""

from __future__ import annotations

import datetime as dt
import types

import pytest

from meridian_core.export import (
    ENTRY_TYPE,
    citation_key,
    tex_escape,
    to_bibtex,
    to_markdown,
)


def source(**over):
    """A stand-in for the ORM row. The export reads attributes, not a session."""
    defaults = dict(
        source_id=7,
        url="https://example.test/reports/2026",
        title="Annual transport report",
        author=None,
        publisher="Example Authority",
        publication_date=dt.date(2026, 4, 2),
        doi=None,
        accessed_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        source_tier="government",
        extra={"media_type": "application/pdf"},
    )
    return types.SimpleNamespace(**(defaults | over))


def chunk(**over):
    defaults = dict(chunk_id=100, chunk_index=0, page_or_offset=4, text="A passage of text.")
    return types.SimpleNamespace(**(defaults | over))


# --------------------------------------------------------------------------
# Nothing is invented
# --------------------------------------------------------------------------


def test_a_field_the_document_did_not_carry_is_omitted() -> None:
    """Not guessed, not blank, not "n.d." in the author slot. A fabricated
    author in a bibliography is wrong in a file somebody pastes into a paper,
    and nothing downstream will ever check it against the source."""
    entry = to_bibtex([source(author=None, publication_date=None, doi=None)])

    # Matched as fields, not substrings: the citation key here derives from
    # "Example Authority", which contains "author" — and a bare `in` check would
    # have passed for the wrong reason on a different fixture.
    assert "author = {" not in entry
    assert "year = {" not in entry
    assert "doi = {" not in entry
    assert "title = {" in entry


def test_what_the_document_did_carry_is_present() -> None:
    entry = to_bibtex([source(author="A Writer", doi="10.5555/x")])

    assert "author = {A Writer}" in entry
    assert "doi = {10.5555/x}" in entry
    assert "year = {2026}" in entry


# --------------------------------------------------------------------------
# It survives contact with LaTeX
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("R&D", r"R\&D"), ("100%", r"100\%"), ("cost_basis", r"cost\_basis"), ("$5", r"\$5")],
)
def test_tex_special_characters_are_escaped(raw: str, expected: str) -> None:
    """An unescaped `&` in a title does not fail here — it fails in the document
    it is pasted into, weeks later, as a TeX error nobody traces back."""
    assert tex_escape(raw) == expected


def test_a_title_with_an_ampersand_exports_safely() -> None:
    assert r"\&" in to_bibtex([source(title="Transport & Housing")])


# --------------------------------------------------------------------------
# Keys are stable and unique
# --------------------------------------------------------------------------


def test_the_key_does_not_change_between_exports() -> None:
    """A bibliography is re-exported and diffed. A key that moved would rewrite
    every citation in a document that referenced it."""
    assert citation_key(source()) == citation_key(source())


def test_two_reports_from_one_body_in_one_year_do_not_collide() -> None:
    """The ordinary case, not an edge one — and colliding keys drop entries
    silently, with no error in any tool involved."""
    first = citation_key(source(source_id=7))
    second = citation_key(source(source_id=8))

    assert first != second


def test_a_source_with_no_author_or_publisher_still_gets_a_key() -> None:
    assert citation_key(source(author=None, publisher=None))


# --------------------------------------------------------------------------
# The entry type is a format, not a verdict
# --------------------------------------------------------------------------


def test_every_tier_maps_to_an_entry_type() -> None:
    """A tier with no mapping would fall to the default silently, which is how
    a new tier ends up mis-formatted in every bibliography until someone
    notices."""
    from meridian_core.models.source import SOURCE_TIER

    assert set(SOURCE_TIER.enums) <= set(ENTRY_TYPE)


def test_the_tier_is_carried_verbatim_rather_than_implied() -> None:
    """§8 extracts structure and scores nothing, and a bibliography is exactly
    where an implied verdict would do damage — `@article` versus `@misc` reads
    as a judgement if it is allowed to. The note says what the tier was."""
    assert "source tier: informal" in to_bibtex([source(source_tier="informal")])


def test_an_unknown_tier_does_not_raise() -> None:
    """Export is not the place a schema change should first be noticed."""
    assert to_bibtex([source(source_tier="something-new")])


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def test_passages_are_grouped_under_their_source() -> None:
    """A reader scanning this later asks "what did this document say" far more
    often than "what was the fourth result"."""
    rows = [
        (chunk(chunk_id=1, chunk_index=1), source()),
        (chunk(chunk_id=2, chunk_index=0), source()),
    ]

    out = to_markdown(rows)

    assert out.count("## Annual transport report") == 1
    assert out.index("chunk 2") < out.index("chunk 1"), "passages should be in document order"


def test_every_passage_carries_what_makes_it_checkable() -> None:
    out = to_markdown([(chunk(), source())])

    assert "https://example.test/reports/2026" in out
    assert "tier: government" in out
    assert "page 4" in out, "a PDF's number is a page (§5.3, P2-18)"


def test_an_html_source_labels_its_number_as_an_offset() -> None:
    out = to_markdown([(chunk(), source(extra={"media_type": "text/html"}))])

    assert "offset 4" in out


def test_an_unknown_media_type_hedges_rather_than_mislabelling() -> None:
    out = to_markdown([(chunk(), source(extra=None))])

    assert "page/offset 4" in out


def test_the_query_is_recorded_when_there_was_one() -> None:
    """The same export means different things depending on what was asked, and
    a file found in six months carries no other context about why these
    passages and not others."""
    assert "`walkability`" in to_markdown([(chunk(), source())], query="walkability")


def test_no_query_means_no_empty_query_line() -> None:
    assert "Query:" not in to_markdown([(chunk(), source())])


def test_exporting_nothing_is_not_an_error() -> None:
    """An empty result set is a state the UI has, not a failure."""
    assert to_bibtex([]) == ""
    assert "0 sources" in to_markdown([])


def test_one_source_is_not_called_sources() -> None:
    """Small, and the kind of thing that makes an export look machine-made in a
    document somebody is going to read."""
    assert "1 source\n" in to_markdown([(chunk(), source())]) or "1 source " in to_markdown(
        [(chunk(), source())]
    )
    assert "1 sources" not in to_markdown([(chunk(), source())])
