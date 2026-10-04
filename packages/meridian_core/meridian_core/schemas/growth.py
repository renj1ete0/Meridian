"""DTOs for corpus growth (task `B-140`, ADRs 0005 and 0010). Mirrors ``meridian_core.growth``."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class GrowthCount(BaseModel):
    """A total, and how much of it arrived inside the window."""

    total: int
    in_window: int


class GrowthDay(BaseModel):
    """One calendar day in the display zone."""

    day: dt.date
    #: Whether the crawl fetched anything that day. False is drawn as a gap, not a zero.
    crawled: bool
    #: Pages kept that day about exactly one topic, by topic.
    by_topic: dict[str, int]
    #: Pages kept that day about two or more of the shown topics; counted once.
    multi: int
    #: Hosts whose first kept page arrived that day.
    new_sites: int


class TopicGrowth(BaseModel):
    """One topic's passages, and its daily new pages across the window."""

    topic: str
    #: Stable colour slot: the topic's place in the sorted list of every topic, so a filter
    #: never repaints the others.
    series: int
    passages: GrowthCount
    daily: list[int]


class MapSize(BaseModel):
    computed_at: dt.datetime
    regions: int
    areas: int
    sub_areas: int
    weak_areas: int


class GrowthRead(BaseModel):
    as_of: dt.datetime
    #: The display zone the days are counted in (ADR 0009).
    zone: str
    #: The window in days; None for all time.
    days: int | None
    first_day: dt.date | None
    #: The topics shown; every topic with pages when none was chosen.
    topics: list[str]
    #: Every topic, with its colour slot, for the filter.
    all_topics: list[TopicGrowth]
    passages: GrowthCount
    sources: GrowthCount
    sites: GrowthCount
    concepts: GrowthCount
    links: GrowthCount
    daily: list[GrowthDay]
    map_now: MapSize | None
    #: Every recorded build inside the window, oldest first.
    map_history: list[MapSize]
    #: When the map's history begins; nothing before it was kept.
    map_history_from: dt.datetime | None
