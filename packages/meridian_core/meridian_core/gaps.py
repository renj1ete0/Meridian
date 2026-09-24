"""Gaps: one ranked list of what the corpus cannot yet answer (task P6-36).

Each gap is a concrete finding with its reason in numbers and an action that
goes through machinery that already exists — a search seed in the queue, a
topic boost through :mod:`meridian_core.steering` — so every action is
reversible and leaves a `steering_log` row (§10.1). Nothing here writes; the
actions are applied by the admin routes.

**The sources are pluggable.** A gap source is an async function from a
session to a list of :class:`Gap`, registered under a name. What exists today:

- ``topic-coverage`` — per topic: thin (few sources), weak (few of them
  government or peer-reviewed), stale (the newest dated one is old). Each
  finding also counts on-topic *passages* (`P2-24`), including those inside
  documents labelled with something else. Per *area* joins when `P6-30`'s
  areas land: register another source.
- ``search-yield`` — a topic whose search seeds keep producing nothing that
  survives the prefilter.
- ``question-set`` — items of the held-out set that score low in the newest
  run file. An operator grade counts; a heuristic proposal is shown as one and
  ranked below any real score.

``routes`` (`P6-32`, "no cited route within N hops") and ``areas`` (`P6-30`)
are listed as *pending* rather than silently absent, so an empty list is never
mistaken for "no gaps of that kind".

**The held-out rule shapes the question-set actions.** `eval/README.md` forbids
using a question as a seed or a steering reason, so a low-scoring item offers
"search it in Find" — reading, not steering — and never "seed this question".
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import false, func, select, true, union
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, ChunkTopics, QueueTask, Source, TopicConfig
from .searchseeds import topic_words

#: Fewer on-topic sources than this is thin. A number to argue with, not a law:
#: an evidence question needs "at least three independent sources"
#: (eval rubric), and a topic needs several questions' worth.
THIN_SOURCES = 10

#: Fewer government or peer-reviewed sources than this is weak.
STRONG_TIERS = ("government", "peer_reviewed")
WEAK_STRONG = 3

#: The newest dated source older than this is stale.
STALE_YEARS = 3

#: Searches that ran for a topic before "none produced anything" is a finding.
SEARCH_YIELD_MIN = 3

#: A boost offered from Gaps: doubled for a week, then gone by itself (§10).
BOOST_FACTOR = 2.0
BOOST_DAYS = 7

#: Topics whose gaps are worth acting on. Archived topics left the pool on
#: purpose; showing them as gaps would argue with a decision.
LIVE_STATUSES = ("active", "maintenance", "paused")


@dataclasses.dataclass(frozen=True)
class Action:
    kind: str  # seed_query | boost_topic | open_search
    label: str
    topic: str | None = None
    query: str | None = None
    factor: float | None = None
    days: int | None = None


@dataclasses.dataclass(frozen=True)
class Gap:
    id: str
    source: str
    kind: str
    subject: str
    title: str
    reason: str
    severity: float
    evidence: dict[str, Any]
    actions: tuple[Action, ...]


@dataclasses.dataclass(frozen=True)
class SourceStatus:
    name: str
    status: str  # ok | unavailable | pending
    note: str | None = None
    gaps: int = 0


GapSource = Callable[[AsyncSession], Awaitable[list[Gap]]]

#: name -> source. Ordered: it is the order statuses are reported in.
SOURCES: dict[str, GapSource] = {}

#: Sources the design names that are not built yet, and the task that builds them.
PENDING: dict[str, str] = {
    "areas": "thin or weak areas arrive with P6-30",
    "routes": "routes with no cited link arrive with P6-32",
}


class SourceUnavailable(RuntimeError):
    """A source that cannot answer here — reported, not raised to the caller."""


def register(name: str) -> Callable[[GapSource], GapSource]:
    def wrap(fn: GapSource) -> GapSource:
        SOURCES[name] = fn
        PENDING.pop(name, None)
        return fn

    return wrap


def rank(gaps: list[Gap]) -> list[Gap]:
    """Most severe first; ties by id so the list does not reshuffle between reads."""
    return sorted(gaps, key=lambda g: (-g.severity, g.id))


async def find_gaps(
    sess: AsyncSession, *, sources: dict[str, GapSource] | None = None
) -> tuple[list[Gap], list[SourceStatus]]:
    """Every registered source's gaps, ranked, with each source's status."""
    found: list[Gap] = []
    statuses: list[SourceStatus] = []
    for name, source in (sources if sources is not None else SOURCES).items():
        try:
            gaps = await source(sess)
        except SourceUnavailable as exc:
            statuses.append(SourceStatus(name, "unavailable", str(exc)))
            continue
        found.extend(gaps)
        statuses.append(SourceStatus(name, "ok", None, len(gaps)))
    if sources is None:
        statuses.extend(SourceStatus(name, "pending", note) for name, note in PENDING.items())
    return rank(found), statuses


# ---------------------------------------------------------------------------
# Topic coverage
# ---------------------------------------------------------------------------


def _seed_and_boost(topic: str, description: str | None) -> tuple[Action, ...]:
    words = " ".join((description or "").split()[:8]) or topic_words(topic)
    return (
        Action("seed_query", "Seed a search", topic=topic, query=words),
        Action(
            "boost_topic",
            f"Boost ×{BOOST_FACTOR:g} for {BOOST_DAYS} days",
            topic=topic,
            factor=BOOST_FACTOR,
            days=BOOST_DAYS,
        ),
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _elsewhere(passage_sources: int) -> str:
    if not passage_sources:
        return ""
    return (
        f" On-topic passages also sit in {_plural(passage_sources, 'other document')}"
        " labelled with something else."
    )


def coverage_gaps(
    topic: str,
    *,
    description: str | None,
    sources: int,
    strong: int,
    passages: int,
    newest: dt.date | None,
    share: float | None,
    corpus_share: float | None,
    today: dt.date,
    passage_sources: int = 0,
) -> list[Gap]:
    """The coverage findings for one topic. Pure, so the thresholds are testable.

    ``passages`` counts on-topic passages (`P2-24`): chunks labelled with the
    topic themselves, or belonging to a source that is. ``passage_sources``
    counts the documents *not* labelled with the topic that hold at least one
    passage about it — a chapter inside a book about something else.

    **Thin is still judged on sources.** A passage label is one chunk's vector,
    noisier than a document's mean, and a topic that exists in the corpus only
    as asides in other documents is thin in the sense that matters: there is
    no document to cite as being about it. So those documents are counted and
    shown, and do not lift a topic out of "thin".
    """
    evidence = {
        "sources": sources,
        "strong_sources": strong,
        "passages": passages,
        "passage_sources": passage_sources,
        "newest": newest.isoformat() if newest else None,
        "crawl_share": None if share is None else round(share, 3),
        "corpus_share": None if corpus_share is None else round(corpus_share, 3),
    }
    actions = _seed_and_boost(topic, description)
    share_note = ""
    if share is not None and corpus_share is not None:
        share_note = (
            f" {corpus_share:.1%} of on-topic sources against a {share:.0%} share of the crawl."
        )
    out: list[Gap] = []
    if sources < THIN_SOURCES:
        if sources:
            title = f"{_plural(sources, 'source')}, {strong} government or peer-reviewed"
        elif passage_sources:
            title = f"No sources; passages in {_plural(passage_sources, 'other document')}"
        else:
            title = "No sources"
        out.append(
            Gap(
                id=f"topic-thin:{topic}",
                source="topic-coverage",
                kind="thin",
                subject=topic,
                title=title,
                reason=(
                    f"{_plural(sources, 'source')} labelled {topic} by content, "
                    f"{_plural(passages, 'passage')} about it; fewer than {THIN_SOURCES} "
                    "sources is thin." + _elsewhere(passage_sources) + share_note
                ),
                severity=round(0.6 + 0.4 * (1 - sources / THIN_SOURCES), 3),
                evidence=evidence,
                actions=actions,
            )
        )
    elif strong < WEAK_STRONG:
        out.append(
            Gap(
                id=f"topic-weak:{topic}",
                source="topic-coverage",
                kind="weak",
                subject=topic,
                title=f"{sources} sources, {strong} government or peer-reviewed",
                reason=(
                    f"{_plural(sources, 'source')}, {strong} of them government or "
                    f"peer-reviewed; fewer than {WEAK_STRONG} leaves an evidence question "
                    "resting on institutional and informal material."
                    + _elsewhere(passage_sources)
                    + share_note
                ),
                severity=round(0.35 + 0.2 * (1 - strong / WEAK_STRONG), 3),
                evidence=evidence,
                actions=actions,
            )
        )
    if sources and newest is not None and (today - newest).days > 365 * STALE_YEARS:
        out.append(
            Gap(
                id=f"topic-stale:{topic}",
                source="topic-coverage",
                kind="stale",
                subject=topic,
                title=f"Newest dated source {newest.year}",
                reason=(
                    f"The newest dated {topic} source is from {newest.isoformat()}, "
                    f"more than {STALE_YEARS} years ago."
                ),
                severity=0.3,
                evidence=evidence,
                actions=actions,
            )
        )
    return out


@register("topic-coverage")
async def topic_coverage(sess: AsyncSession, *, today: dt.date | None = None) -> list[Gap]:
    from . import steering

    today = today or dt.date.today()
    now = dt.datetime.now(dt.UTC)
    rows = list(
        await sess.scalars(
            select(TopicConfig)
            .where(TopicConfig.status.in_(LIVE_STATUSES))
            .order_by(TopicConfig.topic)
        )
    )
    if not rows:
        return []
    shares = steering.draw_shares(rows, now=now)

    label = func.unnest(Source.topic_labels).table_valued("value").render_derived("t")
    per_topic = {
        topic: (n, strong, newest)
        for topic, n, strong, newest in await sess.execute(
            select(
                label.c.value,
                func.count(),
                func.count().filter(Source.source_tier.in_(STRONG_TIERS)),
                func.max(Source.publication_date),
            )
            .select_from(Source)
            .join(label, true())
            .group_by(label.c.value)
        )
    }
    passages, passage_sources = await _passage_counts(sess)
    on_topic_total = sum(n for n, _, _ in per_topic.values()) or 0

    out: list[Gap] = []
    for row in rows:
        n, strong, newest = per_topic.get(row.topic, (0, 0, None))
        out.extend(
            coverage_gaps(
                row.topic,
                description=row.description,
                sources=n,
                strong=strong,
                passages=int(passages.get(row.topic, 0)),
                passage_sources=int(passage_sources.get(row.topic, 0)),
                newest=newest,
                share=shares.get(row.topic),
                corpus_share=(n / on_topic_total) if on_topic_total else None,
                today=today,
            )
        )
    return out


async def _passage_counts(sess: AsyncSession) -> tuple[dict[str, int], dict[str, int]]:
    """Per topic: on-topic live passages, and the other documents holding some.

    A passage is on-topic when its own labels carry the topic (`P2-24`) or its
    source's do — counted once either way. The second count is the documents
    whose *own* labels do not carry the topic but which hold at least one
    passage that does: where the topic lives only as an aside.
    """
    source_label = func.unnest(Source.topic_labels).table_valued("value").render_derived("sl")
    by_source = (
        select(source_label.c.value.label("topic"), Chunk.chunk_id)
        .select_from(Source)
        .join(source_label, true())
        .join(Chunk, Chunk.source_id == Source.source_id)
        .where(Chunk.superseded_at.is_(None))
    )
    passage_label = func.unnest(ChunkTopics.topic_labels).table_valued("value").render_derived("pl")
    by_passage = (
        select(passage_label.c.value.label("topic"), ChunkTopics.chunk_id)
        .select_from(ChunkTopics)
        .join(passage_label, true())
        .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
        .where(Chunk.superseded_at.is_(None))
    )
    on_topic = union(by_source, by_passage).subquery()
    passages = dict(
        (
            await sess.execute(select(on_topic.c.topic, func.count()).group_by(on_topic.c.topic))
        ).all()
    )
    elsewhere = dict(
        (
            await sess.execute(
                select(passage_label.c.value, func.count(func.distinct(Chunk.source_id)))
                .select_from(ChunkTopics)
                .join(passage_label, true())
                .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
                .join(Source, Source.source_id == Chunk.source_id)
                .where(
                    Chunk.superseded_at.is_(None),
                    ~func.coalesce(Source.topic_labels.any(passage_label.c.value), false()),
                )
                .group_by(passage_label.c.value)
            )
        ).all()
    )
    return passages, elsewhere


# ---------------------------------------------------------------------------
# Search yield
# ---------------------------------------------------------------------------


@register("search-yield")
async def search_yield(sess: AsyncSession) -> list[Gap]:
    """Topics whose searches ran and queued nothing.

    The queue does not link a result to the query that found it, so this is
    per topic, not per query: searches done for the topic against URLs any
    search queued for it. Coarser than per query, and honest about it.
    """
    done = dict(
        (
            await sess.execute(
                select(QueueTask.topic, func.count())
                .where(QueueTask.task_type == "query", QueueTask.status == "done")
                .where(QueueTask.topic.is_not(None))
                .group_by(QueueTask.topic)
            )
        ).all()
    )
    found = dict(
        (
            await sess.execute(
                select(QueueTask.topic, func.count())
                .where(QueueTask.task_type == "url", QueueTask.seed_source == "search")
                .group_by(QueueTask.topic)
            )
        ).all()
    )
    descriptions = dict(
        (await sess.execute(select(TopicConfig.topic, TopicConfig.description))).all()
    )
    out = []
    for topic, n in sorted(done.items()):
        if n < SEARCH_YIELD_MIN or found.get(topic, 0) > 0 or topic not in descriptions:
            continue
        out.append(
            Gap(
                id=f"search-yield:{topic}",
                source="search-yield",
                kind="search_empty",
                subject=topic,
                title=f"{n} searches, no result kept",
                reason=(
                    f"{n} searches for {topic} have run and none queued a page that "
                    "survived the prefilter. Different words may reach different pages."
                ),
                severity=0.5,
                evidence={"searches_done": n, "results_queued": 0},
                actions=_seed_and_boost(topic, descriptions.get(topic))[:1],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Acting on a gap — through the queue and steering, so it is logged and undoable
# ---------------------------------------------------------------------------

#: Where a seed from Gaps sits in the queue: the priority `worker.seedsearch`
#: gives its own queries (`QUERY_PRIORITY`), so a person's seed neither jumps
#: nor trails the crawl's own questions. Mirrored, not imported: the worker is
#: a service and `meridian_core` does not import services.
SEED_PRIORITY = 70

#: The steering-log field a Gaps seed is recorded under.
SEED_FIELD = "seed"  # the same field `POST /api/admin/seeds` logs under (`B-55`)

ACTOR = "user"


class HeldOut(ValueError):
    """A question-set item was offered as the reason to steer the crawl."""


def _check_not_held_out(gap_id: str) -> None:
    # eval/README.md: a question is never a seed or a steering reason. The UI
    # never offers it; this is the server refusing it anyway (§2.6).
    if gap_id.startswith("question:"):
        raise HeldOut(
            "question-set items are held out: they may not seed or steer the crawl "
            "(eval/README.md). Fix for the kind of question instead."
        )


async def _topic(sess: AsyncSession, topic: str) -> TopicConfig:
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    return row


async def seed_query(
    sess: AsyncSession, *, topic: str, query: str, gap_id: str, now: dt.datetime
) -> QueueTask:
    """Queue a search for ``topic`` and log why. Flushes; the caller commits.

    Undo: the task can be removed in Admin while it is still pending
    (`DELETE /api/admin/seeds/{id}`); once it has run, its pages are ordinary
    crawl results and nothing is deleted (§2.5).
    """
    from .queueing import enqueue

    _check_not_held_out(gap_id)
    await _topic(sess, topic)
    words = " ".join(query.split())
    if await sess.scalar(select(QueueTask.task_id).where(QueueTask.url_or_query == words)):
        raise ValueError(f"{words!r} is already queued.")
    task = await enqueue(
        sess, words, topic=topic, seed_source=ACTOR, task_type="query", priority=SEED_PRIORITY
    )
    from . import steering

    await steering.record(
        sess,
        actor=ACTOR,
        topic=topic,
        field=SEED_FIELD,
        old=None,
        new=words,
        reason=f"from Gaps: {gap_id} (queue task {task.task_id})",
        now=now,
    )
    await sess.flush()
    return task


async def boost_topic(
    sess: AsyncSession, *, topic: str, factor: float, days: int, gap_id: str, now: dt.datetime
) -> dt.datetime:
    """A temporary boost through :func:`steering.set_boost`, which logs it."""
    from . import steering

    _check_not_held_out(gap_id)
    row = await _topic(sess, topic)
    if row.status != steering.DRAWING:
        raise ValueError(f"{topic!r} is {row.status}; a boost changes nothing until it is active.")
    expires = now + dt.timedelta(days=days)
    await steering.set_boost(
        sess,
        topic,
        factor=factor,
        expires_at=expires,
        actor=ACTOR,
        reason=f"from Gaps: {gap_id}",
        now=now,
    )
    return expires


# ---------------------------------------------------------------------------
# The question set
# ---------------------------------------------------------------------------


def runs_dir() -> Path:
    configured = os.environ.get("MERIDIAN_EVAL_RUNS_DIR", "").strip()
    return Path(configured) if configured else Path.cwd() / "eval" / "runs"


LEXICAL_NOTE = " The run was lexical-only (no embedder), so an empty result is weak evidence."


def _hits_line(n: int) -> str:
    return "No hits in the top results." if n == 0 else f"{_plural(n, 'hit')} in the top results."


def question_gaps(run: dict, run_name: str) -> list[Gap]:
    """Low items of one run. Operator grades are the score; proposals rank below."""
    from .questionset import SCALE

    out = []
    draft = bool(run.get("draft"))
    # A lexical-only run finds almost nothing for a whole question; a low
    # grade from one says more about the run than about the corpus.
    lexical = (run.get("context") or {}).get("mode") == "lexical-only"
    for item in run.get("items") or []:
        op = item.get("operator")
        if isinstance(op, dict) and op.get("grade") in SCALE:
            grade, basis = int(op["grade"]), "operator"
        else:
            proposed = (item.get("proposed") or {}).get("grade")
            # A heuristic cannot judge a gap item (it cannot tell honest
            # absence from failed search), so its proposal there is not a gap.
            if proposed not in SCALE or item.get("kind") == "gap":
                continue
            grade, basis = int(proposed), "proposal"
        if grade > 1:
            continue
        severity = (0.9 - 0.2 * grade) if basis == "operator" else (0.45 - 0.15 * grade)
        who = "graded" if basis == "operator" else "proposed (heuristic, not a score)"
        missing = (op or {}).get("missing") if basis == "operator" else None
        question = item.get("question")
        out.append(
            Gap(
                id=f"question:{item.get('id')}",
                source="question-set",
                kind="question_low",
                subject=str(item.get("id")),
                title=f"{str(item.get('kind')).capitalize()} question, {grade} of 3, {who}",
                reason=(
                    (missing or _hits_line(len(item.get("hits") or [])))
                    + f" From {run_name}"
                    + (", a draft set." if draft else ".")
                    + (LEXICAL_NOTE if lexical else "")
                    + " Fix for the kind of question, not this question (eval/README.md)."
                ),
                severity=round(severity, 3),
                evidence={
                    "grade": grade,
                    "basis": basis,
                    "kind": item.get("kind"),
                    "topics": ", ".join(item.get("topics") or []),
                    "run": run_name,
                },
                actions=(Action("open_search", "Search it in Find", query=question),)
                if question
                else (),
            )
        )
    return out


@register("question-set")
async def question_set(sess: AsyncSession) -> list[Gap]:
    from .questionset import QuestionSetError, previous_run, read_run

    directory = runs_dir()
    latest = previous_run(directory) if directory.is_dir() else None
    if latest is None:
        raise SourceUnavailable(
            f"no run file in {directory} — run scripts/run_question_set.py, or set "
            "MERIDIAN_EVAL_RUNS_DIR"
        )
    try:
        run = read_run(latest)
    except (QuestionSetError, yaml.YAMLError) as exc:
        raise SourceUnavailable(f"{latest.name} could not be read: {exc}") from exc
    return question_gaps(run, latest.name)
