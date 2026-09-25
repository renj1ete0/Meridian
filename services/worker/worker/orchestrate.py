"""One synthesis cycle, and a way to run it without letting it write
(task `P4-09`, §11.10, §6.3).

`P4-08` moves a run through its stages. This is the thing that calls it, and
the two flags §11.10 asks for: `--dry-run`, which applies nothing, and `--once`,
which stops after a single cycle.

**Here rather than in `services/orchestrator/`.** The scaffold reserves a
service for this and the compose file gates it behind `phase4` because its
Dockerfile does not exist. Creating that service now would mean a Dockerfile, a
compose entry, a release-script line and a healthcheck for a process whose
stages are not built — and `docs/handover.md` already records what an empty
profiled service costs. The worker image runs `python -m worker.<x>` entry
points already and has every dependency; when the stages land, moving this
module is a rename.

**`--dry-run` is a rolled-back transaction, not a promise.** A flag that each
stage had to remember to check is a flag one stage will forget, and the way you
find out is that a dry run wrote something. Here the whole cycle runs inside a
transaction that is rolled back unconditionally, so a stage that writes without
asking still writes nothing. The journal is what makes the run legible: every
intended call is recorded and printed whether or not it was applied.

**A cycle that changes nothing stops the loop.** Without that, an orchestrator
whose stages are all unbuilt — which is every orchestrator today — would find
work past the high-water mark, fail to advance it, find the same work, and spin
until something killed it. The loop's exit condition is progress, not emptiness.

**The stages are named, not absent.** Each one says which task builds it, for
the same reason `worker/commands.py` refuses by name: "there is no tagging
stage" and "the tagging stage did nothing" are indistinguishable from a log,
and only one of them is true.

`P4-16` filled in the first three — `pull`, `extract` and `tag` — and four
decisions in them are worth reading before changing anything here:

**A dry run does not call the model.** It would spend real money and then roll
the *record* of having spent it back with everything else, so the ledger would
be wrong in the one direction that matters: understated. A dry run says which
batch it would send and how large the prompt is, and stops there.

**The batch is ordered by chunk id, never by novelty.** §11.9 suggests
processing the most novel first when a day exceeds the budget, and that is
incompatible with a high-water mark: reasoning over chunk 900 and then marking
the run at 900 silently abandons 400 through 899. Novelty is spent as a filter
instead — known duplicates are left out — and the order stays the order the
mark can describe.

**The mark moves in `tag`, not in `extract`.** Both stages read the batch
`pull` chose, so the batch has been reasoned over only once the second of them
is done. A failure in between leaves the mark where it was and the batch is
pulled again next cycle, which is safe because `add_edge` corroborates a claim
it already holds rather than writing it twice.

**One refusal does not end a run.** A malformed proposal, a citation out of
range, a write the guards refuse — each is noted in the journal and the batch
carries on. The run is deferred only when the model itself is unreachable,
which is §13.4's answer rather than a crash.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import os
import signal
import time
from collections.abc import AsyncIterator
from typing import Final

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.budget import BudgetError, load_budget
from meridian_core.db import dispose_engines, get_sessionmaker
from meridian_core.embedder import RemoteEmbedder
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.mentions import embed_missing_entities, embed_names, resolve_mention
from meridian_core.models import (
    Agent,
    AttributeDefinition,
    Chunk,
    ChunkTopics,
    Entity,
    Run,
    Source,
    TopicConfig,
)
from meridian_core.proposals import (
    CitationOutOfRange,
    Passage,
    chunk_ids_for,
    extract_prompt,
    parse_edges,
    parse_tags,
    tag_prompt,
)
from meridian_core.provider import Completion, NotConfigured, ProviderError, complete
from meridian_core.resolution import expansions_from_gazetteer
from meridian_core.routing import NoAgentAvailable
from meridian_core.runs import (
    FINAL_STAGE,
    STAGES,
    RunLocked,
    advance,
    advancing,
    begin_or_resume,
    defer,
    finish,
    unfinished,
)
from meridian_core.validation import ValidationError
from meridian_core.writes import add_edge, tag_entity

from .liveness import beat

log = get_logger(__name__)

__all__ = [
    "BATCH",
    "BUILT_BY",
    "RUNNERS",
    "Batch",
    "Deferred",
    "Journal",
    "cycle",
    "pending_work",
    "run_orchestrator",
    "step",
]

#: How many chunks one cycle reasons over. Small enough that a frontier model
#: reads the whole batch attentively rather than summarising the middle of it,
#: and small enough that a failure costs one batch rather than a day. The
#: corpus is drained by running more cycles, which is what `max_cycles` bounds.
BATCH: Final[int] = 40

#: How many existing entity names the tagging prompt lists for spelling. A long
#: list is expensive on every call and stops being read; this is enough to
#: anchor the names a batch is actually about.
NAMES_IN_PROMPT: Final[int] = 60

#: Which task builds each stage. Named rather than omitted: a stage missing
#: from a log reads as a stage that ran and found nothing, and the two want
#: entirely different responses.
#:
#: `done` is absent because it is not work — it is the state of having finished.
BUILT_BY: Final[dict[str, str]] = {
    "score": "`P5-03`'s coverage scoring, which is schema-aware",
    "analogies": "`P7-04`'s analogical expansion",
    "gap": "`P5-04`'s gap analysis",
    "seed": "`P5-04`'s seed emission — `enqueue_seed` already caps and validates it",
}


@dataclasses.dataclass
class Journal:
    """What the cycle intended to do, in the order it intended to do it.

    Kept even on a real run. §11.9 wants cost per run compared week on week,
    and a run whose stages are only visible as row counts cannot be compared
    with one whose stages refused for different reasons.
    """

    dry_run: bool = False
    entries: list[str] = dataclasses.field(default_factory=list)

    def note(self, stage: str, message: str) -> None:
        self.entries.append(f"{stage:<9} {message}")

    def call(self, tool: str, **params: object) -> None:
        """Record an intended tool call.

        The write itself is the caller's; this only records that it was asked
        for. On a dry run the transaction is rolled back around it, so the two
        stay consistent without the caller knowing which mode it is in.
        """
        shown = ", ".join(f"{k}={v!r}" for k, v in params.items())
        self.entries.append(f"{'call':<9} {tool}({shown})")

    def render(self) -> str:
        head = "would do (nothing was written):" if self.dry_run else "did:"
        if not self.entries:
            return f"{head}\n  (nothing)"
        return head + "\n" + "\n".join(f"  {line}" for line in self.entries)


@contextlib.asynccontextmanager
async def _scope(*, dry_run: bool) -> AsyncIterator[AsyncSession]:
    """A session that commits, or one that cannot.

    Deliberately not a flag threaded through every stage: the guarantee has to
    hold for a stage whose author did not think about dry runs, and the only
    version of that guarantee which does is a transaction nobody can commit.
    """
    async with get_sessionmaker("rw")() as sess:
        try:
            yield sess
            if dry_run:
                await sess.rollback()
            else:
                await sess.commit()
        except Exception:
            await sess.rollback()
            raise


async def pending_work(sess: AsyncSession, run: Run) -> int:
    """How many chunks sit past the high-water mark (§6.3).

    The mark is per-run state but the question is about the corpus, so a fresh
    run with no mark asks about everything — which is the correct answer for
    the first run a deployment ever does.
    """
    stmt = select(func.count()).select_from(Chunk)
    if run.last_chunk_id is not None:
        stmt = stmt.where(Chunk.chunk_id > run.last_chunk_id)
    return int(await sess.scalar(stmt) or 0)


class Deferred(RuntimeError):
    """The run cannot continue now, and saying so is the answer (§13.4).

    Raised only when the model itself could not be reached — no agent declares
    the task, every agent in the chain refused, or the budget will not allow
    the call. Not for a bad proposal or a refused write: those are ordinary and
    the batch carries on without them.
    """


@dataclasses.dataclass
class Batch:
    """The chunks one cycle reasons over, and what the stages learn about them.

    Carried between stages rather than re-queried, for two reasons. `extract`
    and `tag` must see the *same* passages or their citation numbers mean
    different things, and re-running the query would re-read a table the
    crawler is writing to concurrently.
    """

    passages: list[Passage] = dataclasses.field(default_factory=list)
    #: Set once a model has actually answered over this batch. The mark may
    #: only move over chunks something reasoned about, and "the stage ran" is
    #: not the same claim as "the model was reached".
    reasoned: bool = False
    #: Provenance for everything written from this batch, from the agent that
    #: answered rather than from the registry's preferences (§11.12).
    produced_by: str | None = None
    model: str | None = None
    quality_tier: int | None = None

    @property
    def last_chunk_id(self) -> int | None:
        return max((passage.chunk_id for passage in self.passages), default=None)

    def provenance(self, completion: Completion, quality_tier: int | None) -> None:
        self.reasoned = True
        self.produced_by = completion.agent_id
        self.model = completion.model
        self.quality_tier = quality_tier


async def _topics(sess: AsyncSession) -> list[str]:
    """The active topics, for the prompt. Steering is a weight vector (§10),
    and the weights are not the model's business — which topics exist is."""
    rows = await sess.scalars(
        select(TopicConfig.topic).where(TopicConfig.status == "active").order_by(TopicConfig.topic)
    )
    return list(rows)


async def _quality_tier(sess: AsyncSession, agent_id: str) -> int | None:
    """The tier of the agent that answered.

    Read from the registry rather than carried on the completion: §11.12's
    downgrade guard compares tiers across runs, so the number has to be the
    one the registry currently states, not one copied at call time.
    """
    agent = await sess.get(Agent, agent_id)
    return agent.quality_tier if agent is not None else None


#: `B-63`: a deployment whose corpus is mostly off-topic can point synthesis at
#: labelled on-topic passages only. Off by default: it depends on content
#: labelling having run, and a fresh install has no labels yet.
ON_TOPIC_ENV = "MERIDIAN_SYNTHESIS_ON_TOPIC_ONLY"


def on_topic_only() -> bool:
    return os.environ.get(ON_TOPIC_ENV, "").strip().lower() in {"1", "true", "yes"}


def on_topic_chunk():
    """The source, or the passage itself, is labelled with a topic by content."""
    return or_(
        func.cardinality(Source.topic_labels) > 0,
        exists().where(
            ChunkTopics.chunk_id == Chunk.chunk_id,
            func.cardinality(ChunkTopics.topic_labels) > 0,
        ),
    )


async def _before_unexamined(sess: AsyncSession, stmt, run: Run):
    unexamined = await sess.scalar(
        select(func.min(Chunk.chunk_id)).where(
            Chunk.superseded_at.is_(None),
            ~exists().where(ChunkTopics.chunk_id == Chunk.chunk_id),
            *([Chunk.chunk_id > run.last_chunk_id] if run.last_chunk_id is not None else []),
        )
    )
    return stmt.where(Chunk.chunk_id < unexamined) if unexamined is not None else stmt


async def _pull(
    sess: AsyncSession, run: Run, batch: Batch, *, journal: Journal, now: dt.datetime
) -> None:
    """Choose the chunks this cycle reasons over (§6.3).

    **Ordered by id, filtered by novelty** — see the module docstring. The
    filter drops chunks the gate already judged duplicates and chunks a
    re-crawl has superseded (`P1-32`): both are text the corpus holds
    elsewhere, and reasoning over them again costs tokens to reach the same
    conclusion. It also drops junk-tier sources (`P2-21`), as search and the
    map already do: a source demoted as off-topic or duplicate is one the
    corpus has decided is not evidence, and spending a model on it would be
    reasoning over what the reader was told is not there. And it takes only
    passages that content labelling put on a topic, stopping short of any
    passage not yet examined, when the deployment asks for it (`B-63`).

    Takes `now` and does not use it: every runner has one signature, so a stage
    added later cannot be called with arguments the dispatcher does not send.
    """
    del now
    stmt = (
        select(Chunk, Source)
        .join(Source, Chunk.source_id == Source.source_id)
        .where(
            Chunk.superseded_at.is_(None),
            Chunk.duplicate_of.is_(None),
            # A copy of an earlier source (`B-44`) would be the same reasoning
            # twice, and two edges citing one document as if it were two.
            Source.duplicate_of.is_(None),
            # Junk is material a sweep will drop — site furniture, for one
            # (`B-42`). Reasoning over it spends a batch to extract nothing.
            Source.retention_tier != "junk",
        )
        .order_by(Chunk.chunk_id)
        .limit(BATCH)
    )
    if run.last_chunk_id is not None:
        stmt = stmt.where(Chunk.chunk_id > run.last_chunk_id)
    if on_topic_only():
        stmt = stmt.where(on_topic_chunk())
        # ...and never past a passage nobody has examined yet. The mark only
        # moves forward, so a passage skipped because it was not yet labelled
        # would be skipped for good once labelling caught up.
        stmt = await _before_unexamined(sess, stmt, run)

    rows = (await sess.execute(stmt)).all()
    batch.passages = [
        Passage(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            url=source.url,
            source_tier=source.source_tier,
        )
        for chunk, source in rows
    ]

    if not batch.passages:
        journal.note("pull", "nothing past the mark")
        return
    first, last = batch.passages[0].chunk_id, batch.passages[-1].chunk_id
    journal.note("pull", f"{len(batch.passages)} chunks, {first}–{last}")


async def _ask(
    sess: AsyncSession,
    run: Run,
    *,
    task_type: str,
    prompt,
    journal: Journal,
    stage: str,
    now: dt.datetime,
) -> Completion:
    """One model call, with the two refusals that end a run and the one that does not.

    A budget that is unset refuses here rather than in the provider, because
    the message differs: "nobody configured a token cap" sends somebody to
    Admin, while "every agent refused" sends them to the registry or to the
    provider's status page.
    """
    budget = await load_budget(sess)
    if budget is None:
        await defer(sess, run, "no budget is configured, so no run may spend tokens (§11.9)")
        journal.note(stage, "deferred: no budget configured")
        raise Deferred("no budget configured")

    try:
        completion = await complete(
            sess,
            run,
            task_type,
            prompt=prompt.user,
            system=prompt.system,
            token_cap=budget.max_tokens_per_run,
            now=now,
        )
    except (NoAgentAvailable, ProviderError, NotConfigured, BudgetError) as exc:
        # §13.4: a deferred run, not a crash. The run stays resumable and the
        # batch is pulled again when something can answer for it.
        await defer(sess, run, f"{task_type}: {exc}")
        journal.note(stage, f"deferred: {exc}")
        raise Deferred(str(exc)) from exc

    journal.note(
        stage,
        f"{completion.agent_id} answered with {len(completion.text):,} characters "
        f"for {completion.total_tokens:,} tokens",
    )
    return completion


async def _mention_vectors(
    sess: AsyncSession, names: list[str], *, journal: Journal, stage: str
) -> dict[str, list[float]]:
    """Vectors for this batch's mention names, and for entities still without one.

    `B-40`: without these, resolution compares names by their words alone and
    cannot see that two differently-worded names mean the same thing — so
    they became separate nodes and never reached a person's adjudication.
    An unreachable embedder degrades to names alone, as it always did.
    """
    embedder = RemoteEmbedder.from_env()
    if embedder is None:
        return {}
    try:
        backfilled = await embed_missing_entities(sess, embedder)
        if backfilled:
            journal.note(stage, f"embedded {backfilled} entities that had no vector")
        vectors = await embed_names(embedder, names)
    finally:
        await embedder.aclose()
    if names and not vectors:
        journal.note(stage, "embedder unavailable; resolving mentions by name alone")
    return vectors


async def _extract(sess: AsyncSession, run: Run, batch: Batch, *, journal: Journal, now) -> None:
    """Relations, from the passages that state them (§5.4, §11.6).

    Each proposal is resolved, written and counted on its own. A proposal the
    guards refuse is noted and skipped: `validation.py` exists to refuse model
    output, and a refusal it produced is the system working rather than an
    error to propagate.
    """
    if not batch.passages:
        journal.note("extract", "no batch")
        return

    prompt = extract_prompt(batch.passages, topics=await _topics(sess))
    if journal.dry_run:
        journal.note(
            "extract",
            f"would send {len(batch.passages)} passages, {prompt.characters:,} characters; "
            "no model is called on a dry run",
        )
        return

    completion = await _ask(
        sess,
        run,
        task_type="relation_extraction",
        prompt=prompt,
        journal=journal,
        stage="extract",
        now=now,
    )
    batch.provenance(completion, await _quality_tier(sess, completion.agent_id))

    parsed = parse_edges(completion.text)
    journal.note("extract", parsed.summary)
    for rejection in parsed.rejected:
        journal.note("extract", f"dropped — {rejection}")

    expansions = await expansions_from_gazetteer(sess)
    vectors = await _mention_vectors(
        sess,
        [name for p in parsed.accepted for name in (p.subject.name, p.object.name)],
        journal=journal,
        stage="extract",
    )
    for proposal in parsed.accepted:
        try:
            chunk_ids = chunk_ids_for(proposal.citations, batch.passages)
        except CitationOutOfRange as exc:
            journal.note("extract", f"dropped — {exc}")
            continue

        try:
            subject = await resolve_mention(
                sess,
                name=proposal.subject.name,
                node_type=proposal.subject.node_type,
                jurisdiction=proposal.subject.jurisdiction,
                supporting_chunk_ids=chunk_ids,
                produced_by=batch.produced_by,
                model=batch.model,
                quality_tier=batch.quality_tier,
                expansions=expansions,
                embedding=vectors.get(proposal.subject.name.strip()),
                now=now,
            )
            target = await resolve_mention(
                sess,
                name=proposal.object.name,
                node_type=proposal.object.node_type,
                jurisdiction=proposal.object.jurisdiction,
                supporting_chunk_ids=chunk_ids,
                produced_by=batch.produced_by,
                model=batch.model,
                quality_tier=batch.quality_tier,
                expansions=expansions,
                embedding=vectors.get(proposal.object.name.strip()),
                now=now,
            )
            journal.call(
                "add_edge",
                from_node=subject.entity.entity_id,
                to_node=target.entity.entity_id,
                relation_type=proposal.relation,
                supporting_chunk_ids=chunk_ids,
            )
            result = await add_edge(
                sess,
                run,
                from_node=subject.entity.entity_id,
                to_node=target.entity.entity_id,
                relation_type=proposal.relation,
                supporting_chunk_ids=chunk_ids,
                produced_by=batch.produced_by,
                model=batch.model,
                quality_tier=batch.quality_tier,
                confidence=proposal.confidence,
                stance=proposal.stance,
                certainty=proposal.certainty,
                topic_labels=proposal.topic_labels,
                similarity_dimension=proposal.similarity_dimension,
                disanalogy=proposal.disanalogy,
                now=now,
            )
        except ValidationError as exc:
            journal.note("extract", f"refused — {exc}")
            continue
        journal.note("extract", f"{proposal.relation}: {result.detail}")


async def _tag(sess: AsyncSession, run: Run, batch: Batch, *, journal: Journal, now) -> None:
    """Attribute values, onto entities the passages describe (§7.3).

    **This is where the mark moves**, because it is the last stage that reads
    the batch. It moves even when there was nothing to tag — an empty active
    attribute set is a configuration, not a failure, and a run that refused to
    advance over it would re-read the same batch until somebody noticed.
    """
    if not batch.passages:
        journal.note("tag", "no batch")
        return

    attributes = list(
        (
            await sess.execute(
                select(AttributeDefinition.name, AttributeDefinition.description)
                .where(AttributeDefinition.status == "active")
                .order_by(AttributeDefinition.name)
            )
        ).all()
    )

    if not attributes:
        journal.note("tag", "no active attributes; nothing to tag (§7.3)")
        return

    chunk_ids = [passage.chunk_id for passage in batch.passages]
    known = list(
        await sess.scalars(
            select(Entity.canonical_name)
            .where(Entity.redirects_to.is_(None), Entity.supporting_chunk_ids.overlap(chunk_ids))
            .order_by(Entity.entity_id.desc())
            .limit(NAMES_IN_PROMPT)
        )
    )

    prompt = tag_prompt(batch.passages, attributes=attributes, entities=known)
    if journal.dry_run:
        journal.note(
            "tag",
            f"would send {len(batch.passages)} passages against {len(attributes)} "
            "attributes; no model is called on a dry run",
        )
        return

    completion = await _ask(
        sess, run, task_type="tag_attributes", prompt=prompt, journal=journal, stage="tag", now=now
    )
    quality_tier = await _quality_tier(sess, completion.agent_id)

    parsed = parse_tags(completion.text)
    journal.note("tag", parsed.summary)
    for rejection in parsed.rejected:
        journal.note("tag", f"dropped — {rejection}")

    expansions = await expansions_from_gazetteer(sess)
    vectors = await _mention_vectors(
        sess, [p.entity.name for p in parsed.accepted], journal=journal, stage="tag"
    )
    for proposal in parsed.accepted:
        try:
            cited = chunk_ids_for(proposal.citations, batch.passages)
        except CitationOutOfRange as exc:
            journal.note("tag", f"dropped — {exc}")
            continue

        try:
            entity = await resolve_mention(
                sess,
                name=proposal.entity.name,
                node_type=proposal.entity.node_type,
                jurisdiction=proposal.entity.jurisdiction,
                supporting_chunk_ids=cited,
                produced_by=completion.agent_id,
                model=completion.model,
                quality_tier=quality_tier,
                expansions=expansions,
                embedding=vectors.get(proposal.entity.name.strip()),
                now=now,
            )
            journal.call(
                "tag_entity",
                entity_id=entity.entity.entity_id,
                attribute=proposal.attribute,
                supporting_chunk_ids=cited,
            )
            result = await tag_entity(
                sess,
                run,
                entity_id=entity.entity.entity_id,
                attribute=proposal.attribute,
                supporting_chunk_ids=cited,
                produced_by=completion.agent_id,
                model=completion.model,
                quality_tier=quality_tier,
                value=proposal.value,
                value_numeric=proposal.value_numeric,
                confidence=proposal.confidence,
                now=now,
            )
        except ValidationError as exc:
            journal.note("tag", f"refused — {exc}")
            continue
        journal.note("tag", result.detail)


#: The stages that are built, and what runs them. A stage in here must not
#: also be in `BUILT_BY` — one table says "this is done" and the other says
#: "this is not", and a stage in both would log whichever the code happened to
#: check first.
RUNNERS: Final[dict[str, object]] = {"pull": _pull, "extract": _extract, "tag": _tag}


async def step(
    sess: AsyncSession,
    run: Run,
    *,
    journal: Journal,
    now: dt.datetime,
    batch: Batch | None = None,
) -> str:
    """Do the current stage, then move to the next. Returns the new stage.

    The window is where a stage goes. Inside it, writes happen and
    `progress.reached(chunk_id)` records how far they got; on the way out the
    mark moves once, after them (§6.3, `P4-11`). A stage that lands here
    inherits that ordering without restating it — and one that raises leaves
    the mark exactly where it was.
    """
    stage = run.stage or STAGES[0]
    held = batch if batch is not None else Batch()
    runner = RUNNERS.get(stage)

    async with advancing(sess, run, now=now) as progress:
        if runner is None:
            journal.note(
                stage, f"not built — needs {BUILT_BY.get(stage, 'nothing (it is the end)')}"
            )
        else:
            await runner(sess, run, held, journal=journal, now=now)
            # Only `tag` marks, and only over a batch a model actually saw.
            # The two conditions are separate on purpose: a stage that ran
            # without reaching a model has not reasoned over anything, and a
            # mark that moved anyway would abandon the batch silently.
            if stage == "tag" and held.reasoned and held.last_chunk_id is not None:
                progress.reached(held.last_chunk_id)
    return await advance(sess, run, now=now)


async def cycle(
    sess: AsyncSession,
    *,
    journal: Journal,
    now: dt.datetime,
    agent_id: str | None = None,
    stop_after: str | None = None,
) -> Run:
    """Begin or resume a run and walk it to the end, or to `stop_after`.

    `stop_after` leaves the run **unfinished on purpose**, which is the point:
    it stays resumable, so the next call continues from there rather than
    starting again. That is the only way to step through a run by hand without
    the state machine treating each step as a fresh crash.
    """
    if stop_after is not None and stop_after not in STAGES:
        raise ValueError(f"{stop_after!r} is not a stage. Known: {', '.join(STAGES)}")

    run, resumed = await begin_or_resume(sess, agent_id=agent_id, now=now)
    journal.note("run", f"{'resumed' if resumed else 'started'} run {run.run_id} at {run.stage}")

    waiting = await pending_work(sess, run)
    journal.note("run", f"{waiting:,} chunks past the mark")

    # One batch per cycle, shared by every stage that reads it. A resumed run
    # starts with an empty one and `pull` is behind it, so the first thing a
    # run resumed at `extract` does is say it has no batch — which is true, and
    # better than reasoning over a different set of passages than the citation
    # numbers in its last answer referred to.
    batch = Batch()

    while run.stage != FINAL_STAGE:
        reached = run.stage
        try:
            await step(sess, run, journal=journal, now=now, batch=batch)
        except Deferred:
            # Already journalled and already written to the run row. The run is
            # left unfinished on purpose: it resumes at the stage that could not
            # be served, rather than walking on to stages that would fail the
            # same way (§13.4).
            return run
        if stop_after is not None and reached == stop_after:
            journal.note("run", f"stopping after {reached}; the run stays resumable")
            return run

    await finish(sess, run, now=now)
    journal.note("run", f"finished run {run.run_id}")
    return run


async def run_orchestrator(
    *,
    dry_run: bool = False,
    once: bool = False,
    stop_after: str | None = None,
    max_cycles: int = 10,
    agent_id: str | None = None,
    now: dt.datetime | None = None,
) -> Journal:
    """Run cycles while there is work and each one makes progress.

    **Progress, not emptiness, is the exit condition.** A cycle that advanced
    neither the high-water mark nor any counter will do exactly the same thing
    next time, so continuing would spin — and an orchestrator spinning on a
    daily timer looks, from the outside, like one that is busy.
    """
    journal = Journal(dry_run=dry_run)
    moment = now or dt.datetime.now(dt.UTC)

    await _cycles(
        journal,
        dry_run=dry_run,
        once=once,
        stop_after=stop_after,
        max_cycles=max_cycles,
        agent_id=agent_id,
        moment=moment,
    )
    return journal


async def _cycles(
    journal: Journal,
    *,
    dry_run: bool,
    once: bool,
    stop_after: str | None,
    max_cycles: int,
    agent_id: str | None,
    moment: dt.datetime,
) -> None:
    for iteration in range(max_cycles):
        async with _scope(dry_run=dry_run) as sess:
            # The system's mark before this cycle. Since `B-36` a new run
            # inherits it, so "the run has a mark" says nothing about whether
            # this cycle got anywhere — only the mark *moving* does.
            before = await sess.scalar(select(func.max(Run.last_chunk_id)))
            try:
                run = await cycle(
                    sess, journal=journal, now=moment, agent_id=agent_id, stop_after=stop_after
                )
            except RunLocked as exc:
                # §13.4's answer to a busy system is to skip the cycle. Two
                # orchestrators is worse than none.
                journal.note("run", f"another orchestrator holds the run: {exc}")
                break

            deferred = run.status == "deferred"
            progressed = (run.last_chunk_id or 0) > (before or 0)
            waiting = await pending_work(sess, run)

        if once or stop_after is not None:
            break
        if deferred:
            # Nothing can answer, and asking again within the same wake-up only
            # closes the deferred run with no batch and opens another that
            # defers the same way — five throwaway runs per tick, found on the
            # relay agent's first afternoon. The next wake-up retries.
            journal.note("run", "deferred; stopping until something can answer")
            break
        if not waiting:
            journal.note("run", "nothing past the mark; stopping")
            break
        if not progressed:
            journal.note(
                "run",
                f"cycle {iteration + 1} changed nothing while {waiting:,} chunks wait; stopping",
            )
            break


#: How long between synthesis runs when nothing forces one sooner (§6.3, daily).
DEFAULT_INTERVAL_S: Final[int] = 86_400

#: Chunks past the mark that justify waking early. §6.3 offers the early
#: trigger as an option, and this is the number it left open: below it a run
#: spends a frontier model's attention on a handful of passages, which is the
#: expensive way to learn very little.
DEFAULT_EARLY_AT: Final[int] = 500

#: Ceiling on the wait between wake-ups. The daily timer is the schedule; this
#: is how often the backlog is *looked at*, so an early trigger fires within
#: the hour rather than at whatever moment the day happens to turn over.
POLL_S: Final[int] = 900


async def waiting_chunks() -> int:
    """How many chunks sit past the current run's mark, for the early trigger.

    Its own session, because this is asked between runs rather than inside
    one, and holding a transaction open across a sleep would pin a connection
    for the day.
    """
    async with get_sessionmaker("rw")() as sess:
        run = await unfinished(sess)
        if run is None:
            return int(await sess.scalar(select(func.count()).select_from(Chunk)) or 0)
        return await pending_work(sess, run)


async def serve(
    *,
    interval_s: int = DEFAULT_INTERVAL_S,
    early_at: int = DEFAULT_EARLY_AT,
    poll_s: int = POLL_S,
    max_cycles: int = 10,
    agent_id: str | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Run synthesis on §6.3's schedule until told to stop.

    **A service rather than a row in the timetable, and not by preference.**
    The scheduler spawns its jobs as subprocesses of its own container, which
    is the worker image — and the worker image deliberately does not carry
    `meridian-core[agent]`, because §2.1 makes "the fast loop never calls a
    model" mechanical rather than remembered. A timetable row would therefore
    run this in the one image that cannot do it.

    **Early, not sooner.** §6.3 asks for a daily run "optionally
    early-triggered when unprocessed novel chunk count crosses a threshold",
    so the backlog is checked every `poll_s` and a run starts early when it is
    large enough to be worth a model's attention. The daily timer still runs
    on a quiet corpus, because a run that found little is also the run that
    reports the corpus is quiet.
    """
    stopping = stop or asyncio.Event()
    next_run = 0.0  # the first cycle happens at startup, not a day later

    while not stopping.is_set():
        beat()
        now = time.monotonic()
        waiting = await waiting_chunks()
        due = now >= next_run
        early = waiting >= early_at

        if due or early:
            log.info(
                "synthesis run starting",
                extra={"waiting": waiting, "reason": "scheduled" if due else "backlog"},
            )
            journal = await run_orchestrator(max_cycles=max_cycles, agent_id=agent_id)
            for line in journal.render().splitlines():
                log.info("synthesis", extra={"line": line})
            next_run = time.monotonic() + interval_s
        else:
            log.info("synthesis idle", extra={"waiting": waiting, "early_at": early_at})

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=poll_s)


async def _serve(args: argparse.Namespace) -> None:
    """The daemon, with signals wired and the pool closed once."""
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signame), stopping.set)
    try:
        await serve(
            interval_s=args.interval_seconds,
            early_at=args.early_at,
            max_cycles=args.max_cycles,
            agent_id=args.agent,
            stop=stopping,
        )
    finally:
        await dispose_engines()


async def _main(args: argparse.Namespace) -> Journal:
    """Run, then close the pool — both inside one event loop.

    The disposal belongs here rather than in `run_orchestrator`: a library
    function that tears down the process's shared engines is a surprise to
    anybody who calls it twice. It also has to happen in the loop that created
    them — a second `asyncio.run` is a second loop, and disposing from it fails
    at exit, after the work, in a traceback that says nothing about the cause.
    """
    try:
        return await run_orchestrator(
            dry_run=args.dry_run,
            once=args.once,
            stop_after=args.stop_after,
            max_cycles=args.max_cycles,
            agent_id=args.agent,
        )
    finally:
        await dispose_engines()


def main() -> None:
    """Entry point: ``python -m worker.orchestrate``."""
    parser = argparse.ArgumentParser(description="§11.10's synthesis cycle.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would happen and write nothing. The transaction is rolled back.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single cycle and exit, rather than continuing while work remains",
    )
    parser.add_argument(
        "--stop-after",
        metavar="STAGE",
        default=None,
        help=(
            f"stop once this stage is done, leaving the run resumable. One of: {', '.join(STAGES)}"
        ),
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=10,
        help="a ceiling on cycles per invocation, so a loop cannot run away unattended",
    )
    parser.add_argument("--agent", default=None, help="record this agent on the run")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="stay up and run on §6.3's schedule, rather than running now and exiting",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_S,
        help="how long between scheduled runs in --daemon (default: daily)",
    )
    parser.add_argument(
        "--early-at",
        type=int,
        default=DEFAULT_EARLY_AT,
        help="chunks past the mark that trigger a run before the timer (§6.3)",
    )
    args = parser.parse_args()

    configure_logging("orchestrate")
    with bind_run_id(f"orch-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        if args.daemon:
            asyncio.run(_serve(args))
            return
        journal = asyncio.run(_main(args))
        print(journal.render())


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
