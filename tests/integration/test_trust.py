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
    NOVEL_FETCHES_TO_ALLOW,
    awaiting_seed_approval,
    page_state,
    record_discovery,
    record_novel_fetch,
    record_screening,
)
from meridian_core.validation import ValidationError, check_seed_allowed

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


# --------------------------------------------------------------------------
# Whether a domain may be seeded at all (task `P4-12`)
# --------------------------------------------------------------------------


async def test_a_domain_records_how_it_was_first_found(clean) -> None:
    await record_discovery(clean, DOMAIN, seed_source="frontier")

    row = await clean.get(FetchPolicy, DOMAIN)
    assert row.first_seen_via == "frontier"
    assert row.seed_allowed is None, "discovery is not approval"


async def test_the_first_sighting_is_not_overwritten(clean) -> None:
    """A domain found by following a link and later proposed by a model was
    still found by following a link. Letting the later event win would erase
    the provenance that decides whether it can auto-approve."""
    await record_discovery(clean, DOMAIN, seed_source="frontier")
    await record_discovery(clean, DOMAIN, seed_source="model")

    row = await clean.get(FetchPolicy, DOMAIN)
    assert row.first_seen_via == "frontier"


async def test_an_operators_own_seed_is_allowed_immediately(clean) -> None:
    """Typing a URL is consent. Making somebody wait three fetches for a domain
    they chose is the system disbelieving them."""
    await record_discovery(clean, DOMAIN, seed_source="user")

    row = await clean.get(FetchPolicy, DOMAIN)
    assert row.seed_allowed is True


async def test_a_frontier_domain_approves_itself_on_novel_documents(clean) -> None:
    await record_discovery(clean, DOMAIN, seed_source="frontier")

    for _ in range(NOVEL_FETCHES_TO_ALLOW - 1):
        assert await record_novel_fetch(clean, DOMAIN) is None

    assert await record_novel_fetch(clean, DOMAIN) is True


async def test_a_model_proposed_domain_never_approves_itself(clean) -> None:
    """The failure being avoided is a model talking the crawl into a domain by
    describing it confidently. Evidence gathered *after* the proposal is
    evidence the proposal caused, so it cannot be what approves it."""
    await record_discovery(clean, DOMAIN, seed_source="model")

    for _ in range(NOVEL_FETCHES_TO_ALLOW * 3):
        allowed = await record_novel_fetch(clean, DOMAIN)

    assert allowed is None


async def test_an_unapproved_domain_is_refused_to_a_model(clean) -> None:
    await record_discovery(clean, DOMAIN, seed_source="model")

    with pytest.raises(ValidationError) as raised:
        await check_seed_allowed(clean, f"https://{DOMAIN}/page", require_seed_allowed=True)

    assert raised.value.rule == "domain_allowed"
    assert "waiting" in str(raised.value)


async def test_a_declined_domain_says_so_differently(clean) -> None:
    """ "Somebody declined this" and "nobody has looked yet" lead to different
    actions, so they must not produce the same message."""
    clean.add(FetchPolicy(domain=DOMAIN, first_seen_via="model", seed_allowed=False))
    await clean.flush()

    with pytest.raises(ValidationError) as raised:
        await check_seed_allowed(clean, f"https://{DOMAIN}/page", require_seed_allowed=True)

    assert "not allowed" in str(raised.value)
    assert "waiting" not in str(raised.value)


async def test_the_crawls_own_reach_is_not_gated(clean) -> None:
    """`seed_allowed` gates *proposals*. The crawl's own frontier expansion
    seeds constantly and legitimately, and gating it would stop the crawl
    discovering anything it had not already approved."""
    await record_discovery(clean, DOMAIN, seed_source="frontier")

    assert await check_seed_allowed(clean, f"https://{DOMAIN}/page") == DOMAIN


async def test_the_approval_queue_holds_only_what_needs_a_person(clean) -> None:
    await record_discovery(clean, "proposed.test", seed_source="model")
    await record_discovery(clean, "found.test", seed_source="frontier")
    await record_discovery(clean, "typed.test", seed_source="user")

    waiting = {row.domain for row in await awaiting_seed_approval(clean)}

    assert waiting == {"proposed.test"}


# --------------------------------------------------------------------------
# Age-aware ranking, end to end (task `P2-20`)
# --------------------------------------------------------------------------


async def test_ageing_is_off_unless_asked_for(clean) -> None:
    """It changes what a search returns, and turning it on for every existing
    caller would silently move the baseline `P2-04`'s benchmark and `P2-09`'s
    go/no-go are measured against."""
    from meridian_core.search import SearchFilters, search

    await a_source(clean, url="https://age.test/1", trust_state="cleared", text="tram siding")

    result = await search(clean, "tram siding", filters=SearchFilters(), limit=5)

    assert result.hits
    assert result.hits[0].decay == 1.0


async def test_an_old_article_ranks_below_a_paper_of_the_same_age(clean) -> None:
    """The reordering this task exists for, and the failure it exists to avoid
    — a global multiplier would have moved both."""
    import datetime as date_module

    from meridian_core.models import Chunk, Source
    from meridian_core.search import SearchFilters, search

    old = date_module.date.today() - date_module.timedelta(days=20 * 365)
    for url, tier in (
        ("https://age.test/paper", "peer_reviewed"),
        ("https://age.test/news", "press"),
    ):
        source = Source(
            url=url,
            source_tier=tier,
            retention_tier="primary",
            trust_state="cleared",
            language="en",
            publication_date=old,
        )
        clean.add(source)
        await clean.flush()
        clean.add(
            Chunk(source_id=source.source_id, text="cycleway separation study", chunk_index=0)
        )
    await clean.flush()

    result = await search(
        clean, "cycleway separation study", filters=SearchFilters(age_aware=True), limit=5
    )

    by_url = {hit.url: hit for hit in result.hits}
    assert by_url["https://age.test/paper"].decay == 1.0
    assert by_url["https://age.test/news"].decay < 1.0
    assert by_url["https://age.test/paper"].score > by_url["https://age.test/news"].score


async def test_the_hit_shows_what_was_done_to_it(clean) -> None:
    """A result silently demoted is one the reader cannot audit, which is the
    opposite of what this corpus is for."""
    import datetime as date_module

    from meridian_core.models import Chunk, Source
    from meridian_core.search import SearchFilters, search

    source = Source(
        url="https://age.test/shown",
        source_tier="press",
        retention_tier="primary",
        trust_state="cleared",
        language="en",
        publication_date=date_module.date.today() - date_module.timedelta(days=365),
    )
    clean.add(source)
    await clean.flush()
    clean.add(Chunk(source_id=source.source_id, text="verge planting trial", chunk_index=0))
    await clean.flush()

    hit = (
        await search(clean, "verge planting trial", filters=SearchFilters(age_aware=True), limit=5)
    ).hits[0]

    assert hit.age_days is not None and hit.age_days >= 364
    assert hit.decay == pytest.approx(0.5, abs=0.01)
    assert hit.score == pytest.approx(hit.score_before_decay * hit.decay)
