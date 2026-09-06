"""Seeding invariants (spec §13.1, scaffold §1.7).

Two promises the seed script makes, both of which would break something
important if they quietly stopped holding:

- **Configuration only, never content.** Production starts empty. A fixture path
  creeping in here would mean shipping fabricated sources into a research corpus.
- **Insert, never overwrite.** Re-seeding must not undo steering, or §10's
  promise that nothing is destroyed becomes false.
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("require_db")

CONTENT_TABLES = ["sources", "chunks", "figures", "entities", "edges", "attribute_values"]
CONFIG_TABLES = ["topic_config", "attribute_definitions", "gazetteer", "agents", "fetch_policy"]


def _run_seed() -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/seed.py"], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, f"seed failed:\n{result.stdout}\n{result.stderr}"
    return result


async def test_seed_populates_configuration(session_for) -> None:
    _run_seed()
    sess = await session_for("rw")
    for table in CONFIG_TABLES:
        count = await sess.scalar(text(f"SELECT count(*) FROM {table}"))
        assert count > 0, f"{table} is empty after seeding"


async def test_seed_loads_no_content(session_for) -> None:
    """The structural guarantee: production starts empty (scaffold §1.7).

    If this fails, someone added a fixture-loading path, and a research corpus
    would start life containing material nobody crawled.
    """
    _run_seed()
    sess = await session_for("rw")
    for table in CONTENT_TABLES:
        count = await sess.scalar(text(f"SELECT count(*) FROM {table}"))
        assert count == 0, f"{table} contains {count} rows — seeding loaded content"


async def test_seed_is_idempotent(session_for) -> None:
    """Re-running must not duplicate rows."""
    _run_seed()
    sess = await session_for("rw")
    before = {t: await sess.scalar(text(f"SELECT count(*) FROM {t}")) for t in CONFIG_TABLES}
    await sess.rollback()

    _run_seed()
    sess2 = await session_for("rw")
    after = {t: await sess2.scalar(text(f"SELECT count(*) FROM {t}")) for t in CONFIG_TABLES}
    assert before == after


async def test_seed_does_not_overwrite_steering(session_for) -> None:
    """A weight changed months ago in Admin must survive a re-seed.

    Overwriting would silently undo steering — the one thing §10 promises the
    system never does.
    """
    _run_seed()
    sess = await session_for("rw")
    await sess.execute(
        text("UPDATE topic_config SET weight = 0.99, pinned = true WHERE topic = 'walkability'")
    )
    await sess.commit()

    try:
        _run_seed()
        sess2 = await session_for("rw")
        row = (
            await sess2.execute(
                text("SELECT weight, pinned FROM topic_config WHERE topic = 'walkability'")
            )
        ).one()
        assert row.weight == pytest.approx(0.99), "re-seeding reverted a steered weight"
        assert row.pinned is True, "re-seeding reverted a pin"
    finally:
        restore = await session_for("rw")
        await restore.execute(
            text(
                "UPDATE topic_config SET weight = 0.40, pinned = false WHERE topic = 'walkability'"
            )
        )
        await restore.commit()


async def test_seeded_agents_hold_no_credentials(session_for) -> None:
    """The registry stores the name of an env var, never a key (§11.11).

    The database is snapshotted off-device, so a key here would travel with
    every backup.
    """
    _run_seed()
    sess = await session_for("rw")
    rows = (await sess.execute(text("SELECT endpoint, health_url FROM agents"))).all()
    for endpoint, health_url in rows:
        for value in (endpoint, health_url):
            if value:
                assert value.startswith("${") or value.startswith("http"), (
                    f"agent endpoint {value!r} looks like a literal secret"
                )
