"""Find by document kind and by site (task B-254).

Against Postgres because both filters are SQL: the kind is a column, and the site is the host
parsed out of `sources.url` by an expression that must agree with `boilerplate.host_key`. The
failure worth guarding is silent: a filter accepted at the boundary and lost before the query
returns *more*, which reads as a generous search rather than a broken one. So each filter is
checked on both arms, and the rejections (a lookalike host, a wildcard in a host, an
unclassified source) carry as much weight as the matches.
"""

from __future__ import annotations

import math
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, SavedView, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.search import SearchFilters, search
from meridian_core.sites import site_counts
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


def at(similarity: float, axis: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = similarity
    vector[axis] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


QUERY = at(1.0, 1)
#: Passages long enough for the vector arm, which leaves out fragments.
FILLER = " is the subject of this passage, written out at some length about the matter."


def own_words(text: str) -> str:
    """`text` padded with FILLER, each word tagged so no two fixtures share a phrase.

    Search folds a passage whose phrases another source already holds (`B-236`); these
    fixtures are different documents, so their padding must not read as one copied text.
    """
    tag = uuid.uuid4().hex[:6]
    return text + " " + " ".join(f"{word}{tag}" for word in FILLER.split())


@pytest.fixture
def run() -> str:
    """Scopes this run: the language every fixture carries, and the stem of its hosts."""
    return uuid.uuid4().hex[:8]


@pytest.fixture
def scope(run: str) -> str:
    return f"zz-{run}"


@pytest.fixture
def term(run: str) -> str:
    return f"qxz{run}"


@pytest.fixture
async def sess(session_for, scope):
    s = await session_for("rw")
    yield s
    await s.rollback()
    await s.execute(delete(Source).where(Source.language == scope))
    await s.commit()


async def a_source(sess, scope: str, url: str, text: str, axis: int, **fields) -> Source:
    source, _ = await upsert_source(
        sess, url, checksum=f"sha256:{uuid.uuid4().hex}", language=scope, **fields
    )
    await replace_chunks(sess, source.source_id, [ChunkWrite(text=own_words(text), chunk_index=0)])
    chunk = await sess.scalar(select(Chunk).where(Chunk.source_id == source.source_id))
    await store_embeddings(sess, {chunk.chunk_id: at(0.95, axis)})
    await sess.flush()
    return source


def only(scope: str, **kwargs) -> SearchFilters:
    return SearchFilters(languages=[scope], **kwargs)


async def both_arms(sess, term: str, filters: SearchFilters) -> tuple[set[str], set[str]]:
    lexical = await search(sess, term, filters=filters)
    vector = await search(sess, "", query_vector=QUERY, filters=filters)
    return {h.url for h in lexical.hits}, {h.url for h in vector.hits}


# --------------------------------------------------------------------------
# Kind
# --------------------------------------------------------------------------


async def test_a_kind_filter_narrows_both_arms(sess, scope, term, run) -> None:
    news = await a_source(sess, scope, f"https://n{run}.test/a", f"{term} a", 3, doc_kind="news")
    release = await a_source(
        sess, scope, f"https://r{run}.test/a", f"{term} b", 4, doc_kind="press_release"
    )
    await a_source(sess, scope, f"https://u{run}.test/a", f"{term} c", 5)  # never classified

    unfiltered = await both_arms(sess, term, only(scope))
    assert all(len(arm) == 3 for arm in unfiltered), "the fixture did not reach both arms"

    lexical, vector = await both_arms(sess, term, only(scope, doc_kinds=["press_release"]))
    assert lexical == vector == {release.url}

    lexical, vector = await both_arms(sess, term, only(scope, doc_kinds=["news", "press_release"]))
    assert lexical == vector == {news.url, release.url}, "an unclassified source matched a kind"


# --------------------------------------------------------------------------
# Site
# --------------------------------------------------------------------------


async def test_a_site_keeps_its_own_host_and_subdomains_only(sess, scope, term, run) -> None:
    site = f"outlet{run}.test"
    own = await a_source(sess, scope, f"https://www.{site}/news/a", f"{term} a", 3)
    sub = await a_source(sess, scope, f"https://today.{site}/b", f"{term} b", 4)
    # A host that merely ends with the same letters is another site.
    await a_source(sess, scope, f"https://not{site}/c", f"{term} c", 5)
    await a_source(sess, scope, f"https://other{run}.test/d", f"{term} d", 6)

    lexical, vector = await both_arms(sess, term, only(scope, sites=[site]))
    assert lexical == vector == {own.url, sub.url}

    # What a reader pastes reads as the same site.
    pasted = await both_arms(sess, term, only(scope, sites=[f"https://WWW.{site}/x?y=1"]))
    assert pasted == (lexical, vector)


async def test_a_subdomain_does_not_widen_to_its_parent(sess, scope, term, run) -> None:
    site = f"outlet{run}.test"
    await a_source(sess, scope, f"https://{site}/a", f"{term} a", 3)
    sub = await a_source(sess, scope, f"https://today.{site}/b", f"{term} b", 4)

    lexical, vector = await both_arms(sess, term, only(scope, sites=[f"today.{site}"]))
    assert lexical == vector == {sub.url}


async def test_a_wildcard_character_in_a_site_is_matched_literally(sess, scope, term, run) -> None:
    """`_` is a LIKE wildcard; a site containing one must not match a host with any letter there."""
    await a_source(sess, scope, f"https://x.ab{run}.test/a", f"{term} a", 3)

    lexical, vector = await both_arms(sess, term, only(scope, sites=[f"a_{run}.test"]))
    assert lexical == vector == set()


async def test_sites_with_no_host_match_nothing_rather_than_everything(sess, scope, term, run):
    await a_source(sess, scope, f"https://a{run}.test/a", f"{term} a", 3)

    lexical, vector = await both_arms(sess, term, only(scope, sites=["not a host", ""]))
    assert lexical == vector == set()


# --------------------------------------------------------------------------
# What the site filter offers
# --------------------------------------------------------------------------


async def test_site_counts_rank_by_sources_and_leave_out_junk_and_copies(sess, scope, run):
    big, small = f"big{run}.test", f"small{run}.test"
    for i in range(3):
        await a_source(sess, scope, f"https://{big}/{i}", "x", 3, doc_kind="news")
    first = await a_source(sess, scope, f"https://www.{small}/a", "x", 3, doc_kind="news")
    await a_source(sess, scope, f"https://{small}/junk", "x", 3, retention_tier="junk")
    copy = await a_source(sess, scope, f"https://{small}/copy", "x", 3, doc_kind="news")
    copy.duplicate_of = first.source_id
    copy.duplicate_reason = "exact"
    await sess.flush()

    counts = await site_counts(sess, contains=run)

    assert [(s.site, s.sources) for s in counts.sites] == [(big, 3), (small, 1)]


async def test_site_counts_narrow_by_kind_and_by_part_of_a_name(sess, scope, run) -> None:
    await a_source(sess, scope, f"https://wire{run}.test/a", "x", 3, doc_kind="press_release")
    await a_source(sess, scope, f"https://paper{run}.test/a", "x", 3, doc_kind="news")

    releases = await site_counts(sess, doc_kinds=["press_release"], contains=run)
    named = await site_counts(sess, contains=f"PAPER{run}")

    assert [s.site for s in releases.sites] == [f"wire{run}.test"]
    assert [s.site for s in named.sites] == [f"paper{run}.test"]


async def test_a_percent_sign_in_the_search_is_not_a_wildcard(sess, scope, run) -> None:
    await a_source(sess, scope, f"https://a{run}.test/a", "x", 3)

    assert (await site_counts(sess, contains=f"%{run}")).sites == []


# --------------------------------------------------------------------------
# The routes
# --------------------------------------------------------------------------


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed(session_for, scope, term, run):
    sess = await session_for("rw")
    made = [
        await a_source(
            sess, scope, f"https://www.desk{run}.test/a", f"{term} a", 3, doc_kind="news"
        ),
        await a_source(
            sess, scope, f"https://desk{run}.test/b", f"{term} b", 4, doc_kind="opinion"
        ),
        await a_source(
            sess, scope, f"https://agency{run}.test/c", f"{term} c", 5, doc_kind="press_release"
        ),
    ]
    await sess.commit()
    yield made
    await sess.execute(delete(Source).where(Source.language == scope))
    await sess.commit()


async def test_search_and_answer_apply_both_filters(client, committed, scope, term, run) -> None:
    params = {"q": term, "language": scope, "site": f"desk{run}.test", "doc_kind": "opinion"}

    found = await client.get("/api/explore/search", params=params)
    answered = await client.get("/api/explore/answer", params=params)

    assert found.status_code == 200, found.text
    assert [h["url"] for h in found.json()["hits"]] == [committed[1].url]
    assert answered.status_code == 200, answered.text


async def test_the_routes_refuse_a_site_or_kind_they_cannot_apply(client, scope, term) -> None:
    hostless = await client.get("/api/explore/search", params={"q": term, "site": "not a host"})
    unknown = await client.get("/api/explore/search", params={"q": term, "doc_kind": "webpage"})
    answer = await client.get("/api/explore/answer", params={"q": term, "site": "nohost"})

    assert hostless.status_code == 422 and "not a site" in hostless.text
    assert unknown.status_code == 422
    assert answer.status_code == 422


async def test_the_sites_route_lists_by_count(client, committed, run) -> None:
    response = await client.get("/api/explore/sites", params={"q": run})
    kinds = await client.get("/api/explore/sites", params={"q": run, "doc_kind": "press_release"})

    assert response.status_code == 200, response.text
    assert [(s["site"], s["sources"]) for s in response.json()["sites"]] == [
        (f"desk{run}.test", 2),
        (f"agency{run}.test", 1),
    ]
    assert [s["site"] for s in kinds.json()["sites"]] == [f"agency{run}.test"]
    refused = await client.get("/api/explore/sites", params={"doc_kind": "webpage"})
    assert refused.status_code == 422


# --------------------------------------------------------------------------
# Saved views
# --------------------------------------------------------------------------


@pytest.fixture
async def views(session_for, monkeypatch, run):
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")
    yield f"view-{run}"
    sess = await session_for("rw")
    await sess.execute(delete(SavedView).where(SavedView.name.like(f"view-{run}%")))
    await sess.commit()


async def test_a_view_keeps_kinds_and_sites(client, views) -> None:
    filters = {"doc_kinds": ["press_release"], "sites": ["example.org"]}
    saved = await client.post("/api/admin/views", json={"name": views, "filters": filters})

    assert saved.status_code in (200, 201), saved.text
    assert saved.json()["filters"] == filters


@pytest.mark.parametrize(
    "filters",
    [
        {"doc_kinds": ["webpage"]},
        {"doc_kinds": "news"},
        {"sites": ["not a host"]},
        {"sites": "example.org"},
    ],
)
async def test_a_view_refuses_a_kind_or_site_the_search_cannot_apply(
    client, views, filters
) -> None:
    refused = await client.post(
        "/api/admin/views", json={"name": f"{views}-bad", "filters": filters}
    )
    assert refused.status_code == 422, refused.text
