"""An academic domain is not peer review (task B-50)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from meridian_core.tiering import (
    WITHOUT_EVIDENCE,
    document_tier,
    link_tier,
    needs_evidence,
    priority_for_domain,
    resolve_tier,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((REPO / "config" / "source_tiers.yaml").read_text())

MAPPING = {
    "default_tier": "informal",
    "patterns": {
        "peer_reviewed": ["*.edu", "*.ac.uk", "*.springer.com"],
        "government": ["*.gov"],
        "institutional": ["*.org"],
    },
    "exact": {"peer_reviewed": ["journal.example.edu"]},
    "needs_scholarly_evidence": ["*.edu", "*.ac.uk"],
    "priority_by_tier": {"peer_reviewed": 60, "government": 50, "institutional": 20},
}


@pytest.mark.parametrize(
    ("domain", "needs"),
    [
        ("law.example.edu", True),
        ("www.hospital.example.ac.uk", True),
        ("link.springer.com", False),  # a publisher: the domain is the evidence
        ("journal.example.edu", False),  # named exactly: somebody's judgement
        ("transport.gov", False),  # not scholarly at all
        ("example.org", False),
    ],
)
def test_only_an_academic_institutions_scholarly_tier_needs_evidence(domain, needs) -> None:
    assert needs_evidence(domain, MAPPING) is needs


def test_a_link_to_an_academic_domain_ranks_as_institutional() -> None:
    assert link_tier("law.example.edu", MAPPING) == WITHOUT_EVIDENCE
    assert priority_for_domain("law.example.edu", MAPPING) == 20
    assert priority_for_domain("link.springer.com", MAPPING) == 60


def test_a_document_with_its_own_doi_keeps_the_scholarly_tier() -> None:
    assert document_tier("law.example.edu", MAPPING, scholarly=True) == "peer_reviewed"
    assert document_tier("law.example.edu", MAPPING, scholarly=False) == WITHOUT_EVIDENCE


def test_evidence_never_promotes_a_tier_the_domain_does_not_have() -> None:
    assert document_tier("example.org", MAPPING, scholarly=True) == "institutional"


def test_a_map_without_the_key_behaves_as_before() -> None:
    old = {k: v for k, v in MAPPING.items() if k != "needs_scholarly_evidence"}
    assert link_tier("law.example.edu", old) == resolve_tier("law.example.edu", old)


def test_every_evidence_pattern_is_a_peer_reviewed_pattern() -> None:
    """A pattern listed as needing evidence for a tier it does not grant would do nothing."""
    peer = set(CONFIG["patterns"]["peer_reviewed"])
    assert set(CONFIG["needs_scholarly_evidence"]) <= peer


def test_no_publisher_pattern_needs_evidence() -> None:
    publishers = {"*.springer.com", "*.sciencedirect.com", "*.wiley.com", "*.arxiv.org"}
    assert not publishers & set(CONFIG["needs_scholarly_evidence"])


def test_the_migration_and_the_config_list_the_same_patterns() -> None:
    """Drift: the migration seeds running installs, the YAML seeds new ones."""
    path = next(
        (REPO / "migrations" / "versions").glob("*_scholarly_tiers_need_document_evidence.py")
    )
    spec = importlib.util.spec_from_file_location("mig", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.EVIDENCE_PATTERNS == CONFIG["needs_scholarly_evidence"]
