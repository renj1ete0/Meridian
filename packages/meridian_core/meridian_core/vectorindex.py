"""The expression the passage vector index is built on (task `B-136`, ADR 0007).

The HNSW index on `chunks` is built over half-precision copies of the vectors, which keeps it
about a third of the full-precision size with recall within a point. A query uses the index
only when it orders by exactly the indexed expression, so every nearest-neighbour query on
passages goes through :func:`indexed_distance`. A query that orders by the plain column
silently becomes a full scan. Stored vectors stay full precision.
See docs/features/embedding.md#the-vector-index.
"""

from __future__ import annotations

from collections.abc import Sequence

from pgvector.sqlalchemy import HALFVEC, VECTOR
from sqlalchemy import ColumnElement, bindparam, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models.source import EMBEDDING_DIM

#: The indexed type; the migration and the model's index name it too.
HALF = HALFVEC(EMBEDDING_DIM)


def as_indexed(value: ColumnElement | Sequence[float]) -> ColumnElement:
    """A vector column (or ORM attribute) or a literal vector, cast to the indexed type."""
    if hasattr(value, "__clause_element__") or isinstance(value, ColumnElement):
        return cast(value, HALF)
    return cast(bindparam(None, [float(x) for x in value], type_=VECTOR(EMBEDDING_DIM)), HALF)


def indexed_distance(
    column: ColumnElement, other: ColumnElement | Sequence[float]
) -> ColumnElement[float]:
    """Cosine distance in the form the passage index serves."""
    return as_indexed(column).cosine_distance(as_indexed(other))


async def scan_past_filtered(sess: AsyncSession) -> None:
    """Keep an index scan going until enough rows pass the query's filters (`B-151`).

    For the rest of the transaction. Without it the HNSW index offers its nearest candidates,
    the filters run afterwards, and a query whose nearest rows are all filtered out finds
    nothing. Strict order, so a ``LIMIT 1`` is still the nearest. See
    docs/features/embedding.md#filtered-scans.
    """
    await sess.execute(select(func.set_config("hnsw.iterative_scan", "strict_order", True)))
