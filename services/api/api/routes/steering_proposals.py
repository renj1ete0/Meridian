"""Steering proposals in Admin (task P6-38, spec §10.1, §10.2).

A proposal applies by itself once its window passes; these routes are the
objection and the shortcut. **Accept** applies it now, through
:mod:`meridian_core.steering`, logged as the operator's change. **Reject**
means it never applies, and is logged with the reason if one is given — which
also keeps the pass from proposing for that topic again the next hour.

Writes, so they live under `/api/admin`, behind the admin gate, on the
writable role.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query

from meridian_core import steering, steering_proposals
from meridian_core.logging import get_logger
from meridian_core.schemas.steering_proposals import (
    SteeringProposalRead,
    SteeringProposalReject,
    SteeringProposalsRead,
)

from ..deps import AdminAllowed, WriteSession

log = get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: Who the log records for a decision made here — the same actor as every
#: other change made through Admin.
ACTOR = "user"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _refuse(exc: Exception) -> HTTPException:
    if isinstance(exc, steering_proposals.NotPending):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/proposals", response_model=SteeringProposalsRead)
async def list_proposals(
    _: AdminAllowed,
    sess: WriteSession,
    recent: int = Query(default=20, ge=0, le=100),
) -> SteeringProposalsRead:
    """What is waiting, soonest first, and what was recently decided."""
    return SteeringProposalsRead(
        pending=[
            SteeringProposalRead.model_validate(p) for p in await steering_proposals.pending(sess)
        ],
        recent=[
            SteeringProposalRead.model_validate(p)
            for p in await steering_proposals.recent(sess, limit=recent)
        ]
        if recent
        else [],
        window_hours=await steering_proposals.window_hours(sess),
    )


@router.post("/proposals/{proposal_id}/accept", response_model=SteeringProposalRead)
async def accept_proposal(
    proposal_id: int, _: AdminAllowed, sess: WriteSession
) -> SteeringProposalRead:
    try:
        proposal = await steering_proposals.accept(sess, proposal_id, actor=ACTOR, now=_now())
    except (LookupError, ValueError, steering.InfeasibleWeights) as exc:
        # Nothing committed: the proposal stays pending, and the next pass
        # supersedes it if its basis is gone.
        await sess.rollback()
        raise _refuse(exc) from exc
    await sess.commit()
    log.info("steering proposal accepted", extra={"proposal_id": proposal_id})
    return SteeringProposalRead.model_validate(proposal)


@router.post("/proposals/{proposal_id}/reject", response_model=SteeringProposalRead)
async def reject_proposal(
    proposal_id: int,
    _: AdminAllowed,
    sess: WriteSession,
    body: SteeringProposalReject | None = None,
) -> SteeringProposalRead:
    try:
        proposal = await steering_proposals.reject(
            sess,
            proposal_id,
            actor=ACTOR,
            note=body.reason if body else None,
            now=_now(),
        )
    except (LookupError, ValueError) as exc:
        await sess.rollback()
        raise _refuse(exc) from exc
    await sess.commit()
    log.info("steering proposal rejected", extra={"proposal_id": proposal_id})
    return SteeringProposalRead.model_validate(proposal)
