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

### Content, not provenance

`P2-14` gave sources a topic from the queue topic that caused the fetch plus whatever the URL
path matched. That is provenance, and it is wrong about content exactly where it matters: a
crawl pursuing one topic follows a site's navigation into pages about something else, and every
one was stamped with the topic pursued. Since `P2-21`, provenance is `crawled_for` (which
accumulates) and `topic_labels` answers *which topics is this text about*; `retopic` is the only
writer, and each examination replaces the previous labels rather than adding to them. A page
comparing two topics is about both. A source with no embedded text stays NULL: calling it
off-topic would be a claim about text nobody has read. Labels are ordered best first, ties by
name, because a consumer that shows one (a map colours a point once) should show the one the
text is most about.

**Why a reference point.** Raw cosine between these embeddings is compressed into a narrow
band: every page shares a large common component ("this is a web page"), so an unrelated page and
an on-topic one differ by a few hundredths. The reference is the mean embedding of a fixed list of
generic phrases (navigation, boilerplate, the vocabulary of being a document) and depends on
nothing but the model. Changing the list changes the basis and re-examines every source, the
right cost for moving the origin everything is measured from.

**Prototypes** use approved, unrejected gazetteer terms carrying the topic: canonical forms
always, aliases only when the term is unambiguous and the alias is long enough (shorter ones are
mostly acronyms, which an embedder reads as noise or as another expansion; the same floor as the
URL matcher's). An unapproved term is a proposal awaiting a person (§5.6) and does not get to
move labels.

**Duplicate chunks are left out of a source's mean** where it has any others: what the novelty
gate marked duplicate is mostly navigation and footer repeated on every page, the part that says
nothing about the subject. A source made *only* of duplicates falls back to them rather than going
unlabelled; such sources separate far worse (AUC ~0.75) and are also the ones search hides.

### Calibration

The first calibration used a real crawled corpus of a few thousand sources, all embedded and
scored against prototypes built as now. A title-and-URL heuristic gave a silver set: pages naming
a topic outright as positives, and pages plainly about nothing the corpus covers (clinical
condition pages, court rules, privacy and contact pages, unrelated academic listings) as
negatives.

- Area under the ROC curve ~0.985 with the reference point, ~0.97 without.
- At the floor ~80% of positives keep their topic and under 1% of negatives gain one. The band
  just below (0.42–0.45) was, read side by side, mostly institutional landing pages, publication
  listings and legal indexes mentioning a topic among many, so the floor sits above them rather
  than at the best-balanced-accuracy point near 0.40, which let about one negative in twenty
  through.

Re-measured on the live corpus (`B-83`), which the silver set did not resemble: it held no
generic government pages, and a crawl of government sites is mostly those. Judged by reading,
sources whose best score sat in 0.45–0.48 were right about one time in six, 0.48–0.50 one in
three, 0.50–0.52 about half, and 0.55 and over every time sampled. Agency "about" pages, speeches,
tax and careers pages share a topic's vocabulary without being about it. At 0.50 each true label
given up removes nearly four false ones; at 0.52 the trade is about even, so the floor stops at
0.50.

- **Margin.** Adjacent topics in one field score close together on a page about either; inside
  the margin the page was, when read, usually about both, and past it the second score was the
  field's shared vocabulary. A wider margin admitted third labels on generic pages.
- **`OFFTOPIC_FLOOR`**, the only threshold `--demote-offtopic` reads (and overridable there), is
  clearly under the label floor, because labelling is re-derived every pass while a demotion
  hands a source to the retention sweep. On the calibration corpus every sampled source under it
  was off-topic by content; the few silver positives under it were search-result, bot-wall or
  error pages whose *titles* named a topic. Candidates are only sources labelled under the
  current basis (an old score says nothing about now), not already junk, and not labelled from a
  sample: junk is never embedded, so demoting on part of a text would stop the rest from ever
  being read.
- **`TRIAGE_FLOOR`** (`B-89`) sits under the label floor because a sample misreads the whole by a
  few hundredths: on a live corpus, holding at the label floor would have held back about one
  on-topic long document in ten, and at this line about one in fifty (none over 300 passages),
  while still holding back most off-topic ones. Holding orders embedding and deletes nothing, so a
  miss costs a delay. It is not part of the basis: it decides what is embedded next, never a
  label.

Re-measure before moving any of these; `python -m worker.retopic` prints the distribution it saw.

### The labelling pass

`retopic` is a pass, not a fetch-time step, for the reason the novelty gate is: vectors arrive
after the fetch. A source is examined once its *sample* is embedded (`B-89`); before that, waiting
for every chunk made a long document's labels, and so whether it was worth embedding, cost the
whole document. The sample spans the text rather than being whichever half embedded first, which
is what waiting had guarded against. Its labels are provisional and are read again once the whole
text is embedded. The queue is a predicate (unexamined, a different basis, rewritten since, or
sampled and now whole), so a killed pass keeps what it committed; a topic change re-queues every
source at the cost of one aggregate query per batch and a matrix product.

<a id="passages"></a>**Passages** (`P2-24`). A source's labels come from the mean of its chunks:
right for a web page, coarse for a report or a book, where a chapter on another topic is invisible
to a topic filter and to Gaps. So each live embedded chunk is scored on its own vector, against the
same prototypes and reference, leaving source labels untouched. A passage is noisier than a
document mean (averaging cancels each chunk's incidental vocabulary): measured on a real crawl,
passage scores sit lower and spread wider, and the 0.40–0.45 band in documents *not* about a topic
was mostly noise, while above it were mostly passages genuinely about the topic inside a document
about a neighbouring one, which is what this exists to find. So passages use the source floor and
margin. A passage mostly of link labels (a directory of regulations, a publication list) is a
listing labelled `{}`: its vector is the average of what it links to, and it scored topics none of
its entries is about; this structural rule removed most false positives the floor did not.

A passage row records the passage basis (the source basis plus the passage thresholds, so moving
one of those re-labels passages without touching sources) and the embedding view the vector came
from, so a re-embed re-queues it. A re-crawl's new chunks have no row until they have a vector;
superseded chunks keep theirs harmlessly and lose it with the chunk. Passages run second in the
same hourly pass: a sibling job would embed the prototypes twice and could label under two bases
in the minute a topic changed. `topic_match=all` asks that the passage's labels and its source's
together carry every topic: a chapter on one topic inside a document labelled with the other is
where two topics genuinely meet.

**Overlaps** count searchable sources (no junk, no copies, examined and on a topic) by source
labels, since a count of documents is what a person weighing "is there anything here" reads.

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
