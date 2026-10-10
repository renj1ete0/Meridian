"""The "Ask the graph" panel's wire shapes (tasks P6-06, P6-07)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .enums import ChatRole


class ChatAsk(BaseModel):
    """One question, optionally in a thread and about a selection (§12.4)."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    thread_id: int | None = None
    #: The nodes the reader has selected; bounded, since each is looked up.
    context_entity_ids: list[int] = Field(default_factory=list, max_length=20)


class ChatCitationRead(BaseModel):
    n: int
    chunk_id: int
    source_id: int
    url: str
    title: str | None = None
    source_tier: str


class ChatNodeRead(BaseModel):
    ref: int
    entity_id: int
    name: str
    contested: bool


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: int
    thread_id: int
    role: ChatRole
    text: str
    context_entity_ids: list[int] | None = None
    citations: list[ChatCitationRead] | None = None
    nodes: list[ChatNodeRead] | None = None
    agent_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    created_at: dt.datetime


class ChatThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    thread_id: int
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime


class ChatStatusRead(BaseModel):
    """Whether a question can be answered in prose here at all (`B-183`)."""

    available: bool


class ChatThreadsRead(BaseModel):
    threads: list[ChatThreadRead]
    total: int


class ChatThreadDetailRead(BaseModel):
    thread: ChatThreadRead
    messages: list[ChatMessageRead]


class ChatExchangeRead(BaseModel):
    thread: ChatThreadRead
    question: ChatMessageRead
    answer: ChatMessageRead
