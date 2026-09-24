"""Rank queued DOIs by the pages that cite them (task `B-58`).

``python -m worker.requeue_dois`` — reports by default, writes only with
``--apply``, and never deletes.

Every `doi` row was queued at one priority below every link and search result,
so none was ever claimed. :mod:`meridian_core.citedpapers` now ranks a DOI by
its citing page, and the fetch loop records that page on the rows it queues
(``queue.parent_source_id``). This pass does the rest:

- **The backlog.** Rows queued before the parent was recorded have none. Their
  citing pages are found where the fetch loop has always written a page's
  references — ``sources.extra->'citations'``, as ``[{kind, value}]`` — with
  both sides normalised by :func:`~worker.resolve_doi.normalise_doi`, because
  the frontier queued ``doi.org`` paths in whatever case the page used.
- **The provisional ranks.** A page is labelled an hour or more after it is
  fetched, so a DOI it names is queued at the unjudged rank. Once the label
  exists this pass settles it: up for an on-topic page, down to the floor for
  an off-topic one or one on a host since judged off-topic.

A DOI cited by several pages takes the best of them, and records that page as
its parent. A row whose citing page cannot be found keeps its priority: there
is nothing to rank it by, and moving it anyway would be a guess. A DOI queued
more than once (the frontier did not check) is ranked on one row; the others
keep their place, so the same paper is not resolved twice at the top of the
queue.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.boilerplate import host_key
from meridian_core.citedpapers import cited_priority
from meridian_core.db import dispose_engines, session
from meridian_core.hostscores import Score, Standing, load
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, Source
from meridian_core.policy import source_tier_map

from .resolve_doi import DoiError, normalise_doi

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: Sources per lookup.
BATCH = 1000

#: Every DOI any stored page cites, as written. The CASE is not decoration:
#: `jsonb_array_elements` raises on a non-array, and a WHERE on the type is not
#: guaranteed to run before the function does.
CITED_DOIS = text(
    """
    SELECT s.source_id, c->>'value'
      FROM sources s
     CROSS JOIN LATERAL jsonb_array_elements(
           CASE WHEN jsonb_typeof(s.extra->'citations') = 'array'
                THEN s.extra->'citations' ELSE '[]'::jsonb END) AS c
     WHERE c->>'kind' = 'doi'
    """
)


@dataclasses.dataclass
class DoiRequeueStats:
    examined: int = 0
    #: A queued string `normalise_doi` refuses. Left alone: the resolver
    #: abandons it on claim, which records why.
    unparseable: int = 0
    #: A second row for a DOI already counted. Left alone.
    duplicates: int = 0
    #: No citing page recorded or findable. Left alone.
    no_citing_source: int = 0
    raised: int = 0
    lowered: int = 0
    unchanged: int = 0
    #: Rows that gain a recorded parent.
    parents_recorded: int = 0
    #: Target priority → rows that would sit there after the pass.
    by_priority: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Why: the best citing page's standing, for rows that have one.
    by_reason: collections.Counter = dataclasses.field(default_factory=collections.Counter)


@dataclasses.dataclass(frozen=True)
class Citing:
    source_id: int
    priority: int
    reason: str


def _reason(labels: list[str] | None, standing: Standing) -> str:
    if standing is Standing.OFF_TOPIC:
        return "off_topic_host"
    if labels is None:
        return "unjudged_page"
    return "on_topic_page" if labels else "off_topic_page"


def _normalised(raw: str | None) -> str | None:
    try:
        return normalise_doi(raw or "")
    except DoiError:
        return None


async def _citations_by_doi(sess: AsyncSession, wanted: set[str]) -> dict[str, set[int]]:
    """Which sources' reference lists name each of ``wanted``.

    Unnested in SQL and normalised here, not matched in SQL: the stored values
    are what the extractor found, and a second normaliser written in SQL is how
    two passes come to disagree about whether two strings are the same DOI.
    """
    rows = await sess.stream(CITED_DOIS)
    found: dict[str, set[int]] = collections.defaultdict(set)
    async for source_id, value in rows:
        doi = _normalised(value)
        if doi is not None and doi in wanted:
            found[doi].add(source_id)
    return found


async def run_pass(*, apply: bool, session_factory: SessionFactory = session) -> DoiRequeueStats:
    stats = DoiRequeueStats()
    async with session_factory() as sess:
        tiers = await source_tier_map(sess)
        scores = await load(sess)
        rows = (
            await sess.execute(
                select(
                    QueueTask.task_id,
                    QueueTask.url_or_query,
                    QueueTask.priority,
                    QueueTask.parent_source_id,
                )
                .where(QueueTask.status == "pending", QueueTask.task_type == "doi")
                .order_by(QueueTask.created_at.asc(), QueueTask.task_id.asc())
            )
        ).all()

        # One row per DOI: the one already spelled in normal form if there is
        # one — it is what every later dedupe compares against — else the oldest.
        chosen: dict[str, tuple] = {}
        for row in rows:
            stats.examined += 1
            doi = _normalised(row.url_or_query)
            if doi is None:
                stats.unparseable += 1
                continue
            held = chosen.get(doi)
            if held is None:
                chosen[doi] = row
                continue
            stats.duplicates += 1
            if held.url_or_query != doi and row.url_or_query == doi:
                chosen[doi] = row

        citing_ids = await _citations_by_doi(sess, set(chosen))
        for doi, row in chosen.items():
            if row.parent_source_id is not None:
                citing_ids[doi].add(row.parent_source_id)

        all_ids = set().union(*citing_ids.values()) if citing_ids else set()
        pages: dict[int, Citing] = {}
        ids = sorted(all_ids)
        for start in range(0, len(ids), BATCH):
            found = await sess.execute(
                select(
                    Source.source_id,
                    Source.url,
                    Source.extra["final_url"].astext,
                    Source.topic_labels,
                ).where(Source.source_id.in_(ids[start : start + BATCH]))
            )
            for source_id, url, final_url, labels in found:
                standing = scores.get(host_key(final_url or url) or "", Score()).standing
                pages[source_id] = Citing(
                    source_id,
                    cited_priority(labels, standing, tiers),
                    _reason(labels, standing),
                )

        changes: list[dict] = []
        for doi, row in chosen.items():
            candidates = [pages[i] for i in citing_ids.get(doi, ()) if i in pages]
            if not candidates:
                stats.no_citing_source += 1
                continue
            # Best priority; among equals the earliest source, so reruns agree.
            best = min(candidates, key=lambda c: (-c.priority, c.source_id))
            stats.by_priority[best.priority] += 1
            stats.by_reason[best.reason] += 1
            record_parent = row.parent_source_id is None
            stats.parents_recorded += record_parent
            if best.priority > row.priority:
                stats.raised += 1
            elif best.priority < row.priority:
                stats.lowered += 1
            else:
                stats.unchanged += 1
            if best.priority != row.priority or record_parent:
                changes.append(
                    {
                        "task_id": row.task_id,
                        "priority": best.priority,
                        "parent_source_id": row.parent_source_id or best.source_id,
                    }
                )

        if apply:
            for change in changes:
                # Pending only: a row the worker settled since it was read has
                # been answered, and re-ranking an answer is noise.
                await sess.execute(
                    update(QueueTask)
                    .where(QueueTask.task_id == change["task_id"], QueueTask.status == "pending")
                    .values(
                        priority=change["priority"], parent_source_id=change["parent_source_id"]
                    )
                )
            await sess.commit()
    log.info(
        "doi requeue pass complete",
        extra={
            "examined": stats.examined,
            "raised": stats.raised,
            "lowered": stats.lowered,
            "no_citing_source": stats.no_citing_source,
            "applied": apply,
        },
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank queued DOIs by the pages that cite them (B-58)."
    )
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("requeue_dois")

    async def go() -> DoiRequeueStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"requeue-dois-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    print(
        f"examined {stats.examined}  raised {stats.raised}  lowered {stats.lowered}  "
        f"unchanged {stats.unchanged}  parents recorded {stats.parents_recorded}"
    )
    print(
        f"  left alone: no citing source {stats.no_citing_source}  "
        f"duplicates {stats.duplicates}  unparseable {stats.unparseable}"
    )
    for reason, n in stats.by_reason.most_common():
        print(f"  best citing page: {reason:16} {n}")
    for priority, n in sorted(stats.by_priority.items(), reverse=True):
        print(f"  priority {priority:4}  {n}")
    if not args.apply:
        print("\nReport only. --apply writes the priorities and parents; nothing is deleted.")


if __name__ == "__main__":
    main()
