"""The attribute gate and audit against a real Postgres (tasks `P7-01`, `P7-02`, spec §7.3).

Real because every signal is a query: independent sources fold copies through `sources`,
coverage counts entities across the schema, explanatory power reads comparison edges, and the
schema log is a CHECK-constrained table. Every test runs in a transaction that is rolled back,
because the dev database holds a real schema somebody else is measuring; topic-local
attributes on a made-up topic keep the cap arithmetic independent of what that schema holds.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from meridian_core import attributeschema as schema
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import (
    AttributeDefinition,
    AttributeEvent,
    AttributeValue,
    Chunk,
    Edge,
    Entity,
    Source,
)
from meridian_core.sources import upsert_source
from meridian_core.validation import ValidationError
from meridian_core.writes import tag_candidate, tag_entity

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 11, 2, 9, 0, tzinfo=dt.UTC)
PROVENANCE = {"produced_by": "test-tagger", "model": "test-model-1", "quality_tier": 2}


class World:
    """Five sources with one passage each, six entities, and a made-up topic."""

    def __init__(self, sess, marker: str, chunks: list[int], entities: list[int]) -> None:
        self.sess, self.marker, self.chunks, self.entities = sess, marker, chunks, entities
        self.topic = f"{marker}-topic"

    async def definition(self, suffix: str, *, status: str, scope: str = "topic_local"):
        row = AttributeDefinition(
            name=f"{self.marker}_{suffix}",
            scope=scope,
            topic=self.topic if scope == "topic_local" else None,
            status=status,
        )
        self.sess.add(row)
        await self.sess.flush()
        return row

    async def values(self, definition, pairs, *, tagged_at=NOW) -> None:
        """`pairs` is (value, chunk index) per entity, in entity order."""
        for entity_id, (value, chunk) in zip(self.entities, pairs, strict=False):
            self.sess.add(
                AttributeValue(
                    entity_id=entity_id,
                    attribute_id=definition.attribute_id,
                    schema_version=definition.schema_version,
                    value=value,
                    supporting_chunk_ids=[self.chunks[chunk]],
                    tagged_at=tagged_at,
                    **PROVENANCE,
                )
            )
        await self.sess.flush()

    async def events(self, definition) -> list[tuple[str, str]]:
        rows = await self.sess.scalars(
            select(AttributeEvent)
            .where(AttributeEvent.attribute_id == definition.attribute_id)
            .order_by(AttributeEvent.event_id)
        )
        return [(e.kind, e.reason) for e in rows]


@pytest.fixture
async def world(session_for) -> World:
    sess = await session_for("rw")
    marker = f"atq{uuid.uuid4().hex[:8]}"
    chunks: list[int] = []
    for n in range(5):
        source, _ = await upsert_source(
            sess,
            f"https://{marker}-{n}.test/report",
            checksum=f"sha256:{uuid.uuid4().hex}",
            source_tier="government",
            title=f"Report {n}",
        )
        await replace_chunks(
            sess, source.source_id, [ChunkWrite(text=f"{marker} {n}.", chunk_index=0)]
        )
        await sess.flush()
        chunks.append(
            await sess.scalar(select(Chunk.chunk_id).where(Chunk.source_id == source.source_id))
        )
    entities = [
        Entity(
            canonical_name=f"{marker} place {n}", node_type="place", supporting_chunk_ids=chunks[:1]
        )
        for n in range(6)
    ]
    sess.add_all(entities)
    await sess.flush()
    return World(sess, marker, chunks, [e.entity_id for e in entities])


SPLIT = [("hot", 0), ("cold", 1), ("hot", 2), ("cold", 0), ("hot", 1), ("cold", 2)]


# --------------------------------------------------------------------------
# P7-01: the gate
# --------------------------------------------------------------------------


async def test_too_few_independent_sources_is_refused_and_stays_proposed(world) -> None:
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, [("hot", 0), ("cold", 1), ("hot", 0), ("cold", 1)])

    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=99)

    assert verdict.kind == "refused"
    assert candidate.status == "proposed", "a refusal keeps the evidence gathering"
    assert await world.events(candidate) == [
        ("refused", f"evidence from 2 of {schema.EVIDENCE_SOURCES} independent sources")
    ]

    await schema.judge(world.sess, candidate, now=NOW, cap=99)
    assert len(await world.events(candidate)) == 1, "the same refusal is not logged twice"


async def test_a_copy_of_a_source_is_not_an_independent_one(world) -> None:
    """Three sources, one a copy of another (`B-44`): two voices, below the bar."""
    copy = await world.sess.scalar(select(Chunk.source_id).where(Chunk.chunk_id == world.chunks[2]))
    original = await world.sess.scalar(
        select(Chunk.source_id).where(Chunk.chunk_id == world.chunks[0])
    )
    source = await world.sess.get(Source, copy)
    source.duplicate_of, source.duplicate_reason = original, "exact"
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT)

    signals = await schema.measure(world.sess, candidate, NOW)
    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=99)

    assert signals.sources == 2
    assert verdict.kind == "refused"


async def test_a_value_everywhere_the_same_is_refused_for_not_splitting(world) -> None:
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, [("hot", n % 3) for n in range(6)])

    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=99)

    assert verdict.kind == "refused"
    assert "discrimination 0.00" in verdict.reason
    assert candidate.status == "proposed"


async def test_a_candidate_that_qualifies_with_room_is_activated_and_logged(world) -> None:
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT)
    room = await schema.active_count(world.sess, candidate) + 1

    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=room)

    assert (verdict.kind, candidate.status) == ("activated", "active")
    (event,) = await world.sess.scalars(
        select(AttributeEvent).where(AttributeEvent.attribute_id == candidate.attribute_id)
    )
    assert event.signals["sources"] == 3 and event.signals["discrimination"] == 1.0, (
        "the log keeps the numbers the decision was made on"
    )


async def test_at_the_cap_a_candidate_is_held_when_nothing_weaker_is_failing(world) -> None:
    incumbent = await world.definition("shade", status="active")
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT)
    full = await schema.active_count(world.sess, candidate)

    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=full)

    assert verdict.kind == "held"
    assert (candidate.status, incumbent.status) == ("proposed", "active"), (
        "an attribute that has never failed an audit is not displaced: slow schema is a feature"
    )


async def test_at_the_cap_a_stronger_candidate_displaces_a_failing_one_and_keeps_its_values(
    world,
) -> None:
    incumbent = await world.definition("shade", status="active")
    incumbent.consecutive_audit_failures = 1
    await world.values(incumbent, [("wide", 0)])
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT)
    full = await schema.active_count(world.sess, candidate)

    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=full)

    assert verdict.kind == "activated"
    assert (candidate.status, incumbent.status) == ("active", "retired")
    kept = await world.sess.scalar(
        select(AttributeValue.value).where(AttributeValue.attribute_id == incumbent.attribute_id)
    )
    assert kept == "wide", "retiring deletes nothing"
    assert [kind for kind, _ in await world.events(incumbent)] == ["retired"]


async def test_a_global_candidate_never_displaces_a_topic_local_attribute(world) -> None:
    """Scopes compete within themselves: a global candidate looks only at failing globals."""
    local = await world.definition("shade", status="active")
    local.consecutive_audit_failures = 5
    candidate = await world.definition("canopy", status="proposed", scope="global")
    await world.values(candidate, SPLIT)
    globals_failing = await world.sess.scalar(
        select(AttributeDefinition.attribute_id).where(
            AttributeDefinition.status == "active",
            AttributeDefinition.scope == "global",
            AttributeDefinition.consecutive_audit_failures >= 1,
        )
    )
    if globals_failing is not None:
        pytest.skip("the database holds a failing global attribute, which could be displaced")

    verdict = await schema.judge(
        world.sess, candidate, now=NOW, cap=await schema.active_count(world.sess, candidate)
    )

    assert verdict.kind == "held"
    assert local.status == "active"


async def test_judging_an_attribute_that_is_not_proposed_is_refused(world) -> None:
    active = await world.definition("shade", status="active")
    with pytest.raises(ValidationError) as caught:
        await schema.judge(world.sess, active, now=NOW)
    assert caught.value.rule == "attribute_proposed"


# --------------------------------------------------------------------------
# Writing candidate values
# --------------------------------------------------------------------------


async def test_a_tag_off_the_schema_creates_a_proposal_with_full_provenance(world) -> None:
    definition, result = await schema.propose_from_tag(
        world.sess,
        None,
        attribute=f"{world.marker} Canopy Cover",
        entity_id=world.entities[0],
        supporting_chunk_ids=[world.chunks[0]],
        value="dense",
        now=NOW,
        **PROVENANCE,
    )

    assert definition.name == f"{world.marker}_canopy_cover"
    assert (definition.status, definition.scope) == ("proposed", "global")
    value = await world.sess.get(AttributeValue, result.row_id)
    assert (value.produced_by, value.model, value.quality_tier) == (
        "test-tagger",
        "test-model-1",
        2,
    )
    assert value.supporting_chunk_ids == [world.chunks[0]]
    assert [kind for kind, _ in await world.events(definition)] == ["proposed"]


@pytest.mark.parametrize("name", ["!!", "a", "9lives", "x" * 80])
async def test_a_name_with_no_schema_spelling_is_refused(world, name: str) -> None:
    with pytest.raises(ValidationError) as caught:
        await schema.propose_from_tag(
            world.sess,
            None,
            attribute=name,
            entity_id=world.entities[0],
            supporting_chunk_ids=[world.chunks[0]],
            value="v",
            now=NOW,
            **PROVENANCE,
        )
    assert caught.value.rule == "attribute_name"


@pytest.mark.parametrize("status", ["active", "retired"])
async def test_candidate_values_go_only_to_a_proposed_attribute(world, status: str) -> None:
    definition = await world.definition("shade", status=status)
    with pytest.raises(ValidationError) as caught:
        await tag_candidate(
            world.sess,
            None,
            definition,
            entity_id=world.entities[0],
            supporting_chunk_ids=[world.chunks[0]],
            value="wide",
            now=NOW,
            **PROVENANCE,
        )
    assert caught.value.rule == "attribute_proposed"


async def test_a_proposed_attribute_still_cannot_be_tagged_as_if_active(world) -> None:
    definition = await world.definition("canopy", status="proposed")
    with pytest.raises(ValidationError) as caught:
        await tag_entity(
            world.sess,
            None,
            entity_id=world.entities[0],
            attribute=definition.name,
            supporting_chunk_ids=[world.chunks[0]],
            value="dense",
            now=NOW,
            **PROVENANCE,
        )
    assert caught.value.rule == "attribute_active"


async def test_a_candidate_value_needs_a_chunk_that_exists(world) -> None:
    definition = await world.definition("canopy", status="proposed")
    with pytest.raises(ValidationError):
        await tag_candidate(
            world.sess,
            None,
            definition,
            entity_id=world.entities[0],
            supporting_chunk_ids=[-1],
            value="dense",
            now=NOW,
            **PROVENANCE,
        )


# --------------------------------------------------------------------------
# P7-02: the audit
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["topic_local", "global"])
async def test_an_unused_attribute_is_retired_only_after_consecutive_failures(
    world, scope: str
) -> None:
    unused = await world.definition("shade", status="active", scope=scope)
    await world.values(unused, [("wide", 0)], tagged_at=NOW - dt.timedelta(days=400))
    bar = schema.RETIRE_AFTER[scope]

    for month in range(bar - 1):
        verdict = await schema.audit_one(
            world.sess, unused, now=NOW + dt.timedelta(days=30 * month), corpus_tagged=True
        )
        assert unused.status == "active", f"retired after {month + 1} of {bar} failures"
        assert verdict.kind == "audited"
    await schema.audit_one(
        world.sess, unused, now=NOW + dt.timedelta(days=30 * bar), corpus_tagged=True
    )

    assert unused.status == "retired"
    assert unused.consecutive_audit_failures == bar
    kinds = [kind for kind, _ in await world.events(unused)]
    assert kinds == ["audited"] * bar + ["retired"]
    still = await world.sess.scalar(
        select(AttributeValue.value).where(AttributeValue.attribute_id == unused.attribute_id)
    )
    assert still == "wide"


async def test_a_pass_between_failures_resets_the_count(world) -> None:
    attribute = await world.definition("shade", status="active")
    await schema.audit_one(world.sess, attribute, now=NOW, corpus_tagged=True)
    assert attribute.consecutive_audit_failures == 1

    await world.values(attribute, SPLIT)
    await schema.audit_one(world.sess, attribute, now=NOW, corpus_tagged=True)

    assert attribute.consecutive_audit_failures == 0
    assert attribute.discrimination == 1.0


async def test_an_idle_synthesis_is_not_evidence_of_disuse(world) -> None:
    attribute = await world.definition("shade", status="active")
    verdict = await schema.audit_one(world.sess, attribute, now=NOW, corpus_tagged=False)
    assert attribute.consecutive_audit_failures == 0, verdict.reason


async def test_a_newly_activated_attribute_is_measured_but_not_failed(world) -> None:
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT, tagged_at=NOW - dt.timedelta(days=200))
    await schema.judge(world.sess, candidate, now=NOW, cap=99)
    assert candidate.status == "active"

    await schema.audit_one(
        world.sess, candidate, now=NOW + dt.timedelta(days=10), corpus_tagged=True
    )
    assert candidate.consecutive_audit_failures == 0

    await schema.audit_one(
        world.sess, candidate, now=NOW + schema.GRACE + dt.timedelta(days=1), corpus_tagged=True
    )
    assert candidate.consecutive_audit_failures == 1, "after the grace it is judged on use"


async def test_appearing_in_a_comparison_passes_an_unused_attribute(world) -> None:
    """Explanatory power, the strongest signal (§7.3), read off comparison edges (§7.2)."""
    attribute = await world.definition("canopy", status="active")
    world.sess.add(
        Edge(
            from_node=world.entities[0],
            to_node=world.entities[1],
            relation_type="comparable_to",
            similarity_dimension=f"both cities' {world.marker} canopy",
            disanalogy="incomes differ",
            supporting_chunk_ids=[world.chunks[0]],
            **PROVENANCE,
        )
    )
    await world.sess.flush()

    await schema.audit_one(world.sess, attribute, now=NOW, corpus_tagged=True)

    assert attribute.consecutive_audit_failures == 0
    signals = (await world.events(attribute))[-1]
    assert signals == ("audited", "appears in 1 comparison(s)")


async def test_the_pass_audits_an_attribute_once_a_month_however_often_it_runs(world) -> None:
    attribute = await world.definition("shade", status="active")
    other = await world.definition("canopy", status="active")
    await world.values(other, SPLIT)  # something was tagged in the window

    await schema.run_pass(world.sess, now=NOW)
    await schema.run_pass(world.sess, now=NOW + dt.timedelta(days=1))
    assert attribute.consecutive_audit_failures == 1, "a second run the next day is not an audit"

    await schema.run_pass(world.sess, now=NOW + schema.AUDIT_GAP)
    assert attribute.consecutive_audit_failures == 2
    assert attribute.status == "retired", "topic-local attributes retire after two"


async def test_the_pass_audits_before_it_judges_so_a_retirement_frees_a_slot(world) -> None:
    failing = await world.definition("shade", status="active")
    failing.consecutive_audit_failures = schema.RETIRE_AFTER["topic_local"] - 1
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT)
    full = await schema.active_count(world.sess, candidate)

    verdicts = await schema.run_pass(world.sess, now=NOW, cap=full)

    mine = [(v.name, v.kind) for v in verdicts if v.name.startswith(world.marker)]
    assert mine == [(failing.name, "retired"), (candidate.name, "activated")]


# --------------------------------------------------------------------------
# Reversibility
# --------------------------------------------------------------------------


async def test_restoring_brings_an_attribute_back_with_its_values(world) -> None:
    attribute = await world.definition("shade", status="active")
    await world.values(attribute, SPLIT)
    await schema.retire(world.sess, attribute.name, now=NOW)
    attribute.consecutive_audit_failures = 3

    verdict = await schema.restore(world.sess, attribute.name, now=NOW, cap=99)

    assert (verdict.kind, attribute.status) == ("restored", "active")
    assert attribute.consecutive_audit_failures == 0
    count = await world.sess.scalar(
        select(AttributeValue.value_id)
        .where(AttributeValue.attribute_id == attribute.attribute_id)
        .limit(1)
    )
    assert count is not None
    assert [kind for kind, _ in await world.events(attribute)] == ["retired", "restored"]


async def test_restoring_is_refused_when_the_cap_is_full(world) -> None:
    attribute = await world.definition("shade", status="retired")
    full = await schema.active_count(world.sess, attribute)
    with pytest.raises(ValidationError) as caught:
        await schema.restore(world.sess, attribute.name, now=NOW, cap=full)
    assert caught.value.rule == "attribute_cap"
    assert attribute.status == "retired"


@pytest.mark.parametrize("status", ["active", "proposed"])
async def test_only_a_retired_attribute_can_be_restored(world, status: str) -> None:
    attribute = await world.definition("shade", status=status)
    with pytest.raises(ValidationError) as caught:
        await schema.restore(world.sess, attribute.name, now=NOW, cap=99)
    assert caught.value.rule == "attribute_retired"


async def test_a_name_nobody_defined_cannot_be_restored_or_retired(world) -> None:
    for change in (schema.restore, schema.retire):
        with pytest.raises(ValidationError) as caught:
            await change(world.sess, f"{world.marker}_nothing", now=NOW)
        assert caught.value.rule == "attribute_exists"


async def test_a_retired_name_is_not_proposed_again_by_tagging(world) -> None:
    """Retiring a junk proposal by hand is how it stays rejected."""
    definition = await world.definition("canopy", status="retired")
    with pytest.raises(ValidationError) as caught:
        await schema.propose_from_tag(
            world.sess,
            None,
            attribute=definition.name,
            entity_id=world.entities[0],
            supporting_chunk_ids=[world.chunks[0]],
            value="dense",
            now=NOW,
            **PROVENANCE,
        )
    assert caught.value.rule == "attribute_proposed"


async def test_every_kind_the_module_logs_is_one_the_database_accepts(world) -> None:
    """The log's CHECK constraint against the kinds written: a refused insert would surface
    only on the day that kind first happened."""
    attribute = await world.definition("shade", status="active")
    for kind in AttributeEvent.__table__.c.kind.type.enums:
        await schema._log(world.sess, attribute, kind, f"probe {kind}", now=NOW)
    with pytest.raises(Exception, match="attribute_event"):
        await schema._log(world.sess, attribute, "deleted", "never a kind", now=NOW)


async def test_a_refused_tag_off_the_schema_leaves_no_empty_proposal(world) -> None:
    """A refusal writes nothing: a tag citing a passage that does not exist must not leave a
    proposed definition behind with no evidence on it."""
    with pytest.raises(ValidationError):
        await schema.propose_from_tag(
            world.sess,
            None,
            attribute=f"{world.marker} canopy",
            entity_id=world.entities[0],
            supporting_chunk_ids=[-1],
            value="dense",
            now=NOW,
            **PROVENANCE,
        )
    left = await world.sess.scalar(
        select(AttributeDefinition).where(AttributeDefinition.name == f"{world.marker}_canopy")
    )
    assert left is None


async def test_over_the_cap_by_more_than_one_nothing_is_displaced(world) -> None:
    """One displacement frees one slot. With the schema already two over, retiring one would
    still leave the candidate pushing it over, so it is held and the failing one stays."""
    failing = await world.definition("shade", status="active")
    failing.consecutive_audit_failures = 1
    candidate = await world.definition("canopy", status="proposed")
    await world.values(candidate, SPLIT)
    over = await schema.active_count(world.sess, candidate) - 1

    verdict = await schema.judge(world.sess, candidate, now=NOW, cap=over)

    assert verdict.kind == "held"
    assert (candidate.status, failing.status) == ("proposed", "active")
