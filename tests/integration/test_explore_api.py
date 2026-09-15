"""`/api/explore/*` against a real Postgres (task P2-07, spec §12.5, §12.6).

Through a real ASGI transport and a real database, because the two claims worth
checking are both about things no double can tell you: that the role the routes
read through cannot write, and that a filter named in a query string actually
reaches the SQL rather than being accepted and dropped.

**Fixtures commit.** Unlike most of this suite, these tests cannot use a session
that rolls back: the app opens its *own* connection through
`meridian_core.db.session_ro`, so anything this file writes and does not commit
is invisible to the thing under test. Every fixture therefore commits and
deletes itself afterwards by a per-run marker.

Scoping matters for the same reason it does in `test_search.py` — the dev
database holds a real crawl, and an unscoped assertion about result counts would
pass or fail depending on what was last crawled.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select, text

from api.deps import read_session
from api.main import create_app
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    """Scopes this run's rows. Doubles as the lexical term, so a search for it
    cannot match anything the crawler collected."""
    return f"qxz{uuid.uuid4().hex[:10]}"


@pytest.fixture
def scope(marker: str) -> str:
    """A language code unique to this run, used as the filter that keeps
    assertions about counts away from the dev corpus."""
    return f"zz-{marker[:8]}"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """The app over an in-process ASGI transport.

    No network, no uvicorn, and no `TestClient` — the latter runs its own event
    loop in a worker thread, which does not compose with the async fixtures
    these tests need to set up data.
    """
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    # The app's engines are module-global in `meridian_core.db`, so leaving them
    # open leaks connections across the session and eventually exhausts
    # Postgres's 40.
    await dispose_engines()


@pytest.fixture
async def corpus(session_for, marker: str, scope: str):
    """A small committed corpus this file owns, across two tiers.

    Committed because the app reads on another connection; deleted afterwards
    because the dev database is a real corpus somebody else is using.
    """
    sess = await session_for("rw")
    made = []
    for index, tier in enumerate(["government", "government", "press"]):
        source, _ = await upsert_source(
            sess,
            f"https://{marker}.test/doc-{index}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier=tier,
            language=scope,
            title=f"Document {index}",
        )
        await replace_chunks(
            sess,
            source.source_id,
            [
                ChunkWrite(text=f"{marker} paragraph {n} about the subject.", chunk_index=n)
                for n in range(3)
            ],
        )
        made.append(source)
    await sess.commit()

    yield made

    await sess.execute(delete(Source).where(Source.url.like(f"https://{marker}.test/%")))
    await sess.commit()


def params(marker: str, scope: str, **extra) -> dict:
    """A search scoped to this run's fixtures."""
    return {"q": marker, "language": scope, **extra}


# --------------------------------------------------------------------------
# The read-only guarantee (AGENTS.md, scaffold §4, spec §12.4)
# --------------------------------------------------------------------------


async def test_the_explore_session_cannot_write(require_db) -> None:
    """Refused by Postgres, not by application code.

    The invariant the whole surface rests on: §12.6 makes the route prefix the
    role boundary so that auth is later a middleware check rather than a
    refactor, and that is only safe if the role behind the prefix cannot write
    whatever a handler does. AGENTS.md: "enforcing read-only at the database,
    not in application code, is what makes the read-only escape hatch safe."

    This catches the *transaction* half — `session_ro` issues
    `SET TRANSACTION READ ONLY`, which turns a mistake into an error at the
    statement rather than a surprise at commit. The role half is the test below,
    and they are separate on purpose.
    """
    agen = read_session()
    sess = await anext(agen)
    try:
        with pytest.raises(Exception) as caught:
            await sess.execute(
                text("INSERT INTO sources (url) VALUES ('https://write.test/refused')")
            )
        # Naming the error distinguishes the database refusing from SQLAlchemy
        # declining to build the statement. Only the first is the claim.
        assert "ReadOnlySQLTransaction" in str(caught.value) or "permission" in str(caught.value)
    finally:
        await agen.aclose()
        await dispose_engines()


async def test_the_read_role_lacks_write_privilege_independently(session_for) -> None:
    """The belt, checked without the braces.

    `SET TRANSACTION READ ONLY` is one line in `session_ro` and could be removed
    by someone who reasonably concluded the role already covers it. It does —
    but nothing proves that while the transaction setting is masking it, and a
    test that only ever saw `ReadOnlySQLTransactionError` would keep passing if
    the grants were widened.

    So this asks the catalogue directly: `meridian_ro` must hold SELECT and not
    INSERT, UPDATE or DELETE on the corpus tables.
    """
    sess = await session_for("owner")

    for table in ("sources", "chunks", "entities", "edges"):
        assert await sess.scalar(
            text("SELECT has_table_privilege('meridian_ro', :t, 'SELECT')"), {"t": table}
        ), f"meridian_ro cannot read {table} — explore would 500"

        for privilege in ("INSERT", "UPDATE", "DELETE"):
            granted = await sess.scalar(
                text("SELECT has_table_privilege('meridian_ro', :t, :p)"),
                {"t": table, "p": privilege},
            )
            assert not granted, f"meridian_ro holds {privilege} on {table}"


def test_no_explore_route_can_reach_a_writable_session() -> None:
    """Structural, and cheap. The routes module must not import the read-write
    session factory at all — the failure being prevented is a future handler
    reaching for one because it was already in scope, which no runtime test
    would catch until that handler existed.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "services/api/api/routes/explore.py"
    body = source.read_text()

    assert "session_rw" not in body
    # `session_ro` is fine; a bare `import session` is the read-write one.
    assert "from meridian_core.db import session" not in body


# --------------------------------------------------------------------------
# Filters reach the query
# --------------------------------------------------------------------------


async def test_a_tier_filter_actually_narrows(client, corpus, marker, scope) -> None:
    """The failure this guards is silent: a filter accepted at the boundary and
    dropped before the SQL returns *more* results, which reads as a generous
    search rather than as a broken one."""
    everything = await client.get("/api/explore/search", params=params(marker, scope, limit=50))
    government = await client.get(
        "/api/explore/search",
        params=params(marker, scope, limit=50, source_tier="government"),
    )

    assert everything.status_code == 200 and government.status_code == 200
    all_tiers = {h["source_tier"] for h in everything.json()["hits"]}
    assert all_tiers == {"government", "press"}, "the fixture did not cover two tiers"

    returned = government.json()["hits"]
    assert returned, "the filter excluded everything"
    assert {h["source_tier"] for h in returned} == {"government"}
    assert len(returned) < len(everything.json()["hits"])


async def test_an_unknown_tier_is_refused_rather_than_ignored(client, marker, scope) -> None:
    """422, not a silently unfiltered result set. The tier Literal comes from
    the database's own CHECK constraint, so a value rejected here is exactly one
    the database would reject too."""
    response = await client.get(
        "/api/explore/search", params=params(marker, scope, source_tier="excellent")
    )
    assert response.status_code == 422


async def test_duplicates_are_excluded_by_default_and_available_on_request(
    client, session_for, corpus, marker, scope
) -> None:
    """The novelty gate marks rather than deletes precisely so this is a
    read-time choice (§12.5) — a filtered near-duplicate and a never-crawled
    page are otherwise indistinguishable, and only one is worth investigating."""
    sess = await session_for("rw")
    chunk = (
        await sess.execute(
            select(Chunk)
            .where(Chunk.source_id == corpus[0].source_id)
            .order_by(Chunk.chunk_index)
            .limit(1)
        )
    ).scalar_one()
    other = (
        await sess.execute(select(Chunk).where(Chunk.source_id == corpus[1].source_id).limit(1))
    ).scalar_one()
    chunk.duplicate_of = other.chunk_id
    await sess.commit()

    default = await client.get("/api/explore/search", params=params(marker, scope, limit=50))
    widened = await client.get(
        "/api/explore/search", params=params(marker, scope, limit=50, include_duplicates=True)
    )

    hidden = {h["chunk_id"] for h in default.json()["hits"]}
    shown = {h["chunk_id"] for h in widened.json()["hits"]}
    assert chunk.chunk_id not in hidden
    assert chunk.chunk_id in shown
    marked = next(h for h in widened.json()["hits"] if h["chunk_id"] == chunk.chunk_id)
    assert marked["duplicate_of"] == other.chunk_id, "the reason it was hidden is not reported"


# --------------------------------------------------------------------------
# Paging does not lie
# --------------------------------------------------------------------------


async def test_pages_do_not_overlap_or_skip(client, corpus, marker, scope) -> None:
    """The failure worth catching is not an off-by-one in a count — it is a
    reader who walks three pages and never sees a chunk that was there."""
    first = await client.get("/api/explore/search", params=params(marker, scope, limit=4, offset=0))
    second = await client.get(
        "/api/explore/search", params=params(marker, scope, limit=4, offset=4)
    )
    whole = await client.get("/api/explore/search", params=params(marker, scope, limit=50))

    page_one = [h["chunk_id"] for h in first.json()["hits"]]
    page_two = [h["chunk_id"] for h in second.json()["hits"]]
    everything = [h["chunk_id"] for h in whole.json()["hits"]]

    assert len(page_one) == 4
    assert not set(page_one) & set(page_two), "a chunk appeared on two pages"
    assert page_one + page_two == everything[: len(page_one) + len(page_two)]


async def test_has_more_is_true_only_while_more_exists(client, corpus, marker, scope) -> None:
    """Computed by fetching one hit more than the page needs, rather than by a
    second counting query — a total computed a different way than the page
    would eventually disagree with the page."""
    whole = await client.get("/api/explore/search", params=params(marker, scope, limit=50))
    total = len(whole.json()["hits"])
    assert total >= 2, "the fixture is too small to page"

    short = await client.get(
        "/api/explore/search", params=params(marker, scope, limit=total - 1, offset=0)
    )
    exact = await client.get(
        "/api/explore/search", params=params(marker, scope, limit=total, offset=0)
    )

    assert short.json()["has_more"] is True
    assert exact.json()["has_more"] is False


async def test_paging_past_the_candidate_pool_is_refused(client, marker, scope) -> None:
    """Not an empty page. An empty page there is indistinguishable from the end
    of the results, and a client would stop paging believing it had seen
    everything the corpus holds."""
    response = await client.get(
        "/api/explore/search", params=params(marker, scope, limit=20, offset=95, candidates=100)
    )

    assert response.status_code == 422
    assert "candidate pool" in response.json()["detail"]


async def test_an_oversized_limit_is_refused(client, marker, scope) -> None:
    """A chunk runs to 2000 characters, so an uncapped limit is a request that
    can ask for megabytes of text nobody renders."""
    response = await client.get("/api/explore/search", params=params(marker, scope, limit=5000))
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Honest about what it did
# --------------------------------------------------------------------------


async def test_an_empty_query_returns_nothing_rather_than_erroring(client) -> None:
    """An empty search box is a state a UI has on first render. Making the
    client special-case its own initial paint to avoid a 422 is a worse boundary
    than answering honestly: nothing was asked, nothing was found."""
    response = await client.get("/api/explore/search", params={"q": ""})

    assert response.status_code == 200
    body = response.json()
    assert body["hits"] == []
    assert body["has_more"] is False
    assert body["degraded_reason"], "an empty result with no explanation is a dead end"


async def test_a_lexical_only_search_says_so(client, corpus, marker, scope) -> None:
    """This deployment has no embedder. §12.5 asks for hybrid search, and a
    response that delivered half of one silently would make the corpus look
    thinner than it is."""
    body = (await client.get("/api/explore/search", params=params(marker, scope))).json()

    assert body["arms"] == ["lexical"]
    assert body["degraded"] is True
    assert "embedder" in body["degraded_reason"]
    assert body["vector_candidates"] == 0


async def test_every_hit_carries_its_citation(client, corpus, marker, scope) -> None:
    """§2 principle 3: nothing is assertable without a citation you can follow
    back to a file. A hit that returned text plus a foreign key would make the
    citation optional in practice."""
    hits = (await client.get("/api/explore/search", params=params(marker, scope))).json()["hits"]

    assert hits
    for hit in hits:
        assert hit["url"].startswith("https://")
        assert hit["source_tier"]
        assert hit["title"]
        assert hit["chunk_index"] is not None


# --------------------------------------------------------------------------
# Reading around a hit
# --------------------------------------------------------------------------


async def test_a_source_reads_back_with_its_acquisition_record(client, corpus) -> None:
    source_id = corpus[0].source_id
    body = (await client.get(f"/api/explore/sources/{source_id}")).json()

    assert body["source_id"] == source_id
    # `P1-44`: "no text" and "a scan nobody has OCR'd" are different answers.
    assert "extractor" in body
    assert "text_available" in body
    assert "ocr_tier" in body


async def test_a_sources_chunks_come_back_in_document_order(client, corpus) -> None:
    """Ordered by `chunk_index`, so an offset here means what an offset normally
    means — unlike `/search`, where the order is a fused rank."""
    source_id = corpus[0].source_id
    body = (await client.get(f"/api/explore/sources/{source_id}/chunks")).json()

    indexes = [c["chunk_index"] for c in body["chunks"]]
    assert indexes == sorted(indexes)
    assert body["has_more"] is False


async def test_source_chunk_paging_does_not_skip(client, corpus) -> None:
    source_id = corpus[0].source_id
    first = (
        await client.get(f"/api/explore/sources/{source_id}/chunks", params={"limit": 2})
    ).json()
    second = (
        await client.get(
            f"/api/explore/sources/{source_id}/chunks", params={"limit": 2, "offset": 2}
        )
    ).json()

    assert first["has_more"] is True
    assert [c["chunk_index"] for c in first["chunks"]] == [0, 1]
    assert [c["chunk_index"] for c in second["chunks"]] == [2]
    assert second["has_more"] is False


async def test_a_chunk_reads_back_with_the_gates_verdict(client, session_for, corpus) -> None:
    sess = await session_for("rw")
    chunk = (
        await sess.execute(select(Chunk).where(Chunk.source_id == corpus[0].source_id).limit(1))
    ).scalar_one()

    body = (await client.get(f"/api/explore/chunks/{chunk.chunk_id}")).json()

    assert body["chunk_id"] == chunk.chunk_id
    assert "duplicate_of" in body
    assert "nearest_similarity" in body
    assert "embedding" not in body, "a 1024-float vector has no business in a payload"


@pytest.mark.parametrize(
    "path", ["/api/explore/sources/999999999", "/api/explore/chunks/999999999"]
)
async def test_a_missing_row_is_a_404(client, path: str) -> None:
    assert (await client.get(path)).status_code == 404


async def test_a_source_with_no_chunks_is_empty_not_missing(client, session_for, marker) -> None:
    """§6.5: metadata-only is a valid resting state. A reader who followed a
    link here needs to see that the source is real and unextracted, which a 404
    would not tell them."""
    sess = await session_for("rw")
    source, _ = await upsert_source(sess, f"https://{marker}.test/bare", checksum="sha256:bare")
    await sess.commit()
    try:
        body = (await client.get(f"/api/explore/sources/{source.source_id}/chunks")).json()
        assert body["chunks"] == []
        assert body["has_more"] is False
    finally:
        await sess.execute(delete(Source).where(Source.source_id == source.source_id))
        await sess.commit()


# --------------------------------------------------------------------------
# Ops
# --------------------------------------------------------------------------


async def test_health_reports_the_database_separately_from_liveness(client) -> None:
    """They fail separately and the difference decides what to do: a process
    that is up and cannot reach Postgres is a credential or network problem, and
    restarting the container fixes neither."""
    body = (await client.get("/health")).json()

    assert body["status"] == "ok"
    assert body["database"] is True


async def test_health_says_nothing_about_the_corpus(client) -> None:
    """§12.5 puts the interesting detail on the health *line* in the logs. An
    endpoint reporting queue depth or crawl rates would describe the operator's
    research activity to anyone who can reach it."""
    body = (await client.get("/health")).json()

    assert set(body) == {"status", "database"}


async def test_every_response_carries_a_request_id(client) -> None:
    """The header a caller quotes when reporting a slow or failed request.

    It is the same id bound as the log record's `run_id`, which is what makes a
    complaint traceable to the records it produced — several concurrent requests
    log from the same modules, and without a correlation id their records
    interleave into something nobody can separate afterwards.
    """
    first = await client.get("/health")
    second = await client.get("/health")

    assert first.headers["x-request-id"]
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_stats_separates_collected_from_searchable(client, corpus) -> None:
    """§12.5 turns on a reader being able to tell "we never collected this" from
    "we collected it and filtered it". One number for both erases exactly the
    distinction that makes absence visible."""
    body = (await client.get("/api/explore/stats")).json()

    assert body["chunks"] >= body["searchable_chunks"]
    assert body["searchable_chunks"] == body["chunks"] - body["duplicate_chunks"]
    assert body["sources"] >= len(corpus)
