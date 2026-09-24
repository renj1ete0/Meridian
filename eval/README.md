# Held-out question set

[`questions.yaml`](questions.yaml) is the question set spec §14.1 calls *"the only
real regression test"*: 20–30 questions, written once, re-run against the corpus
every month. It is also what `P2-09` uses to make the phase 2 go/no-go call (spec
§15: *is searching my own corpus already useful?*).

It is not [the ten design questions](../docs/design-questions.md). Those stress the
schema; these stress retrieval and exploration.

## Why it is held out

A question set written after seeing results describes the results. Graded against
itself, it will pass. So:

- **It was written without looking at the corpus.** No searches, API calls,
  database reads or crawled text. Only the spec, `config/topics.yaml`,
  `config/attributes.yaml` and the operator's stated goal.
- **It is never used to shape the corpus or the ranking.** Do not use these questions
  as seeds, benchmark queries (`scripts/benchmark_search.py`), gazetteer prompts or
  steering reasons, and do not tune search until a question passes. If a question
  fails, fix the system for the *kind* of question it is, then check whether the
  whole set moved.
- **Changing a question is a new version of the set.** Bump `set_version` and add an
  entry to `changes`. Scores are only comparable within one version.
- **`reviewed: false` means an agent drafted the item and the operator has not yet
  approved it.** The set is `status: draft` until every item is reviewed, then
  `frozen` before its first graded run. `reference_answer` is for the operator to
  fill in from their own knowledge, if they want to, before the first run and
  never after.

## What each item holds

| Field | Meaning |
|---|---|
| `kind` | lookup, evidence, connection, comparison, gap or contested |
| `topics` | the topics the question touches, from `config/topics.yaml` |
| `what_a_good_answer_contains` | criteria, not an answer: the kinds of source, the concepts that must appear, what must be shown, and `wrong_if` conditions |
| `grading` | which rubric applies; all rubrics use the same 0–3 scale, defined once at the top of the file |
| `notes` | why the question is in the set |

## Running the set

1. **Record the context:** date, `VERSION`, `set_version`, embedder model, source and
   chunk counts, and any schema or attribute changes since the last run (§14.1:
   track alongside schema changes, or a change in score cannot be attributed).
2. **Ask each question as a reader would.** In phase 2 that is the Explore
   surface alone: search, filters, node pages, path mode. Once a model is
   connected (phase 3 and later), run the set a second time through it and score
   the two separately. Record which mode each score belongs to.
3. **Time budget: 10 minutes per question.** What is not found in 10 minutes
   counts as not found. The bottleneck the spec names is reading time (§12.5).
4. **Score each item 0–3** using the general rubric, the rubric for its kind, and
   the item's `wrong_if` conditions. Any `wrong_if` that holds caps the item at 0.
5. **Write the run down** as `eval/runs/<date>.yaml`: for each id, the score, the
   sources that carried the answer, and one line on what was missing. A score
   without the line is not useful next month.

Grading is by the operator. An agent may run the set and propose scores, but its
scores are not the result.

**`scripts/run_question_set.py`** (`P2-22`) does steps 1, 2 and the mechanical
half of 5: it asks each question as typed through hybrid search (lexical-only
when no embedder answers, and the file says so), and writes
`eval/runs/<date>.yaml` with the context, the top hits per item, a heuristic
`proposed` grade, an empty `operator` field and a comparison with the previous
run. Fill `operator: {grade, missing, sources}` by hand; only those grades are
compared, and a grade off the 0–3 scale makes the next run refuse to compare.
While any item is `reviewed: false` every run carries a DRAFT banner.

`MERIDIAN_EVAL_RUNS_DIR`, when set, replaces `eval/runs/` for both the runner and
Gaps (`P6-37`). A deployment has no checkout, so there it names a mounted
directory the runner writes and the API reads (docs/deployment.md §7).

## The go/no-go (P2-09) — a proposal for the operator

The threshold is the operator's decision, and should be set **before** the first
graded run for the same reason the questions are. Proposed:

- **Go** if all hold:
  - mean score across all 30 items is at least 1.5
  - at least 4 of the 8 connection items score 2 or more; connection is the
    operator's reason for the system
  - no gap item scores 0; a corpus that makes absence look like an answer is
    worse than a thin one
  - every topic has at least one item at 2 or more
- **Marginal** if the mean is 1.0–1.5, or exactly one of the conditions above
  fails. `TASKS.md` makes an embedder comparison conditional on this case, and
  it is a full re-embed: look at which *kind* failed before changing anything.
- **No-go** if the mean is below 1.0, or more than one condition fails.

## Month to month (§14.1)

Rerun the frozen set monthly. Report the mean by kind and by topic next to the
previous run. Proposed **regression** flag: the mean for any kind drops by 0.5 or
more, or any single item drops by 2 points. Read the flagged items' run lines
before anything else.
