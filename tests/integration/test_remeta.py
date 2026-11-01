"""The stored-metadata pass (task B-234), against Postgres and real raw files.

What matters is what it leaves behind: nothing at all without --apply; with it, a declared
value in place of an inferred one, its source in `extra`, the old value kept, a refused
value cleared, and a value already declared never overwritten by an inferred one.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from meridian_core.models import Source
from meridian_core.sources import upsert_source
from worker.remeta import run_pass

pytestmark = pytest.mark.usefixtures("require_db")

ARTICLE = {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Ferry waits fall",
    "datePublished": "2021-03-09T08:00:00+08:00",
    "author": {"@type": "Person", "name": "Mira Castellan"},
    "publisher": {"@type": "Organization", "name": "Velde Gazette"},
}


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def scoped(sess):
    """The test's transaction, commits downgraded."""

    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def html_source(sess, root: Path, head: str, **fields) -> Source:
    host = f"t{uuid.uuid4().hex[:10]}.test"
    relative = f"{host}/page.html"
    (root / host).mkdir(parents=True)
    (root / relative).write_text(
        f"<html><head><title>Ferry waits fall</title>{head}</head><body><p>Body.</p></body></html>"
    )
    source, _ = await upsert_source(
        sess,
        f"https://{host}/page",
        checksum=f"sha256:{uuid.uuid4().hex}",
        raw_file_path=relative,
        media_type="text/html",
        **fields,
    )
    return source


async def run(sess, sources: list[Source], root: Path, *, apply: bool):
    return await run_pass(
        apply=apply,
        session_factory=scoped(sess),
        root=str(root),
        start_after=min(s.source_id for s in sources) - 1,
        limit=len(sources),
    )


def ld(data) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


async def test_report_only_writes_nothing(sess, tmp_path: Path) -> None:
    source = await html_source(
        sess, tmp_path, ld(ARTICLE), publisher="velde.example", publication_date=dt.date(2026, 1, 1)
    )
    before = (source.author, source.publisher, source.publication_date, dict(source.extra))

    stats = await run(sess, [source], tmp_path, apply=False)
    await sess.refresh(source)

    assert stats.changed_sources == 1
    assert (source.author, source.publisher, source.publication_date, source.extra) == before


async def test_apply_writes_declared_values_with_their_provenance(sess, tmp_path: Path) -> None:
    source = await html_source(
        sess,
        tmp_path,
        ld(ARTICLE),
        author="Castellan; Mira",
        publisher="velde.example",
        publication_date=dt.date(2025, 9, 4),
    )

    await run(sess, [source], tmp_path, apply=True)
    await sess.refresh(source)

    assert source.author == "Mira Castellan"
    assert source.publisher == "Velde Gazette"
    assert source.publication_date == dt.date(2021, 3, 9)
    assert source.extra["author_from"] == "jsonld"
    assert source.extra["publisher_from"] == "jsonld"
    assert source.extra["date_from"] == "jsonld"
    assert source.extra["replaced_metadata"] == {
        "author": "Castellan; Mira",
        "publisher": "velde.example",
        "publication_date": "2025-09-04",
    }
    assert source.extra["media_type"] == "text/html"  # the rest of extra survives


async def test_a_refused_stored_value_is_cleared_and_kept_aside(sess, tmp_path: Path) -> None:
    source = await html_source(
        sess, tmp_path, "", author="Webadmin", publication_date=dt.date(2026, 1, 1)
    )

    await run(sess, [source], tmp_path, apply=True)
    await sess.refresh(source)

    assert source.author is None
    assert source.publication_date is None
    assert source.extra["replaced_metadata"] == {
        "author": "Webadmin",
        "publication_date": "2026-01-01",
    }
    assert "date_from" not in source.extra


async def test_a_declared_value_is_never_overwritten_by_an_inferred_one(
    sess, tmp_path: Path
) -> None:
    """A date an earlier pass took from the page stays, though the file now declares none."""
    source = await html_source(
        sess,
        tmp_path,
        "",
        publication_date=dt.date(2019, 1, 1),
        extra={"date_from": "citation"},
    )

    await run(sess, [source], tmp_path, apply=True)
    await sess.refresh(source)

    assert source.publication_date == dt.date(2019, 1, 1)
    assert source.extra["date_from"] == "citation"
    assert "replaced_metadata" not in source.extra


async def test_a_second_run_changes_nothing(sess, tmp_path: Path) -> None:
    source = await html_source(sess, tmp_path, ld(ARTICLE), publication_date=dt.date(2026, 1, 1))

    await run(sess, [source], tmp_path, apply=True)
    again = await run(sess, [source], tmp_path, apply=True)

    assert again.changed_sources == 0
    assert sum(again.outcomes.values()) == 0


async def test_a_missing_raw_file_still_has_its_stored_values_judged(sess, tmp_path: Path) -> None:
    source, _ = await upsert_source(
        sess,
        f"https://t{uuid.uuid4().hex[:10]}.test/gone",
        checksum=f"sha256:{uuid.uuid4().hex}",
        raw_file_path="nowhere/page.html",
        media_type="text/html",
        author="Administrator",
        publisher="Velde Gazette",
    )

    stats = await run(sess, [source], tmp_path, apply=True)
    await sess.refresh(source)

    assert stats.skipped["file missing"] == 1
    assert source.author is None
    assert source.publisher == "Velde Gazette"
