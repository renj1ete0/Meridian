"""DOI syntax: the one normaliser every service uses (tasks P1-14, B-264).

The worker's resolver and the document connector both take DOIs from outside — a crawled
reference list, an uploaded form — and both must agree on whether two strings are the same
DOI, so there is one function here rather than one per service.
See docs/features/discovery.md#resolution.
"""

from __future__ import annotations

import re

#: A DOI, as registered: a `10.` prefix, a registrant code, then an opaque
#: suffix. Validated rather than trusted because the value reaches a URL path —
#: citations come from crawled pages, and a page can say anything.
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")

#: Prefixes a DOI arrives wearing when it was written as a link or a URN.
DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")


class DoiError(ValueError):
    """The DOI itself is not usable. Not retryable, and not the network's fault."""


def normalise_doi(raw: str) -> str:
    """Strip the ways a DOI is written down, check what is left, and lower-case it.

    Raises `DoiError` rather than returning None: a caller that got a bad DOI
    has a bad row, not an empty result, and the two settle differently.
    """
    doi = (raw or "").strip()
    lowered = doi.lower()
    for prefix in DOI_PREFIXES:
        if lowered.startswith(prefix):
            doi = doi[len(prefix) :]
            lowered = doi.lower()
            break
    doi = doi.strip().rstrip(".,;)")
    if not DOI_PATTERN.match(doi):
        raise DoiError(f"not a DOI: {raw!r}")
    # A DOI cannot contain whitespace or a fragment, and something claiming to
    # is trying to reach a path this code never intended to construct.
    if any(ch in doi for ch in ("#", "?", "\\")) or ".." in doi:
        raise DoiError(f"refusing a DOI with URL syntax in it: {raw!r}")
    return doi.lower()
