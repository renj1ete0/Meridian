"""Shared field types and mixins reused across every DTO in this package.

Validation rules that must not drift between schemas live here once.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from meridian_core.models.mixins import (
    CURRENT_SCHEMA_VERSION,
    QUALITY_TIER_MAX,
    QUALITY_TIER_MIN,
)

# A probability, not a percentage and not a raw model logit (spec §2, §8).
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Ordinal quality tier: 1 (local small) .. 4 (hosted frontier). Bounds come from
# mixins.py so a widened tier range on the model side widens here automatically
# (spec §11.12).
QualityTier = Annotated[int, Field(ge=QUALITY_TIER_MIN, le=QUALITY_TIER_MAX)]

# An edge or attribute value without at least one supporting chunk is not
# assertable and cannot be re-derived from source (spec §2 principle 3).
SupportingChunkIds = Annotated[list[int], Field(min_length=1)]


class CreateBase(BaseModel):
    """Base for every ``*Create`` DTO: unknown fields are an error (§11.6, §11.8).

    See docs/reference/data-model.md#boundary-schemas.
    """

    model_config = ConfigDict(extra="forbid")


class ProvenanceFields(CreateBase):
    """Mirrors ``meridian_core.models.mixins.ProvenanceMixin``; required on write (§11.12)."""

    produced_by: str | None = None
    model: str | None = None
    quality_tier: QualityTier | None = None
    produced_at: dt.datetime | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION
