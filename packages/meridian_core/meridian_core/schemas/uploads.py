"""DTOs for the document connector (task B-264, mirrors ``meridian_core.models.uploads``).

The form an upload arrives in is multipart, so its fields are validated in
:mod:`meridian_core.uploads` rather than by a ``Create`` model here.
See docs/features/connector-api.md.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict

from .enums import UploadStatus


class UploadRead(BaseModel):
    """One handed-in document and what became of it. The inbox path stays internal."""

    model_config = ConfigDict(from_attributes=True)

    upload_id: int
    sha256: str
    filename: str
    media_type: str
    size_bytes: int
    doi: str | None
    title: str | None
    description: str | None
    topic: str | None
    source_url: str | None
    source_tier: str | None
    status: UploadStatus
    task_id: int | None
    #: The `sources` row the document became, once the worker has read it.
    source_id: int | None
    error: str | None
    client_address: str | None
    user_agent: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class WantedPaper(BaseModel):
    """A paper Meridian found and could not retrieve: no provider had a legal copy."""

    doi: str
    #: From any stored source that carries this DOI; usually unknown.
    title: str | None = None
    topic: str | None = None
    #: The page whose references named it first, if recorded.
    cited_from_source_id: int | None = None
    cited_from_url: str | None = None
    first_seen: dt.datetime
    #: When the resolver last answered; null for rows settled before `B-264`.
    last_tried: dt.datetime | None = None
    reason: str


class WantedPage(BaseModel):
    """One page of the wanted list, with the cursor for the next (null at the end)."""

    kind: str
    items: list[WantedPaper]
    next: str | None = None
