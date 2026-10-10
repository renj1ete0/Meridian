# The held-out question set

A fixed set of questions, written without looking at the corpus. It is re-run against
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

## Current state

The set is a draft (`status: draft`, set version 2). The operator's own questions are reviewed by
definition, though the grading criteria under them are agent drafts; the agent-drafted items
still await review (`P0-15`). The runner is built (`P2-22`), but no graded run against the live
corpus has been made, so `P2-09`'s go/no-go is still open.

## Rules that shape the code

- **A proposal is not a score.** The operator's grade is the score; the two are separate
  fields.
- **The loader never writes the questions.** Changing a question is a new version of the set.
- **Never use a question as a seed, benchmark query, gazetteer prompt or steering reason.**
- **An unreviewed set says so.** Items drafted with `reviewed: false` make a draft, and a run
  against it is labelled as one rather than treated as the go/no-go set.
- **Questions are asked verbatim.** Building queries from an item's concepts would tune
  retrieval to the test.

### The proposed grade

The heuristic sees only whether relevant text is present, not whether an answer is assembled or
right, so it is capped at 2: a 3 needs a reader. Gap items (questions whose honest answer is
that the corpus lacks it) are graded inversely on topic presence and capped at 1, because the
heuristic cannot tell "absence is evident" from "the search failed", and a gap item scoring well
by accident is the failure `eval/README.md` warns about.

### The graph's part

The question text is not a node name, so asking the graph the question directly would measure
string matching. What the graph can honestly add is whether the passages search found are
already cited: by a claim (an edge, where derived evidence lives) or by a node (a note).

### Lexical-only runs

The vector arm runs when the embedding service answers. Otherwise the run says it is
lexical-only, per item and in `context.mode`: on a whole question `websearch_to_tsquery` ANDs
every word and returns almost nothing, and a run that hid that would read as a corpus that knows
nothing.

## Operating it

- `python scripts/run_question_set.py` (dev environment exported) writes a run file; grade it
  by filling in the operator fields.
- On a deployment, the API reads runs from `MERIDIAN_EVAL_RUNS_DIR` (see
  [guides/deployment.md §7](../guides/deployment.md)).

## Tests

`tests/unit/test_questionset.py`, `test_eval_runs_mount.py`;
`tests/integration/test_question_set_run.py`.
