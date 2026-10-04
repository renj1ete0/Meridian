# The held-out question set

A fixed set of 20–30 questions, written without looking at the corpus. It is re-run against
the corpus to judge whether search and exploration are actually useful (§14.1's "only real
regression test", and `P2-09`'s go/no-go). The questions and their rules live in
[`eval/README.md`](../../eval/README.md) and `eval/questions.yaml`. This page covers the
machinery that runs them.

- **Code:** `packages/meridian_core/meridian_core/questionset.py`;
  `scripts/run_question_set.py`; the `question-set` gap source in `gaps.py`
- **Tasks:** `P0-15`, `P2-09`, `P2-22`, `P6-37`

## How it works

`scripts/run_question_set.py` loads the set, checks whether it may be scored (a draft set is
labelled as one), asks each question through `meridian_core.search.search` exactly as written,
and writes `eval/runs/<date>.yaml`. Each item carries the hits, a heuristic *proposed* grade,
and an empty operator grade. `compare` reads only operator grades, so a run nobody has graded
compares as "not graded", never as a number.

Gaps lists items that scored low in the newest run. Because a held-out question must never
steer the corpus, the only action offered is to search for it in Find.

## Rules that shape the code

- **A proposal is not a score.** The operator's grade is the score; the two are separate
  fields.
- **The loader never writes the questions.** Changing a question is a new version of the set.
- **Never use a question as a seed, benchmark query, gazetteer prompt or steering reason.**

## Operating it

- `python scripts/run_question_set.py` (dev environment exported) writes a run file; grade it
  by filling in the operator fields.
- On a deployment, the API reads runs from `MERIDIAN_EVAL_RUNS_DIR` (see
  [guides/deployment.md §7](../guides/deployment.md)).

## Tests

`tests/unit/test_questionset.py`, `test_eval_runs_mount.py`;
`tests/integration/test_question_set_run.py`.
