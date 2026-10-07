"""The gazetteer (spec §5.6).

Domain vocabulary loaded into spaCy's ``EntityRuler`` ahead of statistical NER, and
the alias table entity resolution uses. Seeded by hand, grown by acronym harvest. See
docs/features/places-and-terms.md#the-gazetteer.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

GAZETTEER_ENTITY_TYPE = constrained(
    "agency", "scheme", "infrastructure", "metric", "concept", name="gazetteer_entity_type"
)

GAZETTEER_SOURCE = constrained("manual", "auto_acronym", "model_proposed", name="gazetteer_source")


class GazetteerTerm(Base, TimestampMixin):
    __tablename__ = "gazetteer"

    term_id: Mapped[int] = pk()

    canonical: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    entity_type: Mapped[str] = mapped_column(GAZETTEER_ENTITY_TYPE, nullable=False)
    topic_labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Which country this expansion belongs to; NULL for genuinely global terms.
    # The same three-letter agency acronym routinely expands to different bodies
    # in different countries.
    jurisdiction: Mapped[str | None] = mapped_column(Text, index=True)

    # True when the surface form collides (with a common word, another domain, or
    # another term in the same country). The table then offers candidates only; the
    # resolver decides from context or leaves the mention unresolved (§5.5).
    ambiguous: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )

    source: Mapped[str] = mapped_column(
        GAZETTEER_SOURCE, nullable=False, default="manual", server_default="manual"
    )

    # Model-proposed terms land as approved=false and are confirmed in the UI —
    # a two-minute weekly task, or auto-approved above a frequency threshold.
    approved: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # The third state (`P6-13`): rejected, kept as a tombstone so the next harvest does
    # not file it again. Clearing it returns the term to the queue.
    rejected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Not unique on canonical alone: one surface form legitimately has
        # several expansions across jurisdictions and contexts.
        Index("ix_gazetteer_canonical", "canonical"),
        UniqueConstraint(
            "canonical", "jurisdiction", "entity_type", name="uq_gazetteer_term_scope"
        ),
        # The EntityRuler load: approved terms only.
        Index("ix_gazetteer_approved_type", "approved", "entity_type"),
        # The approval queue (`P6-13`): proposed, undecided, most corroborated
        # first. Partial, because once the harvest has run for a week the
        # undecided set is the minority and everything else has a verdict.
        Index(
            "ix_gazetteer_pending",
            "occurrence_count",
            postgresql_where=text("NOT approved AND rejected_at IS NULL"),
        ),
        # The harvest's two lookups per definition (`B-85`): a canonical name
        # case-insensitively, and every row claiming an alias. Without them each
        # was a scan of the table the harvest itself keeps growing.
        Index("ix_gazetteer_canonical_lower", func.lower(text("canonical"))),
        Index("ix_gazetteer_aliases", "aliases", postgresql_using="gin"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GazetteerTerm {self.canonical!r} {self.entity_type} approved={self.approved}>"
