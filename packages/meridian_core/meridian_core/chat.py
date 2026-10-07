"""Ask the graph: a question answered from the corpus, citing it (tasks P6-06, P6-07, §12.4).

Passages come from `search.search`, the retrieval Find uses, and nodes from the edges those
passages support. Retrieved text is framed as data (`P4-06`). Every ``[n]`` and ``{Nn}``
marker is checked against what was supplied, and one that points at nothing is removed
before the answer is stored (§2.6). A day's tokens are capped by
``MERIDIAN_CHAT_DAILY_TOKENS`` (default `DEFAULT_DAILY_TOKENS`). Called by the API, never
the worker (§2.1). See docs/features/ask-the-graph.md.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import re
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .framing import frame, new_delimiter
from .graphview import is_contested
from .logging import get_logger
from .models import ChatMessage, ChatThread, Edge, Entity
from .provider import ProviderError, ask
from .routing import NoAgentAvailable
from .search import SearchHit, search

log = get_logger(__name__)

#: The routing task type (`routing.TARGET_TIER`).
TASK_TYPE = "chat"

#: Passages retrieved per question, and at most this many from one document.
MAX_PASSAGES = 8
MAX_PER_SOURCE = 2
#: Nodes offered to cite: the reader's selection first, then those the
#: passages' edges touch.
MAX_NODES = 12
#: Earlier turns of the thread included, so a follow-up has its antecedent.
HISTORY_TURNS = 3
#: What an answer may run to, and how long to wait for it. A local model on a
#: small board is slow; a reader will wait a minute, not ten.
ANSWER_MAX_TOKENS = 1_200
ANSWER_TIMEOUT_S = 120.0
#: A question's length, in characters.
MIN_QUESTION = 3
MAX_QUESTION = 2_000
#: Tokens a day across every question, unless the environment says otherwise.
DEFAULT_DAILY_TOKENS = 200_000

SYSTEM = (
    "You answer questions about a research corpus for the person who built it. "
    "Answer only from the numbered passages and the listed nodes you are given. "
    "Cite every claim: a passage as [n], a node as {Nn} where you name it. "
    "If the passages do not answer the question, say so plainly and say what is "
    "missing — that gap is useful to the reader. If passages disagree, say that "
    "they disagree and cite both. Do not use knowledge from outside the passages. "
    "Be concise: a few short paragraphs at most."
)

Embed = Callable[[str], Awaitable[Sequence[float] | None]]

_PASSAGE_REF = re.compile(r"\[(\d{1,3})\]")
_NODE_REF = re.compile(r"\{N(\d{1,3})\}")


class ChatRefused(ValueError):
    """A question that is not asked, with the reason in words."""


@dataclasses.dataclass(frozen=True)
class NodeRef:
    ref: int
    entity_id: int
    name: str
    contested: bool


@dataclasses.dataclass(frozen=True)
class Exchange:
    thread: ChatThread
    question: ChatMessage
    answer: ChatMessage


def daily_cap() -> int:
    raw = os.environ.get("MERIDIAN_CHAT_DAILY_TOKENS", "").strip()
    if not raw:
        return DEFAULT_DAILY_TOKENS
    value = int(raw)
    if value < 0:
        raise ValueError("MERIDIAN_CHAT_DAILY_TOKENS must not be negative")
    return value


async def tokens_today(sess: AsyncSession, *, now: dt.datetime) -> int:
    start = now.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = await sess.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(ChatMessage.input_tokens, 0)
                    + func.coalesce(ChatMessage.output_tokens, 0)
                ),
                0,
            )
        ).where(ChatMessage.created_at >= start)
    )
    return int(used or 0)


async def context_nodes(sess: AsyncSession, entity_ids: Sequence[int]) -> list[Entity]:
    """The reader's selection, as live, non-annotation nodes, in the given order."""
    if not entity_ids:
        return []
    rows = {
        e.entity_id: e
        for e in await sess.scalars(select(Entity).where(Entity.entity_id.in_(list(entity_ids))))
        if e.redirects_to is None
    }
    return [rows[i] for i in dict.fromkeys(entity_ids) if i in rows]


async def nodes_for(
    sess: AsyncSession, hits: Sequence[SearchHit], selected: Sequence[Entity]
) -> list[NodeRef]:
    """The nodes the answer may cite: the selection, then those the passages' edges touch."""
    chunk_ids = [h.chunk_id for h in hits]
    touched: dict[int, bool] = {}
    if chunk_ids:
        edges = await sess.scalars(
            select(Edge).where(Edge.supporting_chunk_ids.overlap(chunk_ids)).limit(200)
        )
        for edge in edges:
            for end in (edge.from_node, edge.to_node):
                touched[end] = touched.get(end, False) or is_contested(edge)
    order = [e.entity_id for e in selected] + [
        i for i in touched if i not in {e.entity_id for e in selected}
    ]
    order = order[:MAX_NODES]
    names = (
        {
            e.entity_id: e
            for e in await sess.scalars(select(Entity).where(Entity.entity_id.in_(order)))
            if e.redirects_to is None and not e.is_annotation
        }
        if order
        else {}
    )
    return [
        NodeRef(ref=n, entity_id=i, name=names[i].canonical_name, contested=touched.get(i, False))
        for n, i in enumerate((i for i in order if i in names), start=1)
    ]


def build_prompt(
    question: str,
    hits: Sequence[SearchHit],
    nodes: Sequence[NodeRef],
    history: Sequence[tuple[str, str]],
    *,
    delimiter: str | None = None,
) -> str:
    """The data, fenced, then the question. Instruction before data, never after."""
    blocks = [
        f"[{i}] source: {h.url} (tier: {h.source_tier})\n{h.text}"
        for i, h in enumerate(hits, start=1)
    ] or ["(no passages matched)"]
    node_lines = [
        f"{{N{n.ref}}} {n.name}{' (contested)' if n.contested else ''}" for n in nodes
    ] or ["(no nodes)"]
    body = "PASSAGES\n\n" + "\n\n".join(blocks) + "\n\nNODES\n" + "\n".join(node_lines)
    parts = [frame(body, delimiter=delimiter or new_delimiter())]
    if history:
        earlier = "\n".join(f"{who}: {text}" for who, text in history)
        parts.append(f"Earlier in this conversation:\n{earlier}")
    parts.append(f"Question: {question}")
    return "\n\n".join(parts)


def check_answer(
    text: str, hits: Sequence[SearchHit], nodes: Sequence[NodeRef]
) -> tuple[str, list[dict], list[dict]]:
    """The answer with only references to what was supplied, and what it cited.

    Passage markers stay as ``[n]`` (the panel links them); node markers become
    the node's name. Anything pointing outside the supplied set is removed.
    """
    by_ref = {n.ref: n for n in nodes}
    cited_nodes: dict[int, NodeRef] = {}

    def node(match: re.Match) -> str:
        found = by_ref.get(int(match.group(1)))
        if found is None:
            return ""
        cited_nodes.setdefault(found.ref, found)
        return found.name

    text = _NODE_REF.sub(node, text)

    cited: dict[int, SearchHit] = {}

    def passage(match: re.Match) -> str:
        n = int(match.group(1))
        if not 1 <= n <= len(hits):
            return ""
        cited.setdefault(n, hits[n - 1])
        return match.group(0)

    text = _PASSAGE_REF.sub(passage, text)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()

    citations = [
        {
            "n": n,
            "chunk_id": h.chunk_id,
            "source_id": h.source_id,
            "url": h.url,
            "title": h.title,
            "source_tier": h.source_tier,
        }
        for n, h in sorted(cited.items())
    ]
    node_rows = [
        {"ref": n.ref, "entity_id": n.entity_id, "name": n.name, "contested": n.contested}
        for n in sorted(cited_nodes.values(), key=lambda n: n.ref)
    ]
    return text, citations, node_rows


async def history_of(sess: AsyncSession, thread_id: int) -> list[tuple[str, str]]:
    rows = list(
        await sess.scalars(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.error.is_(None))
            .order_by(ChatMessage.message_id.desc())
            .limit(HISTORY_TURNS * 2)
        )
    )
    return [("Reader" if m.role == "user" else "You", m.text) for m in reversed(rows)]


async def ask_corpus(
    sess: AsyncSession,
    question: str,
    *,
    thread_id: int | None = None,
    context_entity_ids: Sequence[int] = (),
    embed: Embed | None = None,
    now: dt.datetime,
) -> Exchange:
    """Answer one question and store both turns. Flushes; the caller commits.

    Raises `ChatRefused` for a question that is not asked at all (empty, too
    long, over the day's cap, an unknown thread). A model that cannot answer
    is not a refusal: the answer is stored with its reason in `error`, so the
    thread shows what happened.
    """
    question = " ".join((question or "").split())
    if not MIN_QUESTION <= len(question) <= MAX_QUESTION:
        raise ChatRefused(f"A question needs between {MIN_QUESTION} and {MAX_QUESTION} characters.")
    cap = daily_cap()
    if await tokens_today(sess, now=now) >= cap:
        raise ChatRefused(
            f"Today's allowance for questions ({cap:,} tokens, "
            "MERIDIAN_CHAT_DAILY_TOKENS) is spent."
        )

    if thread_id is not None:
        thread = await sess.get(ChatThread, thread_id)
        if thread is None:
            raise ChatRefused(f"No conversation {thread_id}.")
        history = await history_of(sess, thread_id)
    else:
        thread = ChatThread(title=question[:200])
        sess.add(thread)
        await sess.flush()
        history = []

    selected = await context_nodes(sess, list(context_entity_ids))
    asked = ChatMessage(
        thread_id=thread.thread_id,
        role="user",
        text=question,
        context_entity_ids=[e.entity_id for e in selected] or None,
    )
    sess.add(asked)
    await sess.flush()

    # The selection travels with the words: "how does this relate to…" is about
    # the node the reader clicked, and search needs its name to know that.
    query = " ".join([question, *(e.canonical_name for e in selected)])
    vector = await embed(query) if embed is not None else None
    result = await search(
        sess, query, query_vector=vector, limit=MAX_PASSAGES, max_per_source=MAX_PER_SOURCE
    )
    hits = list(result.hits)
    nodes = await nodes_for(sess, hits, selected)

    answer = ChatMessage(thread_id=thread.thread_id, role="assistant", text="")
    try:
        completion = await ask(
            sess,
            TASK_TYPE,
            prompt=build_prompt(question, hits, nodes, history),
            system=SYSTEM,
            max_tokens=ANSWER_MAX_TOKENS,
            timeout_s=ANSWER_TIMEOUT_S,
        )
    except NoAgentAvailable:
        answer.error = (
            "No model is set up to answer questions. Enable an agent for `chat` in "
            "Admin → Agents, with LOCAL_CHAT_LLM_URL and LOCAL_CHAT_MODEL set."
        )
    except ProviderError as exc:
        answer.error = f"The model did not answer: {exc}"
    else:
        text, citations, node_rows = check_answer(completion.text, hits, nodes)
        answer.text = text
        answer.citations = citations
        answer.nodes = node_rows
        answer.agent_id = completion.agent_id
        answer.model = completion.model
        answer.input_tokens = completion.input_tokens
        answer.output_tokens = completion.output_tokens

    sess.add(answer)
    thread.updated_at = now
    await sess.flush()
    log.info(
        "question answered",
        extra={
            "thread_id": thread.thread_id,
            "passages": len(hits),
            "nodes": len(nodes),
            "answered": answer.error is None,
        },
    )
    return Exchange(thread=thread, question=asked, answer=answer)


async def recent_threads(sess: AsyncSession, *, limit: int = 20) -> tuple[list[ChatThread], int]:
    total = int(await sess.scalar(select(func.count()).select_from(ChatThread)) or 0)
    rows = list(
        await sess.scalars(
            select(ChatThread)
            .order_by(ChatThread.updated_at.desc(), ChatThread.thread_id.desc())
            .limit(limit)
        )
    )
    return rows, total


async def thread_messages(sess: AsyncSession, thread_id: int) -> list[ChatMessage] | None:
    if await sess.get(ChatThread, thread_id) is None:
        return None
    return list(
        await sess.scalars(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.message_id)
        )
    )


__all__ = [
    "ChatRefused",
    "Exchange",
    "ask_corpus",
    "check_answer",
    "build_prompt",
    "recent_threads",
    "thread_messages",
]
