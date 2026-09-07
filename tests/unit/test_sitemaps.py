"""Sitemap parsing (`P1-28`, §6.4).

The happy path is three lines of lxml. Everything worth testing here is what a
hostile or merely careless sitemap does to it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from lxml import etree

from worker.sitemaps import (
    MAX_SITEMAP_ENTRIES,
    ParsedSitemap,
    SitemapError,
    local_name,
    parse_sitemap,
    same_site,
)

BASE = "https://www.example-org.test/sitemap.xml"

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def urlset(*locs: str, ns: str | None = NS) -> bytes:
    """A urlset document naming ``locs``."""
    attr = f' xmlns="{ns}"' if ns else ""
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset{attr}>{body}</urlset>'.encode()


def sitemapindex(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<sitemapindex xmlns="{NS}">{body}</sitemapindex>'
    ).encode()


# --------------------------------------------------------------------------
# The two document kinds are not interchangeable


def test_urlset_yields_pages() -> None:
    parsed = parse_sitemap(
        urlset("https://www.example-org.test/a", "https://www.example-org.test/b"), base_url=BASE
    )
    assert parsed.kind == "urlset"
    assert parsed.is_index is False
    assert parsed.urls == ("https://www.example-org.test/a", "https://www.example-org.test/b")


def test_sitemapindex_is_distinguished_from_urlset() -> None:
    """An index names sitemaps, not pages.

    Conflating the two enqueues every page as a `sitemap` task, which then feeds
    XML to the HTML extractor — a failure that looks like bad extraction rather
    than like the routing bug it is.
    """
    parsed = parse_sitemap(sitemapindex("https://www.example-org.test/sitemap-1.xml"), base_url=BASE)
    assert parsed.kind == "sitemapindex"
    assert parsed.is_index is True
    assert parsed.urls == ("https://www.example-org.test/sitemap-1.xml",)


def test_order_is_preserved_and_duplicates_collapse() -> None:
    parsed = parse_sitemap(
        urlset(
            "https://www.example-org.test/b",
            "https://www.example-org.test/a",
            "https://www.example-org.test/b",
        ),
        base_url=BASE,
    )
    assert parsed.urls == ("https://www.example-org.test/b", "https://www.example-org.test/a")
    assert parsed.dropped["duplicate"] == 1


# --------------------------------------------------------------------------
# Entity expansion — the reason this module does not just call fromstring


BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE urlset [
  <!ENTITY a "AAAAAAAAAA">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<urlset><url><loc>&d;</loc></url></urlset>"""


def test_entity_bomb_is_refused_before_parsing() -> None:
    """The pre-scan refuses a DTD outright, and the allocation never happens."""
    with pytest.raises(SitemapError) as exc:
        parse_sitemap(BOMB, base_url=BASE)
    assert exc.value.reason == "dtd_refused"


def test_lxml_defaults_would_have_expanded_that_bomb() -> None:
    """The guard is load-bearing, not decoration.

    If a future lxml stops expanding internal entities by default this fails,
    and the pre-scan can be reconsidered on evidence. Until then it documents
    exactly what would happen without it: four levels of nesting turn ten bytes
    into ten thousand, and each further level multiplies by ten.
    """
    import io

    expanded = 0
    for _, element in etree.iterparse(io.BytesIO(BOMB), events=("end",)):
        if local_name(element.tag) == "loc":
            expanded = len(element.text or "")
    assert expanded == 10_000


def test_parser_settings_defuse_the_bomb_independently_of_the_prescan() -> None:
    """Second defence, tested on its own.

    The pre-scan refuses this document before the parser sees it, so the two
    layers cannot be exercised together through `parse_sitemap`. This asserts the
    parser configuration directly: with the same keywords the module uses, the
    entity that expands to ten thousand characters under lxml's defaults expands
    to nothing.
    """
    import io

    lengths = []
    for _, element in etree.iterparse(
        io.BytesIO(BOMB),
        events=("end",),
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    ):
        if local_name(element.tag) == "loc":
            lengths.append(len(element.text or ""))
    assert lengths == [0]


def test_undefined_entity_yields_no_urls() -> None:
    """The same bomb with its DTD stripped.

    lxml refuses an undefined entity outright rather than treating it as empty,
    so this lands as a malformed document. Either way nothing is expanded and
    nothing reaches the queue, which is the property that matters.
    """
    no_doctype = BOMB.split(b"]>", 1)[1]
    parsed = parse_sitemap(b'<?xml version="1.0"?>' + no_doctype, base_url=BASE)
    assert parsed.urls == ()
    assert parsed.dropped.get("malformed_tail") == 1


def test_external_entity_is_not_fetched() -> None:
    """XXE: a loc that tries to read a local file gets nothing."""
    xxe = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<urlset><url><loc>&xxe;</loc></url></urlset>"
    )
    with pytest.raises(SitemapError) as exc:
        parse_sitemap(xxe, base_url=BASE)
    assert exc.value.reason == "dtd_refused"


# --------------------------------------------------------------------------
# A sitemap may not write to someone else's frontier


def test_cross_site_entries_are_dropped() -> None:
    parsed = parse_sitemap(
        urlset("https://www.example-org.test/ok", "https://evil.test/pwn"), base_url=BASE
    )
    assert parsed.urls == ("https://www.example-org.test/ok",)
    assert parsed.dropped["cross_site"] == 1


def test_subdomains_are_same_site_in_both_directions() -> None:
    """`registrable_domain` keeps subdomains, so equality would be wrong.

    An apex sitemap naming a subdomain and a subdomain sitemap naming the apex
    are both ordinary and must both survive, or a real site's sitemap is thrown
    away in its entirety.
    """
    assert same_site("https://data.example-org.test/x", "https://www.example-org.test/s.xml")
    assert same_site("https://www.example-org.test/x", "https://data.example-org.test/s.xml")
    assert not same_site("https://example-org.test.evil.test/x", "https://www.example-org.test/s.xml")


def test_cross_site_restriction_can_be_lifted_deliberately() -> None:
    parsed = parse_sitemap(
        urlset("https://evil.test/pwn"), base_url=BASE, same_site_only=False
    )
    assert parsed.urls == ("https://evil.test/pwn",)


# --------------------------------------------------------------------------
# Entries that are not fetchable URLs


@pytest.mark.parametrize(
    "loc",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "mailto:someone@example-org.test",
        "",
        "   ",
    ],
)
def test_unfetchable_locs_are_dropped_not_queued(loc: str) -> None:
    """netguard would refuse these later; keeping them out of the queue is
    cheaper than a queue row that exists only to be abandoned."""
    parsed = parse_sitemap(urlset(loc), base_url=BASE)
    assert parsed.urls == ()


def test_overlong_loc_is_dropped() -> None:
    parsed = parse_sitemap(
        urlset("https://www.example-org.test/" + "a" * 5000), base_url=BASE
    )
    assert parsed.urls == ()
    assert parsed.dropped["unusable"] == 1


def test_relative_loc_is_resolved_against_the_sitemap() -> None:
    """Invalid per the specification and common in practice."""
    parsed = parse_sitemap(urlset("/reports/annual"), base_url=BASE)
    assert parsed.urls == ("https://www.example-org.test/reports/annual",)


def test_whitespace_around_loc_is_stripped() -> None:
    body = (
        f'<urlset xmlns="{NS}"><url><loc>\n    '
        "https://www.example-org.test/a\n  </loc></url></urlset>"
    ).encode()
    assert parse_sitemap(body, base_url=BASE).urls == ("https://www.example-org.test/a",)


def test_escaped_ampersand_survives() -> None:
    """Sitemaps escape query separators; a regex over <loc> would not decode."""
    parsed = parse_sitemap(
        urlset("https://www.example-org.test/s?a=1&amp;b=2"), base_url=BASE
    )
    assert parsed.urls == ("https://www.example-org.test/s?a=1&b=2",)


# --------------------------------------------------------------------------
# Namespaces in the wild


@pytest.mark.parametrize(
    "ns",
    [
        None,
        "http://www.google.com/schemas/sitemap/0.84",
        "http://www.sitemaps.org/schemas/sitemap/0.9",
        "HTTP://WWW.SITEMAPS.ORG/SCHEMAS/SITEMAP/0.9",
    ],
)
def test_namespace_is_not_load_bearing(ns: str | None) -> None:
    """Matching qualified names silently returns nothing for a readable file."""
    parsed = parse_sitemap(urlset("https://www.example-org.test/a", ns=ns), base_url=BASE)
    assert parsed.urls == ("https://www.example-org.test/a",)


# --------------------------------------------------------------------------
# Bounds and malformed input


def test_entry_cap_truncates_rather_than_growing_the_queue_without_limit() -> None:
    many = urlset(*[f"https://www.example-org.test/{i}" for i in range(50)])
    parsed = parse_sitemap(many, base_url=BASE, max_entries=10)
    assert len(parsed.urls) == 10
    assert parsed.truncated is True


def test_untruncated_sitemap_does_not_claim_it_was() -> None:
    parsed = parse_sitemap(urlset("https://www.example-org.test/a"), base_url=BASE)
    assert parsed.truncated is False


def test_default_cap_is_the_published_one() -> None:
    assert MAX_SITEMAP_ENTRIES == 50_000


def test_html_error_page_is_not_a_sitemap() -> None:
    """A 200 that returns the site's 404 page is the common real failure."""
    with pytest.raises(SitemapError) as exc:
        parse_sitemap(b"<html><body>Not found</body></html>", base_url=BASE)
    assert exc.value.reason == "not_a_sitemap"


def test_empty_body_is_refused() -> None:
    with pytest.raises(SitemapError) as exc:
        parse_sitemap(b"   \n ", base_url=BASE)
    assert exc.value.reason == "empty"


def test_unparseable_bytes_are_refused() -> None:
    with pytest.raises(SitemapError) as exc:
        parse_sitemap(b"\x00\x01\x02 not xml at all", base_url=BASE)
    assert exc.value.reason in ("malformed_xml", "not_a_sitemap")


def test_truncated_sitemap_keeps_what_it_read() -> None:
    """A partial frontier beats none.

    A sitemap cut off by `max_page_bytes` is the expected shape of a large one,
    and discarding forty thousand good URLs over a missing closing tag would
    make the size cap and the feature mutually exclusive.
    """
    cut = urlset("https://www.example-org.test/a", "https://www.example-org.test/b")
    cut = cut[: cut.rindex(b"</urlset>")][:-10]
    parsed = parse_sitemap(cut, base_url=BASE)
    assert "https://www.example-org.test/a" in parsed.urls
    assert parsed.dropped.get("malformed_tail") == 1


def test_comments_and_processing_instructions_do_not_become_entries() -> None:
    body = (
        f'<?xml version="1.0"?><!-- generated --><?xml-stylesheet href="x.xsl"?>'
        f'<urlset xmlns="{NS}"><!-- a page --><url><loc>https://www.example-org.test/a</loc>'
        "</url></urlset>"
    ).encode()
    parsed = parse_sitemap(body, base_url=BASE)
    assert parsed.urls == ("https://www.example-org.test/a",)


def test_local_name_survives_non_string_tags() -> None:
    """lxml gives comments a callable tag; treating it as a string raises."""
    assert local_name(etree.Comment) == ""
    assert local_name("{ns}loc") == "loc"
    assert local_name("loc") == "loc"


def test_parsed_sitemap_is_frozen() -> None:
    parsed = ParsedSitemap(kind="urlset")
    with pytest.raises(FrozenInstanceError):
        parsed.kind = "sitemapindex"  # type: ignore[misc]

