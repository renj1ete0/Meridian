"""`/contested` and `/tiers` against Postgres (§13.3).

Both read the graph and change nothing, so the session rolls back. `/contested`'s topic filter
is pointed at a topic only these tests use, by standing in for the topic list: the dev
database's own contested claims would otherwise crowd a top-ten list, and creating a real
topic renormalises every weight.
"""

from __future__ import annotations

import datetime as dt
import types
import uuid

import pytest

from meridian_core.models import Edge, Entity
from worker import commands
from worker.commands import execute, parse

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


@pytest.fixture
def topic(monkeypatch) -> str:
    name = f"t{uuid.uuid4().hex[:8]}"

    async def topics(_sess):
        return [types.SimpleNamespace(topic=name)]

    monkeypatch.setattr(commands.steering, "topics", topics)
    return name


async def nodes(sess, *names: str) -> list[Entity]:
    made = [Entity(canonical_name=n, node_type="intervention") for n in names]
    sess.add_all(made)
    await sess.flush()
    return made


def edge(a: Entity, b: Entity, topic: str, *, contested: bool = True, **extra) -> Edge:
    return Edge(
        from_node=a.entity_id,
        to_node=b.entity_id,
        relation_type="reduces",
        topic_labels=[topic],
        contested_with=[1] if contested else None,
        supporting_chunk_ids=[],
        **extra,
    )


async def test_the_most_contested_nodes_come_first_and_only_contested_claims_count(
    sess, topic
) -> None:
    tag = uuid.uuid4().hex[:6]
    hub, spoke, quiet, elsewhere = await nodes(
        sess, f"Kerbside levy {tag}", f"Night buses {tag}", f"Quiet node {tag}", f"Far {tag}"
    )
    sess.add_all(
        [
            edge(hub, spoke, topic),
            edge(hub, quiet, topic, contested=False),
            edge(spoke, hub, topic),
            edge(hub, elsewhere, topic),
            edge(elsewhere, quiet, "another-topic"),
        ]
    )
    await sess.flush()

    reply = await execute(sess, parse(f"/contested {topic}"))

    lines = reply.splitlines()
    assert lines[0] == f"Most contested in {topic}:"
    assert lines[1] == f"Kerbside levy {tag} (intervention): 3 contested claims"
    assert lines[2] == f"Night buses {tag} (intervention): 2 contested claims"
    assert lines[3] == f"Far {tag} (intervention): 1 contested claim"
    assert not any("Quiet node" in line for line in lines), "an uncontested claim was counted"


async def test_a_withdrawn_or_merged_node_is_not_listed(sess, topic) -> None:
    tag = uuid.uuid4().hex[:6]
    kept, gone, merged = await nodes(sess, f"Kept {tag}", f"Gone {tag}", f"Merged {tag}")
    gone.withdrawn_at = dt.datetime.now(dt.UTC)
    merged.redirects_to = kept.entity_id
    sess.add_all([edge(kept, gone, topic), edge(merged, kept, topic)])
    await sess.flush()

    reply = await execute(sess, parse(f"/contested {topic}"))

    assert f"Kept {tag}" in reply
    assert f"Gone {tag}" not in reply and f"Merged {tag}" not in reply


async def test_an_unknown_topic_and_an_empty_one_are_told_apart(sess, topic) -> None:
    assert await execute(sess, parse("/contested no-such-topic")) == "No topic named no-such-topic."
    assert await execute(sess, parse(f"/contested {topic}")) == f"Nothing contested in {topic}."


async def test_tiers_counts_what_each_model_wrote_by_kind(sess, topic) -> None:
    agent, model = f"agent-{uuid.uuid4().hex[:6]}", f"model-{uuid.uuid4().hex[:6]}"
    a, b = await nodes(sess, f"A {agent}", f"B {agent}")
    a.produced_by, a.model, a.quality_tier = agent, model, 2
    sess.add_all(
        [
            edge(a, b, topic, produced_by=agent, model=model, quality_tier=2),
            edge(b, a, topic, produced_by=agent, model=model, quality_tier=2),
        ]
    )
    await sess.flush()

    reply = await execute(sess, parse("/tiers"))

    assert f"tier 2 {agent} · {model}: 1 node, 2 relations" in reply.splitlines()
