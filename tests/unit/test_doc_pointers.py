"""Pointers from code into `docs/` resolve to a file and, with an anchor, to a heading (`D-01`).

As narrative moves out of the code, a comment's `See docs/features/x.md#y` is the only route
to the reasoning it replaced. A renamed heading or file breaks that route silently, so every
pointer in the Python and web sources is checked against the headings that exist.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

#: `docs/<path>.md` with an optional `#anchor`.
POINTER = re.compile(r"docs/(?P<path>[A-Za-z0-9_./-]+\.md)(?:#(?P<anchor>[A-Za-z0-9_-]+))?")

HEADING = re.compile(r"^#{1,6}\s+(?P<text>.+?)\s*#*\s*$", re.MULTILINE)

EXPLICIT = re.compile(r'<a id="(?P<id>[A-Za-z0-9_-]+)"></a>')

FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*]|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(doc: pathlib.Path) -> set[str]:
    """Every anchor in a Markdown file: headings (`-1` for repeats, as GitHub) and `<a id>`."""
    text = FENCE.sub("", doc.read_text())
    seen: dict[str, int] = {}
    out = {match["id"] for match in EXPLICIT.finditer(text)}
    for match in HEADING.finditer(text):
        base = slug(match.group("text"))
        count = seen.get(base, 0)
        out.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return out


def source_files() -> list[pathlib.Path]:
    """Tracked Python and web sources outside the tests."""
    listed = subprocess.run(
        ["git", "ls-files", "packages", "services", "web/src", "scripts"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [REPO / p for p in listed if p.endswith((".py", ".ts", ".tsx"))]


def pointers() -> list[tuple[str, str, str | None]]:
    """(source file, doc path, anchor) for every pointer into `docs/`."""
    found = []
    for path in source_files():
        for match in POINTER.finditer(path.read_text(errors="replace")):
            found.append((str(path.relative_to(REPO)), match["path"], match["anchor"]))
    return found


def test_slug_matches_github() -> None:
    """The slug rule agrees with GitHub on the shapes the docs use."""
    assert slug("The vector index") == "the-vector-index"
    assert slug("`B-97`: places, ranked last") == "b-97-places-ranked-last"
    assert slug("Display time zone") == "display-time-zone"
    assert slug("`chunk_topics` and labels") == "chunk_topics-and-labels"


def test_anchors_number_repeats(tmp_path: pathlib.Path) -> None:
    """A repeated heading gets `-1`, so a pointer to the second one still resolves."""
    doc = tmp_path / "d.md"
    doc.write_text("# A\n\n## Notes\n\n```\n# not a heading\n```\n\n## Notes\n")
    assert anchors(doc) == {"a", "notes", "notes-1"}


def test_explicit_anchors_count(tmp_path: pathlib.Path) -> None:
    """An `<a id>` before a bold paragraph lead is a target, as the feature docs use it."""
    doc = tmp_path / "d.md"
    doc.write_text('# A\n\n<a id="the-index"></a>**The index** is here.\n')
    assert anchors(doc) == {"a", "the-index"}


def test_there_are_pointers_to_check() -> None:
    """The scan finds the pointers the code is known to carry, so an empty pass is not a pass."""
    found = {(doc, anchor) for _, doc, anchor in pointers()}
    assert ("features/gaps.md", "routes") in found


@pytest.mark.parametrize(("source", "doc", "anchor"), pointers())
def test_pointer_resolves(source: str, doc: str, anchor: str | None) -> None:
    """Each pointer names a doc that exists and, if anchored, a heading in it."""
    target = REPO / "docs" / doc
    assert target.is_file(), f"{source} points at docs/{doc}, which does not exist"
    if anchor is not None:
        assert anchor in anchors(target), f"{source} points at docs/{doc}#{anchor}: no such heading"
