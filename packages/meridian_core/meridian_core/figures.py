"""Writing figures (task P1-10, spec §6.6), and telling a figure from page furniture (`B-156`).

Replaced wholesale on a re-crawl, like `chunks.replace_chunks`: figures have no stable
identity across fetches, and a caption for a diagram the page no longer has is worse than
none.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Figure

log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class FigureWrite:
    """One figure to persist. Mirrors the columns extraction can fill.

    Deliberately not the worker's `ExtractedFigure`: this package is imported by
    the API and the orchestrator, and neither has business depending on the
    extractor's dataclasses to talk about rows.
    """

    caption: str | None = None
    alt_text: str | None = None
    image_url: str | None = None
    page: int | None = None


async def replace_figures(
    sess: AsyncSession, source_id: int, figures: Sequence[FigureWrite]
) -> tuple[int, int]:
    """Make ``figures`` the complete set for ``source_id``. Returns (written, deleted).

    Flushes; does not commit: it belongs in the caller's transaction with the source
    row and the chunks.
    """
    deleted = await delete_figures(sess, source_id)

    for figure in figures:
        sess.add(
            Figure(
                source_id=source_id,
                caption=figure.caption,
                alt_text=figure.alt_text,
                image_url=figure.image_url,
                page=figure.page,
            )
        )
    await sess.flush()

    if figures or deleted:
        log.info(
            "figures replaced",
            extra={"source_id": source_id, "written": len(figures), "deleted": deleted},
        )
    return len(figures), deleted


async def delete_figures(sess: AsyncSession, source_id: int) -> int:
    result = await sess.execute(delete(Figure).where(Figure.source_id == source_id))
    return int(result.rowcount or 0)


async def figure_count(sess: AsyncSession, source_id: int) -> int:
    rows = await sess.execute(select(Figure.figure_id).where(Figure.source_id == source_id))
    return len(list(rows.scalars()))


# ---------------------------------------------------------------------------
# What a reader is shown (`B-156`)
# ---------------------------------------------------------------------------

#: Words that mark page furniture rather than a figure: logos, icons, social links,
#: controls. Read in an image's path and, when the label is short, in its caption or alt
#: text. See docs/features/web-app.md#figures-furniture.
_FURNITURE_WORDS = (
    r"logo|logos|icon|icons|favicon|sprite|seal|badge|banner|avatar|button|btn|arrow|chevron|"
    r"close|menu|hamburger|search|share|social|socialmedia|facebook|twitter|linkedin|instagram|"
    r"tiktok|youtube|pinterest|rss|print|header|footer|placeholder|spacer|pixel|loader|spinner"
)
_FURNITURE_IN_PATH = re.compile(rf"\b({_FURNITURE_WORDS})\b", re.I)
#: In a label, messaging apps too: in a path they also name photos sent through them.
_FURNITURE_IN_LABEL = re.compile(rf"\b({_FURNITURE_WORDS}|whatsapp|telegram|bluesky)\b", re.I)

#: A label longer than this is a description, even if it names a logo in passing.
_SHORT_LABEL_WORDS = 6

#: A camera's or stock library's name in a label: the file was named, not described.
_CAMERA_OR_STOCK = re.compile(
    r"\b(img|dsc|dscn|unsplash|shutterstock|istock|getty|gettyimages|pexels|adobestock)\b"
)
#: A token of both cases with digits in two places between letters, like a generated file id
#: ("0Wjcr3j8CU"). One number inside or on the end of a word is a name ("EU4Health",
#: "Design102").
_GENERATED_TOKEN = re.compile(r"\b(?=\w*[a-z])(?=\w*[A-Z])(?=\w*[A-Za-z]\d+[A-Za-z]+\d)\w{7,}\b")

#: A run of digits no year, count or date has: a file or stock-library id. An eight-digit
#: date (20260923) starts many a dated title.
_LONG_ID = re.compile(r"(?<!\d)(?!(?:19|20)\d{6}(?!\d))\d{6,}")

#: A camera's or stock library's word marks a file name only in a label this short; in a
#: longer one it is a photo credit on a description.
_SHORT_CAMERA_WORDS = 3

#: What a publishing system appends to a file name: a thumbnail's size, a "scaled" copy, an
#: edit stamp. Not part of what the author called it.
_FILE_SUFFIX = re.compile(r"[-_](\d{2,5}x\d{2,5}|scaled|e\d{9,})$", re.I)

#: Marks of a name rather than a phrase: a short label ending in a bare number, as a copy
#: uploaded twice is ("blueprint health happiness 1"), or one CamelCase word run together.
#: A year is not such a number, and a long label is a description whatever number ends it.
_TRAILING_NUMBER = re.compile(r"(^|\s)(?!(19|20)\d\d$)\d+$")
_CAMEL_CASE = re.compile(r"[a-z][A-Z]")
_SHORT_NAME_WORDS = 5


def _image_path(image_url: str | None) -> str:
    if not image_url:
        return ""
    return unquote(urlsplit(image_url).path)


def _plain_words(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text).strip().lower()


def is_furniture(image_url: str | None, caption: str | None, alt_text: str | None) -> bool:
    """Whether a figure is part of the page's chrome — a logo, an icon, a control.

    An inline (`data:`) image, an SVG, a path naming such a thing, or a short label that
    does. Checked on a sample of stored figures before use; see
    docs/features/web-app.md#figures-furniture.
    """
    if image_url and image_url.startswith("data:"):
        return True
    path = _image_path(image_url).lower()
    if path.endswith(".svg"):
        return True
    if _FURNITURE_IN_PATH.search(re.sub(r"[-_/.]", " ", path)):
        return True
    label = " ".join(x for x in (caption, alt_text) if x)
    return bool(_FURNITURE_IN_LABEL.search(label)) and len(label.split()) <= _SHORT_LABEL_WORDS


def is_filename_like(text: str, image_url: str | None) -> bool:
    """Whether a caption is the image's file name rather than a description of it."""
    words = _plain_words(text)
    if not words:
        return True
    if _GENERATED_TOKEN.search(text) or _LONG_ID.search(text):
        return True
    if _CAMERA_OR_STOCK.search(words) and len(words.split()) <= _SHORT_CAMERA_WORDS:
        return True
    name = PurePosixPath(_image_path(image_url)).stem
    while (trimmed := _FILE_SUFFIX.sub("", name)) != name:
        name = trimmed
    stem = _plain_words(name)
    # The same words as the file is only a file name when it also looks like one: a page
    # whose author wrote "happiness metrics" twice has still described the picture.
    same = bool(stem) and words.replace(" ", "") == stem.replace(" ", "")
    numbered = len(words.split()) <= _SHORT_NAME_WORDS and bool(_TRAILING_NUMBER.search(words))
    # CamelCase marks a name only when it is the whole label, run together; inside a phrase
    # it is a brand ("SkillsFuture Credit").
    run_together = " " not in text.strip() and bool(_CAMEL_CASE.search(text))
    return same and (numbered or run_together)


def reader_caption(caption: str | None, alt_text: str | None, image_url: str | None) -> str | None:
    """The caption or alt text to show a reader, or None when both are only a file name."""
    for text in (caption, alt_text):
        if text and text.strip() and not is_filename_like(text, image_url):
            return text.strip()
    return None
