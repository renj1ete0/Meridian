"""Areas: nested clusters of passages (task P6-30).

Derived data, rebuilt wholesale by ``worker.areas``. A build is one complete
clustering; reads use the newest build, and older builds are removed by the
job (all but the previous one, which a later build reads for stable
positions). Deleting them breaks no promise that nothing is deleted: an area
cites nothing and is re-derived from the passages every time.
"""

from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import pk
from .source import EMBEDDING_DIM


class AreaBuild(Base):
    __tablename__ = "area_builds"

    build_id: Mapped[int] = pk()
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Passages assigned in this build.
    passages: Mapped[int] = mapped_column(Integer, nullable=False)
    #: How many levels and clusters per level, and the fit sample size.
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Area(Base):
    __tablename__ = "areas"

    area_id: Mapped[int] = pk()
    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("area_builds.build_id", ondelete="CASCADE"), nullable=False
    )
    #: 1 = region (coarsest) … the deepest level holds the leaves.
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("areas.area_id", ondelete="CASCADE")
    )
    #: Most distinctive terms first. The name is the first three.
    terms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    passages: Mapped[int] = mapped_column(Integer, nullable=False)
    sources: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``{source_tier: passages}``.
    tier_mix: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: When the newest passage in the area was stored.
    newest_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Unit-length mean of the passages' vectors: what "similar area" and a
    #: stable position across builds are measured by.
    centroid: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    #: Position in the parent's frame (the whole map's for level 1), in
    #: [-1, 1]; the map spaces circles apart at draw time.
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_areas_build_level", "build_id", "level"),)


class AreaMember(Base):
    """Which leaf area each passage fell in, per build."""

    __tablename__ = "area_members"

    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("area_builds.build_id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id", ondelete="CASCADE"), primary_key=True
    )
    area_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("areas.area_id", ondelete="CASCADE"), nullable=False, index=True
    )
