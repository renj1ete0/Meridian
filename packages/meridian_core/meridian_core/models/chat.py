"""Questions asked of the corpus, and what it answered (tasks P6-06, P6-07, §12.4).

A thread is a conversation; a message is one turn. `citations` and `nodes` are what the
server validated, not what the model wrote, and every answer records its agent, model and
token counts. See docs/features/ask-the-graph.md#threads.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

#: Who wrote a message.
CHAT_ROLE = constrained("user", "assistant", name="chat_role")


class ChatThread(Base, TimestampMixin):
    __tablename__ = "chat_threads"

    thread_id: Mapped[int] = pk()
    #: The first question, as the thread's name in "earlier questions".
    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: When the last message landed: what orders "earlier questions".
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (Index("ix_chat_threads_updated", "updated_at"),)


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    message_id: Mapped[int] = pk()
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_threads.thread_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(CHAT_ROLE, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: The nodes the reader had selected when asking (§12.4, canvas → question).
    context_entity_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    #: Validated passage citations: ``[{"n", "chunk_id", "source_id", "url",
    #: "title", "source_tier"}]``, in citation order. Assistant messages only.
    citations: Mapped[list | None] = mapped_column(JSONB)
    #: Validated node references: ``[{"ref", "entity_id", "name", "contested"}]``.
    nodes: Mapped[list | None] = mapped_column(JSONB)
    agent_id: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    #: Why there is no answer, in words, when there is none.
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_chat_messages_thread", "thread_id", "message_id"),)
