"""Look up other-language names for the search vocabulary (task `B-52`, §7.4).

``python -m worker.translate --once`` asks Wikipedia, for each phrase the search
seeds are built from (a searchable topic's name and its query vocabulary) that
has not been looked up recently, which article the phrase names and what that
article is called in each of the configured `search_languages`. The answers go
to `translation_lookups`, where `worker.seedsearch` finds them.

Polite by construction: one request at a time, :data:`DELAY_S` apart, a
User-Agent that says what this is and how to reach its operator (Wikimedia's
API policy asks for exactly that), and a bounded number of phrases per run.
No model, and nothing here decides what a word means: a phrase with no
article gets no translation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import time

import httpx

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.searchseeds import topic_words
from meridian_core.translations import parse_langlinks, record, search_languages, stale

from .seedsearch import specific_enough, topic_inputs

log = get_logger(__name__)

API = "https://en.wikipedia.org/w/api.php"
DELAY_S = 1.0
DEFAULT_LIMIT = 200


def contact() -> str | None:
    return os.environ.get("MERIDIAN_CONTACT_EMAIL", "").strip() or None


def user_agent() -> str:
    return f"Meridian/1 (research crawler; vocabulary lookups; {contact() or 'no contact'})"


def title_forms(phrase: str) -> list[str]:
    """The article titles to try for a phrase, as written first.

    Titles are case-sensitive after the first character, and vocabulary is not
    written in Wikipedia's sentence case — "Mass Rapid Transit" names nothing,
    "Mass rapid transit" names the article.
    """
    forms = [phrase.strip()]
    sentence = phrase.strip()[:1].upper() + phrase.strip()[1:].lower()
    if sentence not in forms:
        forms.append(sentence)
    return forms


async def look_up(
    client: httpx.AsyncClient, phrase: str, languages: tuple[str, ...], *, delay_s: float = DELAY_S
):
    """``(article, {lang: title})`` for one phrase, trying each title form."""
    for form in title_forms(phrase):
        response = await client.get(
            API,
            params={
                "action": "query",
                "format": "json",
                "redirects": 1,
                "prop": "langlinks",
                "lllimit": "max",
                "titles": form,
            },
        )
        response.raise_for_status()
        article, found = parse_langlinks(response.json(), languages)
        if article is not None:
            return article, found
        await asyncio.sleep(delay_s)
    return None, {}


async def run_once(
    *,
    limit: int = DEFAULT_LIMIT,
    client: httpx.AsyncClient | None = None,
    session_factory=session,
    delay_s: float = DELAY_S,
) -> dict:
    async with session_factory() as sess:
        languages = await search_languages(sess)
        topics = [t for t in await topic_inputs(sess) if specific_enough(t)]
        phrases = sorted(
            {topic_words(t.topic) for t in topics} | {p for t in topics for p in t.terms}
        )
        todo = (await stale(sess, phrases))[:limit]
    summary = {"languages": list(languages), "phrases": len(phrases), "looked_up": 0, "found": 0}
    if client is None and contact() is None:
        # Measured: Wikimedia answers every request 403 under its robot policy
        # when the User-Agent carries no way to reach the operator. Refusing
        # once, loudly, beats a run of failures that reads as a network fault.
        log.error("MERIDIAN_CONTACT_EMAIL is not set; Wikimedia refuses anonymous clients")
        summary["refused"] = "no contact configured (MERIDIAN_CONTACT_EMAIL)"
        return summary
    if not languages:
        log.warning("no search_languages configured; nothing to translate")
        return summary

    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0, headers={"User-Agent": user_agent()})
    try:
        for phrase in todo:
            try:
                article, found = await look_up(client, phrase, languages, delay_s=delay_s)
            except (httpx.HTTPError, ValueError) as exc:
                # One failed lookup is not a failed run; the phrase stays stale
                # and is asked again next time.
                log.warning(
                    "translation lookup failed", extra={"phrase": phrase, "error": str(exc)}
                )
                await asyncio.sleep(delay_s)
                continue
            async with session_factory() as sess:
                await record(sess, phrase, article, found)
                await sess.commit()
            summary["looked_up"] += 1
            summary["found"] += bool(found)
            await asyncio.sleep(delay_s)
    finally:
        if own:
            await client.aclose()
    log.info("vocabulary translations looked up", extra=summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Other-language names for search vocabulary (B-52)."
    )
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    configure_logging("translate")

    async def go() -> dict:
        try:
            return await run_once(limit=args.limit)
        finally:
            await dispose_engines()

    with bind_run_id(f"translate-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        summary = asyncio.run(go())
    print(summary)


if __name__ == "__main__":
    main()
