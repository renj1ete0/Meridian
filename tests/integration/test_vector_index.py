"""The passage vector index is half precision, and queries use it (task `B-136`, ADR 0007).

Two halves, because either alone passes while the other is broken: Postgres must plan the
helper's expression through the index, and no passage query may order by anything else.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import select, text

from meridian_core.models import Chunk
from meridian_core.vectorindex import HALF, indexed_distance

pytestmark = pytest.mark.usefixtures("require_db")

CORE = pathlib.Path(__file__).resolve().parents[2] / "packages/meridian_core/meridian_core"
INDEX = "ix_chunks_embedding_hnsw_half"


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def test_the_index_exists_and_the_full_precision_one_is_gone(sess) -> None:
    names = set(
        await sess.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'"))
    )
    assert INDEX in names
    assert "ix_chunks_embedding_hnsw" not in names, "two indexes would double the writes"


async def test_ordering_by_the_helper_is_planned_through_the_index(sess) -> None:
    from sqlalchemy.dialects import postgresql

    await sess.execute(text("SET LOCAL enable_seqscan = off"))
    probe = select(Chunk.embedding).where(Chunk.embedding.is_not(None)).limit(1)
    stmt = (
        select(Chunk.chunk_id)
        .where(Chunk.embedding.is_not(None))
        .order_by(indexed_distance(Chunk.embedding, probe.scalar_subquery()))
        .limit(10)
    )
    sql = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    plan = "\n".join(await sess.scalars(text(f"EXPLAIN {sql}")))
    assert INDEX in plan, plan


async def test_the_index_is_built_on_the_helpers_type(sess) -> None:
    definition = await sess.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": INDEX}
    )
    assert f"halfvec({HALF.dim})" in definition
    assert "halfvec_cosine_ops" in definition


def test_no_passage_query_orders_by_the_plain_column() -> None:
    """A query on the plain column cannot use the index and becomes a full scan, silently."""
    pattern = re.compile(r"\b(Chunk|candidate|subject)\.embedding\.cosine_distance\(")
    allowed = {"bridges.py"}  # `exact_distance` avoids the index on purpose (`+ 0`).
    offenders = [
        f"{path.name}:{n}"
        for path in CORE.rglob("*.py")
        if path.name not in allowed
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"use vectorindex.indexed_distance: {offenders}"
