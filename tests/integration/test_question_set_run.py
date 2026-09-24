"""Running the question set against a real Postgres (task P2-22, spec §14.1).

Rules under test come from `eval/README.md`: the question is asked as typed,
a lexical-only run says so, the heuristic's grade is a proposal kept apart
from the operator's, and an unreviewed set is labelled a draft.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from meridian_core.models import Chunk, Edge, Entity, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.questionset import (
    LEXICAL_ONLY_NOTE,
    QuestionSetError,
    loads,
    previous_run,
    read_run,
    run_set,
)

pytestmark = pytest.mark.usefixtures("require_db")

REPO = Path(__file__).resolve().parents[2]


def unit(seed: int) -> list[float]:
    return [((seed * 7 + i * 13) % 1000) / 1000.0 for i in range(EMBEDDING_DIM)]


def question_set(marker: str, *, reviewed: bool = False, kind: str = "lookup") -> str:
    return yaml.safe_dump(
        {
            "set_version": 1,
            "status": "draft",
            "questions": [
                {
                    "id": "T01",
                    "kind": kind,
                    "question": f"{marker} sheltered walkway target",
                    "topics": ["walkability"],
                    "what_a_good_answer_contains": {
                        "sources": "government-tier",
                        "concepts": ["sheltered walkway", "coverage target"],
                    },
                    "reviewed": reviewed,
                }
            ],
        }
    )


@pytest.fixture
async def seeded(session_for):
    """One on-topic government passage, cited by one claim. Never committed."""
    marker = f"qs{uuid.uuid4().hex[:8]}"
    sess = await session_for("rw")
    source = Source(
        url=f"https://{marker}.test/doc",
        source_tier="government",
        retention_tier="primary",
        checksum=f"sha256:{uuid.uuid4().hex}",
        text_available=True,
        topic_labels=["walkability"],
    )
    sess.add(source)
    await sess.flush()
    chunk = Chunk(
        source_id=source.source_id,
        text=f"{marker} sheltered walkway coverage target set by the authority",
        chunk_index=0,
        embedding=unit(3),
    )
    sess.add(chunk)
    await sess.flush()
    a = Entity(canonical_name=f"{marker} walkway", node_type="concept")
    b = Entity(canonical_name=f"{marker} target", node_type="concept")
    sess.add_all([a, b])
    await sess.flush()
    sess.add(
        Edge(
            from_node=a.entity_id,
            to_node=b.entity_id,
            relation_type="relates_to",
            supporting_chunk_ids=[chunk.chunk_id],
        )
    )
    await sess.flush()
    return sess, marker, chunk.chunk_id, {a.entity_id, b.entity_id}


async def test_lexical_only_run_finds_the_passage_and_says_it_is_lexical_only(seeded):
    sess, marker, chunk_id, _ = seeded
    run = await run_set(sess, loads(question_set(marker)))

    item = run["items"][0]
    assert [h["chunk_id"] for h in item["hits"]] == [chunk_id]
    assert item["arms"] == ["lexical"]
    assert run["context"]["mode"] == "lexical-only"
    assert run["context"]["mode_note"] == LEXICAL_ONLY_NOTE


async def test_with_a_vector_the_run_is_hybrid(seeded):
    sess, marker, chunk_id, _ = seeded

    async def embed(text: str):
        return unit(3)

    run = await run_set(sess, loads(question_set(marker)), embed=embed, embedder_model="m")
    assert run["context"]["mode"] == "hybrid"
    assert run["context"]["mode_note"] is None
    assert run["items"][0]["hits"][0]["vector_rank"] is not None


async def test_an_embedder_that_fails_for_one_item_makes_the_run_lexical_only(seeded):
    """Hybrid only if every item had both arms; one miss must not be averaged away."""
    sess, marker, *_ = seeded

    async def embed(text: str):
        return None

    run = await run_set(sess, loads(question_set(marker)), embed=embed)
    assert run["context"]["mode"] == "lexical-only"


async def test_the_query_is_the_question_as_typed(seeded):
    """Not the concepts: a passage with the concept words but not the question's
    marker is not found, which it would be if queries were built from concepts."""
    sess, marker, *_ = seeded
    other = f"qs{uuid.uuid4().hex[:8]}"
    run = await run_set(sess, loads(question_set(other)))
    assert run["items"][0]["hits"] == []


async def test_proposal_and_operator_are_apart_and_the_draft_is_labelled(seeded):
    sess, marker, *_ = seeded
    run = await run_set(sess, loads(question_set(marker)))

    item = run["items"][0]
    assert item["operator"] is None
    assert item["proposed"]["grade"] == 2  # right words, right tier, on topic
    assert run["draft"] is True
    assert run["unreviewed"] == ["T01"]
    assert "DRAFT SET" in run["banner"]
    assert "not the operator's score" in run["proposed_summary"]["label"]


async def test_the_graph_reports_claims_that_cite_the_hits(seeded):
    sess, marker, _, entity_ids = seeded
    run = await run_set(sess, loads(question_set(marker)))
    graph = run["items"][0]["graph"]
    assert graph["claims_citing_hits"] == 1
    assert {e["entity_id"] for e in graph["entities"]} == entity_ids


async def test_context_counts_match_the_database(seeded):
    """Drift: the counts in a run are the database's, not a separate tally."""
    sess, marker, *_ = seeded
    run = await run_set(sess, loads(question_set(marker)))
    live = len(
        list(await sess.scalars(select(Chunk.chunk_id).where(Chunk.superseded_at.is_(None))))
    )
    assert run["context"]["corpus"]["chunks_live"] == live


async def test_a_run_works_on_the_read_only_role(session_for):
    """The runner may be pointed at a live corpus; it must need no write."""
    sess = await session_for("ro")
    run = await run_set(sess, loads(question_set("qsnothing")))
    assert run["items"][0]["hits"] == []


# -- the script ---------------------------------------------------------------


def _script():
    spec = importlib.util.spec_from_file_location(
        "run_question_set", REPO / "scripts" / "run_question_set.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_script_writes_a_run_then_compares_the_next_with_it(tmp_path, monkeypatch):
    if not (os.environ.get("PG_RO_URL") or os.environ.get("PG_RW_URL")):
        pytest.skip("the script reads PG_RO_URL from the environment")
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)
    questions = tmp_path / "q.yaml"
    questions.write_text(question_set("qsnothing"))
    runs = tmp_path / "runs"
    script = _script()

    assert await script.main(["--questions", str(questions), "--runs-dir", str(runs)]) == 0
    first = sorted(runs.glob("*.yaml"))
    assert len(first) == 1
    run = read_run(first[0])
    assert run["comparison"]["previous"] is None
    assert run["context"]["mode"] == "lexical-only"

    # The operator grades the first run by hand.
    run["items"][0]["operator"] = {"grade": 1, "missing": "nothing found", "sources": []}
    first[0].write_text(yaml.safe_dump(run, sort_keys=False))

    assert await script.main(["--questions", str(questions), "--runs-dir", str(runs)]) == 0
    second = sorted(runs.glob("*.yaml"))
    assert len(second) == 2
    # By name `<date>-2` sorts before `<date>`; the runner orders by date and number.
    (newest,) = [p for p in second if p != first[0]]
    assert previous_run(runs) == newest
    later = read_run(newest)
    assert later["comparison"]["previous"] == first[0].name
    assert later["comparison"]["items"][0]["before"] == 1
    assert later["comparison"]["items"][0]["after"] is None


async def test_the_script_refuses_to_compare_against_a_mistyped_grade(tmp_path, monkeypatch):
    if not (os.environ.get("PG_RO_URL") or os.environ.get("PG_RW_URL")):
        pytest.skip("the script reads PG_RO_URL from the environment")
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)
    questions = tmp_path / "q.yaml"
    questions.write_text(question_set("qsnothing"))
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "2000-01-01.yaml").write_text(
        yaml.safe_dump({"set_version": 1, "items": [{"id": "T01", "operator": {"grade": 2.5}}]})
    )
    with pytest.raises(QuestionSetError, match="operator grade"):
        await _script().main(["--questions", str(questions), "--runs-dir", str(runs)])
    assert sorted(p.name for p in runs.glob("*.yaml")) == ["2000-01-01.yaml"]
