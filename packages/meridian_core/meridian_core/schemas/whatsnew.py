"""What is new, by topic and kind (task B-237). See docs/features/whats-new.md."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from .enums import DocKind, SourceTier

#: What "new" is measured by: when a source entered the corpus, or the date it carries.
NewBasis = Literal["added", "published"]

#: What a finding is: a link between two concepts, or a value tagged on one.
FindingKind = Literal["link", "tag"]


class NewExcerptRead(BaseModel):
    """A source's best passage, chosen mechanically, cut to one line."""

    chunk_id: int
    text: str
    page_or_offset: int | None


class NewSourceRead(BaseModel):
    """One new source, as the list shows it."""

    source_id: int
    title: str | None
    url: str
    #: The host it was served from, `www.` dropped.
    site: str | None
    publisher: str | None
    doc_kind: DocKind | None
    source_tier: SourceTier
    published: dt.date | None
    #: When it entered the corpus.
    added: dt.datetime
    #: Its other shown topics; it is listed once, under its leading one.
    other_topics: list[str]
    #: None when no passage of it is readable.
    excerpt: NewExcerptRead | None


class NewKindCountRead(BaseModel):
    """How many new sources of one kind; ``doc_kind`` None is unclassified."""

    doc_kind: DocKind | None
    sources: int


class NewTopicRead(BaseModel):
    """One topic's new sources: how many, of which kinds, and the newest few."""

    topic: str
    description: str | None
    #: Every new source led by this topic, listed or not.
    total: int
    #: The same sources by kind, most first.
    by_kind: list[NewKindCountRead]
    #: The newest, at most the asked-for number; listings are counted, never listed.
    sources: list[NewSourceRead]


class NewCitationRead(BaseModel):
    """The passage a finding was derived from, enough to open and cite it."""

    chunk_id: int
    source_id: int
    title: str | None
    url: str
    page_or_offset: int | None
    text: str


class NewFindingRead(BaseModel):
    """A link or tag synthesis added in the window, with its first readable citation."""

    kind: FindingKind
    #: ``edge_id`` for a link, ``value_id`` for a tag.
    finding_id: int
    subject_id: int
    subject: str
    #: The relation's type for a link, the attribute's name for a tag.
    relation: str
    #: The other concept, for a link; None for a tag.
    object_id: int | None
    #: The other concept's name for a link, the tagged value for a tag.
    object: str | None
    #: Its leading shown topic; None when it carries none.
    topic: str | None
    added: dt.datetime
    citation: NewCitationRead


class WhatsNewRead(BaseModel):
    """What is new in the evidence since an instant, by topic (`B-237`)."""

    since: dt.datetime
    #: Nothing created after this counts, so the answer is one consistent moment.
    as_of: dt.datetime
    basis: NewBasis
    #: Heaviest topic first; topics with nothing new are kept, with zero.
    topics: list[NewTopicRead]
    #: New sources on none of the shown topics: labelled off-topic or not labelled yet.
    elsewhere: int
    findings: list[NewFindingRead]
    #: Every finding in the window with a readable citation, listed or not.
    findings_total: int
