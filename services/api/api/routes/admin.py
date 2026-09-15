"""`/api/admin/*` — the control surface (task P6-13, spec §12.6, §5.6).

The first routes in this service that change anything, which is why the shape is
conservative.

**One writable session, gated once.** `WriteSession` and `AdminAllowed` both
come from `deps`, so a route added under this prefix cannot get a session
without also getting the gate — and a route under `/api/explore` cannot get one
at all. §12.6's whole deferred-auth plan rests on the prefix being the role
boundary, and that only holds if nothing crosses it.

**Approving a term reports what the matcher will do with it.** §5.6 loads
approved rows into the `EntityRuler`, and a row two other rows collide with is
withheld — so a curator can approve a term, see it marked approved, and never
see it match anything. The compiler is the only thing that knows, so the answer
comes back with the decision rather than being discoverable by noticing an
absence months later.

**Nothing here deletes.** A rejected term stays as a tombstone, because the
harvest reads the same documents again: a deleted row is re-created by the next
pass, and the queue refills with exactly what somebody already turned down.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from meridian_core import steering
from meridian_core.gazetteer import loading_report
from meridian_core.logging import get_logger
from meridian_core.models import GazetteerTerm, SteeringLog
from meridian_core.schemas.admin import (
    GazetteerQueueRead,
    GazetteerRowRead,
    GazetteerTermEdit,
    SteeringLogPage,
    TopicAdd,
    TopicEdit,
    TopicRowRead,
    TopicsRead,
)
from meridian_core.schemas.config import SteeringLogRead, TopicConfigRead
from meridian_core.schemas.gazetteer import GazetteerTermRead

from ..deps import AdminAllowed, WriteSession

log = get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

QueueState = Literal["pending", "approved", "rejected", "all"]


def _state_filter(state: QueueState):
    """The three states `approved` and `rejected_at` encode between them."""
    if state == "pending":
        return (GazetteerTerm.approved.is_(False), GazetteerTerm.rejected_at.is_(None))
    if state == "approved":
        return (GazetteerTerm.approved.is_(True),)
    if state == "rejected":
        return (GazetteerTerm.rejected_at.is_not(None),)
    return ()


async def _counts(sess) -> tuple[int, int, int]:
    """How many are in each state, unfiltered.

    Unfiltered on purpose (`P6-08`'s reasoning): a filtered list whose counts are
    also filtered cannot tell a curator that the forty terms they came for are
    one tab over, so they conclude there are none.
    """
    row = (
        await sess.execute(
            select(
                func.count().filter(
                    GazetteerTerm.approved.is_(False), GazetteerTerm.rejected_at.is_(None)
                ),
                func.count().filter(GazetteerTerm.approved.is_(True)),
                func.count().filter(GazetteerTerm.rejected_at.is_not(None)),
            )
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


async def _rows_for(sess, terms: list[GazetteerTerm]) -> list[GazetteerRowRead]:
    """Attach each term's verdict, computed against the whole approved set.

    The approved set is loaded even when the page shows none of it, because a
    collision is a fact about two rows and the other one is usually not on this
    page. Cheap: §5.6's table is hundreds of rows, not millions, and it is the
    same read the worker does at startup.
    """
    approved = list(
        await sess.scalars(select(GazetteerTerm).where(GazetteerTerm.approved.is_(True)))
    )
    by_id = {term.term_id: term for term in (*approved, *terms)}
    report = loading_report(list(by_id.values()))

    out = []
    for term in terms:
        verdict = report[term.term_id]
        out.append(
            GazetteerRowRead(
                term=GazetteerTermRead.model_validate(term),
                will_load=verdict.will_load,
                withheld_reason=verdict.reason,
                collides_with=list(verdict.collides_with),
            )
        )
    return out


@router.get("/gazetteer", response_model=GazetteerQueueRead)
async def gazetteer_queue(
    _: AdminAllowed,
    sess: WriteSession,
    state: Annotated[QueueState, Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> GazetteerQueueRead:
    """The approval queue, most corroborated first.

    Ordered by `occurrence_count` because that is the curator's own triage: a
    term forty documents defined the same way is worth two seconds, and one
    document's typo is worth none. `term_id` breaks ties so paging is stable —
    without it two rows with equal counts can swap between pages and one is
    never seen.
    """
    statement = (
        select(GazetteerTerm)
        .where(*_state_filter(state))
        .order_by(GazetteerTerm.occurrence_count.desc(), GazetteerTerm.term_id)
        .limit(limit + 1)
        .offset(offset)
    )
    found = list(await sess.scalars(statement))
    has_more = len(found) > limit
    pending, approved, rejected = await _counts(sess)

    return GazetteerQueueRead(
        rows=await _rows_for(sess, found[:limit]),
        limit=limit,
        offset=offset,
        has_more=has_more,
        pending=pending,
        approved=approved,
        rejected=rejected,
    )


async def _term(sess, term_id: int) -> GazetteerTerm:
    term = await sess.get(GazetteerTerm, term_id)
    if term is None:
        raise HTTPException(status_code=404, detail=f"No gazetteer term {term_id}.")
    return term


async def _decided(sess, term: GazetteerTerm) -> GazetteerRowRead:
    """Commit, then report what the matcher will do with the row as it now is."""
    await sess.commit()
    await sess.refresh(term)
    return (await _rows_for(sess, [term]))[0]


@router.post("/gazetteer/{term_id}/approve", response_model=GazetteerRowRead)
async def approve_term(term_id: int, _: AdminAllowed, sess: WriteSession) -> GazetteerRowRead:
    """Let this term override statistical NER.

    Clears any rejection, because approving is the reversal: leaving the
    tombstone would make the row approved *and* rejected, and the harvest reads
    the tombstone — so the term would load into the matcher while the pass that
    found it went on treating it as thrown away.
    """
    term = await _term(sess, term_id)
    term.approved = True
    term.rejected_at = None
    row = await _decided(sess, term)
    log.info(
        "gazetteer term approved",
        extra={"term_id": term_id, "will_load": row.will_load, "withheld": row.withheld_reason},
    )
    return row


@router.post("/gazetteer/{term_id}/reject", response_model=GazetteerRowRead)
async def reject_term(term_id: int, _: AdminAllowed, sess: WriteSession) -> GazetteerRowRead:
    """Turn this term down, and keep the row so it stays down.

    The row is not deleted. §5.6's harvest reads the same documents on every
    pass, so a deleted row is re-created by the next one and the queue refills
    with what a curator already rejected — which is how an approval queue becomes
    something nobody opens.
    """
    term = await _term(sess, term_id)
    term.approved = False
    term.rejected_at = dt.datetime.now(dt.UTC)
    log.info("gazetteer term rejected", extra={"term_id": term_id})
    return await _decided(sess, term)


@router.post("/gazetteer/{term_id}/restore", response_model=GazetteerRowRead)
async def restore_term(term_id: int, _: AdminAllowed, sess: WriteSession) -> GazetteerRowRead:
    """Put a rejected term back in the queue, undecided.

    Not the same as approving it. A judgement made on two occurrences is worth
    revisiting at twenty, and the second look should start from "undecided"
    rather than from the answer somebody is reconsidering.
    """
    term = await _term(sess, term_id)
    term.rejected_at = None
    term.approved = False
    return await _decided(sess, term)


@router.patch("/gazetteer/{term_id}", response_model=GazetteerRowRead)
async def edit_term(
    term_id: int, edit: GazetteerTermEdit, _: AdminAllowed, sess: WriteSession
) -> GazetteerRowRead:
    """Correct a term before deciding on it.

    A harvested term arrives as `concept` with no jurisdiction, because a regex
    cannot tell an agency from a metric. Correcting that is most of the work of
    approving one, and a queue that could only say yes or no would make the
    curator's only options "accept it filed wrongly" or "throw away a real term".

    `exclude_unset` is what makes clearing a field possible: sending
    `jurisdiction: null` clears it, omitting the key leaves it alone. Without the
    distinction one of those two edits is unexpressible.
    """
    term = await _term(sess, term_id)
    changes = edit.model_dump(exclude_unset=True)

    if "aliases" in changes:
        # Blank aliases produce no pattern and cannot be told apart in a UI from
        # a row that has none, so they are dropped rather than stored.
        aliases = [alias.strip() for alias in (changes["aliases"] or []) if alias.strip()]
        changes["aliases"] = aliases or None

    for field, value in changes.items():
        setattr(term, field, value)

    return await _decided(sess, term)


# ---------------------------------------------------------------------------
# Topics (task P6-12, spec §10, §10.1)
# ---------------------------------------------------------------------------
#
# §10 is one sentence — "attention is a weight vector over topics; seeds are
# drawn proportionally" — and these routes are the only place a person changes
# it. Three things follow.
#
# **Every change is logged before it is applied**, actor and reason, because
# §10.1 says so and because with two writers the alternative is opening this
# screen in a month with no idea what moved anything.
#
# **Nothing here deletes.** Archiving is a status, not a DELETE: it drops the
# topic out of the pool and leaves every node, edge and tag it produced
# untouched, so coming back is a status change rather than a re-crawl.
#
# **An invalid steering change is refused, not clamped.** Silently adjusting a
# number somebody typed shows them a different one and explains nothing — and
# the bound they would need to change is exactly what the message names.

#: Who the log records for a change made through this surface. The other actor
#: §10.1 names is `orchestrator`, which writes the same tables from phase 4.
ACTOR = "user"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _topics_read(sess) -> TopicsRead:
    now = _now()
    rows = await steering.topics(sess)
    shares = steering.draw_shares(rows, now=now)
    return TopicsRead(
        rows=[
            TopicRowRead(
                topic=TopicConfigRead.model_validate(row),
                effective_weight=steering.effective_weight(row, now=now),
                share=shares.get(row.topic, 0.0),
                boost_active=steering.boost_is_active(row, now=now),
            )
            for row in rows
        ],
        sums_to=sum(shares.values()),
    )


@router.get("/topics", response_model=TopicsRead)
async def list_topics(_: AdminAllowed, sess: WriteSession) -> TopicsRead:
    """The vector, with what each topic is actually drawing beside what it stores."""
    return await _topics_read(sess)


def _refused(exc: Exception) -> HTTPException:
    """Turn a steering rule into a 422 that names the rule.

    422 rather than 400: these are semantically invalid changes, not malformed
    requests, and the body carries a sentence a person can act on — which is the
    whole reason the steering layer raises with bounds in the message instead of
    clamping.
    """
    return HTTPException(status_code=422, detail=str(exc))


@router.patch("/topics/{topic}", response_model=TopicsRead)
async def edit_topic(
    topic: str, edit: TopicEdit, _: AdminAllowed, sess: WriteSession
) -> TopicsRead:
    """Steer one topic. Returns the whole vector, because changing one moves all.

    The order matters. Status first, so pausing and re-weighting in one request
    cannot try to set a share on a topic that is leaving the pool; bounds before
    weight, so a weight sent alongside a raised ceiling is judged against the
    new ceiling rather than refused by the old one.
    """
    now = _now()
    changes = edit.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None) or f"changed through admin: {sorted(changes)}"
    kwargs = {"actor": ACTOR, "reason": reason, "now": now}

    try:
        if "status" in changes:
            await steering.set_status(sess, topic, changes["status"], **kwargs)
        if "floor" in changes or "ceiling" in changes:
            await steering.set_bounds(
                sess,
                topic,
                floor=changes.get("floor"),
                ceiling=changes.get("ceiling"),
                **kwargs,
            )
        if "pinned" in changes:
            await steering.set_pinned(sess, topic, changes["pinned"], **kwargs)
        if "boost_factor" in changes or "boost_expires_at" in changes:
            await steering.set_boost(
                sess,
                topic,
                factor=changes.get("boost_factor"),
                expires_at=changes.get("boost_expires_at"),
                **kwargs,
            )
        if "weight" in changes:
            await steering.set_weight(sess, topic, changes["weight"], **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, steering.InfeasibleWeights) as exc:
        # Nothing is committed, so a request that fails halfway leaves the
        # vector as it was rather than partly steered.
        await sess.rollback()
        raise _refused(exc) from exc

    await sess.commit()
    log.info("topics steered", extra={"topic": topic, "fields": sorted(changes)})
    return await _topics_read(sess)


@router.post("/topics", response_model=TopicsRead, status_code=201)
async def add_topic(body: TopicAdd, _: AdminAllowed, sess: WriteSession) -> TopicsRead:
    """§10.2's `add_topic` — insert and re-normalise.

    It starts at its floor rather than at a share somebody chose, because a new
    topic has no hand-seeded sources yet (§10.2 calls this "a small repeat of
    cold start") and a large share spent on a topic with nothing to crawl is
    attention going nowhere.
    """
    try:
        await steering.add_topic(
            sess,
            body.topic,
            floor=body.floor,
            ceiling=body.ceiling,
            actor=ACTOR,
            reason=body.reason or "added through admin",
            now=_now(),
        )
    except (ValueError, steering.InfeasibleWeights) as exc:
        await sess.rollback()
        raise _refused(exc) from exc

    await sess.commit()
    log.info("topic added", extra={"topic": body.topic})
    return await _topics_read(sess)


@router.get("/steering-log", response_model=SteeringLogPage)
async def steering_log(
    _: AdminAllowed,
    sess: WriteSession,
    topic: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> SteeringLogPage:
    """Why the vector is where it is (§10.1). Newest first."""
    statement = select(SteeringLog).order_by(
        SteeringLog.changed_at.desc(), SteeringLog.log_id.desc()
    )
    if topic:
        statement = statement.where(SteeringLog.topic == topic)
    found = list(await sess.scalars(statement.limit(limit + 1)))

    return SteeringLogPage(
        entries=[SteeringLogRead.model_validate(row) for row in found[:limit]],
        limit=limit,
        has_more=len(found) > limit,
    )
