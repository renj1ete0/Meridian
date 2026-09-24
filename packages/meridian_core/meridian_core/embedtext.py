"""What a chunk is embedded as: the text a reader reads (task `B-49`).

A chunk's stored text is a verbatim slice of the extraction, markdown links and
all, and it must stay one: the offset is a citation. But the embedding is not a
citation, it is a representation of meaning — and a URL carries almost none.
Measured over a real crawl, one character in eight of chunk text sat inside a
link target, and one chunk in ten was more than 30% URL. Embedded as-is, pages
cluster by the shape of their links (a listing page of one site sits beside
every other listing page of that site whatever they say), and a search for a
subject finds pages that merely link to it.

So the embedder is handed a *view*: link and image syntax replaced by its
visible text, bare URLs dropped, whitespace collapsed. The stored text, the
lexical index and every citation are untouched. A chunk with no links has a
view identical to its text, so changing this function only needs re-embedding
the chunks it actually changes (:func:`view_differs`).
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

    Measured on the view, not the raw text, so link *targets* never count
    either way: a sentence with one long URL in it is still a sentence. A chunk
    that is mostly link labels is a listing — a table of contents, a directory
    of regulations, a publication list — and its vector is the average of the
    things it links to, not a statement about any of them (`P2-24` reads this
    to keep listings from being labelled as passages about a topic).

    A link whose label is itself a URL — how extractors render most reference
    lists — counts its label as link text although the view drops it, which
    pushes such a list towards 1.0. That is deliberate: a bibliography is a
    listing too, and it was measured that way.

    0.0 for text with no links; 1.0 when nothing but link text remains.
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
