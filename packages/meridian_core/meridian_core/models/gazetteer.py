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

import datetime as dt

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, text
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

    # True when the surface form collides — with a common word, with another
    # domain, or with another term in the SAME country. Jurisdiction alone does
    # not settle it: within a single document a three-letter acronym can be a
    # domain term or an unrelated piece of business vocabulary, and a two-letter
    # one can have three readings at once.
    #
    # An ambiguous surface form therefore maps to SEVERAL rows here, and this
    # table can only offer candidates — it cannot decide. The resolver picks
    # using document context (co-occurring entities, the document's topic,
    # whether a full form appears nearby), and where context is insufficient it
    # must leave the mention UNRESOLVED rather than guess. That is §5.5's middle
    # band: a wrong resolution corrupts the graph invisibly, an unresolved
    # mention stays visible and fixable.
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

    # The third state (`P6-13`). `approved` is a boolean and the queue has three
    # answers: waiting, yes, and no. Without this, "no" can only mean deleting
    # the row — and the next harvest reads the same documents, finds the same
    # definition and files it again, so the queue refills with exactly the terms
    # somebody already turned down.
    #
    # A timestamp rather than a second boolean, because "when was this decided"
    # is the question asked of a rejection nobody remembers making. Clearing it
    # returns the term to the queue: a judgement made on two occurrences is
    # worth revisiting at twenty.
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
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GazetteerTerm {self.canonical!r} {self.entity_type} approved={self.approved}>"
