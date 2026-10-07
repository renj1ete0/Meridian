"""Source tiering, queue priority, and fetch delay (spec §5.2, §6.4).

Pure functions over a mapping: tier assignment is mechanical, never a model's judgement.
Tier breaks ties between conflicting sources, sets queue priority and drives
counter-seeding (§7.4). See docs/features/source-quality.md#tiers.
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

    Resolution order is exact match, then the **longest** matching suffix pattern
    (``*.gov.sg`` beats ``*.sg``), then the default.
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


#: The tier a domain whose scholarly tier needs evidence falls to without it.
WITHOUT_EVIDENCE = "institutional"


def _matches(host: str, pattern: str) -> bool:
    pattern = pattern.lower()
    suffix = pattern[1:] if pattern.startswith("*") else "." + pattern
    return host.endswith(suffix) or host == suffix.lstrip(".")


def needs_evidence(domain: str, mapping: dict[str, Any]) -> bool:
    """Whether ``domain``'s scholarly tier is a guess that a document must confirm (`B-50`).

    True for suffixes listed under `needs_scholarly_evidence`; never for a domain named
    exactly. See docs/features/source-quality.md#scholarly-evidence.
    """
    if resolve_tier(domain, mapping) != "peer_reviewed":
        return False
    host = registrable_domain(domain)
    for names in (mapping.get("exact") or {}).values():
        if host in {n.lower() for n in names or []}:
            return False
    return any(_matches(host, p) for p in mapping.get("needs_scholarly_evidence") or [])


def link_tier(domain: str, mapping: dict[str, Any]) -> str:
    """The tier to rank a *link* by, before any document exists to show evidence."""
    return WITHOUT_EVIDENCE if needs_evidence(domain, mapping) else resolve_tier(domain, mapping)


def document_tier(domain: str, mapping: dict[str, Any], *, scholarly: bool) -> str:
    """The tier of a fetched document: the domain's, confirmed by evidence where needed.

    ``scholarly`` is document evidence of scholarship — today, that the page
    names its own DOI. Absent it, an academic institution's page is that
    institution's publication: `institutional`, which is what it is.
    """
    if scholarly:
        return resolve_tier(domain, mapping)
    return link_tier(domain, mapping)


def is_tier_mapped(domain: str, mapping: dict[str, Any]) -> bool:
    """Whether the curated map actually names this domain (task `P4-14`).

    Not the same as `resolve_tier`, which always answers (an unmapped domain gets
    `default_tier`). Same matching rules, kept as a separate function on purpose; see
    docs/features/source-quality.md#trust.
    """
    host = registrable_domain(domain)

    for names in (mapping.get("exact") or {}).values():
        if host in {n.lower() for n in names or []}:
            return True

    for patterns in (mapping.get("patterns") or {}).values():
        for pattern in patterns or []:
            pattern = pattern.lower()
            suffix = pattern[1:] if pattern.startswith("*") else "." + pattern
            if host.endswith(suffix) or host == suffix.lstrip("."):
                return True
    return False


def priority_for_tier(tier: str, mapping: dict[str, Any]) -> int:
    """Queue priority for a tier — higher is fetched first.

    Kept modest on purpose. Tier decides *order*, never whether something is
    crawled at all: a corpus evidenced only by government sources is its own
    bias, which is what §7.4's counter-seeding exists to correct.
    """
    return int((mapping.get("priority_by_tier") or {}).get(tier, 0))


def priority_for_domain(domain: str, mapping: dict[str, Any]) -> int:
    """Convenience: the priority a link to ``domain`` is queued at.

    By `link_tier` (`B-50`): an academic institution's domain is not ranked as
    scholarship before a document shows it is.
    """
    return priority_for_tier(link_tier(domain, mapping), mapping)


#: How much a short half-life lifts a seed's place in the queue (`P2-20`). Small:
#: a second opinion on tier priority. See docs/features/source-quality.md#urgency.
URGENCY_WEIGHT = 8


def urgency_for_tier(tier: str, half_lives: dict[str, int | None]) -> int:
    """How much sooner this kind of document should be fetched (`P2-20`, §9).

    Reads the same half-life table as ranking. Returns an addition to the tier's
    priority, never a replacement, and nothing for `peer_reviewed`. See
    docs/features/source-quality.md#urgency.
    """
    half_life = half_lives.get(tier)
    if half_life is None or half_life <= 0:
        # No decay, or exempt: nothing is gained by hurrying.
        return 0

    # Inverse and bounded. A 180-day half-life earns the full weight; a
    # ten-year one earns almost none. Expressed against a year so the constant
    # means something a person can hold: "how urgent relative to annual rot".
    return max(0, min(URGENCY_WEIGHT, round(URGENCY_WEIGHT * 365 / half_life)))


def priority_with_urgency(
    domain: str, mapping: dict[str, Any], half_lives: dict[str, int | None]
) -> int:
    """Queue priority for a domain, adjusted for how fast its kind rots.

    The one callers should use when queueing a fetch; `priority_for_domain` is the
    tier map's answer alone.
    """
    tier = link_tier(domain, mapping)
    return priority_for_tier(tier, mapping) + urgency_for_tier(tier, half_lives)


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
