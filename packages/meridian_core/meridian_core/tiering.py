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
    can both match, and the specific one has to win or every domain under the
    broader suffix collapses into one tier.
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


def is_tier_mapped(domain: str, mapping: dict[str, Any]) -> bool:
    """Whether the curated map actually names this domain (task `P4-14`).

    Distinct from `resolve_tier` returning something, because that always
    returns something — an unmapped domain gets `default_tier`. The difference
    matters to screening: being in the map is somebody's curation and clears a
    domain on sight, whereas falling through to the default is precisely the
    "unknown domain" case that has to earn its clearing.

    The same matching rules as `resolve_tier`, deliberately duplicated in shape
    rather than shared through a flag: a predicate that also returned a tier, or
    a tier function that also reported how it decided, would be used for the
    wrong one of the two by somebody in a hurry.
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
    """Convenience: resolve the tier and return its priority in one step."""
    return priority_for_tier(resolve_tier(domain, mapping), mapping)


#: How much a short half-life lifts a seed's place in the queue (`P2-20`).
#:
#: Small, because tier priority already carries most of the ordering and this
#: is a second opinion on it rather than a replacement. Large enough that a
#: news page outranks a paper of the same tier, which is the whole point.
URGENCY_WEIGHT = 8


def urgency_for_tier(tier: str, half_lives: dict[str, int | None]) -> int:
    """How much sooner this kind of document should be fetched (`P2-20`, §9).

    The second half of age-aware ranking, and it reads the *same* table the
    ranking does — `P2-20` is explicit that `P7-06` should do likewise rather
    than inventing a second set of half-lives, and the way two sets diverge is
    that nobody notices they exist.

    **Two separate reasons a fast-rotting source is worth fetching sooner**,
    and they point the same way. Its claim stops being current, so the value of
    having it decays; and the page itself is likelier to be gone — news sites
    reorganise, press releases move, and a paper is still there in five years.
    Neither reason applies to `peer_reviewed`, which is why it gets nothing.

    Returns an addition to the tier's priority, never a replacement. Tier still
    decides the broad order (§5.2); this separates documents *within* a tier
    that age at different speeds, and cannot promote an informal page above a
    government one on urgency alone.
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

    The one callers should use when queueing a fetch. Kept beside
    `priority_for_domain` rather than replacing it, because the plain version
    is what a test or an admin screen wants when asking "what does the tier map
    say" without the ageing opinion mixed in.
    """
    tier = resolve_tier(domain, mapping)
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
