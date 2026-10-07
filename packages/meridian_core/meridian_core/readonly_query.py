"""§12.4's escape hatch (task P3-04): one bounded, read-only SELECT, logged.

The guest role is the enforcement; the textual checks here only make refusals clearer.
The statement timeout, set per transaction, is what makes it safe to expose. Every query
is logged, because they show which curated tools to build next.
See docs/features/mcp.md#read-only-sql-design.
"""

from __future__ import annotations

import dataclasses
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger

log = get_logger(__name__)

#: How long one query may run. Seconds, and deliberately short: this is an
#: interactive tool, and a question worth more than this is worth a curated tool.
DEFAULT_TIMEOUT_MS = 5_000

#: Rows returned. A cap rather than a page, because an agent that needs
#: thousands of rows is doing analysis that belongs in a tool, and returning
#: them costs the model's context rather than this process's memory.
DEFAULT_MAX_ROWS = 200
HARD_MAX_ROWS = 1_000

#: Statements that are not a question. Checked so the refusal is legible; the
#: role is what makes them impossible.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|reindex|call|do|set|reset|listen|notify|lock)\b",
    re.IGNORECASE,
)


class QueryRefused(ValueError):
    """The query was not run. The message is written to be shown to the caller."""


@dataclasses.dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    duration_ms: float


def _check(query: str) -> str:
    stripped = query.strip().rstrip(";").strip()
    if not stripped:
        raise QueryRefused("Empty query.")

    # One statement: no semicolon except a trailing one. Blunt, but a parser for
    # literals would be more than a check.
    if ";" in stripped:
        raise QueryRefused("One statement at a time; remove the semicolon.")

    if not re.match(r"^\s*(select|with)\b", stripped, re.IGNORECASE):
        raise QueryRefused("Only SELECT (or WITH … SELECT) queries are allowed here.")

    if match := FORBIDDEN.search(stripped):
        raise QueryRefused(
            f"`{match.group(0).upper()}` is not allowed. This connection can only read, "
            "and only the corpus and the graph."
        )
    return stripped


async def run_readonly_query(
    sess: AsyncSession,
    query: str,
    *,
    limit: int = DEFAULT_MAX_ROWS,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    agent: str | None = None,
) -> QueryResult:
    """Run one SELECT on a guest session, bounded.

    ``sess`` must already be a guest session (`db.session_guest`); the caller owns
    the transaction.
    """
    statement = _check(query)
    capped = max(1, min(limit, HARD_MAX_ROWS))

    started = time.perf_counter()
    try:
        # `DBAPIError`, not `DatabaseError`: a statement timeout surfaces as the parent.
        # `SET LOCAL`, so the timeout cannot leak onto a pooled connection.
        await sess.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        # One row over the cap, so "there were more" is a fact rather than an
        # inference from having returned exactly the limit.
        result = await sess.execute(text(statement).execution_options(max_row_buffer=capped + 1))
        fetched = result.fetchmany(capped + 1)
        columns = list(result.keys())
    except DBAPIError as exc:
        duration = (time.perf_counter() - started) * 1000
        # Logged as the evidence §12.4 asks for even when it failed — a query
        # that times out or hits a missing privilege says as much about which
        # tool is missing as one that worked.
        log.info(
            "readonly query refused by the database",
            extra={
                "agent": agent,
                "query": statement[:500],
                "duration_ms": round(duration, 1),
                "error": type(exc.orig).__name__ if exc.orig else type(exc).__name__,
            },
        )
        raise QueryRefused(_explain(exc)) from exc

    truncated = len(fetched) > capped
    rows = [dict(zip(columns, row, strict=False)) for row in fetched[:capped]]
    duration = (time.perf_counter() - started) * 1000

    log.info(
        "readonly query",
        extra={
            "agent": agent,
            "query": statement[:500],
            "rows": len(rows),
            "truncated": truncated,
            "duration_ms": round(duration, 1),
        },
    )
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=round(duration, 1),
    )


def _explain(exc: DBAPIError) -> str:
    """Turn a database error into something a model can act on.

    Postgres's message is passed through, except for a timeout and a missing
    privilege, which are translated.
    """
    raw = str(getattr(exc, "orig", exc))
    lowered = raw.lower()
    if "statement timeout" in lowered or "canceling statement" in lowered:
        return (
            "The query took too long and was cancelled. Narrow it — add a WHERE "
            "clause, or a LIMIT — rather than retrying it unchanged."
        )
    if "permission denied" in lowered:
        return (
            "This connection can read the corpus and the graph only. Operational "
            "tables are not available here."
        )
    return f"The database refused the query: {raw.splitlines()[0]}"
