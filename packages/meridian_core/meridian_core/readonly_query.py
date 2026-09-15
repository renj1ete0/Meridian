"""§12.4's escape hatch (task P3-04).

> "An escape hatch alongside the curated tools: `run_readonly_query(query,
> limit)`, enforced by a read-only database role with a statement timeout and
> row cap. **Watch which queries the agent writes there — those are the next
> curated tools.**"

That last sentence is why this exists at all. A curated tool surface can only
answer questions somebody anticipated, and the queries an agent reaches for when
it cannot find a tool are the most direct evidence there is about which tools to
build. So every query is logged, whether it succeeds or not.

**The enforcement is the role, not this module.** `meridian_guest` (`P3-07`) has
SELECT on the corpus and the graph and nothing else — no `agent_tokens`, whose
`token_hash` is the one secret in the schema, and no `fetch_policy`. Arbitrary
SQL cannot talk its way past a privilege it does not hold, and every check here
is a second line rather than the first.

**What the second line is for.** The role stops a query reading what it must
not. It does nothing about a query that reads what it may, slowly, forever: a
cartesian join across the corpus is a perfectly legal SELECT. The statement
timeout is what makes this safe to expose, and it is set per transaction rather
than per deployment so that a badly shaped query costs seconds rather than the
crawl's afternoon.

**Refusing before running is a courtesy, not a defence.** The textual checks
below reject the obvious — a write verb, several statements — because a clear
refusal is more useful to a caller than a permission error from Postgres. They
are not what makes this safe, and anything they miss is caught by the role.
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

    # One statement. Semicolons inside string literals would make this a parser
    # rather than a check, so the rule is simply "no semicolon except a trailing
    # one" — blunt, and it refuses a legal query far less often than it refuses
    # a stacked one.
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

    ``sess`` must already be a guest session (`db.session_guest`). It is taken
    rather than created so the caller owns the transaction — and so a test
    cannot accidentally get a privileged one, which would make every assertion
    here meaningless.
    """
    statement = _check(query)
    capped = max(1, min(limit, HARD_MAX_ROWS))

    started = time.perf_counter()
    try:
        # `DBAPIError`, not `DatabaseError`: a statement timeout surfaces as the
        # former, which is its *parent*, so the narrower catch let exactly the
        # failure this design creates on purpose escape as a stack trace.
        #
        # `SET LOCAL`, so the timeout lasts exactly as long as this transaction
        # and cannot leak onto a pooled connection's next borrower.
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

    Postgres's own message is usually the most useful thing available and is
    passed through, but the two that need translating are the two this design
    produces on purpose — a timeout and a missing privilege — because neither
    reads as "you asked for something you may not have".
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
