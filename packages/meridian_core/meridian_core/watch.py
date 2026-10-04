"""Watched questions: what is new for a saved view since it was last opened (`P6-43`).

A saved view is a question somebody asked and meant to come back to. What they
want on return is not a generic "N sources arrived" but "N arrived that answer
this". So each view is counted against its own words and topic filter, from
the moment it was last opened (or saved, if never opened).

Words match the way search's lexical arm matches them — the stored
``search_vector`` against ``websearch_to_tsquery`` — so the count agrees with
what opening the view will find by words. The vector arm is not run: a count on
the landing page must be cheap for every view at once, and a vector query per
view is not. Junk and duplicates are left out, as search leaves them out.

Read-only, so it can run under the explore role (§12.6).
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
