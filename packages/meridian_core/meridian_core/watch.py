"""Watched questions: what is new for a saved view since it was last opened (`P6-43`).

Counted by the view's words (the lexical arm only) and every filter it stores, through the
search's own predicate, so junk, copies and superseded passages are left out as Find leaves
them. Read-only, so it runs under the explore role (§12.6). See
docs/features/search.md#watched-questions.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, SavedView, Source
from .search import SearchFilters, passage_conditions

#: Past this the landing says "200+": a count exact to the unit costs a scan
#: for a number nobody reads beyond "a lot".
COUNT_CAP = 200


def watched_since(view: SavedView) -> dt.datetime:
    """From when a view counts new sources: last opened, else saved."""
    return view.last_opened_at or view.created_at


def filters_of(view: SavedView) -> SearchFilters | None:
    """A search view's stored filters as the search applies them; None for a node view.

    Stored under `SearchFilters`' names (`B-73`); `topic` is the spelling from before it. Dates
    are stored as ISO strings and become dates here. A set the search cannot apply is None,
    which counts nothing rather than counting wider than the view.
    """
    if view.focus_entity_id is not None:
        return None
    stored = dict(view.filters or {})
    legacy = stored.pop("topic", None)
    if legacy and "topics" not in stored:
        stored["topics"] = legacy
    for key in ("published_after", "published_before"):
        if isinstance(stored.get(key), str):
            try:
                stored[key] = dt.date.fromisoformat(stored[key])
            except ValueError:
                return None
    try:
        return SearchFilters(**stored)
    except TypeError:
        return None


def _narrows(filters: SearchFilters) -> bool:
    return bool(
        filters.topics
        or filters.places
        or filters.source_tiers
        or filters.published_after
        or filters.published_before
    )


async def new_for_view(sess: AsyncSession, view: SavedView) -> int | None:
    """Sources new since the view was last opened that match it, capped at :data:`COUNT_CAP`.

    Matched as Find would match them (`B-194`): the view's words on the lexical arm and every
    filter it stores, through the search's own predicate. None when the view asks nothing a
    count can answer: a node view, or no words and no filter.
    """
    filters = filters_of(view)
    query = (view.query or "").strip()
    if filters is None or (not query and not _narrows(filters)):
        return None

    since = watched_since(view)
    passage = [Chunk.source_id == Source.source_id, *passage_conditions(filters)]
    if query:
        passage.append(Chunk.search_vector.op("@@")(func.websearch_to_tsquery("english", query)))
    conditions = [Source.created_at > since, exists().where(*passage)]
    capped = select(Source.source_id).where(*conditions).limit(COUNT_CAP + 1).subquery()
    return int(await sess.scalar(select(func.count()).select_from(capped)) or 0)
