"""Export (task P6-15, spec §12.5).

> "**Export:** BibTeX (citations) and Markdown (notes) — avoid trapping material
> in a bespoke store."

The requirement is about leaving, and it is not a courtesy. A research corpus
that can only be read through its own interface is a bet that the interface
outlives the research, and that bet is usually lost. BibTeX goes into Zotero and
LaTeX; Markdown goes into anything.

**Nothing here is generated.** Every field comes from a `sources` row that a
crawl filled in, and a field the document did not carry is *omitted* rather than
guessed. A fabricated author or year in a bibliography is worse than a missing
one — it is wrong in a file somebody will paste into a paper, and the whole
point of this corpus is being checkable.

**The entry type is derived from the tier and says nothing about quality.**
§8 extracts structure and scores nothing, and a bibliography is exactly where an
implied verdict would do damage: `@article` versus `@misc` reads as a judgement
if it is allowed to. It is a *format* decision — what fields a reader of the
bibliography will expect — and `note` carries the tier verbatim so the
information is present without being ranked.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence

from .logging import get_logger
from .models import Chunk, Source

log = get_logger(__name__)

#: §5.2's tiers to BibTeX entry types. A *format* mapping, not a ranking — see
#: the module docstring. Government and institutional reports are `@techreport`
#: because that is the entry a reader expects for an issuing body with no
#: journal; everything web-native is `@online`, which is the type that carries
#: `urldate`.
ENTRY_TYPE = {
    "peer_reviewed": "article",
    "government": "techreport",
    "institutional": "techreport",
    "press": "online",
    "informal": "online",
}
DEFAULT_ENTRY_TYPE = "online"

#: BibTeX's own escapes. `&` and friends are commands in TeX, and a title
#: containing one silently breaks the document it is pasted into.
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_KEY_SAFE = re.compile(r"[^A-Za-z0-9]+")


def tex_escape(value: str) -> str:
    return "".join(_TEX_ESCAPES.get(char, char) for char in value)


def citation_key(source: Source) -> str:
    """A stable, unique key for one source.

    Stable because a bibliography is re-exported and diffed, and a key that
    changed between runs would rewrite every citation in a document that
    referenced it. Unique because `source_id` is appended — two reports from the
    same body in the same year is the ordinary case, not an edge one, and
    silently colliding keys drop entries with no error anywhere.
    """
    stem = source.author or source.publisher or _host(source.url) or "source"
    year = source.publication_date.year if source.publication_date else "nd"
    return f"{_KEY_SAFE.sub('', stem)[:24].lower() or 'source'}{year}-{source.source_id}"


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def to_bibtex(sources: Sequence[Source], *, accessed: dt.date | None = None) -> str:
    """A BibTeX bibliography for ``sources``.

    ``accessed`` overrides the per-source access date, for a deterministic
    export in a test. Otherwise each entry carries its own `accessed_at`, which
    is the date that actually matters for a web citation: it says what the page
    said when this corpus read it, which is the claim the raw file backs up.
    """
    return "\n\n".join(_entry(source, accessed) for source in sources) + "\n" if sources else ""


def _entry(source: Source, accessed: dt.date | None) -> str:
    entry_type = ENTRY_TYPE.get(source.source_tier, DEFAULT_ENTRY_TYPE)
    fields: list[tuple[str, str]] = []

    def add(name: str, value: object | None) -> None:
        # Omitted, never guessed. A fabricated year in a bibliography is wrong
        # in a file somebody pastes into a paper.
        if value in (None, ""):
            return
        fields.append((name, tex_escape(str(value))))

    add("title", source.title)
    add("author", source.author)
    add("institution" if entry_type == "techreport" else "publisher", source.publisher)
    if source.publication_date:
        add("year", source.publication_date.year)
        add("month", source.publication_date.strftime("%b").lower())
    add("doi", source.doi)
    add("url", source.url)

    when = accessed or (source.accessed_at.date() if source.accessed_at else None)
    if when:
        add("urldate", when.isoformat())

    # The tier, verbatim and unranked. A reader of the bibliography can see what
    # kind of document this was without the entry type having implied a verdict.
    add("note", f"Meridian source tier: {source.source_tier}")

    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{entry_type}{{{citation_key(source)},\n{body}\n}}"


def to_markdown(
    rows: Iterable[tuple[Chunk, Source]],
    *,
    title: str = "Meridian export",
    query: str | None = None,
) -> str:
    """Passages as Markdown, each under the source that justifies it.

    Grouped by source rather than listed flat, because a reader scanning this
    later is asking "what did this document say" far more often than "what was
    the fourth result". The citation line carries what makes the passage
    checkable — the URL, the page or offset, and the tier.
    """
    grouped: dict[int, tuple[Source, list[Chunk]]] = {}
    for chunk, source in rows:
        grouped.setdefault(source.source_id, (source, []))[1].append(chunk)

    lines = [f"# {title}", ""]
    if query is not None:
        # Recorded because the same export means different things depending on
        # what was asked, and a file found in six months carries no other
        # context about why these passages and not others.
        lines += [f"Query: `{query}`", ""]
    count = len(grouped)
    lines += [
        f"Exported {dt.date.today().isoformat()} · {count} source{'' if count == 1 else 's'}",
        "",
    ]

    for source, chunks in grouped.values():
        lines.append(f"## {source.title or _host(source.url) or 'Untitled'}")
        lines.append("")
        meta = [f"<{source.url}>", f"tier: {source.source_tier}"]
        if source.publication_date:
            meta.append(f"published: {source.publication_date.isoformat()}")
        if source.doi:
            meta.append(f"doi: {source.doi}")
        lines += ["  \n".join(meta), ""]

        for chunk in sorted(chunks, key=lambda c: c.chunk_index):
            where = (
                f"[{_unit(source)} {chunk.page_or_offset}]"
                if chunk.page_or_offset is not None
                else ""
            )
            lines.append(f"> {chunk.text.strip()}".replace("\n", "\n> "))
            lines.append("")
            lines.append(f"— `chunk {chunk.chunk_id}` {where}".strip())
            lines.append("")

    return "\n".join(lines)


def _unit(source: Source) -> str:
    """§5.3's rule, and `P2-18`'s answer to it."""
    media = (source.extra or {}).get("media_type")
    if media is None:
        return "page/offset"
    return "page" if media == "application/pdf" else "offset"
