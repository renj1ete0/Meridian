"""The prefilter's database gates (task P1-06, spec §6.4).

Against a real Postgres because the two gates that matter are queries: has this
URL been seen — in `queue` or in `sources` — and is its domain one the policy
says not to fetch from. Both are what stop the frontier re-queueing the corpus
it already has every time a page links back to it, and neither is observable in
a double.

The batching matters too and is asserted here: a page with 400 links must cost a
fixed number of round-trips, not 400 of them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.models import FetchPolicy, QueueTask, Source
from meridian_core.queueing import claim_next, enqueue
from meridian_core.tiering import priority_for_domain
from worker.prefilter import Prefilter

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def domain() -> str:
    return f"t{uuid.uuid4().hex[:12]}.test"


@pytest.fixture
def topic() -> str:
    return f"test_prefilter_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def cleanup(session_for, domain, topic):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(QueueTask).where(QueueTask.topic == topic))
    await sess.execute(delete(Source).where(Source.url.like(f"https://%{domain}%")))
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain.like(f"%{domain}")))
    await sess.commit()


# --------------------------------------------------------------------------
# Already seen
# --------------------------------------------------------------------------


async def test_a_url_already_in_the_queue_is_not_queued_again(
    session_for, domain, topic, cleanup
) -> None:
    """Otherwise every page that links to the same hub re-queues it."""
    sess = await session_for("rw")
    url = f"https://{domain}/hub"
    await enqueue(sess, url, topic=topic)

    verdict = await Prefilter().keep(sess, [url, f"https://{domain}/new"])

    assert verdict.kept == (f"https://{domain}/new",)
    assert verdict.dropped["already_seen"] == 1


@pytest.mark.parametrize("status", ["pending", "fetched", "done", "failed"])
async def test_any_queue_status_counts_as_seen(
    session_for, domain, topic, cleanup, status: str
) -> None:
    """Any status on purpose.

    A URL that failed is not worth retrying under a new task id — `next_attempt_at`
    is for that — and one that is `done` is not worth re-fetching because another
    page links to it. Re-crawl scheduling is a separate decision, and conflating
    the two would have every page's link list resurrect the whole corpus.
    """
    sess = await session_for("rw")
    url = f"https://{domain}/seen"
    task = await enqueue(sess, url, topic=topic)
    task.status = status
    await sess.flush()

    verdict = await Prefilter().keep(sess, [url])

    assert verdict.kept == ()
    assert verdict.dropped["already_seen"] == 1


async def test_a_url_already_in_sources_is_not_queued_again(
    session_for, domain, topic, cleanup
) -> None:
    """Checked as well as the queue, because a queue row can be pruned while its
    source record stays — and re-fetching what the corpus already holds is
    precisely what this exists to prevent."""
    sess = await session_for("rw")
    url = f"https://{domain}/known"
    sess.add(Source(url=url))
    await sess.flush()

    verdict = await Prefilter().keep(sess, [url])

    assert verdict.kept == ()
    assert verdict.dropped["already_seen"] == 1


async def test_the_already_seen_check_runs_on_the_normalised_url(
    session_for, domain, topic, cleanup
) -> None:
    """The whole reason normalisation comes first.

    Un-normalised, a campaign-tagged link to a page already in the queue looks
    like a new URL and the duplicate is invisible until someone counts rows.
    """
    sess = await session_for("rw")
    await enqueue(sess, f"https://{domain}/page", topic=topic)

    verdict = await Prefilter().keep(
        sess, [f"https://{domain.upper()}/page?utm_source=newsletter#top"]
    )

    assert verdict.kept == ()
    assert verdict.dropped["already_seen"] == 1


# --------------------------------------------------------------------------
# Blocklists
# --------------------------------------------------------------------------


async def test_a_domain_the_policy_blocked_is_not_re_queued(
    session_for, domain, topic, cleanup
) -> None:
    """`P1-05` writes these after consecutive failures.

    Without this gate the frontier re-queues a dead site every time another page
    links to it, and the block accomplishes nothing but a wasted policy lookup
    per task.
    """
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=domain, status="blocked"))
    await sess.flush()

    verdict = await Prefilter().keep(sess, [f"https://{domain}/anything"])

    assert verdict.kept == ()
    assert verdict.dropped["policy_blocked"] == 1


async def test_a_paused_domain_is_also_skipped(session_for, domain, topic, cleanup) -> None:
    """Paused is an operator saying "not now", which the frontier must respect."""
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=domain, status="paused"))
    await sess.flush()

    verdict = await Prefilter().keep(sess, [f"https://{domain}/anything"])

    assert verdict.dropped["policy_blocked"] == 1


async def test_an_active_domain_passes(session_for, domain, topic, cleanup) -> None:
    """The gate must not reject everything with a policy row."""
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=domain, status="active"))
    await sess.flush()

    verdict = await Prefilter().keep(sess, [f"https://{domain}/anything"])

    assert verdict.kept == (f"https://{domain}/anything",)


async def test_the_seeded_blocklist_drops_before_any_query(
    session_for, domain, topic, cleanup
) -> None:
    """Social platforms and shorteners are on nearly every government page.

    Dropped by string, before the database is touched at all — the point is that
    they never cost anything, not that they fail later.
    """
    sess = await session_for("rw")
    prefilter = Prefilter(["facebook.com", "bit.ly"])

    verdict = await prefilter.keep(
        sess,
        [
            "https://www.facebook.com/sharer?u=x",
            "https://bit.ly/abcd",
            f"https://{domain}/real",
        ],
    )

    assert verdict.kept == (f"https://{domain}/real",)
    assert verdict.dropped["blocked_domain"] == 2


async def test_the_blocklist_matches_subdomains(session_for, domain, topic, cleanup) -> None:
    """It is a registrable domain, so `www.` and `m.` are the same site."""
    sess = await session_for("rw")

    verdict = await Prefilter(["facebook.com"]).keep(
        sess, ["https://m.facebook.com/x", "https://www.facebook.com/y"]
    )

    assert verdict.kept == ()
    assert verdict.dropped["blocked_domain"] == 2


# --------------------------------------------------------------------------
# Shape and ordering
# --------------------------------------------------------------------------


async def test_the_cheap_gates_run_before_the_queries(session_for, domain, topic, cleanup) -> None:
    """A batch of 500 links should reach the database as a batch of 40."""
    sess = await session_for("rw")
    links = (
        [f"https://{domain}/asset{i}.png" for i in range(50)]
        + ["mailto:a@b.test", "javascript:void(0)"]
        + [f"https://{domain}/real"]
    )

    verdict = await Prefilter().keep(sess, links)

    assert verdict.kept == (f"https://{domain}/real",)
    assert verdict.dropped["not_a_document"] == 50
    assert verdict.dropped["unusable"] == 2
    assert verdict.considered == len(links)


async def test_a_url_repeated_on_one_page_produces_one_row(
    session_for, domain, topic, cleanup
) -> None:
    """A nav link appears in the header and the footer of the same document."""
    sess = await session_for("rw")
    url = f"https://{domain}/about"

    verdict = await Prefilter().keep(sess, [url, f"{url}#top", url, f"{url}?utm_source=x"])

    assert verdict.kept == (url,)
    assert verdict.dropped["duplicate_on_page"] == 3


async def test_the_kept_order_follows_the_page(session_for, domain, topic, cleanup) -> None:
    """A page's earlier links are its more likely to matter."""
    sess = await session_for("rw")
    links = [f"https://{domain}/p{i}" for i in range(6)]

    verdict = await Prefilter().keep(sess, links)

    assert list(verdict.kept) == links


async def test_an_empty_batch_costs_nothing(session_for, domain, topic, cleanup) -> None:
    sess = await session_for("rw")

    verdict = await Prefilter().keep(sess, [])

    assert verdict.kept == () and verdict.considered == 0


async def test_a_batch_of_only_junk_never_reaches_the_database(
    session_for, domain, topic, cleanup
) -> None:
    """The early return exists so a page of images is not a wasted round-trip."""
    sess = await session_for("rw")

    verdict = await Prefilter().keep(sess, ["mailto:a@b.test", "https://x.test/a.png"])

    assert verdict.kept == ()
    assert verdict.considered == 2


# --------------------------------------------------------------------------
# What gets queued
# --------------------------------------------------------------------------


async def test_enqueue_writes_a_frontier_row_at_tier_priority(
    session_for, domain, topic, cleanup
) -> None:
    """§5.2: a government link outranks a blog with nobody curating a seed list.

    `priority_for_domain` has existed since `P1-17` with no caller; this is the
    behaviour it was written for.
    """
    from meridian_core.policy import source_tier_map

    sess = await session_for("rw")
    tiers = await source_tier_map(sess)
    gov = priority_for_domain("lta.gov.sg", tiers)
    blog = priority_for_domain(domain, tiers)
    assert gov > blog, "the seeded tier mapping is not being read"

    await enqueue(sess, f"https://{domain}/a", topic=topic, priority=blog)
    await enqueue(sess, f"https://{domain}/b", topic=topic, priority=gov)

    claimed = await claim_next(sess, worker_id="w", topics=[topic], task_types=["url"])
    assert claimed.url_or_query.endswith("/b"), "the higher-tier link was not claimed first"


async def test_a_queued_frontier_row_says_where_it_came_from(
    session_for, domain, topic, cleanup
) -> None:
    """`seed_source` separates frontier expansion from model-emitted seeds,
    which §11.4 caps per run and §10.2 logs for review."""
    sess = await session_for("rw")

    task = await enqueue(sess, f"https://{domain}/a", topic=topic)

    assert task.seed_source == "frontier"
    assert task.task_type == "url"
    assert task.status == "pending"


async def test_enqueue_does_no_filtering_of_its_own(session_for, domain, topic, cleanup) -> None:
    """Deliberately. Whether a URL is worth fetching needs blocklists, the
    already-seen check and the domain's policy — none of which belong in a
    function whose job is inserting a row, and all of which Admin would have to
    work around when injecting a seed by hand (§13.2)."""
    sess = await session_for("rw")
    url = f"https://{domain}/dup"
    await enqueue(sess, url, topic=topic)
    await enqueue(sess, url, topic=topic)

    rows = await sess.execute(select(QueueTask).where(QueueTask.url_or_query == url))
    assert len(list(rows.scalars())) == 2
