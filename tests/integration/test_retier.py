"""Re-tiering domain-only scholarly sources (task B-50)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.models import FetchPolicy, Source
from meridian_core.policy import GLOBAL_DOMAIN
from meridian_core.sources import upsert_source
from worker.retier import run_pass

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


@pytest.fixture
async def suffix(sess) -> str:
    """A `.test` suffix of this test's own, mapped as an academic domain."""
    tag = f"ac{uuid.uuid4().hex[:6]}.test"
    glob = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    tiers = dict(glob.settings.get("source_tiers") or {})
    patterns = {k: list(v or []) for k, v in (tiers.get("patterns") or {}).items()}
    patterns.setdefault("peer_reviewed", []).append(f"*.{tag}")
    tiers["patterns"] = patterns
    tiers["needs_scholarly_evidence"] = [*(tiers.get("needs_scholarly_evidence") or []), f"*.{tag}"]
    glob.settings = {**glob.settings, "source_tiers": tiers}
    await sess.flush()
    return tag


async def a_source(sess, url: str, *, doi: str | None = None) -> Source:
    source, _ = await upsert_source(
        sess, url, checksum=f"sha256:{uuid.uuid4().hex}", source_tier="peer_reviewed", doi=doi
    )
    await sess.flush()
    return source


async def tier_of(sess, source_id: int) -> str:
    row = await sess.get(Source, source_id)
    await sess.refresh(row)
    return row.source_tier


async def test_a_domain_only_scholarly_source_becomes_institutional(sess, suffix) -> None:
    page = await a_source(sess, f"https://hr.{suffix}/policy")

    stats = await run_pass(apply=True, session_factory=factory(sess))

    assert await tier_of(sess, page.source_id) == "institutional"
    assert stats.by_host[f"hr.{suffix}"] == 1


async def test_a_source_with_its_own_doi_keeps_its_tier(sess, suffix) -> None:
    paper = await a_source(sess, f"https://journal.{suffix}/article", doi="10.1234/x")

    await run_pass(apply=True, session_factory=factory(sess))

    assert await tier_of(sess, paper.source_id) == "peer_reviewed"


async def test_a_report_changes_no_tier(sess, suffix) -> None:
    page = await a_source(sess, f"https://hr.{suffix}/policy")

    stats = await run_pass(apply=False, session_factory=factory(sess))

    assert stats.by_host[f"hr.{suffix}"] == 1
    assert await tier_of(sess, page.source_id) == "peer_reviewed"


async def test_a_domain_not_needing_evidence_is_left_alone(sess, suffix) -> None:
    other = await a_source(sess, f"https://p{uuid.uuid4().hex[:6]}.publisher.test/a")

    await run_pass(apply=True, session_factory=factory(sess))

    assert await tier_of(sess, other.source_id) == "peer_reviewed"
