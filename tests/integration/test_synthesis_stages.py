"""The three stages that reason, against a real Postgres (task `P4-16`, §6.3,
§11.8, §11.12).

The model is a double. Everything else is real — the same session, the same
write tools, the same guards, the same high-water mark — because the failures
worth catching here are the ones that only exist when those are real: a mark
that moved over chunks nothing reasoned about, provenance written from the
wrong agent, a refused write taking a run down with it.

Each test names the answer the model gave, because that is the variable. The
answers are the ones real models actually produce: a good one, a truncated
one, an apology, and one citing a passage that was never sent.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import (
    Agent,
    AttributeDefinition,
    AttributeValue,
    BudgetConfig,
    Chunk,
    Edge,
    Entity,
    Notification,
    Run,
)
from meridian_core.provider import Completion, ProviderError
from meridian_core.routing import NoAgentAvailable
from meridian_core.runs import begin_or_resume, unfinished
from meridian_core.sources import upsert_source
from worker import orchestrate
from worker.orchestrate import Batch, Journal, step

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.UTC)
AGENT = "test-extractor"


def answer(*items: dict) -> str:
    return json.dumps(list(items))


def relation(subject: str, obj: str, **extra) -> dict:
    return {
        "subject": {"name": subject, "node_type": "organisation"},
        "object": {"name": obj, "node_type": "intervention"},
        "relation": "evaluates",
        "citations": [1],
        **extra,
    }


class FakeModel:
    """Two scripted answers and a count of how many times it was asked.

    The count is the assertion in the dry-run test, and it is the only way to
    make that claim: a dry run rolls its transaction back, so "nothing was
    written" is true whether or not the money was spent.
    """

    def __init__(self, *, extract: str = "[]", tag: str = "[]", fails: Exception | None = None):
        self.answers = {"relation_extraction": extract, "tag_attributes": tag}
        self.fails = fails
        self.calls: list[str] = []

    async def __call__(self, sess, run, task_type, **kwargs):
        self.calls.append(task_type)
        if self.fails is not None:
            raise self.fails
        return Completion(
            text=self.answers[task_type],
            agent_id=AGENT,
            model="test-model-1",
            input_tokens=100,
            output_tokens=50,
        )


@pytest.fixture
def marker() -> str:
    return f"syn{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def corpus(session_for, marker: str):
    """A source with three chunks, an enabled agent, a budget and one attribute.

    Committed because the stages flush against the same session and the run row
    outlives the transaction; removed afterwards, because the dev database is a
    real corpus somebody else is measuring.
    """
    sess = await session_for("rw")
    await sess.rollback()

    stranded = await unfinished(sess)
    held = None
    if stranded is not None:
        held = (stranded.run_id, stranded.status, stranded.error)
        stranded.status, stranded.error = "failed", "set aside by the test suite"

    source, _ = await upsert_source(
        sess,
        f"https://{marker}.test/report",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier="government",
        title="A report",
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"{marker} passage {n}.", chunk_index=n) for n in range(3)],
    )
    await sess.flush()
    # Ids, not rows. `commit()` expires every attribute on these objects, and
    # the next read of one would be lazy IO outside the async context — which
    # surfaces as `MissingGreenlet` and says nothing about the cause.
    source_id = source.source_id
    ids = list(
        await sess.scalars(
            select(Chunk.chunk_id).where(Chunk.source_id == source_id).order_by(Chunk.chunk_id)
        )
    )

    agent = await sess.get(Agent, AGENT)
    if agent is None:
        agent = Agent(agent_id=AGENT, provider="anthropic", model="test-model-1")
        sess.add(agent)
    agent.task_types = ["relation_extraction", "tag_attributes"]
    agent.quality_tier = 4
    agent.enabled = True

    budget = await sess.get(BudgetConfig, 1)
    # Whether this fixture *created* the row matters at teardown: an earlier
    # version restored a pre-existing cap and silently kept a row it had made
    # itself, leaving the dev database with a half-configured budget that a
    # later test then failed on. A fixture that creates shared configuration
    # has to remove it again.
    budget_created = budget is None
    previous_budget = None
    if budget_created:
        sess.add(BudgetConfig(budget_id=1, max_tokens_per_run=1_000_000, max_seeds_per_run=10))
    else:
        previous_budget = budget.max_tokens_per_run
        budget.max_tokens_per_run = budget.max_tokens_per_run or 1_000_000

    attribute = AttributeDefinition(name=f"{marker}-density", scope="global", status="active")
    sess.add(attribute)
    await sess.commit()

    yield sess, ids, marker

    await sess.rollback()
    entity_ids = list(
        await sess.scalars(select(Entity.entity_id).where(Entity.canonical_name.like(f"{marker}%")))
    )
    if entity_ids:
        await sess.execute(delete(AttributeValue).where(AttributeValue.entity_id.in_(entity_ids)))
        await sess.execute(
            delete(Edge).where(Edge.from_node.in_(entity_ids) | Edge.to_node.in_(entity_ids))
        )
        await sess.execute(delete(Entity).where(Entity.entity_id.in_(entity_ids)))
    await sess.execute(delete(Notification).where(Notification.title.like(f"%{marker}%")))
    await sess.execute(
        delete(AttributeDefinition).where(AttributeDefinition.name.like(f"{marker}%"))
    )
    await sess.execute(delete(Chunk).where(Chunk.source_id == source_id))
    await sess.execute(delete(Run).where(Run.agent_id == AGENT))
    await sess.execute(delete(Agent).where(Agent.agent_id == AGENT))
    if budget_created:
        await sess.execute(delete(BudgetConfig).where(BudgetConfig.budget_id == 1))
    elif previous_budget is not None:
        row = await sess.get(BudgetConfig, 1)
        row.max_tokens_per_run = previous_budget
    if held is not None:
        run_id, status, error = held
        row = await sess.get(Run, run_id)
        if row is not None:
            row.status, row.error = status, error
    await sess.commit()


async def drive(sess, ids, model: FakeModel, monkeypatch, *, dry_run: bool = False):
    """Walk pull → extract → tag over exactly the chunks this test made.

    The mark is set one below the first of them rather than left unset: a fresh
    run asks about the whole corpus, and the dev database is a real one, so an
    unset mark would batch somebody else's chunks and the citation numbers
    would point at them.
    """
    monkeypatch.setattr(orchestrate, "complete", model)
    journal = Journal(dry_run=dry_run)
    run, _ = await begin_or_resume(sess, agent_id=AGENT, now=NOW)
    run.last_chunk_id = ids[0] - 1
    await sess.flush()

    batch = Batch()
    for _ in ("pull", "extract", "tag"):
        await step(sess, run, journal=journal, now=NOW, batch=batch)
    return run, journal, batch


# --------------------------------------------------------------------------
# The path that works
# --------------------------------------------------------------------------


async def test_a_good_answer_becomes_an_edge_with_its_citations(corpus, monkeypatch) -> None:
    sess, ids, marker = corpus
    model = FakeModel(extract=answer(relation(f"{marker} Agency", f"{marker} Scheme")))

    run, journal, _ = await drive(sess, ids, model, monkeypatch)

    edge = (
        await sess.scalars(
            select(Edge)
            .join(Entity, Edge.from_node == Entity.entity_id)
            .where(Entity.canonical_name == f"{marker} Agency")
        )
    ).one()
    assert edge.relation_type == "evaluates"
    assert edge.supporting_chunk_ids == [ids[0]], (
        "citation [1] is the first passage sent, not the first chunk in the corpus"
    )
    assert run.edges_added == 1
    assert "add_edge" in journal.render()


async def test_the_edge_records_the_agent_that_produced_it(corpus, monkeypatch) -> None:
    """§11.12: every derived row records which model produced it, or quality
    cannot be improved retroactively and the downgrade guard has nothing to
    compare."""
    sess, ids, marker = corpus
    model = FakeModel(extract=answer(relation(f"{marker} Agency", f"{marker} Scheme")))

    await drive(sess, ids, model, monkeypatch)

    edge = (
        await sess.scalars(
            select(Edge)
            .join(Entity, Edge.from_node == Entity.entity_id)
            .where(Entity.canonical_name == f"{marker} Agency")
        )
    ).one()
    assert (edge.produced_by, edge.model, edge.quality_tier) == (AGENT, "test-model-1", 4)


async def test_both_ends_of_the_edge_became_nodes(corpus, monkeypatch) -> None:
    """The model names mentions; the server decides which nodes they are. An
    edge whose ends were never resolved is the thing `add_edge` refuses."""
    sess, ids, marker = corpus
    model = FakeModel(extract=answer(relation(f"{marker} Agency", f"{marker} Scheme")))

    await drive(sess, ids, model, monkeypatch)

    names = set(
        await sess.scalars(
            select(Entity.canonical_name).where(Entity.canonical_name.like(f"{marker}%"))
        )
    )
    assert names == {f"{marker} Agency", f"{marker} Scheme"}


async def test_the_same_claim_twice_corroborates_rather_than_duplicating(
    corpus, monkeypatch
) -> None:
    """Two passages stating one relation is one edge with two citations. The
    alternative makes every count downstream a count of extraction passes."""
    sess, ids, marker = corpus
    both = answer(
        relation(f"{marker} Agency", f"{marker} Scheme"),
        {**relation(f"{marker} Agency", f"{marker} Scheme"), "citations": [2]},
    )

    run, _, _ = await drive(sess, ids, FakeModel(extract=both), monkeypatch)

    edges = list(
        await sess.scalars(
            select(Edge)
            .join(Entity, Edge.from_node == Entity.entity_id)
            .where(Entity.canonical_name == f"{marker} Agency")
        )
    )
    assert len(edges) == 1
    assert edges[0].supporting_chunk_ids == [ids[0], ids[1]]
    assert run.edges_added == 1, "corroboration is not a new edge"


async def test_a_tag_lands_on_the_entity_extraction_created(corpus, monkeypatch) -> None:
    sess, ids, marker = corpus
    model = FakeModel(
        extract=answer(relation(f"{marker} Agency", f"{marker} Scheme")),
        tag=answer(
            {
                "entity": {"name": f"{marker} Agency", "node_type": "organisation"},
                "attribute": f"{marker}-density",
                "value": "high",
                "citations": [1],
            }
        ),
    )

    run, _, _ = await drive(sess, ids, model, monkeypatch)

    value = (
        await sess.scalars(
            select(AttributeValue)
            .join(Entity, AttributeValue.entity_id == Entity.entity_id)
            .where(Entity.canonical_name == f"{marker} Agency")
        )
    ).one()
    assert value.value == "high"
    assert run.tags_added == 1


# --------------------------------------------------------------------------
# The mark, which is what makes a run resumable
# --------------------------------------------------------------------------


async def test_the_mark_moves_only_after_the_last_stage_that_read_the_batch(
    corpus, monkeypatch
) -> None:
    """`extract` and `tag` read the same batch, so it has been reasoned over
    only when the second one is done. A mark that moved after `extract` would
    abandon the batch if tagging then failed."""
    sess, ids, marker = corpus
    monkeypatch.setattr(orchestrate, "complete", FakeModel(extract=answer(relation("A", "B"))))
    journal = Journal()
    run, _ = await begin_or_resume(sess, agent_id=AGENT, now=NOW)
    run.last_chunk_id = ids[0] - 1
    await sess.flush()
    batch = Batch()

    await step(sess, run, journal=journal, now=NOW, batch=batch)  # pull
    assert run.last_chunk_id == ids[0] - 1
    await step(sess, run, journal=journal, now=NOW, batch=batch)  # extract
    assert run.last_chunk_id == ids[0] - 1, "extract must not claim the batch"
    await step(sess, run, journal=journal, now=NOW, batch=batch)  # tag

    assert run.last_chunk_id == ids[-1]


async def test_a_batch_the_model_found_nothing_in_still_advances(corpus, monkeypatch) -> None:
    """An empty answer is an answer. A run that refused to advance over it
    would re-read the same passages every cycle for ever."""
    sess, ids, _ = corpus

    run, _, _ = await drive(sess, ids, FakeModel(extract="[]", tag="[]"), monkeypatch)

    assert run.last_chunk_id == ids[-1]
    assert run.edges_added == 0


async def test_a_deferred_run_leaves_the_mark_where_it_was(corpus, monkeypatch) -> None:
    """§13.4: a provider outage defers rather than crashes, and the batch it
    could not reason over must be pulled again next time."""
    sess, ids, _ = corpus
    model = FakeModel(fails=ProviderError("every agent refused"))
    monkeypatch.setattr(orchestrate, "complete", model)
    journal = Journal()
    run, _ = await begin_or_resume(sess, agent_id=AGENT, now=NOW)
    run.last_chunk_id = ids[0] - 1
    await sess.flush()
    batch = Batch()

    await step(sess, run, journal=journal, now=NOW, batch=batch)
    with pytest.raises(orchestrate.Deferred):
        await step(sess, run, journal=journal, now=NOW, batch=batch)

    assert run.last_chunk_id == ids[0] - 1
    assert run.status == "deferred"
    assert "deferred" in journal.render()


async def test_a_registry_with_no_agent_defers_rather_than_failing(corpus, monkeypatch) -> None:
    sess, ids, _ = corpus
    monkeypatch.setattr(
        orchestrate, "complete", FakeModel(fails=NoAgentAvailable("nothing declares it"))
    )
    journal = Journal()
    run, _ = await begin_or_resume(sess, agent_id=AGENT, now=NOW)
    run.last_chunk_id = ids[0] - 1
    await sess.flush()
    batch = Batch()
    await step(sess, run, journal=journal, now=NOW, batch=batch)

    with pytest.raises(orchestrate.Deferred):
        await step(sess, run, journal=journal, now=NOW, batch=batch)

    assert run.status == "deferred"


# --------------------------------------------------------------------------
# Answers that are wrong, and the run that carries on anyway
# --------------------------------------------------------------------------


async def test_a_citation_outside_the_batch_writes_nothing_and_keeps_going(
    corpus, monkeypatch
) -> None:
    """A model citing passage nine of a batch of three has stopped describing
    what it was given. The edge is dropped; the run is not."""
    sess, ids, marker = corpus
    model = FakeModel(
        extract=answer(
            {**relation(f"{marker} Agency", f"{marker} Scheme"), "citations": [9]},
            {**relation(f"{marker} Other", f"{marker} Scheme"), "citations": [2]},
        )
    )

    run, journal, _ = await drive(sess, ids, model, monkeypatch)

    names = set(
        await sess.scalars(
            select(Entity.canonical_name).where(Entity.canonical_name.like(f"{marker}%"))
        )
    )
    assert f"{marker} Agency" not in names, "an unverifiable citation writes nothing at all"
    assert f"{marker} Other" in names, "its neighbour was fine and must survive"
    assert "was not in this batch" in journal.render()
    assert run.edges_added == 1


async def test_an_apology_is_recorded_and_the_run_finishes(corpus, monkeypatch) -> None:
    sess, ids, _ = corpus
    model = FakeModel(extract="I'm sorry, I can't help with that.")

    run, journal, _ = await drive(sess, ids, model, monkeypatch)

    assert run.edges_added == 0
    assert "no JSON in the answer" in journal.render()
    assert run.status == "running", "an unusable answer is not a failed run"


async def test_a_refused_write_does_not_end_the_batch(corpus, monkeypatch) -> None:
    """`tag_entity` refuses an attribute that is not active (§7.3). The refusal
    is the system working, and the next proposal must still be tried."""
    sess, ids, marker = corpus
    model = FakeModel(
        extract=answer(relation(f"{marker} Agency", f"{marker} Scheme")),
        tag=answer(
            {
                "entity": {"name": f"{marker} Agency", "node_type": "organisation"},
                "attribute": "an-attribute-nobody-defined",
                "value": "high",
                "citations": [1],
            },
            {
                "entity": {"name": f"{marker} Agency", "node_type": "organisation"},
                "attribute": f"{marker}-density",
                "value": "high",
                "citations": [1],
            },
        ),
    )

    run, journal, _ = await drive(sess, ids, model, monkeypatch)

    assert run.tags_added == 1
    assert "refused" in journal.render()


# --------------------------------------------------------------------------
# The dry run
# --------------------------------------------------------------------------


async def test_a_dry_run_does_not_call_the_model_at_all(corpus, monkeypatch) -> None:
    """The claim `--dry-run` cannot make by rolling back: a call that happened
    spent real money, and the record of having spent it is rolled back with
    everything else, so the ledger would understate the run."""
    sess, ids, marker = corpus
    model = FakeModel(extract=answer(relation(f"{marker} Agency", f"{marker} Scheme")))

    run, journal, _ = await drive(sess, ids, model, monkeypatch, dry_run=True)

    assert model.calls == []
    assert run.edges_added == 0
    assert run.last_chunk_id == ids[0] - 1
    assert "no model is called on a dry run" in journal.render()


async def test_a_dry_run_still_says_what_it_would_have_sent(corpus, monkeypatch) -> None:
    sess, ids, _ = corpus

    _, journal, batch = await drive(sess, ids, FakeModel(), monkeypatch, dry_run=True)

    assert len(batch.passages) == 3
    assert "would send 3 passages" in journal.render()


# --------------------------------------------------------------------------
# What `pull` leaves out
# --------------------------------------------------------------------------


async def test_a_superseded_chunk_is_not_reasoned_over(corpus, monkeypatch) -> None:
    """`P1-32` supersedes rather than deletes, so the text is still here — it
    is just no longer what the page says, and spending tokens to conclude
    something about a withdrawn passage is the worst kind of waste."""
    sess, ids, _ = corpus
    (await sess.get(Chunk, ids[1])).superseded_at = NOW
    await sess.flush()

    _, _, batch = await drive(sess, ids, FakeModel(), monkeypatch, dry_run=True)

    assert [passage.chunk_id for passage in batch.passages] == [ids[0], ids[2]]


async def test_a_known_duplicate_is_not_reasoned_over(corpus, monkeypatch) -> None:
    """The novelty gate already judged it the same text as something the corpus
    holds (§6.1). Reasoning over it again reaches the same conclusion and pays
    for it twice."""
    sess, ids, _ = corpus
    (await sess.get(Chunk, ids[2])).duplicate_of = ids[0]
    await sess.flush()

    _, _, batch = await drive(sess, ids, FakeModel(), monkeypatch, dry_run=True)

    assert [passage.chunk_id for passage in batch.passages] == [ids[0], ids[1]]
