"""Queue fresh search queries for every active topic (task `B-51`, spec §7.4).

``python -m worker.seedsearch --once`` writes up to :data:`PER_TOPIC` new
`query` rows per active topic, built by `meridian_core.searchseeds` from each
topic's name, description and approved vocabulary. Scheduled every six hours.

The rows carry ``seed_source="diversity"`` — §7.4's reserved share of the seed
budget, which is exactly what they are — and :data:`QUERY_PRIORITY`, above
every tier's link priority, so a query is answered promptly and its results
then compete on their own tier. A topic that already has :data:`MAX_PENDING`
unanswered queries gets none this run: a worker that is not keeping up with
search should not come back to a backlog of hundreds.

``--report`` prints what would be queued and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import GazetteerTerm, QueueTask, TopicConfig
from meridian_core.queueing import enqueue
from meridian_core.searchseeds import Query, TopicSeedInput, plan, topic_words
from meridian_core.translations import search_languages, translations_for

log = get_logger(__name__)

PER_TOPIC = 6
MAX_PENDING = 24

#: Above every tier's link priority (the highest is 60), below an operator's
#: own seed (100): a question the system asks itself is answered before the
#: links it already has, and after the ones a person asked.
QUERY_PRIORITY = 70

#: Aliases shorter than this are mostly acronyms, which search engines read as
#: something else entirely.
MIN_ALIAS = 5

#: Vocabulary a query is built from. An agency is *who*, not *what*: paired
#: with a concept it asks for that agency's pages, and the first live run
#: produced exactly that — a concept beside a city government's name.
QUERY_ENTITY_TYPES = frozenset({"concept", "scheme", "metric", "infrastructure"})

#: Below this many approved terms, with no description, a topic is searched by
#: its name alone. That is deliberate: the operator runs topics broad and lets
#: the crawl find its way in, and a bare field name is a broad query. The report
#: names such topics, because a description or vocabulary would widen their
#: queries — but it is an option, never a requirement.
MIN_TERMS_WITHOUT_DESCRIPTION = 3


async def topic_inputs(sess: AsyncSession) -> list[TopicSeedInput]:
    rows = (
        await sess.execute(
            select(TopicConfig.topic, TopicConfig.description)
            .where(TopicConfig.status == "active")
            .order_by(TopicConfig.topic)
        )
    ).all()
    terms: dict[str, list[str]] = {topic: [] for topic, _ in rows}
    for term in await sess.scalars(
        select(GazetteerTerm).where(
            GazetteerTerm.approved.is_(True),
            GazetteerTerm.rejected_at.is_(None),
            GazetteerTerm.entity_type.in_(sorted(QUERY_ENTITY_TYPES)),
        )
    ):
        for topic in term.topic_labels or ():
            if topic not in terms:
                continue
            terms[topic].append(term.canonical)
            if not term.ambiguous:
                terms[topic].extend(a for a in term.aliases or () if len(a) >= MIN_ALIAS)
    languages = await search_languages(sess)
    concepts = {topic: [topic_words(topic), *terms[topic]] for topic, _ in rows}
    known = await translations_for(
        sess, [c for group in concepts.values() for c in group], languages
    )
    return [
        TopicSeedInput(
            topic,
            description,
            tuple(terms[topic]),
            tuple(pair for concept in concepts[topic] for pair in known.get(concept, ())),
        )
        for topic, description in rows
    ]


def specific_enough(topic: TopicSeedInput) -> bool:
    """Whether a topic says enough about itself to be searched for."""
    return bool(topic.description and topic.description.strip()) or (
        len(set(topic.terms)) >= MIN_TERMS_WITHOUT_DESCRIPTION
    )


async def pending_by_topic(sess: AsyncSession) -> dict[str, int]:
    rows = await sess.execute(
        select(QueueTask.topic, func.count())
        .where(QueueTask.task_type == "query", QueueTask.status == "pending")
        .group_by(QueueTask.topic)
    )
    return {topic: int(n) for topic, n in rows if topic}


@dataclasses.dataclass
class SeedRun:
    queries: list[Query]
    #: Active topics searched by name only, having no description or vocabulary.
    vague: list[str]
    #: Active topics left out because their queries are not being answered.
    backlogged: list[str]


async def run_once(
    *, write: bool, per_topic: int = PER_TOPIC, seed: int | None = None, session_factory=session
) -> SeedRun:
    async with session_factory() as sess:
        inputs = await topic_inputs(sess)
        vague = [t.topic for t in inputs if not specific_enough(t)]
        waiting = await pending_by_topic(sess)
        backlogged = [t.topic for t in inputs if waiting.get(t.topic, 0) >= MAX_PENDING]
        inputs = [t for t in inputs if t.topic not in backlogged]
        already = list(
            await sess.scalars(select(QueueTask.url_or_query).where(QueueTask.task_type == "query"))
        )
        queries = plan(
            inputs,
            already=already,
            per_topic=per_topic,
            seed=seed if seed is not None else int(time.time()),
        )
        if write:
            for query in queries:
                await enqueue(
                    sess,
                    query.text,
                    topic=query.topic,
                    seed_source="diversity",
                    task_type="query",
                    priority=QUERY_PRIORITY,
                )
            await sess.commit()
    log.info(
        "search seeds planned",
        extra={"queries": len(queries), "written": write, "vague": vague, "backlogged": backlogged},
    )
    return SeedRun(queries, vague, backlogged)


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue fresh search queries per topic (B-51).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="queue them")
    mode.add_argument("--report", action="store_true", help="print them; queue nothing")
    parser.add_argument("--per-topic", type=int, default=PER_TOPIC)
    args = parser.parse_args()
    configure_logging("seedsearch")

    async def go() -> SeedRun:
        try:
            return await run_once(write=args.once, per_topic=args.per_topic)
        finally:
            await dispose_engines()

    with bind_run_id(f"seedsearch-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        run = asyncio.run(go())
    for query in run.queries:
        print(f"  {query.topic:22} {query.kind:11} {query.text}")
    print(f"{len(run.queries)} queries {'queued' if args.once else 'planned (report only)'}")
    if run.vague:
        print(f"searched by name only (no description or vocabulary): {', '.join(run.vague)}")
    if run.backlogged:
        print(f"left out, queries not being answered: {', '.join(run.backlogged)}")


if __name__ == "__main__":
    main()
