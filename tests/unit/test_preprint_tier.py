"""A preprint server is not peer review (task B-233)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

from meridian_core.ageing import HALF_LIFE_DAYS
from meridian_core.answer import PRIMARY_TIERS
from meridian_core.gaps import STRONG_TIERS
from meridian_core.models.source import SOURCE_TIER
from meridian_core.sources import SOURCE_TIER_RANK
from meridian_core.tiering import document_tier, link_tier, resolve_tier

REPO = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((REPO / "config" / "source_tiers.yaml").read_text())


def _migration():
    path = next(
        (REPO / "migrations" / "versions").glob("*_preprint_servers_are_not_peer_reviewed.py")
    )
    spec = importlib.util.spec_from_file_location("b233", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()


@pytest.fixture
def old_map() -> dict:
    """The shipped map as it stood before this revision: arXiv under `peer_reviewed`."""
    tiers = copy.deepcopy(CONFIG)
    tiers["patterns"].pop("preprint")
    tiers["priority_by_tier"].pop("preprint")
    tiers["patterns"]["peer_reviewed"].append("*.arxiv.org")
    tiers["exact"]["peer_reviewed"].insert(0, "arxiv.org")
    return tiers


@pytest.mark.parametrize("url", ["https://arxiv.org/abs/2401.00001", "https://papers.ssrn.com/x"])
def test_a_preprint_server_naming_its_own_doi_is_still_a_preprint(url: str) -> None:
    """The server mints the DOI; it is no evidence of a venue."""
    assert document_tier(url, CONFIG, scholarly=True) == "preprint"
    assert document_tier(url, CONFIG, scholarly=False) == "preprint"
    assert link_tier(url, CONFIG) == "preprint"


def test_preprint_is_not_a_tier_that_makes_coverage_strong() -> None:
    assert "preprint" not in PRIMARY_TIERS
    assert "preprint" not in STRONG_TIERS


def test_preprint_ranks_below_peer_reviewed_so_upsert_cannot_mistake_order() -> None:
    assert set(SOURCE_TIER.enums) == set(SOURCE_TIER_RANK)
    assert SOURCE_TIER_RANK["preprint"] < SOURCE_TIER_RANK["peer_reviewed"]
    assert SOURCE_TIER_RANK["preprint"] > SOURCE_TIER_RANK["institutional"]


def test_preprints_decay_where_peer_reviewed_work_does_not() -> None:
    assert HALF_LIFE_DAYS["peer_reviewed"] is None
    assert HALF_LIFE_DAYS["preprint"] is not None


def test_the_migration_and_the_config_list_the_same_preprint_servers() -> None:
    """Drift: the migration fixes running installs, the YAML seeds new ones."""
    assert CONFIG["patterns"]["preprint"] == MIGRATION.PREPRINT_PATTERNS
    assert CONFIG["priority_by_tier"]["preprint"] == MIGRATION.PREPRINT_PRIORITY


def test_the_migration_check_lists_the_model_tiers() -> None:
    assert tuple(SOURCE_TIER.enums) == MIGRATION.AFTER
    assert set(MIGRATION.AFTER) - set(MIGRATION.BEFORE) == {"preprint"}


def test_the_migrated_map_resolves_like_the_shipped_one(old_map: dict) -> None:
    assert resolve_tier("arxiv.org", old_map) == "peer_reviewed"

    MIGRATION._add_preprint(old_map)

    for host in ("arxiv.org", "export.arxiv.org", "www.ssrn.com", "doi.org", "lta.gov.sg"):
        assert resolve_tier(host, old_map) == resolve_tier(host, CONFIG), host


def test_the_migration_keeps_an_operator_preprint_list_and_priority(old_map: dict) -> None:
    old_map["patterns"]["preprint"] = ["*.arxiv.org"]
    old_map["priority_by_tier"]["preprint"] = 7

    MIGRATION._add_preprint(old_map)

    assert old_map["patterns"]["preprint"] == ["*.arxiv.org"]
    assert old_map["priority_by_tier"]["preprint"] == 7


def test_the_migration_leaves_an_operator_peer_reviewed_entry_alone(old_map: dict) -> None:
    old_map["exact"]["peer_reviewed"].append("journal.example.org")

    MIGRATION._add_preprint(old_map)

    assert "journal.example.org" in old_map["exact"]["peer_reviewed"]
    assert "arxiv.org" not in old_map["exact"]["peer_reviewed"]


def test_a_downgrade_restores_the_old_resolution(old_map: dict) -> None:
    before = copy.deepcopy(old_map)

    MIGRATION._add_preprint(old_map)
    MIGRATION._remove_preprint(old_map)

    assert "preprint" not in old_map["patterns"]
    assert "preprint" not in old_map["priority_by_tier"]
    for host in ("arxiv.org", "export.arxiv.org", "papers.ssrn.com", "doi.org"):
        assert resolve_tier(host, old_map) == resolve_tier(host, before), host


def test_a_map_with_no_tier_sections_migrates_without_error() -> None:
    tiers: dict = {}

    MIGRATION._add_preprint(tiers)

    assert resolve_tier("arxiv.org", tiers) == "preprint"
