"""The lexical index on chunks (task P2-05, spec §12.5).

Against a real Postgres because every claim here is a claim about the database
and none of it is visible from Python. The column is computed by Postgres, the
stemming comes from a Postgres text-search configuration, and whether the GIN
index is *usable* for the query shape is a planner question.

`alembic check` cannot stand in for any of it. It warns
"Computed default on chunks.search_vector cannot be modified" and moves on —
so the one thing autogenerate normally guards, model-versus-database drift, is
exactly what it does not guard here. That is what `test_generation_expression_*`
is for.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def url() -> str:
    return f"https://t{uuid.uuid4().hex[:12]}.test/a"


@pytest.fixture
async def cleanup(session_for, url):
    yield
    sess = await session_for("rw")
    await sess.execute(text("DELETE FROM sources WHERE url = :u"), {"u": url})
    await sess.commit()


async def a_source(sess, url: str) -> Source:
    source, _ = await upsert_source(sess, url, checksum="sha256:one")
    return source


# --------------------------------------------------------------------------
# The column is generated, and generated from what the model says
# --------------------------------------------------------------------------


async def test_search_vector_is_generated_by_the_database(session_for) -> None:
    """A plain column here would be created empty and stay empty.

    The failure is silent in the worst way: every chunk is present, embedded and
    novelty-judged, and the lexical half of every search returns nothing — which
    looks exactly like a corpus that does not contain the term.
    """
    sess = await session_for("rw")
    attgenerated = await sess.scalar(
        text(
            # `attgenerated` is Postgres's internal "char", which the driver
            # hands back as bytes; cast so the comparison is about the value.
            "SELECT attgenerated::text FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'search_vector'"
        )
    )
    assert attgenerated == "s", (
        "chunks.search_vector is not a STORED generated column — it will never populate"
    )


def _normalised(expression: str) -> str:
    """Strip what Postgres adds when it stores an expression back.

    The parser resolves `'english'` to `'english'::regconfig` and may add
    parentheses and whitespace. Comparing raw strings would fail on a formatting
    difference and pass on nothing extra, so normalise both sides rather than
    hardcoding either.
    """
    expression = re.sub(r"::\s*\w+", "", expression)
    expression = expression.replace("(", " ( ").replace(")", " ) ")
    return " ".join(expression.split()).lower()


async def test_generation_expression_matches_the_model(session_for) -> None:
    """Drift: the model's `Computed` against the database's own record of it.

    Alembic explicitly declines to compare computed defaults, so a model changed
    to a different text-search configuration without a migration would otherwise
    pass `alembic check` while the corpus stayed indexed under the old one.
    """
    sess = await session_for("rw")
    stored = await sess.scalar(
        text(
            "SELECT generation_expression FROM information_schema.columns "
            "WHERE table_name = 'chunks' AND column_name = 'search_vector'"
        )
    )
    declared = str(Chunk.__table__.c.search_vector.computed.sqltext)

    assert _normalised(stored) == _normalised(declared), (
        f"database generates {stored!r}, model declares {declared!r}"
    )


async def test_the_application_cannot_write_the_search_vector(session_for, url, cleanup) -> None:
    """Rejection: a write path that supplies its own vector must be refused.

    That the column is derived is only true while nothing can override it. A
    hand-written INSERT that set it would produce a chunk whose index disagrees
    with its text, and nothing downstream compares the two.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await sess.flush()

    with pytest.raises(DatabaseError):
        await sess.execute(
            text(
                "INSERT INTO chunks (source_id, text, chunk_index, search_vector) "
                "VALUES (:sid, 'some text', 0, to_tsvector('english', 'different text'))"
            ),
            {"sid": source.source_id},
        )
    await sess.rollback()


# --------------------------------------------------------------------------
# It tracks the text, including after the text changes
# --------------------------------------------------------------------------


async def test_the_vector_follows_an_update_to_the_text(session_for, url, cleanup) -> None:
    """The bug a trigger has and a generated column does not.

    A re-crawl rewrites a page's chunks. A trigger wired to INSERT and not
    UPDATE — or bypassed by a bulk UPDATE — leaves the old text findable and the
    new text unfindable, and the row itself looks correct from every angle.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text="the harbour rebuilding programme", chunk_index=0)]
    )
    await sess.flush()

    async def matches(term: str) -> int:
        return await sess.scalar(
            text(
                "SELECT count(*) FROM chunks WHERE source_id = :sid "
                "AND search_vector @@ websearch_to_tsquery('english', :q)"
            ),
            {"sid": source.source_id, "q": term},
        )

    assert await matches("harbour") == 1

    await sess.execute(
        text("UPDATE chunks SET text = 'the quarry access road' WHERE source_id = :sid"),
        {"sid": source.source_id},
    )
    await sess.flush()

    assert await matches("harbour") == 0, "the old text is still findable after a rewrite"
    assert await matches("quarry") == 1, "the new text never became findable"


async def test_the_configuration_is_english_not_simple(session_for, url, cleanup) -> None:
    """Stemming is the observable difference between `english` and `simple`.

    A migration that quietly used `simple` would index and search without error,
    and would simply miss every inflected form — which reads as a thin corpus
    rather than as a misconfigured index.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text="several pedestrian crossings", chunk_index=0)]
    )
    await sess.flush()

    found = await sess.scalar(
        text(
            "SELECT count(*) FROM chunks WHERE source_id = :sid "
            "AND search_vector @@ websearch_to_tsquery('english', 'crossing')"
        ),
        {"sid": source.source_id},
    )
    assert found == 1, "a singular query did not match a plural in the text — stemming is off"


# --------------------------------------------------------------------------
# The index exists and the planner can actually use it
# --------------------------------------------------------------------------


async def test_a_gin_index_covers_the_search_vector(session_for) -> None:
    """Completeness: the column landing without its index is a silent regression.

    Everything still returns the right rows, by sequential scan, until the
    corpus is large enough for that to matter — by which point the cause is
    several months behind.
    """
    sess = await session_for("rw")
    method = await sess.scalar(
        text(
            "SELECT am.amname FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = c.relam "
            "WHERE i.indrelid = 'chunks'::regclass AND c.relname = 'ix_chunks_search_vector'"
        )
    )
    assert method == "gin", f"expected a GIN index on chunks.search_vector, found {method!r}"


async def test_the_planner_uses_the_index_for_a_match(session_for) -> None:
    """The opclass has to suit the operator, not just exist.

    `enable_seqscan = off` removes the only reason a planner would decline on a
    near-empty dev table, so what is left is whether the index is *eligible* for
    `@@` at all. A btree on the same column would pass every other test here and
    fail this one.
    """
    sess = await session_for("rw")
    await sess.execute(text("SET LOCAL enable_seqscan = off"))
    plan = await sess.execute(
        text(
            "EXPLAIN SELECT chunk_id FROM chunks "
            "WHERE search_vector @@ websearch_to_tsquery('english', 'harbour')"
        )
    )
    rendered = "\n".join(row[0] for row in plan)
    await sess.rollback()

    assert "ix_chunks_search_vector" in rendered, f"planner ignored the index:\n{rendered}"
