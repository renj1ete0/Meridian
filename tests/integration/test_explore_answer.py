"""`/api/explore/answer` against a real Postgres.

The grouping itself is unit-tested (`tests/unit/test_answer.py`). What only a
database can show is that `sources.places` as stored — array values, NULL for
never examined — reaches the grouping intact, and that filters named in the
query string reach the one search the answer runs.

Fixtures commit and delete themselves, scoped by a per-run marker and language,
for the reason `test_explore_api.py` gives: the app reads on its own connection
and the dev database holds a real crawl.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, update

from api.main import create_app
from meridian_core.answer import COVERAGE_RULE, STRONG_MIN_PUBLISHERS
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    return f"qxz{uuid.uuid4().hex[:10]}"


@pytest.fixture
def scope(marker: str) -> str:
    return f"zz-{marker[:8]}"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    await dispose_engines()


#: (publisher host, tier, places, what its passages add) per source.
LAYOUT = [
    ("a", "government", ["DE"], ""),
    ("b", "press", ["DEBER"], ""),  # a city: counts for DE
    # About two countries and naming both: counts for each (`B-168`).
    ("c", "press", ["DE", "FR"], " Trials ran in Germany and in France."),
    ("a", "press", ["FR"], ""),  # same publisher as the first
    ("d", "institutional", [], ""),  # examined, about no place
    ("e", "press", None, ""),  # never examined
    # About two countries, but no matching passage names either: unplaced, and counted.
    ("f", "press", ["DE", "FR"], ""),
]


@pytest.fixture
async def corpus(session_for, marker: str, scope: str):
    sess = await session_for("rw")
    made = []
    for index, (host, tier, places, adds) in enumerate(LAYOUT):
        source, _ = await upsert_source(
            sess,
            f"https://{host}.{marker}.test/doc-{index}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier=tier,
            language=scope,
            title=f"Document {index}",
        )
        await replace_chunks(
            sess,
            source.source_id,
            [
                ChunkWrite(
                    text=f"{marker} rules paragraph {n} of document {index}.{adds}", chunk_index=n
                )
                for n in range(2)
            ],
        )
        await sess.execute(
            update(Source).where(Source.source_id == source.source_id).values(places=places)
        )
        made.append(source)
    await sess.commit()

    yield made

    await sess.execute(delete(Source).where(Source.url.like(f"https://%.{marker}.test/%")))
    await sess.commit()


async def test_groups_by_country_with_cities_rolled_up(client, corpus, marker, scope) -> None:
    response = await client.get("/api/explore/answer", params={"q": marker, "language": scope})
    assert response.status_code == 200
    body = response.json()

    by_code = {g["code"]: g for g in body["groups"]}
    assert set(by_code) == {"DE", "FR"}

    de = by_code["DE"]
    assert de["name"] == "Germany"
    assert de["sources"] == 3
    assert de["publishers"] == 3
    assert de["tier_mix"] == {"government": 1, "press": 2}
    # Three publishers and a government source: the rule's boundary, met.
    assert STRONG_MIN_PUBLISHERS == 3
    assert de["coverage"] == "strong"
    # One item per source, each carrying one of its passages and the count.
    assert len({item["source_id"] for item in de["items"]}) == len(de["items"]) == 3
    assert all(item["passages"] == 2 for item in de["items"])
    assert all(marker in item["text"] for item in de["items"])

    fr = by_code["FR"]
    assert fr["sources"] == 2
    assert fr["coverage"] == "thin"

    # Strong first.
    assert body["groups"][0]["code"] == "DE"

    rest = body["unplaced"]
    assert rest["code"] is None
    assert rest["sources"] == 3
    assert rest["unexamined"] == 1
    assert rest["several_places"] == 1, "the two-country source whose passages name neither"

    assert body["coverage_rule"] == COVERAGE_RULE
    assert body["sources_considered"] == len(LAYOUT)
    assert body["passages_considered"] == 2 * len(LAYOUT)


async def test_filters_reach_the_search(client, corpus, marker, scope) -> None:
    response = await client.get(
        "/api/explore/answer",
        params={"q": marker, "language": scope, "source_tier": "government"},
    )
    body = response.json()
    assert [g["code"] for g in body["groups"]] == ["DE"]
    assert body["groups"][0]["sources"] == 1
    assert body["unplaced"] is None

    response = await client.get(
        "/api/explore/answer", params={"q": marker, "language": scope, "top": 1}
    )
    de = next(g for g in response.json()["groups"] if g["code"] == "DE")
    assert len(de["items"]) == 1
    assert de["sources"] == 3


async def test_empty_question_is_an_empty_answer_not_an_error(client) -> None:
    body = (await client.get("/api/explore/answer", params={"q": ""})).json()
    assert body["groups"] == []
    assert body["unplaced"] is None
    assert body["degraded_reason"]


@pytest.mark.parametrize(
    "bad", [{"top": 0}, {"top": 999}, {"candidates": 0}, {"candidates": 5000}, {"topic_match": "x"}]
)
async def test_out_of_range_parameters_are_refused(client, bad) -> None:
    response = await client.get("/api/explore/answer", params={"q": "x", **bad})
    assert response.status_code == 422
