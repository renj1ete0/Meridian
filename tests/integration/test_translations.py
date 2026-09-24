"""Looking up, storing and using other-language names (task B-52)."""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import select

from meridian_core.models import FetchPolicy, TopicConfig, TranslationLookup
from meridian_core.policy import GLOBAL_DOMAIN, resolve_policy
from meridian_core.translations import STALE_AFTER, record, stale, translations_for
from worker import seedsearch, translate

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


@pytest.fixture
async def topic(sess) -> str:
    name = f"langtopic{uuid.uuid4().hex[:6]}"
    for row in await sess.scalars(select(TopicConfig)):
        row.status = "paused"
    sess.add(
        TopicConfig(
            topic=name,
            weight=0.1,
            floor=0.0,
            ceiling=1.0,
            status="active",
            description="a described topic for the test",
        )
    )
    glob = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    glob.settings = {**glob.settings, "search_languages": ["de", "ja"]}
    await sess.flush()
    return name


async def test_a_lookup_is_fresh_until_it_is_stale(sess) -> None:
    phrase = f"Phrase {uuid.uuid4().hex[:6]}"
    assert await stale(sess, [phrase]) == [phrase]

    await record(sess, phrase, "Article", {"de": "Wort"})

    assert await stale(sess, [phrase]) == []
    later = dt.datetime.now(dt.UTC) + STALE_AFTER + dt.timedelta(days=1)
    assert await stale(sess, [phrase], now=later) == [phrase]


async def test_a_phrase_with_no_article_is_recorded_and_not_asked_again(sess) -> None:
    phrase = f"nothing {uuid.uuid4().hex[:6]}"
    await record(sess, phrase, None, {})

    assert await stale(sess, [phrase]) == []
    assert await translations_for(sess, [phrase], ["de"]) == {}


async def test_translations_are_filtered_to_the_asked_languages(sess) -> None:
    phrase = f"Phrase {uuid.uuid4().hex[:6]}"
    await record(sess, phrase, "A", {"de": "Wort", "ja": "言葉", "sv": "ord"})

    assert await translations_for(sess, [phrase], ["ja", "de"]) == {
        phrase: [("ja", "言葉"), ("de", "Wort")]
    }


async def test_a_second_lookup_replaces_the_first(sess) -> None:
    phrase = f"Phrase {uuid.uuid4().hex[:6]}"
    await record(sess, phrase, "A", {"de": "alt"})
    await record(sess, phrase, "A", {"de": "neu"})

    rows = list(
        await sess.scalars(
            select(TranslationLookup).where(TranslationLookup.phrase == phrase.casefold())
        )
    )
    assert len(rows) == 1 and rows[0].translations == {"de": "neu"}


async def test_the_pass_looks_up_the_topic_and_records_what_it_found(sess, topic) -> None:
    asked: list[str] = []

    def wiki(request: httpx.Request) -> httpx.Response:
        title = request.url.params["titles"]
        asked.append(title)
        assert request.headers["user-agent"].startswith("Meridian")
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": {"1": {"title": title, "langlinks": [{"lang": "de", "*": "Deutsch"}]}}
                }
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(wiki), headers={"User-Agent": translate.user_agent()}
    )
    summary = await translate.run_once(client=client, session_factory=factory(sess), delay_s=0)

    assert summary["looked_up"] >= 1 and summary["found"] >= 1
    assert topic in asked
    assert await translations_for(sess, [topic], ["de"]) == {topic: [("de", "Deutsch")]}


async def test_a_failed_lookup_leaves_the_phrase_stale(sess, topic) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    summary = await translate.run_once(client=client, session_factory=factory(sess), delay_s=0)

    assert summary["looked_up"] == 0
    assert topic in await stale(sess, [topic])


async def test_seeds_use_the_translations_in_that_languages_words(sess, topic) -> None:
    await record(sess, topic, "Article", {"de": "Fußgängerfreundlichkeit"})

    run = await seedsearch.run_once(
        write=False, per_topic=50, seed=1, session_factory=factory(sess)
    )

    texts = {q.text for q in run.queries if q.topic == topic}
    assert ":de Fußgängerfreundlichkeit" in texts


async def test_search_languages_is_not_a_fetch_setting(sess, topic) -> None:
    policy = await resolve_policy(sess, "example.org")
    assert "search_languages" not in policy.model_dump()
