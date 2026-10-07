"""Writing figures (task P1-10, spec §6.6).

Replaced wholesale on a re-crawl, like `chunks.replace_chunks`: figures have no stable
identity across fetches, and a caption for a diagram the page no longer has is worse than
none.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

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
