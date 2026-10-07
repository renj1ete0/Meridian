"""Queueing scans for OCR, never running it (task P1-13, spec §6.6).

A scanned PDF becomes a metadata-only source plus a queue row, and `ocr_applied` /
`ocr_tier` on the source say why it has no text. The row is not a promise: OCR runs only
when the operator starts a batch. See docs/features/extraction.md#pdf.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.logging import get_logger
from meridian_core.models import EnrichmentItem, Source

log = get_logger(__name__)

#: §6.6's two-tier split (VLM-based quality OCR, OCRmyPDF/Tesseract cheap OCR). Nothing
#: at ingestion can judge which a scan needs; see docs/features/extraction.md#pdf.
OCR_ITEM_TYPE = "ocr_quality"


async def enqueue_ocr(
    sess: AsyncSession, source_id: int, *, requested_by: str = "worker"
) -> EnrichmentItem | None:
    """File a scan for OCR, unless it is already filed. Flushes; does not commit.

    Returns the row, or None if one was already pending or running: the pending count
    is what an operator decides spending from.
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

    `ocr_applied=False` with `ocr_tier="none"`: findable, not inferred from an absence.
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
