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

## Mutation testing (`Q-02`)

Mutation testing changes the code (a `<` to `<=`, a constant, a dropped line) and runs the
tests; a change no test notices is a *surviving mutant*, an assertion nobody wrote. It is slow,
so it is scoped to pure modules with unit tests. Read survivors, not the score: some are
equivalent (the change cannot alter behaviour) and need no test.

- **Python** (`make mutate`, mutmut 3, `scripts/mutate.sh`). mutmut names a mutant by its
  file's path and expects the tests inside the directory it runs in, and this repo has neither:
  the package is at `packages/meridian_core/meridian_core`, and the editable install put the
  real package ahead of mutmut's copy on `sys.path`. Every test ran unmutated code, and mutmut
  reported that no test covered any mutant. The script builds a staging tree (`.mutate/`,
  ignored) with the package at the top and the tests and the files they read beside it, writes
  the mutmut config there, and runs it. `tests/_mutation_path.py` puts the mutated copy first
  when pytest runs inside `mutants/`. `fields.py` is left out: it finds its data file by
  counting directories up from itself. Pass mutant names to narrow a run
  (`scripts/mutate.sh "meridian_core.references*"`). Baseline, 2026-10-10:

  | module | killed | survived | score |
  |---|---|---|---|
  | references | 32 | 1 | 97% |
  | answer | 244 | 32 | 88% |
  | timefmt | 50 | 15 | 77% |
  | titles | 109 | 45 | 71% |
  | proposals | 154 | 77 | 67% |

  The first survivors read were real: no test pinned the reference filter's thresholds as
  inclusive (`>=` could become `>`); the one left is equivalent.
- **Web** (`cd web && npm run mutate`, StrykerJS, `web/stryker.config.json`). The vitest runner
  activates no mutant under vitest 5: in the dry run every test reported covering nothing, and
  with `coverageAnalysis: "all"` not one mutant of `answer.ts` was killed, which is why the first
  scores were implausible. The config uses Stryker's command runner instead, which selects each
  mutant through the environment and runs the test files that import the mutated modules (keep
  that list in `commandRunner.command` in step with `mutate`). Baseline, 2026-10-10 (timeouts
  count as killed):

  | module | killed | survived | score |
  |---|---|---|---|
  | position | 9 | 1 | 90% |
  | lastVisit | 11 | 2 | 85% |
  | time | 78 | 19 | 80% |
  | readable | 183 | 84 | 69% |
  | answer | 31 | 13 | 70% |

  `time.ts` scored 13% under the vitest runner, so those first scores were the setup. Runs in place, because the drift tests read the Python sources beside `web/`;
  Stryker restores each file at the end, so check `git status` if a run is killed.

## Property-based tests (`Q-03`)

`tests/unit/test_properties.py` states what must hold for any input, and Hypothesis looks for
the input that breaks it: reciprocal rank fusion scores exactly the ids it was given, does not
depend on which arm came first, ranks an id first in every arm highest and never lowers a score
for another arm; the per-source cap fills the page from what it was given and lets no source
past the cap while another's hits wait; the reference-list verdict does not depend on line
order or blank lines. `tests/unit/test_chunking_tables.py` generates tables and unbroken runs
for the chunker (`B-191`). `web/tests/readable-properties.test.ts` does the same for the
display's `readable()` with fast-check: it never throws or makes a passage longer, leaves plain
prose as it was, keeps a link's words and drops its address, and settles (reading its output
again changes nothing). The answer page's `group_hits` is covered too: without a naming
function every source counts once in each of its countries, or once unplaced; with one, a
several-country source counts only under countries a passage of it names, and those naming none
are counted as "several places"; strong groups come first.

## Continuous integration (`Q-03`)

`.github/workflows/ci.yml` runs on every push to main: it builds the project's Postgres image
(pgvector and AGE) and runs it as the dev stack does, applies the migrations, then runs
`make lint`, the whole Python suite and the web typecheck and tests. The environment is the
development stack's throwaway values, inline in the workflow. First green run 2026-10-10. Failing
test names are written as one annotation, readable through the public API without a token.

## Still to do

- Raise the coverage floors only after mutation scores say the covered code is actually checked.
