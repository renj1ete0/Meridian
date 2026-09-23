"""The corpus map's wire shape (task P6-26). Mirrors ``meridian_core.corpusmap``."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class MapPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    source_id: int
    x: float
    y: float
    topic: str | None
    title: str | None
    url: str
    snippet: str


class CorpusMapRead(BaseModel):
    """A projected sample of the embedding space.

    ``eligible`` beside ``len(points)`` is what tells a reader the picture is a
    sample, and ``explained_variance`` how much of the space two axes can show —
    both there so the map cannot be mistaken for more than it is.
    """

    model_config = ConfigDict(from_attributes=True)

    as_of: dt.datetime
    points: list[MapPointRead]
    eligible: int
    explained_variance: tuple[float, float]
