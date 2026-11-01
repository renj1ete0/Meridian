"""Whether a source's author, publisher and date are what they say they are (task B-234).

Each ``judge_*`` returns the value to keep, or None with the name of the rule that refused
it, so a pass can count what each rule does. Where a field's value came from is recorded
by the caller in ``sources.extra['<field>_from']`` (:data:`DECLARED` names the sources a
document declares itself; anything else was inferred). See
docs/features/extraction.md#source-metadata.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

#: Where a value can come from that the document itself declares. An inferred value
#: (`inferred`: trafilatura's date heuristics; `host`: the address) never replaces one of these.
DECLARED = frozenset({"citation", "jsonld", "dc", "article", "meta", "microdata", "pdfinfo"})

#: The `extra` key that records which source a field's stored value came from.
FROM_KEY = {
    "title": "title_from",
    "author": "author_from",
    "publisher": "publisher_from",
    "publication_date": "date_from",
    "language": "language_from",
}

#: No PDF can have been made before the format existed (version 1.0, 1993).
FIRST_PDF_YEAR = 1993
#: A declared date before this is a placeholder, not a publication of the open web.
FIRST_YEAR = 1900
#: Within this many days of a fetch, a PDF's creation date says it was rendered on request,
#: and an inferred date is the server's clock.
FETCH_DAY_SLACK = 1


@dataclasses.dataclass(frozen=True)
class Verdict[T]:
    """The value to keep, or None and the rule that refused it."""

    value: T | None
    rule: str | None = None


def is_declared(source: str | None) -> bool:
    """Whether a value from ``source`` is one the document declares about itself."""
    return source in DECLARED


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def judge_date(
    value: dt.date | None,
    *,
    source: str,
    fetched: dt.date | None = None,
    also_fetched: dt.date | None = None,
) -> Verdict[dt.date]:
    """A publication date, unless it cannot be one.

    ``fetched`` is the day the document was fetched (today when None); ``also_fetched``
    another fetch day the stored file may come from (a source's first fetch).
    """
    if value is None:
        return Verdict(None)
    today = fetched or dt.datetime.now(dt.UTC).date()
    # A day of slack: a date written in a zone ahead of UTC is not in the future.
    if value > today + dt.timedelta(days=1):
        return Verdict(None, "future")
    if value.year < FIRST_YEAR or value == dt.date(1970, 1, 1):
        return Verdict(None, "placeholder")
    if source == "pdfinfo" and value.year < FIRST_PDF_YEAR:
        return Verdict(None, "before pdf")
    near_fetch = min(abs((value - day).days) for day in (today, also_fetched) if day is not None)
    if source == "pdfinfo" and near_fetch <= FETCH_DAY_SLACK:
        return Verdict(None, "made on request")
    if not is_declared(source):
        if (value.month, value.day) == (1, 1):
            # A year completed to 1 January: what a copyright line yields.
            return Verdict(None, "year only")
        if near_fetch <= FETCH_DAY_SLACK:
            # A server clock, "last updated", a page-generation stamp: not a publication.
            return Verdict(None, "fetch day")
    return Verdict(value)


_ISO_DAY = re.compile(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:$|[T\s])")
_PDF_DAY = re.compile(r"^\s*D:(\d{4})(\d{2})(\d{2})")


def parse_day(value: object) -> dt.date | None:
    """A full calendar day from an ISO date, a `2024/05/01` date or a PDF date, else None.

    A year or a month alone is refused, never completed.
    """
    if not isinstance(value, str):
        return None
    match = _ISO_DAY.match(value) or _PDF_DAY.match(value)
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Authors
# --------------------------------------------------------------------------

#: What templates, office suites and CMS accounts write where a name belongs.
_PLACEHOLDER_AUTHOR = re.compile(
    r"^(?:authors?|author\s*name|author\s*(?:last|first)\s*name|first\s*name\s*last\s*name|"
    r"name|your\s*name|users?|admin(?:istrator)?|site\s*admin|web\s*admin|web\s*master|"
    r"super\s*user|editor|staff\s*writer|guest|owner|default|unknown|anonymous|un-?named|"
    r"none|null|n/?a|test(?:\s*auth(?:or)?)?|(?:microsoft\s*)?office\s*user|windows\s*user|"
    r"python-docx|openpyxl|apache\s*poi|wkhtmltopdf|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)$",
    re.IGNORECASE,
)
#: A credit for whoever built the site or its template: "Government Websites by …".
_CREDIT = re.compile(
    r"\b(?:websites?|site|powered|designed|developed|built|hosted|inspired)\s+by\b|\btemplate\b",
    re.IGNORECASE,
)
_URL_LIKE = re.compile(r"https?://|www\.|^/|\.(?:com|org|net|html?|php)(?:/|$)", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+\.\S+")
#: An email address in brackets after a name: "Example Holdings (desk@example.org)".
_BRACKETED_EMAIL = re.compile(r"\s*[(<\[]\s*\S+@\S+\s*[)>\]]")
#: An office account's suffix: "Marlowe, Jan@AGENCY" is Jan Marlowe of the agency.
_ACCOUNT_SUFFIX = re.compile(r"@[A-Za-z][\w-]*(?=$|[;,])")
#: A postal or telephone line: an address block, not a byline.
_CONTACT = re.compile(r"\b(?:telephone|tel|phone|fax|e-?mail)\s*[:.]|\d{5,}", re.IGNORECASE)
#: A login name: one lower-case token ("jdoe", "first.last").
_USERNAME = re.compile(r"^[a-z0-9._-]+$")
_BY = re.compile(r"^\s*by[:\s]+", re.IGNORECASE)


def judge_author(value: str | None) -> Verdict[str]:
    """An author, unless what is written there names nobody."""
    if not value:
        return Verdict(None)
    text = _ACCOUNT_SUFFIX.sub("", _BRACKETED_EMAIL.sub("", _BY.sub("", value)))
    text = " ".join(text.split()).strip(" ,;|")
    if len(text) < 2:
        return Verdict(None, "empty")
    if _URL_LIKE.search(text) or _EMAIL.search(text):
        return Verdict(None, "address")
    if _CREDIT.search(text):
        return Verdict(None, "site credit")
    if _PLACEHOLDER_AUTHOR.match(text.strip("[]()<>{}")):
        return Verdict(None, "placeholder")
    if _USERNAME.match(text):
        return Verdict(None, "login name")
    if _CONTACT.search(text):
        return Verdict(None, "contact")
    return Verdict(text)


def join_authors(names: list[str]) -> str | None:
    """Several declared names as one field, in order and without repeats."""
    judged = (judge_author(name).value for name in names)
    return "; ".join(dict.fromkeys(name for name in judged if name)) or None


# --------------------------------------------------------------------------
# Publishers
# --------------------------------------------------------------------------

#: Platforms a site is built on, which pages name where their own name belongs.
_PLATFORM = re.compile(
    r"^(?:powered\s+by\s+)?(?:wordpress(?:\.com)?|drupal|joomla!?|blogger|wix(?:\.com)?|"
    r"squarespace|ghost|hubspot|sitecore|umbraco|typo3|weebly|civicplus|granicus|"
    r"sharepoint|adobe\s+experience\s+manager|liferay|plone|webflow|shopify|"
    r"my\s+(?:wordpress\s+)?(?:site|blog|website)|site\s*name|website|default|home|untitled)$",
    re.IGNORECASE,
)
_IP_ADDRESS = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?$")
#: Part of an address: a scheme, as in "example.orghttps:", a shape seen in stored rows.
_ADDRESS_PART = re.compile(r"https?:")


def judge_publisher(value: str | None, *, generator: str | None = None) -> Verdict[str]:
    """A publisher, unless it names the site's software or an address.

    ``generator`` is the page's `<meta name="generator">`, whose product a publisher
    field naming it has copied.
    """
    if not value:
        return Verdict(None)
    text = " ".join(value.split()).strip(" |-")
    if len(text) < 2:
        return Verdict(None, "empty")
    if _IP_ADDRESS.match(text):
        return Verdict(None, "address")
    if _ADDRESS_PART.search(text):
        return Verdict(None, "address")
    if _PLATFORM.match(text):
        return Verdict(None, "platform")
    if generator:
        product = generator.split()[0].lower() if generator.split() else ""
        if product and text.lower() == product:
            return Verdict(None, "platform")
    return Verdict(text)
