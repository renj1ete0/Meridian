"""The attribute gate's and audit's arithmetic (tasks `P7-01`, `P7-02`, spec §7.1, §7.3).

The measurements are pure functions so their edges can be probed here, without a database:
what counts as a split, what a number's class is, and which signal outweighs which. The
database half is `tests/integration/test_attribute_schema.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from meridian_core import attributeschema as schema
from meridian_core.models.graph import ATTRIBUTE_EVENT, ATTRIBUTE_SCOPE
from meridian_core.proposals import tag_schema
from meridian_core.schemas.proposals import TagProposal

REPO = Path(__file__).resolve().parents[2]


def _migration():
    path = next((REPO / "migrations" / "versions").glob("*_p701a1c2d3e4_*.py"))
    spec = importlib.util.spec_from_file_location("mig_p701", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Fleet Size", "fleet_size"),
        ("  canopy-cover ", "canopy_cover"),
        ("ODD", "odd"),
        ("shade / provision", "shade_provision"),
    ],
)
def test_a_name_is_spelled_the_way_the_schema_spells_names(raw: str, expected: str) -> None:
    assert schema.normalise_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "--", "ab", "9lives", "x" * 64, "é"])
def test_a_name_with_no_schema_spelling_has_none(raw: str) -> None:
    assert schema.normalise_name(raw) is None


def test_every_name_a_tag_may_carry_fits_or_is_refused_cleanly() -> None:
    """The tag DTO allows 100 characters; a name the schema cannot hold must come back None,
    never a truncated name that collides with another."""
    limit = TagProposal.model_fields["attribute"].metadata[-1].max_length
    assert schema.normalise_name("a" * limit) is None
    assert schema.normalise_name("a" * 63) == "a" * 63


# --------------------------------------------------------------------------
# Classes and splits
# --------------------------------------------------------------------------


def test_text_values_are_compared_without_case_or_spacing() -> None:
    assert schema.bucket("  High  Density", None) == schema.bucket("high density", None)
    assert schema.bucket("   ", None) is None
    assert schema.bucket(None, None) is None


@pytest.mark.parametrize(
    "a,b,same",
    [(120.0, 980.0, True), (120.0, 1200.0, False), (-5.0, 5.0, False), (0.0, 0.0, True)],
)
def test_a_number_is_classed_by_sign_and_order_of_magnitude(a, b, same: bool) -> None:
    assert (schema.bucket(None, a) == schema.bucket(None, b)) is same


def test_a_number_wins_over_text_when_both_are_given() -> None:
    assert schema.bucket("about a thousand", 1000.0) == schema.bucket(None, 1000.0)


def test_too_few_entities_cannot_be_judged_either_way() -> None:
    assert schema.discrimination(["a", "b", "a"][: schema.MIN_ENTITIES - 1]) is None


def test_one_value_everywhere_splits_nothing() -> None:
    assert schema.discrimination(["hot"] * 10) == 0.0


@pytest.mark.parametrize("k", [2, 3, 4])
def test_an_even_split_scores_the_maximum(k: int) -> None:
    classes = [str(n % k) for n in range(k * 4)]
    assert schema.discrimination(classes) == 1.0


def test_a_heavily_skewed_split_falls_below_the_bar() -> None:
    assert schema.discrimination(["hot"] * 19 + ["cold"]) < schema.MIN_DISCRIMINATION


def test_values_all_worded_differently_fall_below_the_bar() -> None:
    """`B-170`: an attribute whose every value is phrased differently discriminates nothing,
    though its raw entropy is the maximum."""
    assert schema.discrimination([f"wording {n}" for n in range(20)]) < schema.MIN_DISCRIMINATION


def test_more_balance_never_scores_lower() -> None:
    scores = [schema.discrimination(["a"] * (20 - n) + ["b"] * n) for n in range(1, 11)]
    assert scores == sorted(scores)


# --------------------------------------------------------------------------
# Signals and verdicts
# --------------------------------------------------------------------------


def signals(**changed) -> schema.Signals:
    base = dict(
        entities=10, discrimination=0.9, sources=5, used_in_window=3, coverage=0.5, explanatory=0
    )
    return schema.Signals(**{**base, **changed})


def test_explanatory_power_is_the_strongest_signal() -> None:
    """§7.3: one comparison is worth more than a much better split over the same entities."""
    split = signals(discrimination=1.0).utility() - signals(discrimination=0.5).utility()
    one_comparison = signals(explanatory=1).utility() - signals().utility()
    assert one_comparison > split


def test_the_logged_signals_carry_the_utility_they_rank_by() -> None:
    logged = signals().as_json()
    assert logged["utility"] == round(signals().utility(), 4)
    assert set(logged) - {"utility"} == set(schema.Signals.__dataclass_fields__)


def test_going_unused_while_others_are_tagged_fails() -> None:
    passed, reason = schema.audit_verdict(
        signals(used_in_window=0), in_grace=False, corpus_tagged=True
    )
    assert not passed and "no values written" in reason


def test_going_unused_while_nothing_is_tagged_does_not_fail() -> None:
    passed, _ = schema.audit_verdict(signals(used_in_window=0), in_grace=False, corpus_tagged=False)
    assert passed


def test_a_poor_split_fails_even_when_used() -> None:
    passed, reason = schema.audit_verdict(
        signals(discrimination=0.1), in_grace=False, corpus_tagged=True
    )
    assert not passed and "discrimination" in reason


def test_a_comparison_passes_an_attribute_that_would_otherwise_fail() -> None:
    passed, _ = schema.audit_verdict(
        signals(used_in_window=0, discrimination=0.0, explanatory=2),
        in_grace=False,
        corpus_tagged=True,
    )
    assert passed


def test_the_grace_period_passes_anything() -> None:
    passed, _ = schema.audit_verdict(
        signals(used_in_window=0, discrimination=0.0), in_grace=True, corpus_tagged=True
    )
    assert passed


def test_too_few_entities_to_judge_a_split_is_not_a_failure() -> None:
    passed, _ = schema.audit_verdict(
        signals(entities=2, discrimination=None), in_grace=False, corpus_tagged=True
    )
    assert passed


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------


def test_every_scope_has_a_retirement_bar_and_globals_have_the_higher_one() -> None:
    """§7.1: global attributes carry cross-topic reasoning and warrant a higher bar; §7.3:
    two to three consecutive failures."""
    assert set(schema.RETIRE_AFTER) == set(ATTRIBUTE_SCOPE.enums)
    assert schema.RETIRE_AFTER["global"] > schema.RETIRE_AFTER["topic_local"]
    assert set(schema.RETIRE_AFTER.values()) <= {2, 3}


def test_an_audit_is_spaced_closer_than_the_usage_window_it_judges() -> None:
    """Otherwise a value written between two audits could fall in neither window."""
    assert schema.AUDIT_GAP <= schema.USAGE_WINDOW


def test_the_migration_creates_the_log_with_the_models_kinds() -> None:
    source = next((REPO / "migrations" / "versions").glob("*_p701a1c2d3e4_*.py")).read_text()
    for kind in ATTRIBUTE_EVENT.enums:
        assert f'"{kind}"' in source, f"the migration's CHECK lacks {kind!r}"


def test_the_timetable_row_in_the_migration_matches_the_config() -> None:
    jobs = yaml.safe_load((REPO / "config" / "schedule.yaml").read_text())["jobs"]
    name, module, args, interval = _migration().JOB
    (job,) = [j for j in jobs if j["name"] == name]
    assert (job["module"], job["args"], job["interval_seconds"]) == (module, args, interval)
    assert job["enabled"] is True


def test_the_schema_held_to_the_active_set_cannot_carry_a_proposal() -> None:
    """A local model held to the answer's schema (`B-247`) can only name an active attribute,
    so proposals come from models that are not held to it. Pinned so that changing it is a
    decision rather than a side effect."""
    held = tag_schema(passages=3, attributes=["density"])
    items = held["properties"]["assignments"]["items"]
    assert items["properties"]["attribute"]["enum"] == ["density"]
