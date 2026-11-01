# 0026. Strata is the recommended local model for synthesis, held to the answer's schema

- **Status:** Accepted
- **Date:** 2026-11-02
- **Tasks:** `B-247`, `B-259`

## Context

The GPU machine serves two candidate language models for the local synthesis agent: a Qwen3 27B
(IQ4_XS, llama.cpp) and Strata (IQ3_XXS). The operator judged Strata the better and faster of the
two and asked for a test before switching. Unheld, Strata had run away on long batches, writing
past the token limit; since `B-247` the local agent sends the answer's schema, which removes that
failure.

## Decision

The deployment guide recommends Strata as the model of the `local-llamacpp` agent, with thinking
off and `structured: schema`. The 27B remains a supported choice. Which model a server exposes
stays an operator setting (Admin → Agents), not a migration.

Measured on 2026-11-02 through the GPU machine, both models held to the schema with thinking off,
on the same six batches of 10, 20 and 40 crawled passages:

| | Strata | Qwen3 27B |
|---|---|---|
| Extraction, wall time for six batches | 58 s | 129 s |
| Relations accepted | 40 | 48 |
| Relations a reader judged not stated by the cited passage | about 3 | about 3 |
| Tag assignments | 25 in 33 s; several on a loosely fitting attribute | 6 in 40 s; most answers empty |

## Consequences

- Extraction runs roughly twice as fast at similar precision, so a GPU window covers twice the
  batches.
- Strata tags more readily and less carefully: it puts a value on the nearest attribute where the
  27B answers nothing. Tagging precision is the place to watch; the attribute gate (`P7-01`) and
  routine precision sampling (`P7-10`) are where it is measured.
- The sample is small (six batches, one deployment's corpus). A larger graded comparison should
  revisit this if precision sampling shows the tag error rate rising.

## Alternatives considered

- **Keep the 27B.** Precision on extraction was no better, at twice the time.
- **Strata for extraction, the 27B for tagging.** Needs two local agents routed by task type, and
  two models resident on one card; not worth it on six batches of evidence.
