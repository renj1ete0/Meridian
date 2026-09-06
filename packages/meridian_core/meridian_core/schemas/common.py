"""Shared field types and mixins reused across every DTO in this package.

A validation rule fixed in one place — confidence is a probability, quality
tier is a small ordinal, provenance is mandatory — must not drift between the
dozen schemas that reuse it, so those rules live here once rather than being
retyped per module (AGENTS.md: "pydantic for all boundaries").
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
    """Base for every ``*Create`` DTO: unknown fields are an error.

    Pydantic ignores unrecognised keys by default, which is the wrong default at
    this boundary. These schemas receive model-generated tool calls (§11.6), and
    an agent inventing a field — or trying to set a state-machine column the
    worker owns, like ``queue.status`` — must fail loudly rather than have its
    intent silently dropped. Server-side validation is the control that makes
    autonomous writes safe (§11.8); silently discarding input is not validation.
    """

    model_config = ConfigDict(extra="forbid")


class ProvenanceFields(CreateBase):
    """Mirrors ``meridian_core.models.mixins.ProvenanceMixin``.

    Every table carrying that mixin requires this on write so quality tier can
    only move up automatically — a lower tier must never silently overwrite a
    higher one (§11.12), which is only checkable because every row records the
    tier that produced it.
    """

    produced_by: str | None = None
    model: str | None = None
    quality_tier: QualityTier | None = None
    produced_at: dt.datetime | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION
