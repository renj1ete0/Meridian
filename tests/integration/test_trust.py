"""Quarantine and screening for unknown domains (task `P4-14`, §2.5, §11.8).

`P1-23` built the pre-screen and blocked nothing, on purpose: a screen that
quarantines before anyone has measured its false-positive rate quarantines the
corpus. This is the half that acts on it, and the tests are arranged around the
two ways that goes wrong — admitting what should be held back, and holding back
what should be admitted.

Against a real Postgres because the verdict is a row read under `FOR UPDATE`
and the filter is SQL, and because the thing most worth asserting is a
*negative*: that a quarantined page is absent from what a model can reach while
still being present in the corpus.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest
from sqlalchemy import delete, select

from meridian_core.models import Chunk, FetchPolicy, Source
from meridian_core.search import SearchFilters, search
from meridian_core.tiering import is_tier_mapped
from meridian_core.trust import (
    CLEAN_FETCHES_TO_CLEAR,
    DECIDED_BY_CLEAN,
    DECIDED_BY_SCREEN,
    DECIDED_BY_TIER,
    page_state,
    record_screening,
)

pytestmark = pytest.mark.usefixtures("require_db")

REPO = pathlib.Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)
DOMAIN = "unknown-domain.test"


@pytest.fixture
async def clean(session_for):
    sess = await session_for("rw")
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain.like("%.test")))
    await sess.execute(delete(Source).where(Source.url.like("https://%.test/%")))
    await sess.flush()
    return sess


async def a_source(sess, *, url: str, trust_state: str, text: str) -> Source:
    source = Source(
        url=url,
        source_tier="informal",
        retention_tier="background",
        trust_state=trust_state,
        language="en",
    )
    sess.add(source)
    await sess.flush()
    sess.add(Chunk(source_id=source.source_id, text=text, chunk_index=0))
    await sess.flush()
    return source


# --------------------------------------------------------------------------
# How a domain earns its verdict


async def test_an_unknown_domain_starts_unscreened(clean) -> None:
    """Not cleared. "Nothing has looked at this" is the honest starting state
    and the whole reason the filter admits `cleared` rather than excluding
    `quarantined`."""
    state = await record_screening(clean, DOMAIN, flagged=False, tier_mapped=False, now=NOW)

    assert state == "unscreened"


async def test_a_tier_mapped_domain_clears_on_sight(clean) -> None:
    """Being in the curated map is somebody's judgement already made — exactly
    the judgement this would otherwise be asking a model for."""
    state = await record_screening(clean, DOMAIN, flagged=False, tier_mapped=True, now=NOW)

    row = await clean.get(FetchPolicy, DOMAIN)
    assert state == "cleared"
    assert row.trust_decided_by == DECIDED_BY_TIER


async def test_an_unknown_domain_clears_after_enough_clean_fetches(clean) -> None:
    for _ in range(CLEAN_FETCHES_TO_CLEAR - 1):
        assert await record_screening(clean, DOMAIN, flagged=False, tier_mapped=False) == (
            "unscreened"
        )

    state = await record_screening(clean, DOMAIN, flagged=False, tier_mapped=False, now=NOW)

    row = await clean.get(FetchPolicy, DOMAIN)
    assert state == "cleared"
    assert row.trust_decided_by == DECIDED_BY_CLEAN


async def test_a_flag_quarantines_an_unknown_domain(clean) -> None:
    state = await record_screening(clean, DOMAIN, flagged=True, tier_mapped=False, now=NOW)

    row = await clean.get(FetchPolicy, DOMAIN)
    assert state == "quarantined"
    assert row.trust_decided_by == DECIDED_BY_SCREEN
    assert row.trust_reason


async def test_a_flag_resets_the_streak(clean) -> None:
    """The same shape as `consecutive_failures`: a domain that starts serving
    hostile pages must not keep a stale streak behind it."""
    for _ in range(CLEAN_FETCHES_TO_CLEAR - 1):
        await record_screening(clean, DOMAIN, flagged=False, tier_mapped=False)

    await record_screening(clean, DOMAIN, flagged=True, tier_mapped=False)
    row = await clean.get(FetchPolicy, DOMAIN)
    assert row.clean_fetches == 0

    # And one more clean fetch must not now tip it over the threshold.
    state = await record_screening(clean, DOMAIN, flagged=False, tier_mapped=False)
    assert state == "quarantined"


async def test_a_cleared_domain_stays_cleared_when_one_page_trips(clean) -> None:
    """Clearing a domain is a statement that its content is trusted.
    Re-quarantining individual pages afterwards would make the clearing
    meaningless and produce the drip of false positives `P1-23` avoided."""
    await record_screening(clean, DOMAIN, flagged=False, tier_mapped=True)

    state = await record_screening(clean, DOMAIN, flagged=True, tier_mapped=True)

    assert state == "cleared"


async def test_a_rejected_domain_is_not_un_rejected_by_clean_fetches(clean) -> None:
    """Rejection is somebody's decision. A crawl that overturned it by fetching
    five clean pages would be overruling them."""
    clean.add(FetchPolicy(domain=DOMAIN, trust_state="rejected"))
    await clean.flush()

    for _ in range(CLEAN_FETCHES_TO_CLEAR + 2):
        state = await record_screening(clean, DOMAIN, flagged=False, tier_mapped=True)

    assert state == "rejected"


# --------------------------------------------------------------------------
# What state a page is stored under


@pytest.mark.parametrize(
    "domain_state,flagged,expected",
    [
        ("unscreened", False, "unscreened"),
        ("unscreened", True, "quarantined"),
        ("quarantined", False, "quarantined"),
        ("cleared", True, "cleared"),
        ("rejected", False, "rejected"),
    ],
)
def test_the_page_state_follows_the_domain_except_when_it_must_not(
    domain_state: str, flagged: bool, expected: str
) -> None:
    """The one row that is not simply the domain's state: a flagged page on an
    unscreened domain is quarantined on its own account, because the domain
    having no verdict is not a reason to admit a page that tripped the screen."""
    assert page_state(domain_state, flagged=flagged) == expected


# --------------------------------------------------------------------------
# What a model can reach — the negative that matters


async def test_a_quarantined_page_is_stored_and_chunked(clean) -> None:
    """§2.5 is explicit: quarantined content is kept. Holding it back is
    reversible and deleting it is not."""
    source = await a_source(
        clean, url="https://q.test/1", trust_state="quarantined", text="quarantined passage"
    )

    chunks = (
        (await clean.execute(select(Chunk).where(Chunk.source_id == source.source_id)))
        .scalars()
        .all()
    )

    assert len(chunks) == 1


async def test_the_model_surface_cannot_see_quarantined_material(clean) -> None:
    await a_source(clean, url="https://q.test/1", trust_state="quarantined", text="zoning appeal")
    await a_source(clean, url="https://c.test/1", trust_state="cleared", text="zoning appeal")

    result = await search(
        clean, "zoning appeal", filters=SearchFilters(cleared_only=True), limit=10
    )

    urls = {hit.url for hit in result.hits}
    assert "https://c.test/1" in urls
    assert "https://q.test/1" not in urls


async def test_unscreened_material_is_also_withheld(clean) -> None:
    """The filter is "only cleared", not "not quarantined". A page nothing has
    examined is not a page that has been checked."""
    await a_source(clean, url="https://u.test/1", trust_state="unscreened", text="tram corridor")

    result = await search(
        clean, "tram corridor", filters=SearchFilters(cleared_only=True), limit=10
    )

    assert [hit.url for hit in result.hits] == []


async def test_the_operators_own_search_still_shows_it(clean) -> None:
    """Deliberate, and the reason `cleared_only` defaults to False: somebody
    reading their own corpus has to be able to see what was quarantined, or a
    false positive is invisible and the screen is unaccountable."""
    await a_source(clean, url="https://q.test/2", trust_state="quarantined", text="ferry terminal")

    result = await search(clean, "ferry terminal", filters=SearchFilters(), limit=10)

    assert [hit.url for hit in result.hits] == ["https://q.test/2"]


def test_the_mcp_tool_asks_for_cleared_only() -> None:
    """The filter is worth nothing if the surface that needs it does not set
    it, and that is one keyword somebody can drop without any behaviour test
    noticing — the tool would simply start returning more."""
    body = (REPO / "services/api/api/mcp/server.py").read_text()

    assert "cleared_only=True" in body, (
        "the MCP search tool no longer restricts itself to cleared sources"
    )


# --------------------------------------------------------------------------
# The map lookup that decides "known"


def test_being_in_the_map_is_not_the_same_as_getting_a_tier() -> None:
    """`resolve_tier` always returns something — an unmapped domain gets the
    default — so "mapped" needs its own question, and that difference is what
    separates a curated domain from the unknown one screening is about."""
    mapping = {
        "exact": {"government": ["example.gov"]},
        "patterns": {"academic": ["*.edu"]},
        "default_tier": "informal",
    }

    assert is_tier_mapped("example.gov", mapping)
    assert is_tier_mapped("mit.edu", mapping)
    assert not is_tier_mapped("some-blog.test", mapping)
