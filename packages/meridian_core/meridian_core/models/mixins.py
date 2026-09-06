"""Column mixins and shared enum helpers.

Provenance is the reason this module exists. Spec §2 principle 3 requires every
node, edge, and tag to record what justified it, and §11.12 requires every
artifact to record which model produced it so quality can be improved
retroactively. Both are easy to forget on a new table, so they live here rather
than being retyped per model.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

from sqlalchemy import BigInteger, DateTime, Enum, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

# VARCHAR + CHECK rather than native Postgres enums: adding a value to a native
# enum needs ALTER TYPE and cannot run inside some migrations, and this schema is
# expected to gain statuses and tiers as it grows (§7.3 schema evolution).
def constrained(*values: str, name: str) -> Enum:
    """A string column constrained to ``values`` by a CHECK, not a native enum.

    ``create_constraint=True`` is not optional here. SQLAlchemy has defaulted it
    to False since 1.4, so without it these columns are plain VARCHAR that
    silently accept any string — which defeats the entire point, and is only
    visible if you actually try to insert a bad value.
    """
    return Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


# Ordinal from the agent registry: local small = 1, local large = 2,
# hosted mid = 3, hosted frontier = 4. Distinct from cost_tier — cheap and good
# are different axes, and conflating them makes routing decisions wrong (§11.12).
QUALITY_TIER_MIN: Final[int] = 1
QUALITY_TIER_MAX: Final[int] = 4

# Bumped when the extraction or tagging schema changes, so a half-finished
# backfill reads as "untagged" rather than "thin evidence" (§7.3).
CURRENT_SCHEMA_VERSION: Final[int] = 1


class TimestampMixin:
    """``created_at``, set by the database rather than the application clock."""

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ProvenanceMixin:
    """Which model produced this artifact, and under which schema.

    The invariant this supports: quality tier only ever moves up automatically.
    A lower tier never silently overwrites a higher one (§11.12), which is only
    checkable because every row carries the tier that produced it.
    """

    produced_by: Mapped[str | None] = mapped_column(Text, index=True)  # agent_id
    model: Mapped[str | None] = mapped_column(Text)  # exact model string
    quality_tier: Mapped[int | None] = mapped_column(Integer, index=True)
    produced_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, default=CURRENT_SCHEMA_VERSION, nullable=False
    )


def pk() -> Mapped[int]:
    """Standard surrogate primary key."""
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)
