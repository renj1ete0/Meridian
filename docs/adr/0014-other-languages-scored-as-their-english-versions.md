# 0014. Pages in other languages are scored as their English versions would be

- **Status:** Accepted
- **Date:** 2026-10-07
- **Tasks:** `B-53`

## Context

The topic labeller compares a page's embedding with prototypes written in English. The same
page in another language scores lower against them. Pages paired with their English versions
by URL show the shape of the gap: it is not a constant. Off-topic pages lose nothing, pages in
the middle band lose a few hundredths, and the on-topic page in the sample lost enough to fall
under the label floor in every translation. Per-topic scores correlate 0.95–0.99 between the
two versions, so the order of topics survives translation and only the height does not.

The operator delegated the choice among four options: a lower floor for other languages, a
proportional rescale, per-language prototypes built from `translation_lookups` once `B-52`
writes them, or waiting for more non-English pages.

## Decision

Rescale. A non-English page's scores are stretched away from a pivot point,
`s' = 0.20 + 1.20 × (s − 0.20)`, capped at 1, at the moment they are computed, so they read as
the English version's would. English and unknown languages are unchanged, and a passage is
scored in its source's language. The scores stored are the rescaled ones, so every floor
(label, off-topic, triage) and every reader of `topic_scores` sees one scale without carrying
the language along. The constants are part of the basis fingerprint, so changing them
relabels every source once.

The pivot and stretch are a least-squares fit of the translated score against the English one,
over every topic of every paired page (918 pairs). After rescaling, the mean gap in every band
is within 0.005 of zero. The three topic labels lost in translation come back, and one pair
gains a label its English version does not have.

## Consequences

- Pages in other languages are labelled on the same terms as English ones, which also feeds
  the host gate and the per-topic counts in Gaps fairly.
- The raw score is not stored; it is recovered exactly by inverting the stretch.
- The top band rests on one clearly on-topic page in several translations, which is thin.
  Once `B-52` has written translations and non-English on-topic pages exist in number,
  re-measure; per-language prototypes may then replace the rescale.

## Alternatives considered

- **A lower floor for other languages.** It fixes the one comparison it is applied to, and no
  other: the margin between topics, the off-topic floor and the triage floor would each need
  their own adjustment, and a constant shift misdescribes a gap that grows with the score.
- **Per-language prototypes.** Likely the better answer, but it needs translated prototype
  text, and `translation_lookups` is empty until `B-52` runs.
- **Wait.** Leaves on-topic pages in other languages unlabelled, and so excluded from the
  host gate's view of which sites are on topic, until then.
