"""The clock check imitates the dev database and never touches it (`B-147`).

`make clock-check` is only meaningful if its throwaway Postgres is the dev one with a moved
clock: the same image, roles and init script. And it must stay off the dev database's port and
project, or a run would wipe the developer's data with `down -v`.
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
    run = (HERE / "run.sh").read_text()
    assert "21111" not in run
    assert set(re.findall(r"localhost:(\d+)", run)) == ours


def test_the_make_target_runs_the_script() -> None:
    """`make clock-check` calls a script that exists and can be executed."""
    assert "./scripts/clockshift/run.sh" in (REPO / "Makefile").read_text()
    assert os.access(HERE / "run.sh", os.X_OK)


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
