"""What a chunk is embedded as: the text a reader reads (task `B-49`).

The embedder is handed a *view*: link and image syntax replaced by its visible text, bare
URLs dropped, whitespace collapsed. The stored text, the lexical index and every citation
are untouched, and only chunks whose view differs need re-embedding
(:func:`view_differs`). See docs/features/embedding.md#the-embedding-view.
"""

from __future__ import annotations

import re

#: Bumped when the view changes, so a re-embed can be asked for by version.
VIEW_VERSION = 1

#: `![alt](src "title")` → alt.
_IMAGE = re.compile(r"!\[((?:\\.|[^\]\\])*)\]\(\s*[^)\s]*(?:\s+\"[^\"]*\")?\s*\)")

#: `[label](target "title")` → label. Nested brackets in labels are rare in
#: extracted markdown, and a missed nesting leaves text in, never takes it out.
_LINK = re.compile(r"\[((?:\\.|[^\]\\])*)\]\(\s*[^)\s]*(?:\s+\"[^\"]*\")?\s*\)")

#: `<https://…>` autolinks and bare URLs, not counting a trailing full stop or
#: comma, which belongs to the sentence the URL ended.
_URL = re.compile(r"<?\b(?:https?|ftp)://[^\s<>)\]]*[^\s<>)\].,;:!?'\"]>?", re.IGNORECASE)

#: Markdown escapes the extractor adds before punctuation: `\[`, `\(`, `\*`.
_ESCAPE = re.compile(r"\\([\[\]()*_`\\#.!-])")

_SPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")


def embedding_view(text: str) -> str:
    """The text an embedder should see for ``text``.

    Falls back to the original when the view would be empty — a chunk that is
    nothing but a URL still has to embed as *something*, and its own text is a
    better something than an empty string, which every model embeds the same.
    """
    view, images = _IMAGE.subn(lambda m: m.group(1), text)
    view, links = _LINK.subn(lambda m: m.group(1), view)
    view, urls = _URL.subn(" ", view)
    if not (images or links or urls):
        # Untouched text embeds exactly as before, so only chunks with links
        # need re-embedding when the view is introduced or changed.
        return text
    view = _ESCAPE.sub(r"\1", view)
    view = _SPACE.sub(" ", view)
    view = _BLANK_LINES.sub("\n\n", view)
    view = "\n".join(line.strip() for line in view.split("\n")).strip()
    return view if view.strip() else text


def view_differs(text: str) -> bool:
    """Whether a chunk's embedding would change under the current view."""
    return embedding_view(text) != text


def link_text_share(text: str) -> float:
    """How much of what a reader sees in ``text`` is the visible text of links.

    Measured on the view, so link targets never count; a label that is itself a URL
    counts as link text. 0.0 for text with no links; 1.0 when nothing but link text
    remains. See docs/features/embedding.md#the-embedding-view.
    """
    labels = sum(len(m.group(1)) for m in _IMAGE.finditer(text))
    without_images = _IMAGE.sub(lambda m: m.group(1), text)
    labels += sum(len(m.group(1)) for m in _LINK.finditer(without_images))
    if not labels:
        return 0.0
    visible = embedding_view(text)
    if not visible.strip():
        return 1.0
    return min(1.0, labels / len(visible))


#: A Markdown table's separator row: `|---|:--:|`.
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def table_head(text: str) -> str | None:
    """The header row of a pipe table in ``text``, the row above its separator; or None."""
    lines = text.split("\n")
    for index, line in enumerate(lines[1:], start=1):
        above = lines[index - 1].strip()
        if _SEPARATOR.match(line) and above.startswith("|"):
            return above
    return None


def continues_table(text: str) -> bool:
    """Whether ``text`` is rows of a table whose header is in an earlier passage (`B-195`).

    A table cut between rows (`B-191`) leaves every passage after the first without the row
    that names its columns: the numbers are there and what they measure is not.
    """
    first = next((line.strip() for line in text.split("\n") if line.strip()), "")
    return first.startswith("|") and table_head(text) is None


def with_table_head(view: str, head: str | None) -> str:
    """The view with its table's header row in front, when it continues a table.

    The view only, never the stored text: a passage stays a slice of its source.
    """
    return f"{head}\n{view}" if head else view
