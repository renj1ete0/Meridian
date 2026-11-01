"""Watched news sitemaps and feeds: reading them, and what is queued (task B-249).

No network and no database: the parser on real document shapes (a Google News
sitemap, RSS 2.0, RSS 1.0, Atom), and the decision that files or drops an entry.
"""

from __future__ import annotations

import pytest

from meridian_core.models import Watch
from worker.main import watch_topic
from worker.sitemaps import SitemapError, parse_sitemap
from worker.topicmatch import TopicVocabulary

BASE = "https://paper.test/news-sitemap.xml"


def news(*entries: tuple[str, str]) -> bytes:
    body = "".join(
        f"<url><loc>{loc}</loc><news:news><news:publication><news:name>Paper</news:name>"
        f"<news:language>en</news:language></news:publication>"
        f"<news:publication_date>2026-11-01T08:00:00+08:00</news:publication_date>"
        f"<news:title>{title}</news:title></news:news></url>"
        for loc, title in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        f"{body}</urlset>"
    ).encode()


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_a_news_sitemap_gives_each_entry_its_headline() -> None:
    parsed = parse_sitemap(
        news(
            ("https://paper.test/a/1", "New  road safety\\n rules"),
            ("https://paper.test/a/2", "Derby result"),
        ),
        base_url=BASE,
    )
    assert parsed.kind == "urlset"
    assert parsed.urls == ("https://paper.test/a/1", "https://paper.test/a/2")
    assert parsed.titles["https://paper.test/a/2"] == "Derby result"


def test_an_rss_feed_is_read_and_its_channel_is_not_an_entry() -> None:
    """The channel has its own title and link; taking them would queue the home page."""
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Paper</title><link>https://paper.test/</link>
      <item><title>One</title><link>https://paper.test/story/1</link></item>
      <item><link>https://paper.test/story/2</link><title>Two</title></item>
    </channel></rss>"""
    parsed = parse_sitemap(feed, base_url="https://paper.test/feed")
    assert parsed.kind == "feed" and not parsed.is_index
    assert parsed.urls == ("https://paper.test/story/1", "https://paper.test/story/2")
    assert parsed.titles == {
        "https://paper.test/story/1": "One",
        "https://paper.test/story/2": "Two",
    }


def test_an_atom_feed_takes_the_page_not_its_enclosure() -> None:
    feed = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <title>Paper</title><link href="https://paper.test/"/>
      <entry><title>One</title>
        <link rel="enclosure" href="https://paper.test/audio/1.mp3"/>
        <link rel="alternate" href="https://paper.test/story/1"/></entry>
      <entry><title>Two</title><link href="https://paper.test/story/2"/></entry>
    </feed>"""
    parsed = parse_sitemap(feed, base_url="https://paper.test/atom")
    assert parsed.urls == ("https://paper.test/story/1", "https://paper.test/story/2")


def test_an_rss_1_feed_is_read() -> None:
    feed = b"""<?xml version="1.0"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
      <channel><title>Paper</title><link>https://paper.test/</link></channel>
      <item><title>One</title><link>https://paper.test/story/1</link></item>
    </rdf:RDF>"""
    assert parse_sitemap(feed, base_url="https://paper.test/rdf").urls == (
        "https://paper.test/story/1",
    )


def test_a_feed_pointing_elsewhere_is_held_to_the_same_site() -> None:
    """A feed is as untrusted as a sitemap: a stranger's links are dropped and counted."""
    feed = b"""<rss><channel>
      <item><title>Ours</title><link>https://paper.test/story/1</link></item>
      <item><title>Theirs</title><link>https://elsewhere.test/x</link></item>
      <item><title>Script</title><link>javascript:alert(1)</link></item>
    </channel></rss>"""
    parsed = parse_sitemap(feed, base_url="https://paper.test/feed")
    assert parsed.urls == ("https://paper.test/story/1",)
    assert parsed.dropped == {"cross_site": 1, "unusable": 1}


def test_a_feed_with_a_doctype_is_refused_like_a_sitemap() -> None:
    feed = b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "y">]><rss><channel/></rss>'
    with pytest.raises(SitemapError, match="dtd_refused"):
        parse_sitemap(feed, base_url="https://paper.test/feed")


def test_a_headline_is_capped() -> None:
    parsed = parse_sitemap(news(("https://paper.test/a/1", "x" * 2000)), base_url=BASE)
    assert len(parsed.titles["https://paper.test/a/1"]) == 500


def test_an_html_page_is_still_not_a_sitemap() -> None:
    with pytest.raises(SitemapError, match="not_a_sitemap"):
        parse_sitemap(b"<html><body>hi</body></html>", base_url=BASE)


# --------------------------------------------------------------------------
# What is queued
# --------------------------------------------------------------------------

VOCAB = TopicVocabulary(
    phrases={"road safety": ("roads",), "cycling": ("cycling",), "bus lanes": ("buses",)}
)


def watch(**fields) -> Watch:
    fields.setdefault("headline_filter", False)
    return Watch(url=BASE, **fields)


@pytest.mark.parametrize(
    ("fields", "url", "title", "expected"),
    [
        # No filter: the watch's topic, whatever the entry says.
        ({"topic": "roads"}, "https://paper.test/sport/1", "Derby result", "roads"),
        # Filter, no topic: the headline's topic, then the path's.
        ({"headline_filter": True}, "https://paper.test/a/1", "Cycling to school", "cycling"),
        ({"headline_filter": True}, "https://paper.test/bus-lanes/2", "Ministers meet", "buses"),
        ({"headline_filter": True}, "https://paper.test/a/3", "Derby result", None),
        ({"headline_filter": True}, "https://paper.test/a/4", None, None),
        # Filter and a topic: only entries naming that topic.
        (
            {"topic": "roads", "headline_filter": True},
            "https://paper.test/a/5",
            "Road safety",
            "roads",
        ),
        ({"topic": "roads", "headline_filter": True}, "https://paper.test/a/6", "Cycling", None),
    ],
)
def test_an_entry_is_filed_or_left(fields, url, title, expected) -> None:
    assert watch_topic(watch(**fields), url, title, VOCAB) == expected


def test_a_headline_matches_on_whole_words() -> None:
    """`bus lanes` must not match `omnibus lanes`; the path matcher's rule, applied to text."""
    assert VOCAB.topics_for_text("Omnibus lanes reviewed") == ()
    assert VOCAB.topics_for_text("New BUS-LANES on the coast road") == ("buses",)


def test_an_image_title_is_not_the_headline_and_an_image_loc_not_the_address() -> None:
    """Seen in a real news sitemap: `image:title` (a file name) after `news:title`. An
    image block placed first must not lend its address either."""
    doc = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <image:image><image:loc>https://paper.test/img/1.png</image:loc>
          <image:title>first.png</image:title></image:image>
        <loc>https://paper.test/a/1</loc>
        <news:news><news:title>Bus lanes extended</news:title></news:news>
        <image:image><image:title>113008.png</image:title></image:image>
      </url>
    </urlset>"""
    parsed = parse_sitemap(doc, base_url=BASE)
    assert parsed.urls == ("https://paper.test/a/1",)
    assert parsed.titles == {"https://paper.test/a/1": "Bus lanes extended"}
