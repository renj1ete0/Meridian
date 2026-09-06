"""Source tiering, queue priority, and fetch delay (spec §5.2, §6.4).

Pure functions over a mapping, deliberately: tier assignment is mechanical and
must never be a model judgement (§5.2), so it needs to be trivially testable and
give the same answer every time for the same domain.

Tier does three jobs:

- it is the first tiebreaker when sources conflict;
- it sets queue priority, so a search result from a government domain is fetched
  before a blog without anyone curating a seed list;
- it drives tier-imbalance counter-seeding (§7.4), where a node evidenced
  entirely by one tier gets seeds aimed at the missing ones.
"""

from __future__ import annotations

import random
from typing import Any

DEFAULT_TIER = "informal"


def registrable_domain(host: str) -> str:
    """Strip scheme, port, path and a leading ``www.`` from a host or URL."""
    host = host.strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    host = host.split("/", 1)[0].split("@")[-1].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def resolve_tier(domain: str, mapping: dict[str, Any]) -> str:
    """Return the source tier for ``domain``.

    Resolution order is exact match, then the **longest** matching suffix
    pattern, then the default. Longest-wins matters: ``*.gov.sg`` and ``*.sg``
    can both match, and the specific one has to win or every Singapore domain
    collapses into one tier.
    """
    host = registrable_domain(domain)

    for tier, names in (mapping.get("exact") or {}).items():
        if host in {n.lower() for n in names or []}:
            return tier

    best_len, best_tier = 0, mapping.get("default_tier", DEFAULT_TIER)
    for tier, patterns in (mapping.get("patterns") or {}).items():
        for pattern in patterns or []:
            pattern = pattern.lower()
            suffix = pattern[1:] if pattern.startswith("*") else "." + pattern
            if (host.endswith(suffix) or host == suffix.lstrip(".")) and len(suffix) > best_len:
                best_len, best_tier = len(suffix), tier
    return best_tier


def priority_for_tier(tier: str, mapping: dict[str, Any]) -> int:
    """Queue priority for a tier — higher is fetched first.

    Kept modest on purpose. Tier decides *order*, never whether something is
    crawled at all: a corpus evidenced only by government sources is its own
    bias, which is what §7.4's counter-seeding exists to correct.
    """
    return int((mapping.get("priority_by_tier") or {}).get(tier, 0))


def priority_for_domain(domain: str, mapping: dict[str, Any]) -> int:
    """Convenience: resolve the tier and return its priority in one step."""
    return priority_for_tier(resolve_tier(domain, mapping), mapping)


def jittered_delay_ms(base_ms: int, jitter_ms: int = 0, rng: random.Random | None = None) -> int:
    """A floor plus a random draw from ``[0, jitter_ms]``.

    A fixed interval is both a recognisable fingerprint and a way to synchronise
    bursts across domains once several workers settle into step. The floor is
    still honoured — jitter only ever adds (§6.4).
    """
    if base_ms < 0:
        raise ValueError("base_ms must not be negative")
    if jitter_ms <= 0:
        return base_ms
    return base_ms + (rng or random).randint(0, jitter_ms)
