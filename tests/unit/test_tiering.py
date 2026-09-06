"""Tier resolution, queue priority, and fetch delay.

Tests run against the real `config/source_tiers.yaml` rather than a fixture. The
mapping is configuration a human edits, so the thing worth protecting is that
*this* file keeps producing the right answers — a fixture would pass happily
while the shipped config was wrong.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from meridian_core.tiering import (
    jittered_delay_ms,
    priority_for_domain,
    registrable_domain,
    resolve_tier,
)

CONFIG = Path(__file__).resolve().parents[2] / "config" / "source_tiers.yaml"


@pytest.fixture(scope="module")
def mapping() -> dict:
    return yaml.safe_load(CONFIG.read_text())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.lta.gov.sg/content/x", "lta.gov.sg"),
        ("http://LTA.GOV.SG:8080", "lta.gov.sg"),
        ("www.ura.gov.sg", "ura.gov.sg"),
        ("arxiv.org", "arxiv.org"),
    ],
)
def test_registrable_domain_normalises(raw: str, expected: str) -> None:
    assert registrable_domain(raw) == expected


@pytest.mark.parametrize(
    "domain,expected",
    [
        # Bare domains a "*.x" pattern cannot match — the arxiv.org case.
        ("arxiv.org", "peer_reviewed"),
        ("export.arxiv.org", "peer_reviewed"),
        ("doi.org", "peer_reviewed"),
        # Comparison-set governments. If any of these regress to informal, that
        # city's official sources start ranking below blogs (§7.2).
        ("www.lta.gov.sg", "government"),
        ("td.gov.hk", "government"),
        ("metro.tokyo.go.jp", "government"),
        ("seoul.go.kr", "government"),
        ("tfl.gov.uk", "government"),
        # Promotions and demotions that only exist because a pattern is wrong.
        ("openalex.org", "peer_reviewed"),
        ("humantransit.org", "informal"),
        ("itdp.org", "institutional"),
        ("straitstimes.com", "press"),
        # Unmapped domains fall to the default rather than erroring.
        ("some-random-blog.net", "informal"),
        ("landtransportguru.net", "informal"),
    ],
)
def test_resolve_tier(domain: str, expected: str, mapping: dict) -> None:
    assert resolve_tier(domain, mapping) == expected


def test_longest_pattern_wins(mapping: dict) -> None:
    """`*.gov.sg` must beat a shorter suffix, or every SG domain collapses."""
    custom = {
        "default_tier": "informal",
        "patterns": {"institutional": ["*.sg"], "government": ["*.gov.sg"]},
    }
    assert resolve_tier("lta.gov.sg", custom) == "government"
    assert resolve_tier("example.sg", custom) == "institutional"


def test_exact_beats_pattern(mapping: dict) -> None:
    """humantransit.org matches *.org but is one person's blog."""
    assert resolve_tier("humantransit.org", mapping) == "informal"
    assert resolve_tier("someothergroup.org", mapping) == "institutional"


def test_government_outranks_press_and_informal(mapping: dict) -> None:
    """The point of tiering: a .gov result is fetched before a blog."""
    gov = priority_for_domain("td.gov.hk", mapping)
    press = priority_for_domain("straitstimes.com", mapping)
    blog = priority_for_domain("some-random-blog.net", mapping)
    assert gov > press > blog


def test_peer_reviewed_outranks_government(mapping: dict) -> None:
    """Deliberate: papers carry more weight than an agency's own press page."""
    assert priority_for_domain("arxiv.org", mapping) > priority_for_domain("lta.gov.sg", mapping)


def test_unknown_tier_has_zero_priority(mapping: dict) -> None:
    from meridian_core.tiering import priority_for_tier

    assert priority_for_tier("not_a_tier", mapping) == 0


def test_jitter_respects_the_floor_and_ceiling() -> None:
    rng = random.Random(0)
    values = {jittered_delay_ms(1000, 1500, rng) for _ in range(500)}
    assert min(values) >= 1000, "jitter must only ever add to the floor"
    assert max(values) <= 2500
    assert len(values) > 100, "a fixed interval is a fingerprint; this must vary"


def test_jitter_is_optional() -> None:
    assert jittered_delay_ms(1000, 0) == 1000


def test_negative_delay_is_rejected() -> None:
    with pytest.raises(ValueError):
        jittered_delay_ms(-1, 100)
