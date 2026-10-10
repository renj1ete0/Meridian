"""`/api/admin/*` — the control surface (task P6-13, spec §12.6, §5.6).

The only routes that change anything. `WriteSession` and `AdminAllowed` both come from
`deps`, so no route here gets a session without the gate. Approving a term reports what
the matcher will do with it, and nothing here deletes except a saved view.
See docs/features/api-and-access.md#admin-routes.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from meridian_core import annotations, chat, duplicates, mapsteer, steering
from meridian_core.areaview import AreaNotFound
from meridian_core.budget import BUDGET_ID, load_budget, month_to_date_cost
from meridian_core.gazetteer import loading_report
from meridian_core.logging import get_logger
from meridian_core.models import (
    Agent,
    BudgetConfig,
    FetchPolicy,
    GazetteerTerm,
    QueueTask,
    Run,
    SavedView,
    Source,
    SteeringLog,
)
from meridian_core.policy import (
    GLOBAL_DOMAIN,
    NOT_FETCH_SETTINGS,
    ResolvedPolicy,
    file_defaults,
    learned_render_js,
    merge_layers,
)
from meridian_core.queueing import enqueue
from meridian_core.routing import TASK_TYPES
from meridian_core.schemas.admin import (
    AgentEdit,
    AgentRowRead,
    AgentsRead,
    BudgetEdit,
    BudgetRead,
    FetchPolicyEdit,
    FetchPolicyPage,
    FetchPolicyRowRead,
    FirstRunRead,
    GazetteerBulkDecision,
    GazetteerBulkRead,
    GazetteerQueueRead,
    GazetteerRowRead,
    GazetteerTermEdit,
    RunRowRead,
    RunsRead,
    SeedCreate,
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
from meridian_core.schemas.areas import (
    MapSteerCreate,
    MapSteerRead,
    MapSuggestCreate,
    NoiseRestoreRead,
)
from meridian_core.schemas.chat import ChatAsk, ChatExchangeRead, ChatMessageRead, ChatThreadRead
from meridian_core.schemas.config import FetchPolicyRead, SteeringLogRead, TopicConfigRead
from meridian_core.schemas.duplicates import (
    DuplicateDecision,
    DuplicateDecisionRead,
    DuplicatesRead,
)
from meridian_core.schemas.enums import DomainStatus
from meridian_core.schemas.gazetteer import GazetteerTermRead
from meridian_core.schemas.graphview import GraphFilters
from meridian_core.schemas.queue import QueueTaskRead
from meridian_core.schemas.settings import DisplaySettingsRead, DisplayZoneEdit
from meridian_core.schemas.views import SavedViewCreate, SavedViewEdit, SavedViewRead
from meridian_core.search import SearchFilters
from meridian_core.timefmt import ZONE_KEY, zone_label
from meridian_core.validation import ValidationError, check_seed_allowed

from ..deps import AdminAllowed, WriteSession
from ..search_service import embed_query

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

    The whole approved set is loaded, because the colliding row is usually on
    another page.
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
    q: Annotated[
        str | None,
        Query(max_length=120, description="Terms containing this, any case (`B-209`)."),
    ] = None,
) -> GazetteerQueueRead:
    """The approval queue, most corroborated first.

    Ordered by `occurrence_count`, with `term_id` breaking ties so paging is stable. `q`
    narrows to terms containing it; the counts stay the whole queue's.
    """
    narrowed = []
    if q and q.strip():
        # Escaped, so a reader's "%" or "_" is a character, not a wildcard.
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        narrowed.append(GazetteerTerm.canonical.ilike(f"%{needle}%", escape="\\"))
    statement = (
        select(GazetteerTerm)
        .where(*_state_filter(state), *narrowed)
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

    Clears any rejection, which the harvest would otherwise still read.
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

    The row is kept as a tombstone, or the next harvest pass re-creates it.
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

    An explicit `null` clears a field; an omitted key leaves it alone (`exclude_unset`).
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
# Every change is logged before it is applied; archiving is a status, not a DELETE;
# an invalid change is refused, not clamped. See docs/features/api-and-access.md#admin-routes.

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

    422: a semantically invalid change, with a sentence a person can act on.
    """
    return HTTPException(status_code=422, detail=str(exc))


@router.patch("/topics/{topic}", response_model=TopicsRead)
async def edit_topic(
    topic: str, edit: TopicEdit, _: AdminAllowed, sess: WriteSession
) -> TopicsRead:
    """Steer one topic. Returns the whole vector, because changing one moves all."""
    fields = await _steer(sess, topic, edit)
    await sess.commit()
    log.info("topics steered", extra={"topic": topic, "fields": fields})
    return await _topics_read(sess)


async def _steer(sess, topic: str, edit: TopicEdit) -> list[str]:
    """Apply a steering change to the session without committing it.

    Shared by the write and its preview (`P6-28`). Applies status, then bounds, then
    weight. See docs/features/api-and-access.md#admin-routes.
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
        if "description" in changes:
            await steering.set_description(sess, topic, changes["description"], **kwargs)
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
    return sorted(changes)


@router.post("/topics", response_model=TopicsRead, status_code=201)
async def add_topic(body: TopicAdd, _: AdminAllowed, sess: WriteSession) -> TopicsRead:
    """§10.2's `add_topic` — insert and re-normalise."""
    await _add(sess, body)
    await sess.commit()
    log.info("topic added", extra={"topic": body.topic})
    return await _topics_read(sess)


async def _add(sess, body: TopicAdd) -> None:
    """Insert a topic into the session without committing it.

    It starts at its floor: a new topic has nothing to crawl yet (§10.2).
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
            description=body.description,
        )
    except (ValueError, steering.InfeasibleWeights) as exc:
        await sess.rollback()
        raise _refused(exc) from exc


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
# Only politeness, patience and render mode are editable: never the SSRF guards,
# `respect_robots` or `user_agent`. The global row needs `confirm`.
# See docs/features/api-and-access.md#admin-routes.

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
            statement.order_by((FetchPolicy.status != "blocked"), FetchPolicy.domain)
            .limit(limit + 1)
            .offset(offset)
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


@router.put("/settings/display-timezone", response_model=DisplaySettingsRead)
async def set_display_timezone(
    edit: DisplayZoneEdit, _: AdminAllowed, sess: WriteSession
) -> DisplaySettingsRead:
    """Change the zone times are shown in (`B-145`, ADR 0009). Stored times do not move."""
    row = await sess.get(FetchPolicy, GLOBAL_DOMAIN)
    if row is None:
        raise HTTPException(
            status_code=409, detail="The global policy row is missing; run the seed."
        )
    row.settings = {**(row.settings or {}), ZONE_KEY: edit.display_timezone}
    row.updated_by = "admin"
    await sess.commit()
    log.info("display time zone changed", extra={"zone": edit.display_timezone})
    return DisplaySettingsRead(
        display_timezone=edit.display_timezone, label=zone_label(edit.display_timezone)
    )


@router.patch("/fetch-policy/{domain}", response_model=FetchPolicyRowRead)
async def edit_fetch_policy(
    domain: str, edit: FetchPolicyEdit, _: AdminAllowed, sess: WriteSession
) -> FetchPolicyRowRead:
    """Change one domain's politeness, patience, or render mode.

    Validated by building a `ResolvedPolicy` from an edited copy, so its field bounds
    apply (§2.6).
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

    Both together; either alone looks like the unblock did not work.
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

    For when the expiry is too slow. Clears the observation rather than setting a
    policy, so the domain decides for itself again.
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
# Read under `/api/explore/views`, written here: the prefixes split by mutation, so a
# guest can open the owner's views but not add to them.
# See docs/features/api-and-access.md#admin-routes.


@router.post("/views", response_model=SavedViewRead, status_code=201)
async def create_view(body: SavedViewCreate, _: AdminAllowed, sess: WriteSession) -> SavedViewRead:
    """Save a filter set under a name.

    The filters are validated against what will apply them, so a view cannot store one
    that would be dropped on reopening: a node view's against the graph's filters, a
    search view's against `SearchFilters` (`B-193`).
    """
    _validated_filters(body.filters, node=body.focus_entity_id is not None)

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


def _validated_filters(filters: dict, *, node: bool) -> None:
    """Refuse a filter set the view's own page could not apply.

    A node view reopens the graph workspace, so its filters are `GraphFilters`; a search
    view reopens Find, so its are `SearchFilters` (`B-193`: node views were checked against
    the search's names and every filtered one was refused). Either way an unknown key is a
    422 naming it, rather than a view that reopens narrower or wider than it was saved.
    """
    if node:
        try:
            GraphFilters.model_validate(filters)
        except PydanticValidationError as exc:
            raise HTTPException(
                status_code=422, detail=f"Unusable filter set for a node view: {_first_error(exc)}"
            ) from exc
        return
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
        focus = changes.get("focus_entity_id", view.focus_entity_id)
        _validated_filters(changes["filters"], node=focus is not None)
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

    What orders the landing screen. A separate call: listing is not returning.
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

    The one delete on this surface: a view holds no evidence and nothing depends on it.
    """
    view = await sess.get(SavedView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No saved view {view_id}.")
    await sess.delete(view)
    await sess.commit()
    log.info("view deleted", extra={"view_id": view_id})


# ---------------------------------------------------------------------------
# Possible duplicates (task B-202, spec §5.5)
# ---------------------------------------------------------------------------
#
# Resolution queues the middle band for a person; these decide it. A merge is reversible
# (`resolution.reverse`), so "undo" splits it exactly. See
# docs/features/knowledge-graph.md#deciding-a-possible-duplicate.


@router.get("/duplicates", response_model=DuplicatesRead)
async def list_duplicates(
    _: AdminAllowed, sess: WriteSession, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> DuplicatesRead:
    """Undecided possible duplicates, newest first, with both nodes and their evidence."""
    return await duplicates.open_pairs(sess, limit=limit)


@router.post("/duplicates/{notification_id}", response_model=DuplicateDecisionRead)
async def decide_duplicate(
    notification_id: int, body: DuplicateDecision, _: AdminAllowed, sess: WriteSession
) -> DuplicateDecisionRead:
    """Merge the created node into the one it might be, or keep them apart."""
    try:
        decided = await duplicates.decide(sess, notification_id, body.decision, decided_by=ACTOR)
    except duplicates.DuplicateRefused as refused:
        raise HTTPException(status_code=409, detail=str(refused)) from refused
    await sess.commit()
    log.info(
        "duplicate decided",
        extra={"notification_id": notification_id, "decision": decided.decision},
    )
    return decided


@router.post("/duplicates/{notification_id}/undo", response_model=DuplicateDecisionRead)
async def undo_duplicate(
    notification_id: int, _: AdminAllowed, sess: WriteSession
) -> DuplicateDecisionRead:
    """Reverse a decision: split the merge exactly, or reopen a pair kept apart."""
    try:
        undone = await duplicates.undo(sess, notification_id, decided_by=ACTOR)
    except duplicates.DuplicateRefused as refused:
        raise HTTPException(status_code=409, detail=str(refused)) from refused
    await sess.commit()
    log.info("duplicate decision undone", extra={"notification_id": notification_id})
    return undone


# ---------------------------------------------------------------------------
# Annotations (task P6-05, spec §12.5)
# ---------------------------------------------------------------------------
#
# Written here, read under `/api/explore/annotations`, as saved views are: a guest
# sees the owner's notes and cannot add to them.


@router.post("/annotations", response_model=AnnotationRead, status_code=201)
async def write_annotation(
    body: AnnotationCreate, _: AdminAllowed, sess: WriteSession
) -> AnnotationRead:
    """Write one of the reader's own notes.

    `AnnotationCreate` forbids extra keys, so a request claiming `produced_by` is
    refused at the boundary.
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

    404 for a corpus-derived entity: this writes the reader's own nodes only (§2.4).
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


# ---------------------------------------------------------------------------
# The budget (tasks `P4-10`, `P4-13`, §16)
# ---------------------------------------------------------------------------
#
# Where §16's caps are set. Nothing seeds a default budget.
# See docs/features/api-and-access.md#admin-routes.


async def _budget_read(sess: WriteSession) -> BudgetRead:
    """One shape for both handlers, so a GET after a PUT cannot disagree."""
    row = await sess.get(BudgetConfig, BUDGET_ID)
    budget = await load_budget(sess)
    spent = await month_to_date_cost(sess)

    ready = False
    if budget is not None and budget.is_complete:
        ceiling = budget.monthly_cost_ceiling_usd
        ready = ceiling is not None and spent < ceiling

    return BudgetRead(
        max_tokens_per_run=budget.max_tokens_per_run if budget else None,
        max_seeds_per_run=budget.max_seeds_per_run if budget else None,
        monthly_cost_ceiling_usd=budget.monthly_cost_ceiling_usd if budget else None,
        month_to_date_usd=round(spent, 4),
        ready=ready,
        missing=list(budget.missing) if budget else sorted(CAP_FIELDS),
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
    )


#: The three caps, named once. Used for "everything is missing" when there is no
#: row at all, which is a different state from a row with nulls in it.
CAP_FIELDS = frozenset(BudgetEdit.model_fields)


@router.get("/budget", response_model=BudgetRead)
async def read_budget(_: AdminAllowed, sess: WriteSession) -> BudgetRead:
    """The caps, the month's spend, and whether a run could start right now."""
    return await _budget_read(sess)


@router.put("/budget", response_model=BudgetRead)
async def set_budget(edit: BudgetEdit, _: AdminAllowed, sess: WriteSession) -> BudgetRead:
    """Set or change the caps.

    A partial `PUT` on the singleton: omitted fields are kept, an explicit `null`
    clears a cap (and stops runs). The row is created on first write.
    """
    changes = edit.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to change.")

    row = await sess.get(BudgetConfig, BUDGET_ID)
    if row is None:
        row = BudgetConfig(budget_id=BUDGET_ID)
        sess.add(row)

    for field, value in changes.items():
        setattr(row, field, value)
    row.updated_at = _now()
    row.updated_by = ACTOR

    await sess.commit()
    await sess.refresh(row)
    log.info(
        "budget changed",
        extra={
            "fields": sorted(changes),
            "cleared": sorted(k for k, v in changes.items() if v is None),
        },
    )
    return await _budget_read(sess)


# ---------------------------------------------------------------------------
# The first run (task `B-07`, scaffold §1.7, §15 phase 0)
# ---------------------------------------------------------------------------
#
# Seeds stay editable while pending; not a wizard or a gate.
# See docs/features/api-and-access.md#admin-routes.


@router.get("/first-run", response_model=FirstRunRead)
async def read_first_run(_: AdminAllowed, sess: WriteSession) -> FirstRunRead:
    """Whether anything has been crawled yet, and what is still queued."""
    sources = int(await sess.scalar(select(func.count()).select_from(Source)) or 0)

    pending = (
        (
            await sess.execute(
                select(QueueTask)
                .where(QueueTask.seed_source == "user", QueueTask.status == "pending")
                .order_by(QueueTask.priority.desc(), QueueTask.task_id)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    in_flight = int(
        await sess.scalar(
            select(func.count())
            .select_from(QueueTask)
            .where(QueueTask.seed_source == "user", QueueTask.status != "pending")
        )
        or 0
    )

    return FirstRunRead(
        is_first_run=sources == 0,
        sources=sources,
        pending_seeds=[QueueTaskRead.model_validate(row) for row in pending],
        seeds_in_flight=in_flight,
    )


@router.post("/seeds", response_model=QueueTaskRead, status_code=201)
async def add_seed(seed: SeedCreate, _: AdminAllowed, sess: WriteSession) -> QueueTaskRead:
    """Add a cold-start seed from the interface.

    `seed_source="user"`, so `P4-12` allows the domain at once; validated by
    `check_seed_allowed` as a model's seed is.
    """
    if seed.task_type == "url":
        try:
            await check_seed_allowed(sess, seed.url_or_query)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if await sess.scalar(
        select(QueueTask.task_id).where(QueueTask.url_or_query == seed.url_or_query)
    ):
        raise HTTPException(status_code=409, detail="That is already queued.")

    task = await enqueue(
        sess,
        seed.url_or_query,
        topic=seed.topic,
        seed_source="user",
        task_type=seed.task_type,
        priority=seed.priority,
    )
    # `B-55`: a seed changes what the crawl acquires, which is steering, and
    # steering nobody can find in the log is steering nobody can undo.
    await steering.record(
        sess,
        actor="user",
        topic=seed.topic,
        field="seed",
        old=None,
        new=seed.url_or_query,
        reason=seed.reason or f"{seed.task_type} seed added",
        now=dt.datetime.now(dt.UTC),
    )
    await sess.commit()
    await sess.refresh(task)
    log.info("seed added", extra={"url": seed.url_or_query, "topic": seed.topic})
    return QueueTaskRead.model_validate(task)


@router.delete("/seeds/{task_id}", status_code=204)
async def drop_seed(task_id: int, _: AdminAllowed, sess: WriteSession) -> None:
    """Remove a cold-start seed that has not been fetched yet.

    Only while `pending` and unclaimed (a claim is a lease, `P1-01`); the 409 says which
    condition failed.
    """
    task = await sess.get(QueueTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task {task_id}.")
    if task.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"That seed is {task.status}, not pending — it has already been reached.",
        )
    if task.claimed_by is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"That seed is being fetched right now by {task.claimed_by}. "
                f"Block the domain in fetch policy if you want it to stop."
            ),
        )

    await steering.record(
        sess,
        actor="user",
        topic=task.topic,
        field="seed",
        old=task.url_or_query,
        new=None,
        reason=f"{task.task_type} seed withdrawn before it ran",
        now=dt.datetime.now(dt.UTC),
    )
    await sess.delete(task)
    await sess.commit()
    log.info("seed removed", extra={"task_id": task_id, "url": task.url_or_query})


# ---------------------------------------------------------------------------
# The agent registry and run history (task `P6-23`, §11.3, §11.10)
# ---------------------------------------------------------------------------


def _blocked_by(agent: Agent, *, key_present: bool) -> list[str]:
    """Why routing would skip this row, in routing's own terms.

    Computed here rather than in the client because the client would eventually
    disagree with `routing.eligible`, and the direction it disagrees in is the
    bad one: a screen showing a usable agent that every run then refuses.
    """
    reasons: list[str] = []
    if not agent.enabled:
        reasons.append("disabled")
    if not (agent.task_types or []):
        reasons.append("declares no task types")
    unknown = sorted(set(agent.task_types or []) - TASK_TYPES)
    if unknown:
        reasons.append(f"unknown task type: {', '.join(unknown)}")
    model = (agent.model or "").strip()
    if not model or model.startswith("<"):
        reasons.append("no model string")
    if agent.api_key_env_var and not key_present:
        reasons.append(f"{agent.api_key_env_var} is unset here")
    if agent.provider == "openai_compatible" and not (agent.endpoint or "").strip():
        reasons.append("no endpoint")
    return reasons


@router.get("/agents", response_model=AgentsRead)
async def list_agents(_: AdminAllowed, sess: WriteSession) -> AgentsRead:
    """The registry, with the reason each row is or is not routable.

    `unserved_tasks` lists task types no enabled row declares: stages that defer every
    run.
    """
    rows = list(await sess.scalars(select(Agent).order_by(Agent.quality_tier.desc().nulls_last())))

    read: list[AgentRowRead] = []
    served: set[str] = set()
    for agent in rows:
        # The name, never the value (§11.11). `bool` of it and nothing else
        # reaches the response.
        key_present = bool(os.environ.get(agent.api_key_env_var or "", "").strip())
        blocked = _blocked_by(agent, key_present=key_present)
        if not blocked:
            served.update(agent.task_types or [])
        row = AgentRowRead.model_validate(agent)
        read.append(row.model_copy(update={"key_present": key_present, "blocked_by": blocked}))

    return AgentsRead(rows=read, unserved_tasks=sorted(TASK_TYPES - served))


@router.patch("/agents/{agent_id}", response_model=AgentsRead)
async def edit_agent(
    agent_id: str, body: AgentEdit, _: AdminAllowed, sess: WriteSession
) -> AgentsRead:
    """Enable or disable one agent, and return the whole registry.

    The whole registry, because `unserved_tasks` spans rows. Only `enabled` is writable.
    """
    agent = await sess.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"No agent {agent_id!r}.")

    if body.enabled is not None and agent.enabled != body.enabled:
        agent.enabled = body.enabled
        await sess.flush()
        log.info(
            "agent toggled from admin",
            extra={"agent": agent_id, "enabled": body.enabled},
        )
    if body.model is not None and agent.model != body.model:
        # `P6-06`: which model a local server runs is the operator's choice,
        # and waiting on a migration to change it was the wrong trade. A
        # `${VARIABLE}` is kept verbatim and read at call time.
        log.info(
            "agent model changed from admin",
            extra={"agent": agent_id, "was": agent.model, "now": body.model},
        )
        agent.model = body.model
        await sess.flush()
    await sess.commit()
    return await list_agents(_, sess)


@router.get("/runs", response_model=RunsRead)
async def list_runs(
    _: AdminAllowed,
    sess: WriteSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunsRead:
    """Recent synthesis runs, newest first, with the one in flight called out.

    Counters rather than a verdict, so a run that wrote nothing is visible. `offset` pages
    back through older runs (`B-198`); the run in flight is looked for among the newest.
    """
    total = int(await sess.scalar(select(func.count()).select_from(Run)) or 0)
    rows = list(
        await sess.scalars(select(Run).order_by(Run.run_id.desc()).offset(offset).limit(limit))
    )
    newest = (
        rows
        if offset == 0
        else list(await sess.scalars(select(Run).order_by(Run.run_id.desc()).limit(limit)))
    )
    active = next((row for row in newest if row.status in ("running", "deferred")), None)
    return RunsRead(
        rows=[RunRowRead.model_validate(row) for row in rows],
        total=total,
        active=RunRowRead.model_validate(active) if active is not None else None,
    )


# ---------------------------------------------------------------------------
# Admin as designed (task P6-28)
# ---------------------------------------------------------------------------
#
# A topic preview that is the write, rolled back, and gazetteer decisions a page at
# a time. See docs/features/api-and-access.md#admin-routes.


async def _preview(sess) -> TopicsRead:
    """Read the vector as the session now has it, then discard the session."""
    try:
        return await _topics_read(sess)
    finally:
        await sess.rollback()


@router.post("/topics/preview", response_model=TopicsRead)
async def preview_add_topic(body: TopicAdd, _: AdminAllowed, sess: WriteSession) -> TopicsRead:
    """What `POST /topics` would leave the vector as, without leaving it so.

    Refused exactly as the write would be, with the same sentence, so a dialog
    can say why before anybody presses the button.
    """
    await _add(sess, body)
    return await _preview(sess)


@router.post("/topics/{topic}/preview", response_model=TopicsRead)
async def preview_edit_topic(
    topic: str, edit: TopicEdit, _: AdminAllowed, sess: WriteSession
) -> TopicsRead:
    """What `PATCH /topics/{topic}` would leave the vector as, without leaving it so."""
    await _steer(sess, topic, edit)
    return await _preview(sess)


@router.post("/gazetteer/decide", response_model=GazetteerBulkRead)
async def decide_terms(
    body: GazetteerBulkDecision, _: AdminAllowed, sess: WriteSession
) -> GazetteerBulkRead:
    """One verdict for up to a page of terms, all or none.

    An unknown id refuses the whole request and names every id not found.
    """
    wanted = list(dict.fromkeys(body.term_ids))
    found = list(await sess.scalars(select(GazetteerTerm).where(GazetteerTerm.term_id.in_(wanted))))
    missing = sorted(set(wanted) - {term.term_id for term in found})
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"No gazetteer term {', '.join(str(i) for i in missing)}. Nothing was decided.",
        )

    now = dt.datetime.now(dt.UTC)
    for term in found:
        # The same three transitions as the per-term routes, and for the same
        # reasons: approving clears a tombstone, turning down keeps the row,
        # putting back leaves it undecided rather than approved.
        if body.decision == "approve":
            term.approved = True
            term.rejected_at = None
        elif body.decision == "reject":
            term.approved = False
            term.rejected_at = now
        else:
            term.approved = False
            term.rejected_at = None

    await sess.commit()
    for term in found:
        await sess.refresh(term)
    order = {term_id: i for i, term_id in enumerate(wanted)}
    found.sort(key=lambda term: order[term.term_id])
    rows = await _rows_for(sess, found)
    log.info(
        "gazetteer terms decided in bulk",
        extra={"decision": body.decision, "count": len(found)},
    )
    return GazetteerBulkRead(rows=rows)


# ---------------------------------------------------------------------------
# Steering from the map (task P6-35)
# ---------------------------------------------------------------------------
#
# Writes through existing steering (a boost with an expiry, a search, a saved view),
# so each steer is reversible and logged. Refusals are in words.


def _steer_refused(exc: Exception) -> HTTPException:
    if isinstance(exc, AreaNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, mapsteer.AlreadyDone):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/map/areas/{area_id}/steer", response_model=MapSteerRead)
async def steer_area(
    area_id: int, body: MapSteerCreate, _: AdminAllowed, sess: WriteSession
) -> MapSteerRead:
    """More, less or watch one area of the map."""
    try:
        result = await mapsteer.steer_area(sess, area_id, body.action, actor=ACTOR, now=_now())
    except (AreaNotFound, ValueError, steering.InfeasibleWeights) as exc:
        await sess.rollback()
        raise _steer_refused(exc) from exc
    await sess.commit()
    log.info("map steer", extra={"area_id": area_id, "action": body.action})
    return MapSteerRead.model_validate(result)


@router.post("/map/noise/{mark}/restore", response_model=NoiseRestoreRead)
async def restore_noise(mark: str, _: AdminAllowed, sess: WriteSession) -> NoiseRestoreRead:
    """Undo one "this is noise" marking: every source it moved goes back (`P6-42`)."""
    restored = await mapsteer.restore_noise(sess, mark, actor=ACTOR, now=_now())
    if restored == 0:
        await sess.rollback()
        raise HTTPException(status_code=404, detail=f"No source carries the mark “{mark}”.")
    await sess.commit()
    log.info("map noise restored", extra={"mark": mark, "restored": restored})
    return NoiseRestoreRead(mark=mark, restored=restored)


@router.post("/map/suggest", response_model=MapSteerRead, status_code=201)
async def suggest_search(
    body: MapSuggestCreate, _: AdminAllowed, sess: WriteSession
) -> MapSteerRead:
    """Queue something new to search for, from empty space on the map."""
    try:
        result = await mapsteer.suggest_seed(
            sess, body.text, topic=body.topic, actor=ACTOR, now=_now()
        )
    except ValueError as exc:
        await sess.rollback()
        raise _steer_refused(exc) from exc
    await sess.commit()
    log.info("map suggestion", extra={"seed_task_ids": result.seed_task_ids})
    return MapSteerRead.model_validate(result)


# ---------------------------------------------------------------------------
# Ask the graph (tasks P6-06, P6-07)
# ---------------------------------------------------------------------------


@router.post("/chat/ask", response_model=ChatExchangeRead)
async def ask_the_graph(body: ChatAsk, _: AdminAllowed, sess: WriteSession) -> ChatExchangeRead:
    """One question answered from the corpus, both turns stored.

    Under Admin because it writes a thread and spends tokens. A model that cannot
    answer is stored with its reason, not returned as an error.
    """
    try:
        exchange = await chat.ask_corpus(
            sess,
            body.question,
            thread_id=body.thread_id,
            context_entity_ids=body.context_entity_ids,
            embed=embed_query,
            now=_now(),
        )
    except chat.ChatRefused as exc:
        await sess.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await sess.commit()
    return ChatExchangeRead(
        thread=ChatThreadRead.model_validate(exchange.thread),
        question=ChatMessageRead.model_validate(exchange.question),
        answer=ChatMessageRead.model_validate(exchange.answer),
    )
