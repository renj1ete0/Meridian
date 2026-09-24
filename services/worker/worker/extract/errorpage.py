"""Pages that say they are not there (task `B-45`).

A missing page is supposed to answer 404. Many sites answer 200 with a page
titled "Page not found" wrapped in the full site template — a menu, a footer, a
search box, sometimes a list of popular links — and the fetch path, seeing a
success, stored it, chunked it and followed its links. On a real crawl those
pages were whole templates of navigation under an error title.

**The title decides, one segment at a time.** A page title is usually
``<page> | <site>`` or ``<page> - <site>``; the error is a whole segment, not a
word in one. "Rule 404. Character Evidence" and "Harmless Error" are real
documents, and a rule that matched the word would junk a court's rules of
evidence. So a segment must *be* an error phrase — "Page not found", "404",
"Error 404", "Not Found" — to count.

**The body can confirm when the title is generic.** Some templates keep the
site's own title on every page. For those, the opening of the text has to carry
one of a few unmistakable sentences ("The page you are looking for could not be
found"), and only in the first stretch of text, where an error template puts
it — an article *about* broken links says it much further down, if at all.
"""

from __future__ import annotations

import re

#: A whole title segment that says the page is missing.
_TITLE_SEGMENT = re.compile(
    r"^(?:"
    r"(?:error\s*)?404(?:\s*(?:error|-?\s*not found|page))?"
    r"|(?:page|file|document|content)\s+not\s+found"
    r"|not\s+found"
    r"|page\s+(?:does\s+not|doesn['’]t)\s+exist"
    r"|page\s+(?:cannot|can['’]t)\s+be\s+found"
    r"|oops!?.*"
    r"|(?:sorry,?\s+)?(?:this|the)\s+page\s+(?:is\s+)?(?:no\s+longer\s+available|has\s+been\s+removed)"
    r")$",
    re.IGNORECASE,
)

#: Title separators: `|`, ` - `, ` – `, ` — `, `·`, `::`.
_SEPARATORS = re.compile(r"\s+[|\-–—·]\s+|\s*\|\s*|\s*::\s*|\s*·\s*")

#: Sentences an error template carries in its opening text.
_BODY = re.compile(
    r"(?:the\s+)?page\s+(?:you\s+(?:are|were|'re)\s+looking\s+for|you\s+requested)"
    r"\s+(?:could\s+not\s+be\s+found|cannot\s+be\s+found|can['’]t\s+be\s+found|"
    r"does\s+not\s+exist|doesn['’]t\s+exist|is\s+not\s+available|no\s+longer\s+exists|"
    r"(?:may\s+)?(?:have\s+been|has\s+been|was)\s+(?:moved|removed|deleted))",
    re.IGNORECASE,
)

#: How far into the text the body sentence may sit.
BODY_WINDOW = 600


def title_says_missing(title: str | None) -> bool:
    if not title:
        return False
    return any(_TITLE_SEGMENT.match(segment.strip()) for segment in _SEPARATORS.split(title))


def body_says_missing(text: str | None) -> bool:
    return bool(text) and _BODY.search(text[:BODY_WINDOW]) is not None


def error_page_reason(title: str | None, text: str | None) -> str | None:
    """Why this page is an error page, or None if it is not one."""
    if title_says_missing(title):
        return "title"
    if body_says_missing(text):
        return "body"
    return None
