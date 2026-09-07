"""The source record (task P1-11, spec §5.2, §5.4, §6.4).

Against a real Postgres because the properties that matter are the ones a double
cannot have: `sources.url` carries a unique constraint that makes "upsert"
mean something, `extra` is JSONB whose in-place mutations SQLAlchemy does not
track, and `source_tier` / `retention_tier` are CHECK-constrained columns that
would silently accept any string if the constraints had never been created.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import StatementError

from meridian_core.models import Source
from meridian_core.sources import (
    SOURCE_TIER_RANK,
    get_source,
    touch_source,
    upsert_source,
)

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def url() -> str:
    return f"https://t{uuid.uuid4().hex[:12]}.test/a"


@pytest.fixture
async def cleanup(session_for, url):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.url == url))
    await sess.commit()


# --------------------------------------------------------------------------
# Creating and updating
# --------------------------------------------------------------------------


async def test_a_first_fetch_creates_the_row(session_for, url, cleanup) -> None:
    sess = await session_for("rw")

    row, changed = await upsert_source(
        sess,
        url,
        checksum="sha256:abc",
        etag='"v1"',
        raw_file_path="example.test/ab/abc.html",
        source_tier="government",
        retention_tier="primary",
        media_type="text/html",
    )

    assert row.source_id is not None
    assert changed is True, "content nobody had before is content that changed"
    assert row.checksum == "sha256:abc"
    assert row.etag == '"v1"'
    assert row.raw_file_path == "example.test/ab/abc.html"
    assert row.source_tier == "government"
    assert row.retention_tier == "primary"
    assert row.accessed_at is not None


async def test_a_second_fetch_updates_rather_than_duplicating(session_for, url, cleanup) -> None:
    """`sources.url` is unique; a second row is a constraint violation, not a bug
    that shows up later."""
    sess = await session_for("rw")
    first, _ = await upsert_source(sess, url, checksum="sha256:one")
    second, changed = await upsert_source(sess, url, checksum="sha256:two")

    assert second.source_id == first.source_id
    assert changed is True
    count = await sess.scalar(select(Source).where(Source.url == url).exists().select())
    assert count
    rows = (await sess.execute(select(Source).where(Source.url == url))).scalars().all()
    assert len(rows) == 1


async def test_identical_bytes_report_no_change(session_for, url, cleanup) -> None:
    """The cheap half of keeping a corpus fresh.

    Most origins do not implement conditional requests, so a 200 returning
    byte-identical content is far more common than a 304 — and it means exactly
    the same thing to extraction, which can be skipped entirely.
    """
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:same")

    _, changed = await upsert_source(sess, url, checksum="sha256:same")

    assert changed is False


async def test_a_fetch_that_learned_nothing_erases_nothing(session_for, url, cleanup) -> None:
    """The bug this shape exists to prevent.

    A response with no ETag must not blank the one stored last week — the next
    request would silently stop being conditional, and nothing would notice
    except the bandwidth graph.
    """
    sess = await session_for("rw")
    await upsert_source(
        sess,
        url,
        checksum="sha256:one",
        etag='"kept"',
        last_modified="Sun, 30 Aug 2026 04:11:49 GMT",
        raw_file_path="a/b/c.html",
    )

    await upsert_source(sess, url, checksum="sha256:two")

    row = await get_source(sess, url)
    assert row.etag == '"kept"'
    assert row.last_modified == "Sun, 30 Aug 2026 04:11:49 GMT"
    assert row.raw_file_path == "a/b/c.html"


async def test_accessed_at_advances_on_every_fetch(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    early = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    await upsert_source(sess, url, checksum="sha256:one", accessed_at=early)

    await upsert_source(sess, url, checksum="sha256:one")

    row = await get_source(sess, url)
    assert row.accessed_at > early


# --------------------------------------------------------------------------
# Tier, and not overwriting a human
# --------------------------------------------------------------------------


async def test_a_source_tier_someone_raised_is_not_silently_reverted(
    session_for, url, cleanup
) -> None:
    """§11.12's rule, applied to the field that decides what gets kept.

    Tiering is mechanical and deterministic, so the two normally agree. They
    stop agreeing the moment a domain is corrected by hand in Admin, and a
    correction the next crawl reverts is worse than no correction at all.
    """
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:one", source_tier="informal")
    row = await get_source(sess, url)
    row.source_tier = "government"  # the operator's correction
    await sess.flush()

    await upsert_source(sess, url, checksum="sha256:two", source_tier="informal")

    row = await get_source(sess, url)
    assert row.source_tier == "government"


async def test_a_source_tier_still_moves_up(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:one", source_tier="press")

    await upsert_source(sess, url, checksum="sha256:two", source_tier="peer_reviewed")

    row = await get_source(sess, url)
    assert row.source_tier == "peer_reviewed"


def test_every_source_tier_is_ranked() -> None:
    """Completeness probe: a new tier must be placed, not left unranked.

    An unranked tier compares as lower than everything, so a new one would
    silently lose every comparison it entered.
    """
    from meridian_core.models.source import SOURCE_TIER

    assert set(SOURCE_TIER.enums) == set(SOURCE_TIER_RANK)


async def test_a_tier_that_is_not_one_is_refused_before_it_reaches_the_database(
    session_for, url, cleanup
) -> None:
    """`validate_strings=True` rejects while binding, so the value never leaves Python.

    This is deliberately *not* a test that the CHECK constraint exists. It would
    pass either way — the value is refused before any SQL is sent — and that is
    exactly how Phase 0 shipped unchecked VARCHAR columns with a green suite.
    The database half is tested through raw SQL in `test_schema_and_roles.py`,
    which is the only way to see it.

    What is worth asserting here is that the error names the offending value and
    the enum, because `sources.source_tier` is written from a mapping seeded out
    of YAML and a typo there should be readable rather than an opaque bind
    failure.
    """
    sess = await session_for("rw")
    with pytest.raises(StatementError, match="extremely_official") as caught:
        await upsert_source(sess, url, checksum="sha256:x", source_tier="extremely_official")
    assert "source_tier" in str(caught.value)
    await sess.rollback()


# --------------------------------------------------------------------------
# extra: what extraction needs and the schema has no column for
# --------------------------------------------------------------------------


async def test_the_media_type_survives_for_extraction(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:x", media_type="application/pdf")

    row = await get_source(sess, url)
    assert row.extra["media_type"] == "application/pdf"


async def test_a_redirect_records_where_the_bytes_actually_came_from(
    session_for, url, cleanup
) -> None:
    """The queued URL and the URL served are different facts after a 301."""
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:x", final_url=f"{url}/redirected")

    row = await get_source(sess, url)
    assert row.extra["final_url"] == f"{url}/redirected"


async def test_no_final_url_is_recorded_when_there_was_no_redirect(
    session_for, url, cleanup
) -> None:
    """Recording it unconditionally would make every row look redirected."""
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:x", final_url=url)

    row = await get_source(sess, url)
    assert "final_url" not in (row.extra or {})


async def test_extra_is_merged_and_the_write_actually_lands(session_for, url, cleanup) -> None:
    """JSONB in-place mutation is not tracked, so this must replace the dict.

    `row.extra["k"] = v` is a write that never reaches the database, and it
    fails by doing nothing — the exact failure a test that only checks the
    in-memory object would miss. The refresh forces a real read back, so the
    assertion is about what Postgres holds and not about what the ORM remembers.
    """
    sess = await session_for("rw")
    await upsert_source(sess, url, checksum="sha256:x", media_type="text/html")
    await upsert_source(sess, url, checksum="sha256:y", extra={"language_hint": "en"})

    row = await get_source(sess, url)
    await sess.refresh(row)

    assert row.extra == {"media_type": "text/html", "language_hint": "en"}


# --------------------------------------------------------------------------
# The 304 path
# --------------------------------------------------------------------------


async def test_touch_records_the_check_without_touching_the_content(
    session_for, url, cleanup
) -> None:
    """A source verified this morning and one verified in March are different
    things to a corpus that has to be trusted."""
    sess = await session_for("rw")
    early = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    await upsert_source(
        sess, url, checksum="sha256:one", etag='"v1"', raw_file_path="a/b", accessed_at=early
    )

    row = await touch_source(sess, url)

    assert row is not None
    assert row.accessed_at > early
    assert row.checksum == "sha256:one"
    assert row.etag == '"v1"'
    assert row.raw_file_path == "a/b"


async def test_touching_a_url_with_no_row_says_so_rather_than_inventing_one(
    session_for, url, cleanup
) -> None:
    """A 304 for a URL nothing ever stored means the validators came from
    somewhere this process did not write."""
    sess = await session_for("rw")

    assert await touch_source(sess, url) is None
    assert await get_source(sess, url) is None
