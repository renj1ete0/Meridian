"""Documents handed in through the connector (task B-264).

One row per distinct file (by sha256): what was sent, what the sender said it is, and what
became of it. The worker turns it into an ordinary `sources` row through the same path a
fetched document takes. See docs/features/connector-api.md.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

#: queued → done | failed. A failed upload stays failed; see the feature doc.
UPLOAD_STATUS = constrained("queued", "done", "failed", name="upload_status")


class Upload(Base, TimestampMixin):
    __tablename__ = "uploads"

    upload_id: Mapped[int] = pk()
    #: Hex digest of the bytes; the deduplication key.
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: As the sender named it. Provenance only: never part of a path.
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Relative to the inbox root (`MERIDIAN_INBOX_ROOT`), built from the digest.
    inbox_path: Mapped[str] = mapped_column(Text, nullable=False)

    # What the sender said, each validated before the row is written.
    doi: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    #: Checked against `SOURCE_TIER` in code, not by a CHECK here, so a new tier needs no
    #: second constraint to widen. `sources.source_tier` keeps its own CHECK.
    source_tier: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        UPLOAD_STATUS, nullable=False, default="queued", server_default="queued"
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("queue.task_id", ondelete="SET NULL")
    )
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sources.source_id", ondelete="SET NULL"), index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    # Who sent it, as far as the connector can tell.
    client_address: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_is_hex"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Upload {self.upload_id} {self.status} {self.filename!r}>"
