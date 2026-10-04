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
