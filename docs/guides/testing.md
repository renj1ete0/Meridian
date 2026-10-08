# Testing: coverage and mutation testing

The suite (`make test`) is the gate. These two measure how good it is: coverage says what the
tests run, mutation testing says whether they would notice the code being wrong. AGENTS.md
"Testing" says what a good test is; this page is how to measure the suite.

## Coverage (`Q-01`)

- `make coverage` runs the whole Python suite with branch coverage over `meridian_core`,
  `worker` and `api` (config: `[tool.coverage]` in `pyproject.toml`). It needs the dev
  database, as `make test` does.
- `cd web && npm run coverage` does the same for the web package (V8, config in
  `vite.config.ts`).

Baseline, 2026-10-08: Python 90.7% (statements and branches together; branches 4,293 of
5,014). Web 78.8% statements, 75.0% branches. The least covered Python files are the scheduled
jobs' command-line wrappers (`worker/sweep.py`, `digest.py`, `hostscore.py`), whose logic lives
in `meridian_core` and is covered there; `run_sweep` itself now has a dry-run test, because it is
the only path that deletes. The least covered web files draw with WebGL (`GraphCanvas`,
`Scene3D`), which jsdom cannot run; `DisplayPanel` and `NodeSearchBox` are real gaps.

A percentage is a floor to hold, not a target to chase: a test written to raise it tends to
execute code without asserting anything, which is exactly what mutation testing catches.

## Mutation testing (`Q-02`) — set up, not yet trustworthy

Mutation testing changes the code (a `<` to `<=`, a constant, a dropped line) and runs the
tests; a change no test notices is a *surviving mutant*, an assertion nobody wrote. It is slow,
so it is scoped to pure modules with unit tests.

- **Web** (`cd web && npm run mutate`, StrykerJS, `web/stryker.config.json`). Runs in place,
  because the drift tests read the Python sources beside `web/`; Stryker restores each file at
  the end, so check `git status` if a run is killed. TypeScript 7 has no JavaScript API, so the
  tsconfig rewrite is skipped by pointing `tsconfigFile` at a path that does not exist. First
  run, 2026-10-08: 34% overall, but `time.ts` scored 13% and `answer.ts` 0% despite dense
  tests, which points at the setup (per-test coverage attribution) rather than the tests.
  Verify with `"coverageAnalysis": "all"` before reading anything into the scores.
- **Python** (`make mutate`, mutmut 3, `[tool.mutmut]` in `pyproject.toml`). Configured for six
  pure modules and their unit tests, but **not working yet**: mutmut runs from a copy under
  `mutants/`, and the tests import the installed `meridian_core` (a workspace editable install)
  rather than the mutated copy, so mutmut reports that no test covers any mutant. Next step:
  make the copy win on `sys.path` (for example `PYTHONPATH=mutants/packages/meridian_core` via
  `pytest_add_cli_args`, or running mutmut inside the package directory).

## Still to do

- Property-based tests (Hypothesis is installed) for the pure functions whose invariants matter:
  `references.is_reference_list`, `search.fuse` / `cap_per_source`, `answer.group_hits`, and
  the web's `readable()` (with fast-check).
- A CI workflow running lint, the unit tests and the web tests on every push.
- Raise the floors only after mutation scores say the covered code is actually checked.
