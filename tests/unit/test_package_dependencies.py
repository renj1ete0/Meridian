"""A workspace package declares what it imports (task B-13).

This is the third time the same bug has been found, and each time by *running* a
container rather than by building one:

- `P1-30`: `meridian_core.policy` imports `yaml`, and `pyyaml` was declared on
  the root project. Development never noticed, because a root install provides
  it to everything.
- `B-13`: `meridian_core.embedder` imports `httpx`, which was declared on
  `meridian-worker` — deliberately, on the reasoning that an HTTP client had no
  business in the core package's closure. That was true until `P2-17` put the
  sidecar *client* in core. The API image died at import.
- `B-13`: `worker.embedserver` imports `fastapi`, which is in the `embed` extra
  the worker image did not install. The sidecar had never been started from a
  container at all.

The handover predicted the second one — "worth remembering when `services/api`
and `services/orchestrator` get images" — and predicting it was not enough. So
this asserts it instead.

**Imports are read from the source, not from a list.** A test listing known
dependencies would be updated to match whatever was there; this one derives both
sides and fails when they disagree.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


#: Workspace members, from the root project rather than a glob — `[tool.uv.
#: workspace].members` is the list that decides what `uv sync --package` can be
#: asked for, so it is the list that matters.
def workspace_members() -> list[Path]:
    root = tomllib.loads((REPO / "pyproject.toml").read_text())
    members: list[Path] = []
    for pattern in root["tool"]["uv"]["workspace"]["members"]:
        members.extend(sorted(p for p in REPO.glob(pattern) if (p / "pyproject.toml").exists()))
    return members


#: Import name → distribution name, where they differ. Kept small on purpose: a
#: large alias table is a sign the test is guessing. Every entry here is a real
#: package this repository depends on and spells differently when importing it.
DISTRIBUTION = {
    "yaml": "pyyaml",
    "sqlalchemy": "sqlalchemy",
    "pgvector": "pgvector",
    "dotenv": "python-dotenv",
    "sentence_transformers": "sentence-transformers",
    "dateutil": "python-dateutil",
    "bs4": "beautifulsoup4",
    "jwt": "pyjwt",
}

#: Imports that resolve to something other than a declared dependency, with the
#: reason. An entry is a claim that the import cannot fail in the image that
#: runs it, and that claim should be checkable by reading the line next to it.
EXEMPT = {
    # Guarded by try/except ImportError with an actionable message, and only
    # reached on the path that needs it (`uv sync --extra ner`).
    "spacy",
    # Re-exported by a dependency that does declare it; importing it directly is
    # the documented way to use the library.
    "typing_extensions",
}


def declared_for(package_dir: Path) -> set[str]:
    """Everything a package may import: its own dependencies, its extras, and
    the workspace siblings it depends on, transitively."""
    data = tomllib.loads((package_dir / "pyproject.toml").read_text())
    project = data.get("project", {})

    names: set[str] = set()
    specs = list(project.get("dependencies") or [])
    for extra in (project.get("optional-dependencies") or {}).values():
        specs.extend(extra)

    for spec in specs:
        # "sqlalchemy[asyncio]>=2.0.36" → "sqlalchemy". Underscores and hyphens
        # are the same name to packaging (PEP 503) and different to `import`, so
        # both sides are normalised rather than aliased one at a time.
        bare = re.split(r"[<>=!\[;\s]", spec, maxsplit=1)[0].strip().lower()
        names.add(bare.replace("_", "-"))

    # A sibling's dependencies are available transitively, which is legitimate:
    # `meridian-worker` depends on `meridian-core`, so it may import what core
    # declares. This is the one place a missing declaration is *not* a bug.
    for sibling in workspace_members():
        sibling_name = tomllib.loads((sibling / "pyproject.toml").read_text())["project"]["name"]
        if sibling_name.lower() in names:
            names |= declared_for(sibling)

    return names


def imports_in(package_dir: Path) -> set[str]:
    """Top-level module names imported anywhere in a package's source."""
    found: set[str] = set()
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def third_party(names: set[str], package_dir: Path) -> set[str]:
    """Drop the standard library and the package's own modules."""
    own = {p.name for p in package_dir.iterdir() if p.is_dir() and not p.name.startswith(".")}
    return {
        name
        for name in names
        if name not in sys.stdlib_module_names
        and name not in own
        and not name.startswith("_")
        and name not in EXEMPT
    }


def test_the_workspace_has_members_to_check() -> None:
    """Guard on the parse: an empty member list would make every assertion below
    vacuously true, which is how a drift test stops testing anything."""
    members = workspace_members()

    assert len(members) >= 3, members
    assert any(m.name == "meridian_core" for m in members)


@pytest.mark.parametrize("package_dir", workspace_members(), ids=lambda p: p.name)
def test_every_import_is_declared_by_the_package_that_makes_it(package_dir: Path) -> None:
    """The rule `P1-30` wrote down and `B-13` had to learn twice more.

    A container built with `uv sync --package X` gets X's declared closure and
    nothing the root project happens to also pull in. So an import that only
    works because something *else* installed the module is an import that dies
    at startup in the image — and building the image does not catch it, because
    nothing imports anything at build time.
    """
    declared = declared_for(package_dir)
    used = third_party(imports_in(package_dir), package_dir)

    undeclared = {
        name
        for name in used
        if DISTRIBUTION.get(name, name).lower().replace("_", "-") not in declared
    }

    assert not undeclared, (
        f"{package_dir.name} imports these and does not declare them: "
        f"{sorted(undeclared)} — a container built with "
        f"`uv sync --package` will die at import"
    )
