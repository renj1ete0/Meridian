"""The proposal heuristic and its bounds, without a database (task P6-38, §10, §10.1).

§10.1's guard rails on autonomous adjustment — floors, ceilings, pinned topics
the orchestrator cannot modify — are transcribed here as refusals: each test
builds the case that should produce *no* proposal, or a smaller one, and
asserts that it does. The drift tests hold the window's three copies (YAML,
migration, module) and the timetable's two together.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
from pathlib import Path

import pytest
import yaml

from meridian_core import steering_proposals as sp
from meridian_core.models import runs
from meridian_core.policy import NOT_FETCH_SETTINGS, ResolvedPolicy

REPO = Path(__file__).resolve().parents[2]


def m(topic: str, **kw) -> sp.TopicMeasure:
    base = dict(
        weight=1 / 3,
        floor=0.05,
        ceiling=0.8,
        share=1 / 3,
        boosted=False,
        fetches=100,
        new_sources=20,
    )
    base.update(kw)
    return sp.TopicMeasure(topic=topic, **base)


def balanced(**overrides: dict) -> list[sp.TopicMeasure]:
    """Three topics, each getting exactly its share — no proposal."""
    topics = {"a": {}, "b": {}, "c": {}}
    for name, kw in overrides.items():
        topics[name] = kw
    return [m(name, **kw) for name, kw in topics.items()]


def by_topic(drafts: list[sp.Draft]) -> dict[str, sp.Draft]:
    return {d.topic: d for d in drafts}


# -- what is proposed ------------------------------------------------------------


def test_a_balanced_crawl_proposes_nothing() -> None:
    assert sp.draft_proposals(balanced()) == []


def test_a_starved_topic_gets_a_boost_that_expires() -> None:
    drafts = by_topic(sp.draft_proposals(balanced(a={"new_sources": 2})))
    boost = drafts["a"]
    assert (boost.kind, boost.current, boost.proposed) == ("boost", 1.0, sp.BOOST_FACTOR)
    assert boost.evidence["boost_hours"] == sp.BOOST_HOURS > 0
    # The reason carries the numbers it rests on, in words.
    assert "2 of 42" in boost.reason and "33%" in boost.reason


def test_an_over_served_topic_is_lowered_by_a_capped_step() -> None:
    drafts = by_topic(
        sp.draft_proposals(balanced(a={"new_sources": 60, "fetches": 200}, b={}, c={}))
    )
    lowered = drafts["a"]
    assert lowered.kind == "weight"
    step = lowered.current - lowered.proposed
    assert 0 < step <= sp.MAX_WEIGHT_STEP + 1e-9
    assert step <= lowered.current * sp.MAX_RELATIVE_STEP + 1e-9


def test_a_topic_that_yields_well_but_takes_few_fetches_is_not_lowered() -> None:
    """Many sources for little crawl is efficiency, not over-service; lowering
    its weight would take crawl from the topic using it best."""
    drafts = sp.draft_proposals(balanced(a={"new_sources": 60, "fetches": 30}))
    assert "a" not in by_topic(drafts)


def test_a_thin_topic_that_is_behind_its_share_is_boosted() -> None:
    drafts = by_topic(sp.draft_proposals(balanced(a={"new_sources": 15, "thin_sources": 4})))
    assert drafts["a"].kind == "boost"
    assert drafts["a"].evidence["labelled_sources"] == 4


def test_a_thin_topic_already_ahead_of_its_share_is_left_alone() -> None:
    drafts = sp.draft_proposals(balanced(a={"new_sources": 21, "thin_sources": 4}))
    assert "a" not in by_topic(drafts)


# -- what is refused ---------------------------------------------------------------


@pytest.mark.parametrize(
    "held",
    [{"pinned": True}, {"quiet": True}, {"boosted": True}, {"share": 0.0}],
    ids=["pinned", "recently-steered", "already-boosted", "draws-nothing"],
)
def test_no_proposal_for_a_topic_that_is_held(held: dict) -> None:
    """§10.1: pinned topics are the ones autonomous adjustment may not touch;
    a recently steered topic's manual choice wins; a boost is not stacked."""
    starved = sp.draft_proposals(balanced(a={"new_sources": 1, **held}))
    over = sp.draft_proposals(balanced(a={"new_sources": 60, "fetches": 200, **held}))
    assert "a" not in by_topic(starved) and "a" not in by_topic(over)


@pytest.mark.parametrize(
    "totals",
    [{"fetches": sp.MIN_FETCHES // 3 - 1}, {"new_sources": sp.MIN_NEW_SOURCES // 3 - 1}],
    ids=["few-fetches", "few-sources"],
)
def test_too_little_evidence_proposes_nothing(totals: dict) -> None:
    quiet = {name: {**totals} for name in "abc"}
    quiet["a"] = {**totals, "new_sources": 0}
    assert sp.draft_proposals(balanced(**quiet)) == []


def test_a_single_active_topic_is_never_steered() -> None:
    """It draws everything whatever its weight; a proposal would change nothing."""
    assert sp.draft_proposals([m("a", share=1.0, fetches=500, new_sources=1)]) == []


def test_a_weight_at_its_floor_is_not_lowered() -> None:
    drafts = sp.draft_proposals(
        balanced(a={"weight": 0.05, "share": 0.05, "new_sources": 60, "fetches": 200})
    )
    assert "a" not in by_topic(drafts)


@pytest.mark.parametrize(
    ("weight", "floor"),
    # Up to 0.6: above that, 1.5x its share is more than the whole of the new
    # sources, and "over-served" is unreachable by construction.
    list(itertools.product([0.06, 0.1, 0.2, 0.4, 0.6], [0.0, 0.05, 0.1, 0.3])),
)
def test_a_lowered_weight_never_crosses_its_floor_or_moves_past_the_caps(
    weight: float, floor: float
) -> None:
    if floor > weight:
        pytest.skip("a weight below its floor is not a state steering produces")
    drafts = by_topic(
        sp.draft_proposals(
            balanced(
                a={
                    "weight": weight,
                    "share": weight,
                    "floor": floor,
                    "new_sources": 400,
                    "fetches": 900,
                }
            )
        )
    )
    if "a" not in drafts:
        # Only allowed when the room above the floor is below the minimum step.
        assert min(sp.MAX_WEIGHT_STEP, weight * sp.MAX_RELATIVE_STEP, weight - floor) < (
            sp.MIN_WEIGHT_STEP
        )
        return
    d = drafts["a"]
    assert d.proposed >= floor - 1e-9
    assert d.current - d.proposed <= sp.MAX_WEIGHT_STEP + 1e-9
    assert d.current - d.proposed <= d.current * sp.MAX_RELATIVE_STEP + 1e-9


def test_one_proposal_per_topic_at_most() -> None:
    measures = [
        m(f"t{i}", new_sources=n, fetches=f)
        for i, (n, f) in enumerate([(0, 10), (90, 300), (5, 100), (40, 90)])
    ]
    drafts = sp.draft_proposals(measures)
    assert len({d.topic for d in drafts}) == len(drafts)


# -- the window ----------------------------------------------------------------------


@pytest.mark.parametrize("raw", [0, -3, 0.5, 169, "soon", True, [], {}, float("nan")])
def test_an_unusable_window_falls_back_to_the_default(raw) -> None:
    """A window under an hour would make a proposal a change nobody could stop."""
    assert sp.parse_window(raw) == sp.DEFAULT_WINDOW_HOURS


@pytest.mark.parametrize(("raw", "hours"), [(None, 12.0), (6, 6.0), ("24", 24.0), (1, 1.0)])
def test_a_usable_window_is_read(raw, hours) -> None:
    assert sp.parse_window(raw) == hours


# -- drift ------------------------------------------------------------------------------


def _migration():
    path = next((REPO / "migrations" / "versions").glob("*_63509269e4e1_*.py"))
    spec = importlib.util.spec_from_file_location("mig_p638", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_window_default_is_the_same_in_config_migration_and_code() -> None:
    config = yaml.safe_load((REPO / "config" / "fetch_policy.yaml").read_text())
    assert config[sp.WINDOW_KEY] == _migration().WINDOW_HOURS == sp.DEFAULT_WINDOW_HOURS


def test_the_timetable_row_in_the_migration_matches_the_config() -> None:
    jobs = yaml.safe_load((REPO / "config" / "schedule.yaml").read_text())["jobs"]
    name, module, args, interval = _migration().JOB
    (job,) = [j for j in jobs if j["name"] == name]
    assert (job["module"], job["args"], job["interval_seconds"]) == (module, args, interval)


def test_the_migration_allows_every_notification_type_the_model_does() -> None:
    assert set(_migration().NEW_TYPES) == set(runs.NOTIFICATION_TYPE.enums)


def test_every_global_policy_key_is_a_fetch_setting_or_stripped() -> None:
    """A key in the global row that is neither becomes an unknown "setting" on
    every domain (``ResolvedPolicy`` keeps extras), and one that is both would
    be silently stripped from every fetch."""
    config = set(yaml.safe_load((REPO / "config" / "fetch_policy.yaml").read_text()))
    fields = set(ResolvedPolicy.model_fields)
    assert config - fields - NOT_FETCH_SETTINGS == set()
    assert NOT_FETCH_SETTINGS & fields == set()
    assert sp.WINDOW_KEY in NOT_FETCH_SETTINGS


def test_the_proposal_actor_is_not_an_operator() -> None:
    """The basis check tells an operator's change from an auto-applied one by
    actor. If the two were spelled the same, every applied proposal would
    supersede the next."""
    assert sp.ACTOR not in {"user", "telegram"}
    assert dataclasses.is_dataclass(sp.PassReport)
