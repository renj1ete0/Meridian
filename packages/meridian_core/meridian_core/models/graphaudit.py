"""The graph audit: sampled relations, merges and matches, graded by a person (`P7-10`, §14.1).

A draw is one reproducible random sample over a window of time; each sampled item is kept
with what the grader was shown, so a grade refers to the claim as it stood when drawn. A
grade never changes the graph. See docs/features/knowledge-graph.md#the-graph-audit.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

#: What a sampled item is: a relation (`edges`), a merge (`merge_log`) or a mention matched to
#: an existing node at resolution (`resolution_matches`, `B-273`).
AUDIT_KIND = constrained("edge", "merge", "match", name="audit_kind")

#: A person's verdict. `unsure` is counted but left out of precision.
AUDIT_GRADE = constrained("correct", "wrong", "unsure", name="audit_grade")


class GraphAuditDraw(Base, TimestampMixin):
    """One sample: the window it covered, the seed that ordered it, and what it drew from."""

    __tablename__ = "graph_audit_draws"

    draw_id: Mapped[int] = pk()

    #: Orders the population (`md5(seed:id)`), so the same seed over the same rows draws the
    #: same sample.
    seed: Mapped[str] = mapped_column(Text, nullable=False)

    #: Half-open, `[start, end)`. Unique on the start, so two schedulers racing to draw the
    #: same period cannot both record one.
    window_start: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, unique=True
    )
    window_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: How many items each sample was drawn from: a precision over 30 of 30 and over 30 of
    #: 3,000 are different statements.
    edge_population: Mapped[int] = mapped_column(Integer, nullable=False)
    merge_population: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Zero on draws made before matches were recorded (`B-273`).
    match_population: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    #: Merges in the window already reversed: left out of the sample (a person has judged
    #: them), counted because "merged then reversed" is a signal in itself.
    merges_reversed: Mapped[int] = mapped_column(Integer, nullable=False)

    #: `scheduler`, or the person who asked for a draw.
    drawn_by: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("window_end > window_start", name="audit_window_ordered"),
        CheckConstraint(
            "edge_population >= 0 AND merge_population >= 0 AND match_population >= 0"
            " AND merges_reversed >= 0",
            name="audit_counts_not_negative",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GraphAuditDraw {self.draw_id} {self.window_start:%Y-%m-%d}>"


class GraphAuditSample(Base, TimestampMixin):
    """One sampled relation, merge or match, what the grader was shown, and the grade."""

    __tablename__ = "graph_audit_samples"

    sample_id: Mapped[int] = pk()

    draw_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("graph_audit_draws.draw_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(AUDIT_KIND, nullable=False)

    #: `edges.edge_id`, `merge_log.merge_id` or `resolution_matches.match_id`. Not a foreign
    #: key, as `merge_log`'s ids are not: a grade must outlive the row it judged.
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Its place in the draw's order, so the page lists items as they were drawn.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The claim as drawn: names, relation, passages; for a merge or a match, both sides and
    #: the score.
    shown: Mapped[dict] = mapped_column(JSONB, nullable=False)

    #: Who produced the item: an edge's or a matched mention's agent, model and tier; a
    #: merge's `decided_by`.
    #: Columns rather than JSON, so precision can be split by them.
    produced_by: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    quality_tier: Mapped[int | None] = mapped_column(Integer)

    grade: Mapped[str | None] = mapped_column(AUDIT_GRADE)
    note: Mapped[str | None] = mapped_column(Text)
    graded_by: Mapped[str | None] = mapped_column(Text)
    graded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("draw_id", "kind", "item_id", name="uq_audit_sample_item"),
        # A grade without a time and a grader, or the other way round, is half a write.
        CheckConstraint(
            "(grade IS NULL) = (graded_at IS NULL) AND (grade IS NULL) = (graded_by IS NULL)",
            name="audit_graded_together",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GraphAuditSample {self.sample_id} {self.kind}:{self.item_id}>"
