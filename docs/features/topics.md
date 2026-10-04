# Topics

A topic is a subject the operator wants researched, described in Admin by a name, a
description and approved vocabulary. Every source, and every passage, is labelled with the
topics its *content* is about: zero, one or several. Labels drive the topic filter in search,
Gaps, the map's shading, host scores and steering. They are computed from vectors with
arithmetic, never by a model.

- **Code:** `packages/meridian_core/meridian_core/topiclabels.py`, `passagetopics.py`,
  `topicoverlaps.py`; `services/worker/worker/retopic.py`
- **Tasks:** `P2-14`, `P2-21`, `P2-24`, `B-72`, `B-83`, `B-89`
- **Decisions:** [ADR 0006](../adr/0006-stricter-triage-for-very-long-documents.md)

## How it works

1. Each topic gets a **prototype**: the embedding of a short text built from its name,
   description and approved vocabulary.
2. Each source is represented by the normalised mean of its live passage vectors. Passages
   that the novelty gate marked as duplicates are left out, because those are usually the
   site's navigation.
3. Both are measured from a **fixed reference point**: the mean embedding of a list of
   generic, topic-free phrases. Subtracting it removes the large component every page shares,
   which widens the gap between on-topic and off-topic.
4. A source carries every topic whose score clears `LABEL_FLOOR` (0.50) and lies within
   `LABEL_MARGIN` (0.04) of its best topic.
5. Passages are scored the same way on their own vectors (`P2-24`). A passage that is mostly
   link labels (`LISTING_SHARE`) is examined and labelled `{}`.

`sources.crawled_for` records *why* a page was fetched. `sources.topic_labels` records what
it *says*. Only `retopic` writes the second.

**NULL and `{}` are different.** NULL means not examined yet. `{}` means examined and about
none of the topics.

**Labels go stale by fingerprint.** Each label records the *basis* it was computed under: the
model, the thresholds, the reference, and every prototype's text. Adding, archiving or
re-describing a topic changes the basis and re-queues every source. A re-crawl re-queues one
source. No stored vectors need invalidating.

<a id="triage-of-long-documents"></a>**Triage of long documents** (`B-89`). A source is
normally labelled only once every passage has a vector. A long document is first embedded
from a sample (its opening passages and every 16th after them) and labelled from that,
provisionally (`topic_sample_best` is set). The rest is embedded early only if the sample's
best score is at least `TRIAGE_FLOOR` (0.46, below the label floor because a sample misreads
the whole by a few hundredths). Otherwise it waits in the last embedding tier. When the whole
text has been embedded, the labels are read again from all of it.

Documents of `LONG_DOCUMENT` (1,000) live passages or more need `LONG_TRIAGE_FLOOR` (0.48)
instead (`B-133`, ADR 0006). Long listings of titles beat the ordinary floor with a single
matching line. Measured on fully embedded sources of that length, the higher bar held back no
on-topic document and clearly more off-topic text. Length is tested by whether a live passage
exists at index 999 or beyond, which is one index probe. That doubled the cost of the backlog
count (run once a minute) and left the tier queries unchanged. `triage_floor()` and
`long_sources()` are the one definition, shared by the hold rule and the `retopic` report.

**Overlaps** (`B-72`). Because a source can carry several topics, the corpus is a web rather
than a partition. `topicoverlaps` counts sources by exact combination, and search can require
all selected topics (`topic_match=all`).

## Design choices

- **A fixed reference, not the corpus mean.** The corpus mean was measured and rejected: it
  would make one source's labels depend on everything else in the corpus, so a month spent on
  one topic would quietly move every other label.
- **Floor and margin were calibrated** against a hand-checked set. The floor rose from 0.45
  to 0.50 (`B-83`) after generic official pages were found in the band just above 0.45. The
  calibration notes live in the separate calibration repository.
- **Passages use the source floor, not a lower one.** Single passages are noisier, and the
  band below the floor was mostly listings and abstracts from adjacent fields.
- **`--demote-offtopic` is never scheduled.** Junking a source hands it to the retention
  sweep, so it is a decision a person makes. Sources labelled only from a sample are never
  demoted.

## Configuration

- Topics, descriptions, vocabulary and status (active, paused, maintenance, archived):
  **Admin → Topic weights**.
- Thresholds are constants in `topiclabels.py`. Changing one changes the basis, so every
  source is relabelled on the next `topics` run (about minutes, not hours).

## Operating it

- Job: `topics` (hourly, `worker.retopic --apply`). It labels sources first, then passages.
- `python -m worker.retopic` alone prints a report: how many were labelled, examples near the
  floor, and "from a sample N, the rest held back M".

## Failure modes and traps

- **Generic pages near the floor.** Official landing pages and event pages can score just
  above the floor. Better descriptions or a higher floor are the levers. Measure with the
  report before moving the floor.
- **The colour-token test scans tests too.** Use palette names, not hex values, for fake
  topic colours.

## Tests

`tests/integration/test_topic_labeller.py`, `test_passage_topics.py`,
`test_topics_on_sources.py`, `test_topic_draw.py`, `test_admin_topics.py`;
`tests/unit/test_passagetopics.py`.
