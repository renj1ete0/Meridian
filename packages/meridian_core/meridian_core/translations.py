"""Other-language names for the vocabulary, from Wikipedia (task `B-52`, §7.4).

Interlanguage titles of the same article, looked up, never generated; a phrase with no
article has no translation. Cleaning is conservative: a parenthesised qualifier is
dropped, a title identical to the English is not a translation. See
docs/features/discovery.md#other-languages.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FetchPolicy, TranslationLookup
from .policy import GLOBAL_DOMAIN

#: How long a lookup stands before it is asked again. Articles are renamed and
#: gain interlanguage links slowly.
STALE_AFTER = dt.timedelta(days=90)

_QUALIFIER = re.compile(r"\s*\([^)]*\)\s*$")
_SPACE = re.compile(r"\s+")


def phrase_key(phrase: str) -> str:
    return _SPACE.sub(" ", phrase).strip().casefold()


def clean_title(title: str | None) -> str | None:
    """A title as a search phrase: qualifier dropped, whitespace collapsed."""
    if not title:
        return None
    cleaned = _SPACE.sub(" ", _QUALIFIER.sub("", title)).strip()
    return cleaned or None


def parse_langlinks(
    payload: Mapping[str, Any], languages: Iterable[str]
) -> tuple[str | None, dict[str, str]]:
    """``(article, {lang: title})`` from one `action=query&prop=langlinks` answer.

    A missing page (the phrase names no article) gives ``(None, {})``.
    """
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), None) if pages else None
    if not page or "missing" in page or "invalid" in page:
        return None, {}
    article = page.get("title")
    wanted = set(languages)
    english = {phrase_key(article or "")}
    out: dict[str, str] = {}
    for link in page.get("langlinks") or ():
        lang = link.get("lang")
        title = clean_title(link.get("*") or link.get("title"))
        if lang in wanted and title and phrase_key(title) not in english:
            out[lang] = title
    return article, out


async def search_languages(sess: AsyncSession) -> tuple[str, ...]:
    """The languages non-English seeds are written in, from the global policy row."""
    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    languages = (row.settings or {}).get("search_languages") if row else None
    return tuple(str(lang) for lang in languages or ())


async def record(
    sess: AsyncSession,
    phrase: str,
    article: str | None,
    translations: Mapping[str, str],
    *,
    now: dt.datetime | None = None,
) -> None:
    """Write one lookup, replacing any earlier one for the phrase. Flushes."""
    now = now or dt.datetime.now(dt.UTC)
    stmt = insert(TranslationLookup).values(
        phrase=phrase_key(phrase),
        article=article,
        translations=dict(translations),
        looked_up_at=now,
    )
    await sess.execute(
        stmt.on_conflict_do_update(
            index_elements=[TranslationLookup.phrase],
            set_={
                "article": stmt.excluded.article,
                "translations": stmt.excluded.translations,
                "looked_up_at": stmt.excluded.looked_up_at,
            },
        )
    )


async def stale(
    sess: AsyncSession, phrases: Sequence[str], *, now: dt.datetime | None = None
) -> list[str]:
    """The phrases never looked up, or looked up longer ago than :data:`STALE_AFTER`."""
    now = now or dt.datetime.now(dt.UTC)
    keys = {phrase_key(p): p for p in phrases if phrase_key(p)}
    fresh = set(
        await sess.scalars(
            select(TranslationLookup.phrase).where(
                TranslationLookup.phrase.in_(list(keys)),
                TranslationLookup.looked_up_at > now - STALE_AFTER,
            )
        )
    )
    return [original for key, original in keys.items() if key not in fresh]


async def translations_for(
    sess: AsyncSession, phrases: Iterable[str], languages: Sequence[str]
) -> dict[str, list[tuple[str, str]]]:
    """``{phrase: [(lang, text), …]}`` for the phrases that have any, in ``languages``."""
    keys = {phrase_key(p): p for p in phrases if phrase_key(p)}
    if not keys or not languages:
        return {}
    rows = await sess.scalars(
        select(TranslationLookup).where(TranslationLookup.phrase.in_(list(keys)))
    )
    out: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        found = [(lang, row.translations[lang]) for lang in languages if row.translations.get(lang)]
        if found:
            out[keys[row.phrase]] = found
    return out
