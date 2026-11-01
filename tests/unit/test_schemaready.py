"""Services wait for the schema their release expects (task B-263)."""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

from meridian_core import schemaready
from meridian_core.schemaready import EXPECTED_REVISION, wait_for_schema

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"


def heads() -> set[str]:
    """The heads of `migrations/versions`, as Alembic computes them."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(VERSIONS.parent))
    return set(ScriptDirectory.from_config(config).get_heads())


def test_the_expected_revision_is_the_one_head() -> None:
    """Drift: a migration added without moving EXPECTED_REVISION would leave every updated
    service starting against the old schema, which is what the wait exists to prevent."""
    found = heads()
    assert len(found) == 1, f"migrations have {len(found)} heads: {sorted(found)}"
    assert found == {EXPECTED_REVISION}


class Revisions:
    """A database whose revision moves on as the test says."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = 0

    def factory(self):
        db = self

        class Sess:
            async def scalar(self, _):
                db.asked += 1
                return db.answers.pop(0) if len(db.answers) > 1 else db.answers[0]

        @asynccontextmanager
        async def make():
            yield Sess()

        return make


async def no_sleep(_):
    return None


async def test_a_database_at_the_revision_starts_at_once() -> None:
    db = Revisions(EXPECTED_REVISION)
    assert await wait_for_schema(timeout_s=60, session_factory=db.factory(), sleep=no_sleep)
    assert db.asked == 1


async def test_a_service_waits_while_migrate_catches_up() -> None:
    db = Revisions(None, "aaaaaaaaaaaa", EXPECTED_REVISION)
    assert await wait_for_schema(timeout_s=60, session_factory=db.factory(), sleep=no_sleep)
    assert db.asked == 3


async def test_it_starts_anyway_after_the_timeout_and_says_so(caplog) -> None:
    """A database ahead of the image (a rollback) never matches; the service must still start."""
    now = [0.0]

    async def tick(seconds):
        now[0] += seconds

    db = Revisions("ffffffffffff")
    ok = await wait_for_schema(
        timeout_s=20, poll_s=5, session_factory=db.factory(), clock=lambda: now[0], sleep=tick
    )
    assert ok is False
    assert db.asked == 5  # at 0, 5, 10, 15 and 20 s


async def test_zero_turns_the_wait_off(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_SCHEMA_WAIT_S", "0")
    db = Revisions(None)
    assert await wait_for_schema(session_factory=db.factory(), sleep=no_sleep)
    assert db.asked == 0


def test_every_long_running_service_waits() -> None:
    """Completeness: an entry point that forgot the wait would be the one service that
    starts new code on the old schema after an update."""
    root = VERSIONS.parents[1]
    for path in (
        "worker/main.py",
        "worker/scheduler.py",
        "worker/bot.py",
        "worker/embed.py",
        "worker/orchestrate.py",
    ):
        assert "wait_before_start()" in (root / "services/worker" / path).read_text(), path
    assert "await wait_for_schema()" in (root / "services/api/api/main.py").read_text()
    assert "await wait_for_schema()" in (root / "services/api/api/connector.py").read_text()


def test_the_module_says_where_its_doc_is() -> None:
    assert "docs/guides/deployment.md#updates-and-migrations" in (schemaready.__doc__ or "")
