"""Paywalled articles kept as a stub of what the page shows everyone (task `B-256`).

A paywall is never bypassed. A page whose publisher declares the article not free, and
from which extraction got only a teaser, becomes a stub: the headline, the time and the
visible opening, assembled into one passage and marked as not the article. See
docs/features/extraction.md#paywalled-articles.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import html
from typing import Any

from .base import ExtractedDocument
from .html import json_ld_items, meta_content

#: Extracted text at or above this many characters is the article, whatever the markup
#: declares: a metered site that serves the whole text to every reader has not walled it.
#: Measured: walled pages yielded 0 to 3,018 characters, whole ones served behind the
#: markup 5,352 and more; see the feature doc.
STUB_CEILING = 4000

#: The least a stub must hold to be worth a passage: a headline and a sentence.
STUB_FLOOR = 60

#: What a stub's passage holds, in order, as recorded in its mark.
VISIBLE = ("headline", "published", "teaser")


@dataclasses.dataclass(frozen=True)
class Stub:
    """What a paywalled page shows everyone, and why it is a stub."""

    #: Which declaration decided: ``isAccessibleForFree`` on the article, or on a part.
    rule: str
    headline: str | None
    #: The article's publication instant as the page declares it (ISO 8601), or None.
    published: str | None
    teaser: str
    #: How much text extraction got from the page, for the record.
    chars_seen: int

    @property
    def text(self) -> str:
        """The stub's one passage: headline, time line and teaser, each verbatim."""
        head = [line for line in (self.headline, self._published_line()) if line]
        return "\n".join(head) + ("\n\n" if head else "") + self.teaser

    def _published_line(self) -> str | None:
        return f"Published {self.published}" if self.published else None

    def mark(self) -> dict[str, Any]:
        """What `sources.extra['stub']` records."""
        return {
            "reason": "paywall",
            "rule": self.rule,
            "from": "page",
            "visible": [
                part
                for part, value in zip(
                    VISIBLE, (self.headline, self.published, self.teaser), strict=True
                )
                if value
            ],
            "published": self.published,
            "chars_seen": self.chars_seen,
        }

    def as_document(self, document: ExtractedDocument) -> ExtractedDocument:
        """``document`` with the stub's passage as its text, and nothing paginated."""
        return dataclasses.replace(document, text=self.text, pages=(), figures=())


def _not_free(value: Any) -> bool:
    """Whether an ``isAccessibleForFree`` value says *not free*: ``false`` or ``"False"``."""
    if isinstance(value, bool):
        return value is False
    return isinstance(value, str) and value.strip().lower() == "false"


def _parts(item: dict[str, Any]) -> list[dict[str, Any]]:
    parts = item.get("hasPart")
    if isinstance(parts, dict):
        return [parts]
    if isinstance(parts, list):
        return [part for part in parts if isinstance(part, dict)]
    return []


def walled_item(html_text: str) -> tuple[dict[str, Any], str] | None:
    """The JSON-LD item the page declares not free, and which rule found it, or None.

    The schema.org markup publishers write for search engines: ``isAccessibleForFree:
    false`` on the article, or on a ``hasPart`` element naming the walled section. Only
    items the page declares itself to be; a related article listed inside does not count.
    """
    for item in json_ld_items(html_text):
        if _not_free(item.get("isAccessibleForFree")):
            return item, "isAccessibleForFree"
        if any(_not_free(part.get("isAccessibleForFree")) for part in _parts(item)):
            return item, "hasPart"
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(html.unescape(value).split())
    return cleaned or None


def _instant(value: Any) -> str | None:
    """A declared publication time, kept verbatim when it parses as ISO 8601, else None."""
    text = _text(value)
    if text is None:
        return None
    try:
        dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def _without_headline(text: str, headline: str | None) -> str:
    """The visible text, less a first line that repeats the headline."""
    if not headline:
        return text.strip()
    first, _, rest = text.strip().partition("\n")
    if " ".join(first.split()).casefold() == headline.casefold():
        return rest.strip()
    return text.strip()


def paywall_stub(html_text: str, document: ExtractedDocument | None) -> Stub | None:
    """A stub of this page when its article is walled off and only a teaser came back.

    None when the page declares nothing walled, when extraction got the article anyway
    (:data:`STUB_CEILING`), when it is a scholarly page (an abstract is a paper's own
    summary, and resolution looks for its open copy), or when nothing visible is left.
    """
    if document is None or document.doi or document.scholarly_meta:
        return None
    found = walled_item(html_text)
    if found is None or document.char_count >= STUB_CEILING:
        return None
    item, rule = found

    headline = _text(item.get("headline")) or _text(document.title)
    # What the page shows a reader; when extraction found none, what it says of itself.
    teaser = _without_headline(document.text, headline) or (
        _text(item.get("description"))
        or _text(meta_content(html_text, "og:description", "twitter:description", "description"))
        or _text(document.excerpt)
        or ""
    )
    if not teaser or len(f"{headline or ''} {teaser}") < STUB_FLOOR:
        return None
    return Stub(
        rule=rule,
        headline=headline,
        published=_instant(item.get("datePublished")),
        teaser=teaser,
        chars_seen=document.char_count,
    )
