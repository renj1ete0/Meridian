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
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from meridian_core import annotations, steering
from meridian_core.gazetteer import loading_report
from meridian_core.logging import get_logger
from meridian_core.models import FetchPolicy, GazetteerTerm, SavedView, SteeringLog
from meridian_core.policy import (
    GLOBAL_DOMAIN,
    ResolvedPolicy,
    file_defaults,
    learned_render_js,
    merge_layers,
)
from meridian_core.schemas.admin import (
    FetchPolicyEdit,
    FetchPolicyPage,
    FetchPolicyRowRead,
    GazetteerQueueRead,
    GazetteerRowRead,
    GazetteerTermEdit,
    SteeringLogPage,
    TopicAdd,
    TopicEdit,
    TopicRowRead,
    TopicsRead,
)
from meridian_core.schemas.annotations import (
    AnnotationCreate,
    AnnotationEdit,
    AnnotationRead,
)
from meridian_core.schemas.config import FetchPolicyRead, SteeringLogRead, TopicConfigRead
from meridian_core.schemas.enums import DomainStatus
from meridian_core.schemas.gazetteer import GazetteerTermRead
from meridian_core.schemas.views import SavedViewCreate, SavedViewEdit, SavedViewRead
from meridian_core.search import SearchFilters

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


# ---------------------------------------------------------------------------
# Fetch policy (task P6-22, spec §6.4, §13.2)
# ---------------------------------------------------------------------------
#
# The one admin surface whose changes reach the outside world. Everything else
# here rearranges rows; this decides how a machine behaves towards somebody
# else's server, so two things are stricter than they are elsewhere.
#
# **Only some keys are editable, and the list is short.** `ResolvedPolicy`
# carries the SSRF guards — `block_private_addresses`, `block_cloud_metadata`,
# `allowed_schemes`, `require_https_final`, `block_mixed_dns`,
# `revalidate_each_redirect` — and none of them belongs behind a form field. A
# browser form that could turn off private-address blocking is the single worst
# change available in this system, and it would be one click on a screen whose
# other controls are about politeness. `respect_robots` and `user_agent` are out
# for a different reason: a crawler that can stop honouring robots.txt, or change
# who it says it is, from a web form is a crawler whose operator did not decide
# that. Those are deployment decisions and they stay in the deployment.
#
# **Editing the global row needs `confirm`.** It is the only edit here whose
# blast radius is the entire crawl, and a client-side dialog is a promise rather
# than a check.

#: What Admin may change: politeness, patience, and how a page is fetched.
#: Everything absent is either a safety guard or an identity claim.
EDITABLE = frozenset(
    {
        "concurrency_per_domain",
        "delay_per_domain_ms",
        "delay_jitter_ms",
        "respect_crawl_delay",
        "timeout_s",
        "max_retries",
        "backoff_base_s",
        "blocked_after_failures",
        "conditional_requests",
        "prefetch_filter",
        "render_js",
        "challenge_wait_s",
        "max_page_bytes",
    }
)


def _resolved(row: FetchPolicy, glob: FetchPolicy | None) -> dict:
    """What a fetch to this domain actually gets, without a query per row.

    The same merge `resolve_policy` does, minus the round trip — a page of fifty
    domains would otherwise be fifty identical reads of the global row.
    """
    merged = merge_layers(
        row.settings if row.domain != GLOBAL_DOMAIN else None,
        glob.settings if glob else None,
        file_defaults(),
    )
    for key in NOT_FETCH_SETTINGS:
        merged.pop(key, None)
    status = row.status if row.domain != GLOBAL_DOMAIN else "active"
    resolved = ResolvedPolicy(domain=row.domain, status=status, **merged)
    if row.domain != GLOBAL_DOMAIN and resolved.render_js == "auto" and learned_render_js(row):
        # Shown as what the crawler will do, not as what is configured — the
        # row's own `render_js` is still absent, and the learned columns beside
        # it are what say why.
        resolved = resolved.model_copy(update={"render_js": "always"})
    return resolved.model_dump()


#: In the settings blob and not fetch settings. `source_tiers` is the domain →
#: tier map and `frontier` decides what enters the queue; both ride in the
#: global row (§13.1) and neither is something a fetch reads.
NOT_FETCH_SETTINGS = frozenset({"source_tiers", "frontier"})


def _row_read(row: FetchPolicy, glob: FetchPolicy | None) -> FetchPolicyRowRead:
    return FetchPolicyRowRead(
        policy=FetchPolicyRead.model_validate(row),
        resolved=_resolved(row, glob),
        overridden=sorted(set(row.settings or {}) - NOT_FETCH_SETTINGS),
    )


@router.get("/fetch-policy", response_model=FetchPolicyPage)
async def list_fetch_policy(
    _: AdminAllowed,
    sess: WriteSession,
    status: Annotated[DomainStatus | None, Query()] = None,
    q: Annotated[str | None, Query(description="Substring of the domain.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FetchPolicyPage:
    """Every domain with a row, and what it resolves to.

    Blocked first, then by domain. A blocked domain is the one an operator came
    to find — it is consuming no crawl budget and producing no sources, and
    §6.4's auto-blocking means one can appear without anybody choosing it.
    """
    glob = await sess.get(FetchPolicy, GLOBAL_DOMAIN)

    statement = select(FetchPolicy)
    if status:
        statement = statement.where(FetchPolicy.status == status)
    if q:
        statement = statement.where(FetchPolicy.domain.ilike(f"%{q}%"))

    found = list(
        await sess.scalars(
            statement.order_by(
                (FetchPolicy.status != "blocked"), FetchPolicy.domain
            ).limit(limit + 1).offset(offset)
        )
    )

    counts = (
        await sess.execute(
            select(
                func.count().filter(FetchPolicy.status == "active"),
                func.count().filter(FetchPolicy.status == "paused"),
                func.count().filter(FetchPolicy.status == "blocked"),
            )
        )
    ).one()

    return FetchPolicyPage(
        rows=[_row_read(row, glob) for row in found[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(found) > limit,
        active=int(counts[0]),
        paused=int(counts[1]),
        blocked=int(counts[2]),
    )


async def _policy_row(sess, domain: str) -> FetchPolicy:
    row = await sess.get(FetchPolicy, domain)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No fetch policy for {domain!r}.")
    return row


@router.patch("/fetch-policy/{domain}", response_model=FetchPolicyRowRead)
async def edit_fetch_policy(
    domain: str, edit: FetchPolicyEdit, _: AdminAllowed, sess: WriteSession
) -> FetchPolicyRowRead:
    """Change one domain's politeness, patience, or render mode.

    The edit is applied to a copy and validated by building a `ResolvedPolicy`
    from it, so the field bounds that already exist — a delay may not be
    negative, a timeout may not be zero — are the same ones enforced here. §2.6:
    all writes validate server-side, and a value that reached the crawler
    unchecked would fail at whatever hour the domain came up next.
    """
    row = await _policy_row(sess, domain)

    if domain == GLOBAL_DOMAIN and not edit.confirm:
        raise HTTPException(
            status_code=422,
            detail=(
                "Editing the global policy changes every domain the crawl touches. "
                "Re-send with confirm: true."
            ),
        )

    changes = edit.model_dump(exclude_unset=True)
    if edit.settings is not None:
        rejected = sorted(set(edit.settings) - EDITABLE)
        if rejected:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{', '.join(rejected)} cannot be changed here. Safety guards and the "
                    "crawler's identity are deployment settings, not form fields."
                ),
            )
        merged = {**(row.settings or {}), **edit.settings}
        glob = await sess.get(FetchPolicy, GLOBAL_DOMAIN)
        try:
            ResolvedPolicy(
                domain=domain,
                **merge_layers(
                    merged,
                    glob.settings if glob and domain != GLOBAL_DOMAIN else None,
                    file_defaults(),
                ),
            )
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=_first_error(exc)) from exc
        row.settings = merged

    if "status" in changes:
        row.status = changes["status"]
    if "note" in changes:
        row.note = changes["note"]

    row.updated_at = _now()
    row.updated_by = ACTOR
    await sess.commit()
    await sess.refresh(row)
    log.info("fetch policy changed", extra={"domain": domain, "fields": sorted(changes)})
    return _row_read(row, await sess.get(FetchPolicy, GLOBAL_DOMAIN))


def _first_error(exc: PydanticValidationError) -> str:
    """One sentence a person can act on, rather than a list of dicts."""
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ()))
        return f"{field}: {error.get('msg', 'invalid')}" if field else str(error.get("msg"))
    return "invalid settings"


@router.post("/fetch-policy/{domain}/unblock", response_model=FetchPolicyRowRead)
async def unblock_domain(domain: str, _: AdminAllowed, sess: WriteSession) -> FetchPolicyRowRead:
    """Put an auto-blocked domain back in the crawl, and clear the count.

    Both, because either alone is a trap. Clearing the status without the
    counter leaves the domain one failure from being blocked again, which reads
    as the unblock not having worked; clearing the counter without the status
    leaves it blocked with nothing explaining why.
    """
    row = await _policy_row(sess, domain)
    row.status = "active"
    row.consecutive_failures = 0
    row.updated_at = _now()
    row.updated_by = ACTOR
    await sess.commit()
    await sess.refresh(row)
    log.info("domain unblocked", extra={"domain": domain})
    return _row_read(row, await sess.get(FetchPolicy, GLOBAL_DOMAIN))


@router.post("/fetch-policy/{domain}/forget-render", response_model=FetchPolicyRowRead)
async def forget_render_learning(
    domain: str, _: AdminAllowed, sess: WriteSession
) -> FetchPolicyRowRead:
    """Discard what the crawl learned about needing a browser (`P1-27`).

    For the case the expiry is too slow for: a site that dropped its JavaScript
    shell today, where waiting a week to re-probe means a week of browser
    launches that were not needed. It clears the observation rather than setting
    a policy, so the domain goes back to deciding for itself.
    """
    row = await _policy_row(sess, domain)
    row.render_js_escalations = 0
    row.render_js_learned_at = None
    await sess.commit()
    await sess.refresh(row)
    return _row_read(row, await sess.get(FetchPolicy, GLOBAL_DOMAIN))


# ---------------------------------------------------------------------------
# Saved views (task P6-09, spec §12.5)
# ---------------------------------------------------------------------------
#
# Reading them is `/api/explore/views`; every write is here. That looks
# inconsistent for something a reader creates while reading, and it is the
# consequence of §12.6 splitting the prefixes by *mutation* rather than by
# audience — which turns out to be the right split for this table specifically.
# Saved views are shared state with no per-viewer scoping, so on an instance
# shared with somebody else (`P3-06`'s grants) a guest should be able to open the
# owner's views and should not be able to add to them.


@router.post("/views", response_model=SavedViewRead, status_code=201)
async def create_view(body: SavedViewCreate, _: AdminAllowed, sess: WriteSession) -> SavedViewRead:
    """Save a filter set under a name.

    The filters are validated against `SearchFilters` on the way in, so a view
    cannot store something the search cannot apply. A view that silently drops a
    filter when it is reopened is worse than one that refuses to save: the
    reader gets a result set they believe is narrowed and is not.
    """
    _validated_filters(body.filters)

    if await sess.scalar(select(SavedView).where(SavedView.name == body.name)):
        raise HTTPException(
            status_code=409,
            detail=f"A view called {body.name!r} already exists. Rename it or update that one.",
        )

    view = SavedView(
        name=body.name,
        query=body.query,
        filters=body.filters,
        focus_entity_id=body.focus_entity_id,
        note=body.note,
    )
    sess.add(view)
    await sess.commit()
    await sess.refresh(view)
    log.info("view saved", extra={"view_id": view.view_id})
    return SavedViewRead.model_validate(view)


def _validated_filters(filters: dict) -> None:
    """Refuse a filter set the search could not apply.

    `SearchFilters` is a frozen dataclass, so an unknown key raises `TypeError`
    and a bad value raises on use — both become a 422 naming the field rather
    than a view that reopens narrower or wider than it was saved.
    """
    try:
        SearchFilters(**filters)
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"Unusable filter set: {exc}") from exc


@router.patch("/views/{view_id}", response_model=SavedViewRead)
async def edit_view(
    view_id: int, edit: SavedViewEdit, _: AdminAllowed, sess: WriteSession
) -> SavedViewRead:
    """Rename a view, re-note it, or point it somewhere else."""
    view = await sess.get(SavedView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No saved view {view_id}.")

    changes = edit.model_dump(exclude_unset=True)
    if "filters" in changes and changes["filters"] is not None:
        _validated_filters(changes["filters"])
    if "name" in changes:
        clash = await sess.scalar(
            select(SavedView).where(SavedView.name == changes["name"], SavedView.view_id != view_id)
        )
        if clash is not None:
            raise HTTPException(status_code=409, detail=f"{changes['name']!r} is already taken.")

    for field, value in changes.items():
        setattr(view, field, value)
    await sess.commit()
    await sess.refresh(view)
    return SavedViewRead.model_validate(view)


@router.post("/views/{view_id}/opened", response_model=SavedViewRead)
async def mark_view_opened(view_id: int, _: AdminAllowed, sess: WriteSession) -> SavedViewRead:
    """Record that somebody returned to this view.

    What orders the landing screen. A separate call rather than a side effect of
    reading the list, because listing views is not returning to one — and a read
    that wrote would also put `/api/explore` on the wrong side of §12.6's
    boundary.
    """
    view = await sess.get(SavedView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No saved view {view_id}.")
    view.last_opened_at = _now()
    await sess.commit()
    await sess.refresh(view)
    return SavedViewRead.model_validate(view)


@router.delete("/views/{view_id}", status_code=204)
async def delete_view(view_id: int, _: AdminAllowed, sess: WriteSession) -> None:
    """Remove a view.

    The one delete on this surface, and it is right: a saved view holds no
    evidence and cites nothing. Everything else here keeps its row because
    something downstream depends on it — a view depends on nothing, and keeping a
    tombstone would clutter the list it exists to be read from.
    """
    view = await sess.get(SavedView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No saved view {view_id}.")
    await sess.delete(view)
    await sess.commit()
    log.info("view deleted", extra={"view_id": view_id})


# ---------------------------------------------------------------------------
# Annotations (task P6-05, spec §12.5)
# ---------------------------------------------------------------------------
#
# Writing a note is here and reading them is `/api/explore/annotations`, the
# same split saved views take and for a sharper reason. §12.5 calls this layer
# the one that actually reflects the reader's thinking; an annotation surface
# open to the internet is a way to put text into the corpus that reads as the
# owner's own thinking, which is the worst thing on this system to be able to
# forge. On a shared instance (`P3-06`) a guest should see the owner's notes and
# have no way to add to them.


@router.post("/annotations", response_model=AnnotationRead, status_code=201)
async def write_annotation(
    body: AnnotationCreate, _: AdminAllowed, sess: WriteSession
) -> AnnotationRead:
    """Write one of the reader's own notes.

    `produced_by` is absent from `AnnotationCreate` and the model forbids extra
    keys, so a request that tries to claim authorship is refused at the boundary
    rather than silently overwritten — including, later, a request from a model
    holding a write tool (`P4-04`).
    """
    try:
        note = await annotations.create(sess, body)
    except LookupError as exc:
        await sess.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # Nothing is committed, so a refused note leaves no half-attached row.
        await sess.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return (await annotations.hydrate(sess, [note]))[0]


@router.patch("/annotations/{entity_id}", response_model=AnnotationRead)
async def rewrite_annotation(
    entity_id: int, change: AnnotationEdit, _: AdminAllowed, sess: WriteSession
) -> AnnotationRead:
    """Rewrite a note, or re-point what it is about.

    404 for a corpus-derived entity, which is the refusal that matters: §2.4
    re-derives the graph from source chunks, and a hand-edit surviving into a
    derived node is a change nothing can re-derive or explain. This surface
    writes the reader's own nodes only.
    """
    try:
        note = await annotations.edit(sess, entity_id, change)
    except LookupError as exc:
        await sess.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await sess.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return (await annotations.hydrate(sess, [note]))[0]
