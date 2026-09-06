"""The gazetteer (spec §5.6).

Generic NER does not know domain entities. "Land Transport Authority" may resolve
as an ORG; "Electronic Road Pricing", "Silver Zone" and "ODD" will not. This table
is loaded into spaCy's ``EntityRuler`` at worker startup so gazetteer matches take
precedence over statistical NER.

It is bootstrapped, not hand-written: ~50 seeded manually, then grown by
auto-harvesting the ``Full Name Here (ACRONYM)`` pattern — extremely high-yield in
government and academic documents, and one regex over already-extracted text.

Its alias lists are exactly the alias matching entity resolution needs, so the two
share one table rather than duplicating.
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, Text, text
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

    __table_args__ = (
        Index("ix_gazetteer_canonical", "canonical", unique=True),
        # The EntityRuler load: approved terms only.
        Index("ix_gazetteer_approved_type", "approved", "entity_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GazetteerTerm {self.canonical!r} {self.entity_type} approved={self.approved}>"
