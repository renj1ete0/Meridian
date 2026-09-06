"""Entities, edges, and the attribute system (spec §5.4, §5.5, §7).

Modelled as relational adjacency tables rather than Apache AGE storage. The
scaffold's `graph.py` specifies "traversal queries, recursive CTEs", and §12.1
loads a *filtered subgraph* client-side and runs topology (Louvain, centrality,
pathfinding) in graphology rather than in the database. Recursive CTEs over
these tables serve that retrieval directly; AGE can be layered on later (P4-01)
without the schema changing.

Two invariants worth restating because the columns exist to enforce them:

- Every edge names the chunks that justify it (``supporting_chunk_ids``). An
  edge without provenance is not assertable (§2 principle 3).
- Contradictions are kept, not resolved. When sources conflict, both edges live
  and the pair is marked ``contested_with`` (§9).
"""

from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meridian_core.db import Base

from .mixins import ProvenanceMixin, TimestampMixin, constrained, pk
from .source import EMBEDDING_DIM

# Typed ontology per domain, not one generic "concept" (§5.4). Shared types
# first, then the AV/ConOps set.
NODE_TYPE = constrained(
    "concept",
    "place",
    "organisation",
    "intervention",
    "finding",
    "source",
    "use_case",
    "odd",
    "vehicle_class",
    "service_model",
    "stakeholder",
    "failure_mode",
    "regulatory_requirement",
    "annotation",
    name="node_type",
)

# Observable properties, not verdicts (§8). The model is never asked whether a
# source is biased or true — it reports the position argued and how hedged it is.
STANCE = constrained("supports", "opposes", "mixed", "neutral", "unclear", name="stance")
CERTAINTY = constrained("hedged", "qualified", "asserted", "measured", name="certainty")

# The relation used for analogical expansion (§7.2). Named as a constant because
# a CHECK constraint below depends on it: a comparison without stated limits is
# rejected by the database, not merely discouraged in a prompt (§2 principle 6).
COMPARISON_RELATION = "comparable_to"

ATTRIBUTE_SCOPE = constrained("global", "topic_local", name="attribute_scope")
ATTRIBUTE_STATUS = constrained("active", "proposed", "retired", name="attribute_status")


class Entity(Base, TimestampMixin, ProvenanceMixin):
    """A resolved node. Resolution happens at write time, not as cleanup (§5.5).

    Duplicates that reach the graph propagate into edges before anyone notices,
    and conflation is invisible once done — which is why merges are reversible:
    the old id is retained as a redirect rather than deleted.
    """

    __tablename__ = "entities"

    entity_id: Mapped[int] = pk()

    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    node_type: Mapped[str] = mapped_column(NODE_TYPE, nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    topic_labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    description: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    # Reversibility: ids merged into this entity, and the redirect target if this
    # entity was itself merged away. Never delete the loser of a merge.
    merged_from: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    redirects_to: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="SET NULL"), index=True
    )

    # Set for annotation nodes — the highest-quality layer in the system, and the
    # one that actually reflects the user's thinking (§12.5).
    is_annotation: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )

    __table_args__ = (
        # Blocking step of entity resolution: candidates of the same type only.
        # Never merge across node types (§5.5).
        Index("ix_entities_type_name", "node_type", "canonical_name"),
        UniqueConstraint("canonical_name", "node_type", name="uq_entities_canonical_name_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Entity {self.entity_id} {self.node_type}:{self.canonical_name!r}>"


class Edge(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "edges"

    edge_id: Mapped[int] = pk()

    from_node: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    to_node: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    topic_labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Without this the edge is not assertable and cannot be re-derived (§2.3).
    supporting_chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)

    confidence: Mapped[float | None] = mapped_column(Float)
    stance: Mapped[str | None] = mapped_column(STANCE)
    certainty: Mapped[str | None] = mapped_column(CERTAINTY)

    # Contested nodes are the highest-value nodes: they locate live debates, and
    # a graph that silently resolves conflicts hides the interesting part (§9).
    contested_with: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))

    # Analogical expansion (§7.2) decomposes a context into attributes and
    # searches each axis independently, so a comparison is only meaningful
    # alongside the axis it runs on and the point where it breaks down.
    # "Singapore is equatorial, therefore Jakarta's findings apply" is the
    # shallow inference this guards against: the two share climate and almost
    # nothing on governance capacity or income. Comparisons without stated
    # limits are how bad policy papers get written.
    similarity_dimension: Mapped[str | None] = mapped_column(Text)
    disanalogy: Mapped[str | None] = mapped_column(Text)

    # When the *fact* held — distinct from created_at (when we wrote the edge),
    # produced_at (when a model derived it), and Source.publication_date (when
    # the evidence was published). Without this, an AV pilot that ran 2019–2021
    # is indistinguishable from one still running, and the difference is only
    # recoverable by re-reading the chunk. Null means open-ended or unknown.
    valid_from: Mapped[dt.date | None] = mapped_column(Date, index=True)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, index=True)

    created_by: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Traversal in both directions, for recursive-CTE neighbourhood queries.
        Index("ix_edges_from", "from_node", "relation_type"),
        Index("ix_edges_to", "to_node", "relation_type"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_period_ordered",
        ),
        # §7.2 is unambiguous: "Every comparison edge stores both the dimension
        # of similarity and the disanalogy." Enforced here so a model cannot
        # emit a bare comparison, however the prompt is worded.
        CheckConstraint(
            f"relation_type <> '{COMPARISON_RELATION}' "
            "OR (similarity_dimension IS NOT NULL AND disanalogy IS NOT NULL)",
            name="comparison_states_its_limits",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Edge {self.edge_id} {self.from_node}-[{self.relation_type}]->{self.to_node}>"


class AttributeDefinition(Base, TimestampMixin):
    """The attribute schema itself (§7.1, §7.3).

    Hard cap of roughly a dozen active attributes, with auto-retirement of the
    lowest-utility one when a stronger candidate qualifies. Retirement requires
    failing two or three consecutive audits — every schema change triggers
    backfill cost, so slow-moving schema is a feature, not friction.
    """

    __tablename__ = "attribute_definitions"

    attribute_id: Mapped[int] = pk()

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(
        ATTRIBUTE_SCOPE, nullable=False, default="global", server_default="global"
    )
    topic: Mapped[str | None] = mapped_column(Text, index=True)  # set when topic_local
    status: Mapped[str] = mapped_column(
        ATTRIBUTE_STATUS, nullable=False, default="active", server_default="active"
    )

    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    # Audit signals (§7.3): discrimination is entropy across applicable entities;
    # explanatory power counts appearances in cross-topic edges and contradiction
    # resolutions, which is the strongest signal of the four.
    discrimination: Mapped[float | None] = mapped_column(Float)
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    consecutive_audit_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_audited_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AttributeDefinition {self.name} {self.scope} {self.status}>"


class AttributeValue(Base, TimestampMixin, ProvenanceMixin):
    """One attribute assigned to one entity, with the chunk that justified it."""

    __tablename__ = "attribute_values"

    value_id: Mapped[int] = pk()

    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    attribute_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("attribute_definitions.attribute_id", ondelete="CASCADE"),
        nullable=False,
    )

    value: Mapped[str | None] = mapped_column(Text)
    value_numeric: Mapped[float | None] = mapped_column(Float)
    value_json: Mapped[dict | None] = mapped_column(JSONB)

    confidence: Mapped[float | None] = mapped_column(Float)
    supporting_chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    tagged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    entity: Mapped[Entity] = relationship()
    attribute: Mapped[AttributeDefinition] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "attribute_id",
            "schema_version",
            name="uq_attribute_values_entity_attribute_version",
        ),
        Index("ix_attribute_values_attribute", "attribute_id", "entity_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AttributeValue e={self.entity_id} a={self.attribute_id} {self.value!r}>"


class Observation(Base, TimestampMixin, ProvenanceMixin):
    """A measured quantity attached to an entity.

    Added after the §14.3 design exercise: the schema could not answer "what is
    the market share of AVs by deployment?" at all. A measurement is
    ``(metric, value, unit, denominator, geography, period)``, and none of that
    fits a ``finding`` node — whose only text fields are a name and a
    description — nor ``attribute_values``, which holds the ~12 capped,
    audited comparison dimensions of §7.3 rather than arbitrary facts.

    **One entity, many observations.** The subject is the recurring claim
    ("AV share of Phoenix taxi trips"), not the individual reading, so a time
    series accumulates against one stable node. Modelling each reading as its
    own ``finding`` entity would produce thousands of near-identical
    ``canonical_name`` values, and entity resolution — which matches on exactly
    string similarity, embedding cosine and shared neighbours (§5.5) — would
    eventually merge two quarters into one. Conflation is invisible once done,
    which makes that failure worse than the duplication it looks like.

    Keeping observations in the graph rather than in a side store means edges,
    contested pairs, and path traversal all reach them unchanged.
    """

    __tablename__ = "observations"

    observation_id: Mapped[int] = pk()

    # The finding, vehicle_class, place or organisation being measured.
    subject_entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )

    metric: Mapped[str] = mapped_column(Text, nullable=False)
    value_numeric: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)

    # "3.2 percent" is meaningless without it — percent *of what* (§8: extract
    # the structure, do not let the model summarise it away).
    denominator: Mapped[str | None] = mapped_column(Text)

    geography_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.entity_id", ondelete="SET NULL"), index=True
    )

    # What the number describes, not when it was published. Temporal decay
    # (§9) is topic-dependent and needs the measurement's own period.
    period_start: Mapped[dt.date | None] = mapped_column(Date, index=True)
    period_end: Mapped[dt.date | None] = mapped_column(Date)

    # survey | operator disclosure | modelled | estimated — how it was arrived
    # at, which is the observable property that matters rather than a verdict
    # on whether the figure is trustworthy (§8).
    method: Mapped[str | None] = mapped_column(Text)

    # Open-ended dimensions the measurement is broken down by: user segment,
    # time of day, trip purpose. A column per dimension does not scale — "price
    # sensitivity for students at peak on the MRT" has three at once — and
    # pushing them into the subject entity instead would produce thousands of
    # near-identical node names for entity resolution to mis-merge (§5.5).
    # JSONB is what the stack already uses to absorb schema evolution (§4).
    qualifiers: Mapped[dict | None] = mapped_column(JSONB)

    confidence: Mapped[float | None] = mapped_column(Float)
    supporting_chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)

    # Two sources reporting different figures for the same metric and period is
    # signal, not error — the same treatment edges get (§9).
    contested_with: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))

    subject: Mapped[Entity] = relationship(foreign_keys=[subject_entity_id])

    __table_args__ = (
        Index("ix_observations_subject_metric", "subject_entity_id", "metric"),
        # GIN so filtering by a qualifier stays fast as the corpus grows.
        Index("ix_observations_qualifiers", "qualifiers", postgresql_using="gin"),
        # "this metric over time", which is the query a time series exists for.
        Index("ix_observations_metric_period", "metric", "period_start"),
        CheckConstraint(
            "value_numeric IS NOT NULL OR value_text IS NOT NULL",
            name="has_a_value",
        ),
        CheckConstraint(
            "period_end IS NULL OR period_start IS NULL OR period_end >= period_start",
            name="period_ordered",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Observation {self.observation_id} {self.metric}="
            f"{self.value_numeric if self.value_numeric is not None else self.value_text}"
            f"{' ' + self.unit if self.unit else ''}>"
        )
