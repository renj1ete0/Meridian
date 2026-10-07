"""Watched questions: what is new for a saved view since it was last opened (`P6-43`).

Counted by the view's words (the lexical arm only) and topic filter, leaving out junk and
duplicates. Read-only, so it runs under the explore role (§12.6). See
docs/features/search.md#watched-questions.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, SavedView, Source

#: Past this the landing says "200+": a count exact to the unit costs a scan
#: for a number nobody reads beyond "a lot".
COUNT_CAP = 200


def watched_since(view: SavedView) -> dt.datetime:
    """From when a view counts new sources: last opened, else saved."""
    return view.last_opened_at or view.created_at


async def new_for_view(sess: AsyncSession, view: SavedView) -> int | None:
    """Sources new since the view was last opened that match it, capped at :data:`COUNT_CAP`.

    None when the view asks nothing a count can answer — no words and no topic (a view of one node's
    neighbourhood, say).
    """
    query = (view.query or "").strip()
    topics = [t for t in (view.filters or {}).get("topic") or [] if t]
    if not query and not topics:
        return None

    since = watched_since(view)
    conditions = [
        Source.created_at > since,
        Source.retention_tier != "junk",
        Source.duplicate_of.is_(None),
    ]
    if topics:
        conditions.append(Source.topic_labels.overlap(topics))
    if query:
        tsquery = func.websearch_to_tsquery("english", query)
        conditions.append(
            exists().where(
                Chunk.source_id == Source.source_id,
                Chunk.superseded_at.is_(None),
                Chunk.search_vector.op("@@")(tsquery),
            )
        )
    capped = select(Source.source_id).where(*conditions).limit(COUNT_CAP + 1).subquery()
    return int(await sess.scalar(select(func.count()).select_from(capped)) or 0)
