"""DTOs for deployment-wide display settings (task `B-145`, ADR 0009)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..timefmt import is_zone


class DisplaySettingsRead(BaseModel):
    """How times are shown: the zone, and how a reader would say it now."""

    #: An IANA zone name, e.g. ``Asia/Singapore``.
    display_timezone: str
    #: ``GMT+8`` and the like, at the moment of the request.
    label: str


class DisplayZoneEdit(BaseModel):
    """A new display zone; refused unless it is an IANA name this server knows."""

    model_config = ConfigDict(extra="forbid")

    display_timezone: str = Field(min_length=1, max_length=64)

    @field_validator("display_timezone")
    @classmethod
    def _known(cls, value: str) -> str:
        value = value.strip()
        if not is_zone(value):
            raise ValueError(f"{value!r} is not an IANA time zone (e.g. Asia/Singapore)")
        return value
