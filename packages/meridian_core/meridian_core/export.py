"""Export (task P6-15, spec §12.5).

BibTeX and Markdown from stored fields only: a field the document did not carry is
omitted, never guessed. The entry type is a format choice, not a ranking. See
docs/features/knowledge-graph.md#export.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence

from .logging import get_logger
from .models import Chunk, Source

log = get_logger(__name__)

#: §5.2's tiers to BibTeX entry types: a format mapping, not a ranking.
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

    Unique because `source_id` is appended.
    """
    stem = source.author or source.publisher or _host(source.url) or "source"
    year = source.publication_date.year if source.publication_date else "nd"
    return f"{_KEY_SAFE.sub('', stem)[:24].lower() or 'source'}{year}-{source.source_id}"


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def to_bibtex(sources: Sequence[Source], *, accessed: dt.date | None = None) -> str:
    """A BibTeX bibliography for ``sources``.

    ``accessed`` overrides each entry's own `accessed_at`, for a deterministic test.
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

    Each citation line carries the URL, the page or offset, and the tier.
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
