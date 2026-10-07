"""The clock and leak checks imitate the dev database and never touch it (`B-147`, `B-148`).

`make clock-check` and `make leak-check` are only meaningful if their throwaway Postgres is the
dev one: the same image, roles and init script. And they must stay off the dev database's port
and project, or a run would wipe the developer's data with `down -v`.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
HERE = REPO / "scripts/clockshift"


def compose(path: pathlib.Path) -> dict:
    """A compose file, parsed."""
    return yaml.safe_load(path.read_text())


def test_the_image_is_the_dev_database_image() -> None:
    """The faketime image is built from the image the dev stack runs."""
    dev = compose(REPO / "docker-compose.dev.yml")["services"]["postgres"]["image"]
    base = re.search(r"^FROM\s+(\S+)", (HERE / "Dockerfile").read_text(), re.MULTILINE)
    assert base and base.group(1) == dev


def test_the_roles_match_the_dev_database() -> None:
    """Every role password the dev database is given, plus the guest's, which tests log in as."""
    dev = compose(REPO / "docker-compose.dev.yml")["services"]["postgres"]["environment"]
    shifted = compose(HERE / "compose.yml")["services"]["postgres"]["environment"]
    for key, value in dev.items():
        assert shifted.get(key) == value, key
    assert shifted.get("PG_GUEST_PASSWORD")


def test_the_init_script_it_mounts_exists() -> None:
    """The roles come from the same init script, resolved from the compose file's folder."""
    volumes = compose(HERE / "compose.yml")["services"]["postgres"]["volumes"]
    source = volumes[0].split(":")[0]
    assert (HERE / source).resolve() == REPO / "scripts/init-roles.sh"


def test_it_never_shares_the_dev_database() -> None:
    """Another project and another port, so `down -v` cannot remove the dev volume."""
    dev = compose(REPO / "docker-compose.dev.yml")
    shifted = compose(HERE / "compose.yml")
    assert shifted["name"] != dev["name"]
    dev_ports = {p.split(":")[0] for s in dev["services"].values() for p in s.get("ports", [])}
    ours = {p.split(":")[0] for p in shifted["services"]["postgres"]["ports"]}
    assert ours and not ours & dev_ports
    for script in ("run.sh", "leakcheck.sh"):
        text = (HERE / script).read_text()
        assert "21111" not in text, script
        assert set(re.findall(r"localhost:(\d+)", text)) == ours, script


@pytest.mark.parametrize("script", ["run.sh", "leakcheck.sh"])
def test_the_make_targets_run_the_scripts(script: str) -> None:
    """`make clock-check` and `make leak-check` call scripts that exist and can be executed."""
    assert f"./scripts/clockshift/{script}" in (REPO / "Makefile").read_text()
    assert os.access(HERE / script, os.X_OK)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_date_preload_moves_now_and_leaves_given_dates_alone() -> None:
    """`new Date()` and `Date.now()` move; a date built from a value does not."""
    probe = (
        "const t=new Date().getTime()-Date.now();"
        "console.log(new Date().getUTCFullYear(), Date.now()>1e12,"
        " new Date(0).getTime(), Math.abs(t)<5000)"
    )
    run = subprocess.run(
        ["node", "--import", str(HERE / "shiftdate.mjs"), "-e", probe],
        env={**os.environ, "SHIFT_DAYS": "3650"},
        capture_output=True,
        text=True,
        check=True,
    )
    year, now_ok, epoch, agree = run.stdout.split()
    today = subprocess.run(
        ["node", "-e", "console.log(new Date().getUTCFullYear())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(year) - int(today) in (9, 10)
    assert (now_ok, epoch, agree) == ("true", "0", "true")


def test_tests_that_wait_on_a_server_timer_are_marked() -> None:
    """A test that waits for Postgres to cancel it is left out of the clock check.

    libfaketime moves Postgres's timers along with its clock, so a statement timeout stops
    firing; an unmarked test would show up as a calendar failure that is not one.
    """
    unmarked = []
    for path in (REPO / "tests/integration").glob("test_*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            waits = any(
                isinstance(n, ast.Constant) and isinstance(n.value, str) and "pg_sleep" in n.value
                for n in ast.walk(node)
            )
            marked = any("server_timer" in ast.unparse(d) for d in node.decorator_list)
            if waits and not marked:
                unmarked.append(f"{path.name}::{node.name}")
    assert not unmarked


def test_readiness_is_probed_over_tcp() -> None:
    """The healthcheck `--wait` relies on must not be satisfied by the first-start server.

    That server listens on the socket only; a socket probe let migrations race it.
    """
    check = compose(HERE / "compose.yml")["services"]["postgres"]["healthcheck"]["test"]
    assert "-h 127.0.0.1" in " ".join(check)
    assert "--wait" in (HERE / "run.sh").read_text()


def snapdiff(tmp_path: pathlib.Path, before: str, after: str) -> subprocess.CompletedProcess:
    """Run the leak check's differ over two snapshot texts."""
    (tmp_path / "b").write_text(before)
    (tmp_path / "a").write_text(after)
    return subprocess.run(
        ["python3", str(HERE / "snapdiff.py"), str(tmp_path / "b"), str(tmp_path / "a")],
        capture_output=True,
        text=True,
    )


def test_an_unchanged_database_passes(tmp_path: pathlib.Path) -> None:
    """Equal snapshots print nothing and exit 0."""
    snap = 'sources rows 3\ntopic_config {"topic": "a", "weight": 0.5}\n'
    result = snapdiff(tmp_path, snap, snap)
    assert (result.returncode, result.stdout) == (0, "")


def test_the_differ_names_counts_nested_fields_and_rows(tmp_path: pathlib.Path) -> None:
    """A count, a key deep in a settings blob, and a row that appeared, each on its own line."""
    before = (
        "sources rows 3\n"
        'fetch_policy {"domain": "*", "settings": {"timeout_s": 30, "x": 1}, "by": "seed"}\n'
        'topic_config {"topic": "a", "weight": 0.5}\n'
    )
    after = (
        "sources rows 4\n"
        'fetch_policy {"domain": "*", "settings": {"timeout_s": 31, "x": 1}, "by": "seed"}\n'
        'topic_config {"topic": "a", "weight": 0.5}\n'
        'topic_config {"topic": "b", "weight": 0.1}\n'
    )
    result = snapdiff(tmp_path, before, after)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "fetch_policy *.settings.timeout_s: 30 -> 31",
        "sources rows: 3 -> 4",
        "topic_config b: added",
    ]


def test_the_snapshot_reads_every_table_and_the_configuration() -> None:
    """Counts come from the catalogue, so a new table is covered without editing the check."""
    sql = (HERE / "snapshot.sql").read_text()
    assert "information_schema.tables" in sql
    for table in ("topic_config", "agents", "budget_config", "fetch_policy", "scheduled_jobs"):
        assert f"FROM {table}" in sql, table
