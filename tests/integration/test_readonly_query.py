"""§12.4's escape hatch (task P3-04).

Against a real Postgres because the enforcement *is* Postgres: the guest role's
privileges and the statement timeout. Every textual check in
`readonly_query.py` is a courtesy that makes a refusal legible, and a test that
only exercised those would be testing the courtesy while the thing that keeps
this safe went unverified.

Skips when `PG_GUEST_URL` is unset, which is the honest state for a deployment
that shares nothing — the tool is not registered there either.
"""

from __future__ import annotations

import pytest

from meridian_core.db import dispose_engines, guest_configured, session_guest
from meridian_core.readonly_query import (
    DEFAULT_MAX_ROWS,
    HARD_MAX_ROWS,
    QueryRefused,
    run_readonly_query,
)

pytestmark = [
    pytest.mark.usefixtures("require_db"),
    pytest.mark.skipif(not guest_configured(), reason="no PG_GUEST_URL — see docs/setup.md"),
]


@pytest.fixture(autouse=True)
async def _dispose():
    yield
    await dispose_engines()


async def run(query: str, **kwargs):
    async with session_guest() as sess:
        return await run_readonly_query(sess, query, **kwargs)


# --------------------------------------------------------------------------
# What the role refuses — the defence that matters
# --------------------------------------------------------------------------


async def test_the_secret_table_is_unreachable() -> None:
    """`agent_tokens.token_hash` is the one secret this schema holds, and
    `meridian_ro` can read it — which is exactly why this runs as
    `meridian_guest` (`P3-07`) instead. Refused by a privilege, not by a regex.
    """
    with pytest.raises(QueryRefused, match="corpus and the graph"):
        await run("SELECT token_hash FROM agent_tokens")


@pytest.mark.parametrize("table", ["fetch_policy", "queue", "fetch_attempts", "agents"])
async def test_operational_tables_are_unreachable(table: str) -> None:
    """Not secrets, but not the corpus either: `queue` and `fetch_policy`
    describe what this crawler is about to look at and how it behaves, which is
    the operator's research direction rather than evidence."""
    with pytest.raises(QueryRefused):
        await run(f"SELECT * FROM {table} LIMIT 1")


async def test_a_write_is_refused_even_if_it_reaches_the_database() -> None:
    """The textual check catches this first, so this asserts the refusal
    happens — the role behind it is what makes the check non-load-bearing."""
    with pytest.raises(QueryRefused):
        await run("UPDATE sources SET title = 'x'")


# --------------------------------------------------------------------------
# What it allows
# --------------------------------------------------------------------------


async def test_a_real_question_gets_a_real_answer() -> None:
    result = await run("SELECT source_tier, count(*) AS n FROM sources GROUP BY 1 ORDER BY 2 DESC")

    assert result.columns == ["source_tier", "n"]
    assert result.row_count >= 1
    assert all(isinstance(row, dict) for row in result.rows)


async def test_a_cte_is_allowed() -> None:
    """`WITH … SELECT` is how a non-trivial question gets asked, and refusing it
    would push every real query into an unreadable subselect."""
    result = await run("WITH t AS (SELECT 1 AS a) SELECT a FROM t")

    assert result.rows == [{"a": 1}]


async def test_it_can_join_the_corpus_to_the_graph() -> None:
    """The reason the guest role covers both. A question worth the escape hatch
    usually spans them."""
    result = await run(
        "SELECT count(*) AS n FROM chunks c JOIN sources s ON s.source_id = c.source_id"
    )

    assert result.row_count == 1


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------


async def test_a_long_query_is_cancelled_rather_than_allowed_to_run() -> None:
    """The role stops a query reading what it must not. It does nothing about a
    query that reads what it may, forever — a cartesian join is a legal SELECT.
    The timeout is what makes this safe to expose at all.
    """
    with pytest.raises(QueryRefused, match="too long"):
        await run("SELECT pg_sleep(10)", timeout_ms=300)


async def test_the_row_cap_truncates_and_says_so() -> None:
    """Silently returning exactly the limit is indistinguishable from "that was
    all of it", and an agent would report a partial count as a finding."""
    result = await run("SELECT generate_series(1, 500) AS n", limit=10)

    assert result.row_count == 10
    assert result.truncated is True


async def test_a_result_that_fits_is_not_marked_truncated() -> None:
    """The converse. A `truncated` flag that is always true says nothing."""
    result = await run("SELECT generate_series(1, 3) AS n", limit=10)

    assert result.row_count == 3
    assert result.truncated is False


async def test_the_caller_cannot_raise_the_cap_past_the_hard_limit() -> None:
    result = await run(f"SELECT generate_series(1, {HARD_MAX_ROWS + 50}) AS n", limit=10**6)

    assert result.row_count == HARD_MAX_ROWS


async def test_the_timeout_does_not_leak_onto_the_next_query() -> None:
    """`SET LOCAL`, so it dies with the transaction. A session-level setting
    would ride a pooled connection to whoever borrowed it next."""
    with pytest.raises(QueryRefused):
        await run("SELECT pg_sleep(10)", timeout_ms=200)

    result = await run("SELECT pg_sleep(0.4) AS slept", timeout_ms=DEFAULT_MAX_ROWS * 50)
    assert result.row_count == 1


# --------------------------------------------------------------------------
# Refusals that are legible
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO sources (url) VALUES ('x')",
        "DROP TABLE sources",
        "GRANT ALL ON sources TO meridian_guest",
        "COPY sources TO '/tmp/x'",
        "SET statement_timeout = 0",
    ],
)
async def test_statements_that_are_not_questions_are_refused(query: str) -> None:
    with pytest.raises(QueryRefused):
        await run(query)


async def test_stacked_statements_are_refused() -> None:
    """A second statement is where a read-only query stops being one. The role
    would refuse the write anyway; refusing here makes the reason legible."""
    with pytest.raises(QueryRefused, match="One statement"):
        await run("SELECT 1; DROP TABLE sources")


async def test_a_trailing_semicolon_is_fine() -> None:
    """People and models both write them, and refusing would be pedantry that
    costs a turn to discover."""
    assert (await run("SELECT 1 AS a;")).rows == [{"a": 1}]


async def test_an_empty_query_is_refused() -> None:
    with pytest.raises(QueryRefused, match="Empty"):
        await run("   ")


async def test_a_syntax_error_comes_back_as_a_sentence() -> None:
    """A model handed a stack trace pastes it at the reader. Handed a sentence,
    it fixes the query."""
    with pytest.raises(QueryRefused, match="refused the query"):
        await run("SELECT FROM WHERE")
