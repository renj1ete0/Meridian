"""DTOs for annotation nodes (task P6-05, spec §12.5).

An annotation is an ``entities`` row, so these could have been the graph DTOs
with a flag. They are not, and the difference is deliberate: this is a *reading*
surface, and ``canonical_name`` is the graph's word for what a reader calls the
title of a note. A notes API that speaks in ``canonical_name`` and
``is_annotation`` has pushed the schema through to the person using it.

**No provenance fields on the way in.** Every other ``*Create`` in this package
inherits :class:`ProvenanceFields`, because a model that produced an artifact
has to say which model it was (§11.12). Here the answer is fixed — a person
wrote it — and accepting it as input would mean anything could claim to be the
reader's own thinking, which is the one property this layer sells. The server
sets it; a request that tries is refused by ``extra="forbid"``.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

#: How many nodes one note may be about. A note spanning two or three nodes is
#: the interesting case — "these disagree", "same programme, different name" —
#: and one spanning fifty is a tag. The cap is also what stops a single request
#: writing fifty edges.
MAX_ABOUT = 20

#: How many passages one note may cite. Higher than `MAX_ABOUT` because a note
#: about one node can reasonably quote a document several times over.
MAX_CITED = 50

MAX_TITLE = 200


class AnnotationTarget(BaseModel):
    """A node a note is about, named rather than numbered.

    `P6-04`'s rule, applied here: a panel showing ``entity_id: 412`` asks the
    reader to resolve a foreign key by hand.
    """

    model_config = ConfigDict(from_attributes=True)

    entity_id: int
    canonical_name: str
    node_type: str


class AnnotationRead(BaseModel):
    entity_id: int
    title: str
    body: str | None
    about: list[AnnotationTarget]
    supporting_chunk_ids: list[int]
    topic_labels: list[str] | None

    #: Always the reserved human author. Carried in the payload rather than left
    #: to be looked up, because a client that has to fetch the node again to
    #: find out who wrote it will render the layer undifferentiated.
    produced_by: str

    #: When the text was last the author's — moved by a rewrite. Distinct from
    #: ``created_at``, which is when the row appeared: a list ordered by the
    #: second shows a note rewritten today in the position it had in March.
    produced_at: dt.datetime | None
    created_at: dt.datetime


class AnnotationCreate(BaseModel):
    """What a reader writes. See the module docstring for the absent fields."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str | None = None

    #: Entity ids. Empty is allowed: a thought that has not found its node yet
    #: is still worth keeping, and requiring a target would make the affordance
    #: unavailable exactly when somebody is reading something the graph does not
    #: cover yet — which is when it is most worth having.
    about: list[int] = Field(default_factory=list, max_length=MAX_ABOUT)

    #: The passages in front of the reader when they wrote it. §12.5 puts
    #: annotation inside reading, so this is usually not empty.
    supporting_chunk_ids: list[int] = Field(default_factory=list, max_length=MAX_CITED)

    topic_labels: list[str] | None = None


class AnnotationEdit(BaseModel):
    """A rewrite. Unset fields are left alone; ``about`` given is ``about`` replaced.

    Replacement rather than merge, because re-reading changes what a note is
    about — and a note that accumulated every node it was ever pointed at would
    end up attached to the reader's whole search history.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    body: str | None = None
    about: list[int] | None = Field(default=None, max_length=MAX_ABOUT)
    supporting_chunk_ids: list[int] | None = Field(default=None, max_length=MAX_CITED)
    topic_labels: list[str] | None = None


class AnnotationsRead(BaseModel):
    annotations: list[AnnotationRead]

    #: How many match the filter, not how many came back. A reader who has
    #: written four hundred notes is owed the number; a page that only ever
    #: reports its own length cannot tell them.
    total: int
