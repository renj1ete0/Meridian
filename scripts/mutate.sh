#!/usr/bin/env bash
# Mutation testing of meridian_core's pure modules (`Q-02`); see docs/guides/testing.md.
#
# mutmut names a mutant by its file's path and expects the tests inside the directory it runs
# in, so it runs in a staging tree laid out the way it expects: the package at the top, the
# tests and the files they read beside it. Copies, so a killed run leaves the repo untouched.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
stage="$root/.mutate"
rm -rf "$stage"
mkdir -p "$stage"
cp -r "$root/packages/meridian_core/meridian_core" "$stage/meridian_core"
for path in tests config docs eval VERSION AGENTS.md; do cp -r "$root/$path" "$stage/$path"; done
cat > "$stage/pyproject.toml" <<'TOML'
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["tests"]
addopts = "-p no:logging --import-mode=importlib"

[tool.mutmut]
source_paths = ["meridian_core"]
only_mutate = [
    "meridian_core/references.py",
    "meridian_core/answer.py",
    "meridian_core/titles.py",
    "meridian_core/proposals.py",
    "meridian_core/timefmt.py",
]
pytest_add_cli_args_test_selection = [
    "tests/unit/test_answer.py",
    "tests/unit/test_proposals.py",
    "tests/unit/test_references.py",
    "tests/unit/test_timefmt.py",
    "tests/unit/test_titles.py",
]
also_copy = ["tests", "config", "docs", "eval", "VERSION", "AGENTS.md"]
mutate_only_covered_lines = true
TOML
cd "$stage"
uv run --project "$root" mutmut run "$@" || true
uv run --project "$root" mutmut results
