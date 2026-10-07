"""Pages that say they are not there (task `B-45`).

A site that answers 200 with "Page not found" in its template. The title decides, one
segment at a time (a segment must *be* an error phrase); when the title is generic, one of
a few unmistakable sentences in the opening text confirms. See
docs/features/extraction.md#error-pages.
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
