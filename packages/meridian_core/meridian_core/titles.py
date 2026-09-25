"""What a source is called, when what it says it is called is not a title (task B-69).

A title is read off the document: the `<title>` element, `og:title`, a PDF's
metadata. Measured on a live corpus, those are wrong in three recurring ways:

- **A placeholder.** PDF exporters write "untitled", "Microsoft Word -
  report.docx" or a bare file name; data pipelines write "nan" and "None".
  Stored as the title, it is a citation that names nothing.
- **The site, not the page.** "Home", "Results", "Main navigation", or the
  site's own name on every page of it — hundreds of sources with one title.
- **Nothing at all**, on thousands of PDFs.

:func:`clean_title` turns the first two into None, and strips a site name off
"Page | Site". :func:`title_from_text` is the fallback for None: the document's
own first line, when that line looks like a heading and nothing else. The
caller records which one it used, so a title guessed from text is never
mistaken for one the document declared.
"""

from __future__ import annotations

import re

#: What exporters and pipelines write when there is no title.
_PLACEHOLDER = re.compile(
    r"^(?:untitled(?:\s+document)?|nan|none|null|undefined|n/?a|no\s+title|title|"
    r"document\d*|default|slide\s*\d*|page\s*\d*|powerpoint\s+presentation|"
    r"microsoft\s+(?:word|powerpoint|excel)\b.*|\(untitled\))$",
    re.IGNORECASE,
)

#: A file name in the title field says what the file was saved as.
_FILE_NAME = re.compile(r"\.(?:docx?|pdf|pptx?|xlsx?|tex|dvi|indd|rtf|odt|txt)$", re.IGNORECASE)

#: Titles that name a kind of page on any site, never a page.
GENERIC = frozenset(
    {
        "home",
        "homepage",
        "home page",
        "index",
        "welcome",
        "main navigation",
        "menu",
        "search",
        "search results",
        "results",
        "news",
        "events",
        "resources",
        "publications",
        "about",
        "about us",
        "contact",
        "contact us",
        "topics",
        "overview",
        "login",
        "log in",
        "sign in",
        "page not found",
        "introduction",
        "abstract",
        "contents",
        "table of contents",
        "references",
        "appendix",
        "summary",
    }
)

#: How sites join a page's title to their own name.
_SEGMENTS = re.compile(r"\s+(?:\||-|–|—|::|·|»)\s+")

#: A section number glued to a heading: "1Introduction", "2.3Methods".
_GLUED_NUMBER = re.compile(r"^\d+(?:\.\d+)*(?=[A-Z])")

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _is_site(segment: str, publisher: str | None, host: str | None) -> bool:
    low = segment.lower()
    if publisher and low == publisher.strip().lower():
        return True
    if host:
        bare = host.lower().removeprefix("www.")
        # "LTA.GOV.SG", "data.gov.sg", or the site's first label on its own.
        if low in (bare, f"www.{bare}", bare.split(".")[0]):
            return True
    return False


def clean_title(
    title: str | None, *, publisher: str | None = None, host: str | None = None
) -> str | None:
    """The title, or None when what the document declared is not one."""
    if not title:
        return None
    text = _norm(title)
    if len(text) < 3 or _PLACEHOLDER.match(text) or _FILE_NAME.search(text):
        return None

    segments = [s for s in _SEGMENTS.split(text) if s]
    kept = [s for s in segments if not _is_site(s, publisher, host) and s.lower() not in GENERIC]
    if not kept:
        return None
    text = " - ".join(kept) if len(kept) < len(segments) else text
    text = _GLUED_NUMBER.sub("", text).strip()
    if len(text) < 3 or text.lower() in GENERIC or _PLACEHOLDER.match(text):
        return None
    if _is_site(text, publisher, host):
        return None
    return text


#: A first line containing any of these is page furniture, not a heading.
_NOT_A_HEADING = re.compile(
    r"https?://|www\.|©|\bdoi\b|\bissn\b|\bvol(?:ume)?\.?\s*\d|\breceived\b|\baccepted\b|"
    r"\bcookies?\b|skip to|javascript|\bpage \d+ of\b|\bdownloaded\b",
    re.IGNORECASE,
)


def title_from_text(text: str | None, *, lines: int = 5) -> str | None:
    """A heading-like line from the start of the text, or None.

    Narrow on purpose: three to twenty-five words, starting with a capital or a
    digit, not ending in a colon or a full stop, and none of the furniture a
    PDF's first page carries (journal banners, DOIs, "Received:"). A wrong
    title is worse than none — the page shows its address instead.
    """
    if not text:
        return None
    for raw in text.splitlines()[: lines * 3]:
        line = _norm(raw.strip("#*_ "))
        if not line:
            continue
        lines -= 1
        words = line.split()
        if (
            3 <= len(words) <= 25
            and 15 <= len(line) <= 180
            and (line[0].isupper() or line[0].isdigit())
            and not line.endswith((":", ".", ","))
            and not _NOT_A_HEADING.search(line)
        ):
            cleaned = clean_title(line)
            if cleaned:
                return cleaned
        if lines <= 0:
            break
    return None
