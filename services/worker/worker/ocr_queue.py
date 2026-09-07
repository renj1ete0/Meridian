"""Queueing scans for OCR, never running it (task P1-13, spec §6.6).

The rule this module exists to enforce is one line of §6.6: **OCR never runs
inline** — it would stall the 23-hour loop for one document. So a scanned PDF
does not block, does not fail, and does not quietly vanish. It becomes a source
record that enters the graph as metadata-only, plus a row saying what it is
waiting for.

That row is *not* a promise the work will happen. §6.6 makes VLM and OCR passes
explicitly user-triggered — "the UI shows pending counts by type and estimated
cost; a button starts a batch" — because they are expensive and optional. What
this guarantees is only that a scan is **findable**, which is the thing that
fails silently otherwise: a scanned planning report extracted to nothing looks
exactly like a page with no content, and nobody goes looking for it again.

`ocr_applied` and `ocr_tier` are written on the source for the same reason. §6.6
is explicit that skipped OCR must be findable rather than inferred from an
absence, and an absence is what you get when nothing records the decision.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.logging import get_logger
from meridian_core.models import EnrichmentItem, Source

log = get_logger(__name__)

#: §6.6's two-tier split. Quality OCR is VLM-based and runs on the Fedora box;
#: `cheap` is OCRmyPDF/Tesseract on the Pi. Which tier a document deserves is a
#: judgement about the scan — multi-column planning reports need the expensive
#: one — that nothing at ingestion time can make, so the request is filed at the
#: tier the operator will choose from rather than guessed at here.
OCR_ITEM_TYPE = "ocr_quality"


async def enqueue_ocr(
    sess: AsyncSession, source_id: int, *, requested_by: str = "worker"
) -> EnrichmentItem | None:
    """File a scan for OCR, unless it is already filed. Flushes; does not commit.

    Returns the row, or None if one was already pending or running. Idempotent
    because a re-crawl of the same scanned PDF must not stack a second request —
    the pending count in the UI is a number an operator makes a spending
    decision from, and inflating it with duplicates makes that decision wrong.
    """
    existing = await sess.scalar(
        select(EnrichmentItem).where(
            EnrichmentItem.item_type == OCR_ITEM_TYPE,
            EnrichmentItem.target_id == source_id,
            EnrichmentItem.status.in_(["pending", "queued", "running"]),
        )
    )
    if existing is not None:
        return None

    item = EnrichmentItem(
        item_type=OCR_ITEM_TYPE,
        target_id=source_id,
        status="pending",
        requested_by=requested_by,
    )
    sess.add(item)
    await sess.flush()
    log.info("scan queued for OCR", extra={"source_id": source_id, "item_id": item.item_id})
    return item


async def mark_scanned(sess: AsyncSession, source: Source) -> None:
    """Record on the source that it is a scan nothing has read yet.

    `ocr_applied=False` with `ocr_tier="none"` is not the same as never having
    looked: `text_available` says there is no text, and these two say why and
    what would fix it. §6.6 asks for exactly this — "record `ocr_applied`
    explicitly so skipped documents are findable".
    """
    source.ocr_applied = False
    source.ocr_tier = "none"
    source.text_available = False
    await sess.flush()


async def pending_ocr_count(sess: AsyncSession) -> int:
    """How many scans are waiting. The number §6.6's UI puts on the button."""
    return (
        await sess.scalar(
            select(func.count())
            .select_from(EnrichmentItem)
            .where(
                EnrichmentItem.item_type == OCR_ITEM_TYPE,
                EnrichmentItem.status == "pending",
            )
        )
        or 0
    )
