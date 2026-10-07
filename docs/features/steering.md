# Steering

Attention is a weight vector over topics, and the crawl draws its work in proportion to it
(§10). Steering changes those weights, adds temporary boosts, or pauses and archives topics.
It is driven by the operator, by Gaps and the Map, and by an hourly pass that proposes changes
and applies them unless someone objects. Steering adjusts what is generated; it never
deletes what was recorded (§2.5).

- **Code:** `packages/meridian_core/meridian_core/steering.py`, `steering_proposals.py`,
  `mapsteer.py`; `services/worker/worker/steerproposals.py`, `commands.py` (Telegram);
  `services/api/api/routes/steering_proposals.py`, `routes/admin.py`
- **Tasks:** `P6-12`, `P6-35`, `P6-38`, `B-64`

## How it works

**Weights.** Each active topic has a weight, a floor and a ceiling. Normalising fills the pool
by clamping and redistributing until every topic sits within its bounds, so a floor is never
silently violated when one topic dominates.

**Boosts** multiply a topic's weight until an expiry. They are applied at read time and never
cleared: an expired boost simply stops counting, so nothing has to remember to remove it.

**Status.** Paused, maintenance and archived topics leave the pool; their stored weight is
kept, so returning is a status change.

**Proposals** (`steerproposals`, hourly). For each active topic, the pass compares its share of
the last 24 hours' new on-topic sources with the share its weight gives it, and its yield (new
on-topic sources per fetch) with the crawl's. It proposes one of two changes:

- **starved** (produced well under its share, and more crawl would help): a ×1.5 boost for
  24 hours;
- **inefficient** (takes at least its share of fetches and yields under half the average): a
  small cut to its weight, at most 0.05 and at most a quarter of the weight, never below the
  floor, never for a thin topic.

A proposal waits for its window (`steering_proposal_window_hours` in the global policy row),
then applies itself unless rejected. Accepting applies it at once. Every change lands in
`steering_log`.

**From the map and Gaps**, steering uses the same calls (boost, seed), with the area or gap
recorded in the reason. See [map.md](map.md) and [gaps.md](gaps.md).

## Design choices

- **Silence is consent only while the basis holds.** A pending proposal is superseded, never
  applied, if its signal has gone, the topic was paused or pinned, someone else changed it,
  or its weight moved underneath it.
- **The operator's change wins.** A topic changed in the last 24 hours is left alone, so the
  same day's numbers cannot ratchet a weight down twice.
- **Pinned topics are never proposed for** (§10.1).
- **Cutting the over-producers was a mistake** (`B-64`). The first rule cut topics that
  produced *more* than their share, which meant cutting what the crawl did well. Low yield
  per fetch is the waste; volume is the goal.
- **Evidence floors.** With fewer than 100 fetches or 20 new sources in the window, the pass
  proposes nothing.

### Weights

**Normalising is not dividing by the total.** §10's floor ("5–10% minimum so nothing fully
stalls") and each topic's ceiling are both violated by proportional scaling the moment one
topic dominates, and a silently violated floor is the guarantee not existing. `normalise`
clamps and redistributes: each pass scales the still-free topics into what the clamped ones
left, pins anything outside its bounds, and runs again. It settles in at most one pass per
topic, since a pinned topic is never freed. All-zero weights are spread evenly first (a fresh
topic at 0 beside others at 0 shares rather than dividing by zero); an empty set returns
nothing.

**Bounds that cannot be met are relaxed on read, refused on write**, and the two relax
differently:

- *Ceilings yield* whenever they cannot reach 1.0. Pausing every topic but one leaves a single
  topic with a ceiling of 0.6, and the seeds still have to come from somewhere; a ceiling
  guards against crowding out others, and with none it constrains nothing.
- *Floors summing past 1.0* are scaled down proportionally, so every topic keeps a share in
  the promised ratio. The write paths refuse to create that configuration
  (`InfeasibleWeights`, said while the mistake is being made rather than found weeks later
  from a stalled crawl), but a read must not throw, because the screen that would fix it is
  the one reading.

**Exact values are refused, not clamped.** `set_weight` refuses a value outside the topic's
bounds: silently clamping a typed number shows a different one with no explanation, and the
bounds are what the person would need to change. `renormalise` takes `hold` to pin a topic at
the exact share typed, or setting 40% and then rescaling everything would show something else.

**A new topic starts at weight 0** and gets its floor from renormalisation: §10.2 calls adding a
topic "a small repeat of cold start", and a large share with no seeded sources is spent on
nothing.

**Boosts are a factor *and* an expiry, or nothing.** §10 makes decay the mechanism ("steer back
later without needing to remember"), so a factor without an expiry would be a permanent
multiplier wearing a temporary one's clothes; the write refuses it and the read ignores it. The
stored weight is untouched, so when a boost lapses there is nothing to restore, and no cleanup
job has to run.

**Archived and paused topics leave the pool** (§10.2) with their weight kept, so returning is a
status change, not a rebuild.

**Every change is logged with an actor and a reason, neither defaulted** (§10.1: with two
writers, the alternative is opening the UI in a month and not knowing why a weight is where it
is). A renormalisation logs every weight that moved, not only the one requested, since "why is
this topic at 0.18" is usually answered by a change to a different topic. A description change
moves no share but is logged too: it is most of what the labeller compares a page against, so
it re-labels every source.

### Drawing a topic

`draw_topic` (`B-26`) is where the vector meets the crawl: the next claim is filtered to a
topic chosen with probability equal to its share. Before it, nothing in acquisition read the
weights; the crawl claimed by priority and age alone, and since a discovered link inherits its
page's topic, whatever the crawl was working on produced more of itself. One early run ended
with nearly all of its frontier on the topic weighted *lowest*, because an early citation trail
went that way and nothing pulled it back. Claiming proportionally closes that loop: consumption
is what makes a topic's frontier grow.

An empty pool returns None, meaning claim without a topic filter rather than stop: a deployment
can briefly have no active topics (during a re-topic, before seeding), and stalling for it would
choose purity over the work. The generator is a parameter so a test can check proportions
exactly rather than statistically.

### Proposals

The operator's rule: *by default you propose what to steer, and if I don't select, it will be
steered that way.* §10.2 already has the system steer itself with nothing waiting for approval;
proposals are how it does so without surprising anybody. Each change is shown first in plain
words with its numbers, waits its window, then applies through `steering.py`, so it is logged
and undone like any other change.

**The signal is counted, and heuristic.** The worker never calls a model (§2.1). Fetches are
attributed to the topic that drew them (the queue task's topic); new sources to the topics their
content carries, which is what "on-topic" means everywhere else (`P2-21`), and copies of an
earlier source are not new. So a fetch drawn for one topic that lands a page about another counts
for the other; over a day that is the signal wanted: which topics' crawl turns into on-topic
pages at all.

- **Starved** needs both too little output (well under its share, or thin by Gaps' measure and
  behind) *and* a reason more crawl would help: the draw is not reaching it, or its fetches do
  yield. Boosting a topic whose fetches already find nothing spends more crawl finding nothing.
  A boost is the gentlest lever, since it removes itself.
- **Inefficient** lowers the baseline weight: that frees crawl for topics that turn it into
  sources. Raising a weight on a day's evidence would be permanent, and a boost already does
  that job temporarily.

**Bounds**, each against a failure: one proposal per topic per pass and one change per
proposal; at most one pending per topic and kind (a partial unique index); weights within floor
and ceiling, and a step of at most `MAX_WEIGHT_STEP` and `MAX_RELATIVE_STEP` of the weight;
boosts always expire, and a boosted topic is not boosted again; pinned, paused, archived and
maintenance topics are skipped; a topic anyone changed within `QUIET_HOURS` is left alone, so
evidence is measured after the last change (otherwise a weight lowered in the morning is
lowered again that evening on the same day's numbers and ratchets to its floor).

**Superseding is the safe direction.** A pending proposal this pass did not re-derive is
superseded: its signal is gone or the topic stopped being eligible. The cost is a restarted
window; the cost of not superseding is a change applied on evidence that no longer holds.

**Accept and reject** (`routes/steering_proposals.py`, writable role behind the admin gate):
accept applies now, logged as the operator's change; reject means it never applies, logged with
any reason given, and keeps the pass from proposing for that topic again the next hour.

## Operating it

- **Admin → Topic weights** sets weights, floors and status; **Admin → Pins & boosts** pins
  topics and ends boosts; **Admin → Proposals** accepts or rejects; **Admin → Seeds** queues
  searches by hand.
- From a phone, Telegram's `/weights`, `/boost`, `/pause` and `/seed` do the same
  ([operations.md](operations.md)).
- `python -m worker.steerproposals --report` shows what the pass would do, without writing.

## Tests

`tests/unit/test_steering.py`, `test_steering_proposals.py`, `test_proposals.py`;
`tests/integration/test_steering_proposals.py`, `test_map_steering.py`.
