"""Deciding whether two mentions are the same thing (task `P4-02`, §5.5).

Without this the graph fragments: §5.5's own example is "LTA", "Land Transport
Authority", "the Authority" and "LTA Singapore" becoming four nodes. It breaks
downstream *silently* — coverage undercounts, the attribute discrimination test
misfires, and cross-topic edges never form because the shared entity was split.

**Resolve at write time, not as periodic cleanup.** A duplicate that reaches the
graph propagates into edges before anybody notices, and then the cleanup has to
reason about edges too.

The pipeline §5.5 specifies, in four steps, each a separate function so each is
testable on its own: normalise, block, score, decide.

**This module decides and does not act.** `merge` lives in `P4-03` with the
reversibility it needs. Splitting them is deliberate: §16 calls bad merges
"harder to detect than duplicates", so the thing that *scores* and the thing
that *writes* should be separately arguable, and a change to the threshold
should not be a change to a function that rewrites rows.

**Never across node types.** §5.5's first "cheap win", enforced in `block` so
it cannot be forgotten by a caller — an organisation and a place that share a
name are two things, always, and no score should be able to overturn that.
"""

from __future__ import annotations

import dataclasses
import difflib
import re
import unicodedata
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Entity, GazetteerTerm

log = get_logger(__name__)

#: Above this, merge without asking. Set where a false merge is very unlikely
#: rather than where merges become common: §16 is explicit that conflation is
#: invisible once done, so the auto band has to be the boring one.
AUTO_MERGE = 0.90

#: Below this, they are different entities and nothing is queued. A low
#: threshold here would fill the adjudication queue with obvious non-matches
#: and train whoever reads it to approve without looking.
SEPARATE_BELOW = 0.55

#: How the three signals are weighted. Context is the heaviest because §5.5
#: says so and gives the reason: "Cambridge" the city and "Cambridge" the
#: university sit in entirely different neighbourhoods, and no amount of string
#: or vector similarity separates them.
WEIGHTS = {"string": 0.3, "embedding": 0.3, "context": 0.4}

#: Candidates `block` will consider. Not a limit on correctness — a blocking
#: stage exists to keep scoring off the whole table, and an entity with more
#: than this many plausible near-names is a signal in itself.
MAX_CANDIDATES = 25

__all__ = [
    "AUTO_MERGE",
    "MAX_CANDIDATES",
    "SEPARATE_BELOW",
    "WEIGHTS",
    "Candidate",
    "Verdict",
    "block",
    "decide",
    "expansions_from_gazetteer",
    "normalise",
    "score",
]

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")

#: Words that carry no identity. "The Authority" and "Authority" are the same
#: mention; so are "LTA" and "LTA Ltd". Kept short deliberately — a long list
#: starts removing words that *do* distinguish, and "National" is the example
#: that always gets added and always should not be.
_NOISE = frozenset({"the", "a", "an", "of", "and", "ltd", "limited", "inc", "plc", "pte"})


def normalise(name: str, *, expansions: dict[str, str] | None = None) -> str:
    """Step 1. Lowercase, strip punctuation, expand known abbreviations.

    Unicode is normalised to NFKD and stripped of combining marks, so "Zürich"
    and "Zurich" are the same mention. A crawl reaches both spellings of the
    same place routinely, and a resolver that treated them as different
    entities would fragment on exactly the material it is most likely to see.

    `expansions` comes from the gazetteer (§5.6) — which already holds the
    alias lists resolution needs, so the two share one table rather than
    duplicating. Expansion happens per token, so "LTA Singapore" becomes "land
    transport authority singapore" without needing that whole phrase to have
    been curated.
    """
    folded = unicodedata.normalize("NFKD", name.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _SPACE.sub(" ", _PUNCT.sub(" ", folded)).strip()

    expansions = expansions or {}
    tokens = [expansions.get(token, token) for token in folded.split()]
    # Re-split: an expansion is usually several words.
    tokens = " ".join(tokens).split()
    kept = [token for token in tokens if token not in _NOISE]

    # If a name is *entirely* noise, keep it rather than returning nothing —
    # an empty normalisation matches every other empty one, which is the worst
    # possible outcome for a resolver.
    return " ".join(kept or tokens)


async def expansions_from_gazetteer(sess: AsyncSession) -> dict[str, str]:
    """Abbreviation → expansion, from the approved gazetteer terms (§5.6).

    Only approved rows. A proposed term is a suggestion nobody has checked, and
    letting it rewrite names during resolution would let an unreviewed
    suggestion silently merge two entities.
    """
    rows = (
        (await sess.execute(select(GazetteerTerm).where(GazetteerTerm.approved.is_(True))))
        .scalars()
        .all()
    )

    table: dict[str, str] = {}
    for row in rows:
        canonical = (row.canonical or "").strip().casefold()
        if not canonical:
            continue
        for alias in row.aliases or ():
            key = (alias or "").strip().casefold()
            # Only single-token aliases: this expands token by token, and a
            # multi-word alias would never match a single token anyway.
            if key and key != canonical and " " not in key:
                table[key] = canonical
    return table


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One entity that might be the same thing, and why it was considered."""

    entity: Entity
    #: Which blocking rule surfaced it, for a log line that explains a merge.
    via: str


@dataclasses.dataclass(frozen=True)
class Verdict:
    """A score and what to do about it."""

    entity_id: int
    score: float
    band: str  # merge | adjudicate | separate
    #: The three signals, kept separately. A single number cannot be argued
    #: with; "string 0.9, context 0.1" is a merge somebody should look at.
    signals: dict[str, float]

    @property
    def merges(self) -> bool:
        return self.band == "merge"


def _tokens(normalised: str) -> frozenset[str]:
    return frozenset(normalised.split())


def string_similarity(left: str, right: str) -> float:
    """Token-set overlap, with a character-level tiebreak.

    §5.5 says "token-set / Jaro-Winkler" and this takes the token-set half,
    plus `difflib` for the character level. **Deliberately not a new
    dependency**: the alternative was `rapidfuzz`, and the argument against is
    not size — it is that these two are a set intersection and a stdlib call,
    both of which are hard to get subtly wrong, and a subtle bug here is a
    silent bad merge, which §16 says is the failure that cannot be spotted
    afterwards.

    Token-set leads because entity names differ by word rather than by
    character: "Land Transport Authority" against "Authority, Land Transport"
    is a perfect token match and a poor character one.
    """
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0

    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    # Weighted toward tokens, but a pure-token score of 0 on "walkability" vs
    # "walkable" would throw away a real signal.
    return 0.75 * jaccard + 0.25 * ratio


def embedding_similarity(
    left: Sequence[float] | None, right: Sequence[float] | None
) -> float | None:
    """Cosine similarity, or None when either side has no embedding.

    None rather than 0.0, and the difference matters: an un-embedded entity is
    unknown on this axis, and scoring it as maximally dissimilar would make
    every entity written before the backfill unmergeable.
    """
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    # Vectors are stored normalised (`embeddings.py`), so the norms are 1 and
    # the dot product is the cosine. Clamped because floating point puts it
    # fractionally outside [-1, 1] often enough to matter.
    return max(0.0, min(1.0, dot))


def context_overlap(left: Sequence[int] | None, right: Sequence[int] | None) -> float | None:
    """How much two entities' evidence overlaps, or None when either has none.

    §5.5 calls context the strongest signal and gives the reason: "Cambridge"
    the city and "Cambridge" the university sit in entirely different
    neighbourhoods. Before there are edges, the neighbourhood is the set of
    chunks each entity was drawn from — two mentions supported by the same
    passages are the same thing far more often than two that merely spell alike.

    Jaccard rather than raw count: an entity cited by two hundred chunks would
    otherwise overlap with everything.
    """
    if not left or not right:
        return None
    a, b = set(left), set(right)
    return len(a & b) / len(a | b)


def score(left: Entity, right: Entity, *, expansions: dict[str, str] | None = None) -> Verdict:
    """Step 3. Combine the three signals into one number and a band.

    **Absent signals are dropped and the weights renormalised**, rather than
    counted as zero. An entity with no embedding and no supporting chunks would
    otherwise score at most 0.3 however exactly its name matched, and a corpus
    that has not finished embedding would be unable to resolve anything.
    """
    exp = expansions or {}
    signals: dict[str, float] = {
        "string": string_similarity(
            normalise(left.canonical_name, expansions=exp),
            normalise(right.canonical_name, expansions=exp),
        )
    }

    embedded = embedding_similarity(left.embedding, right.embedding)
    if embedded is not None:
        signals["embedding"] = embedded

    shared = context_overlap(left.supporting_chunk_ids, right.supporting_chunk_ids)
    if shared is not None:
        signals["context"] = shared

    weight = sum(WEIGHTS[name] for name in signals)
    combined = sum(signals[name] * WEIGHTS[name] for name in signals) / weight

    return Verdict(
        entity_id=right.entity_id,
        score=combined,
        band=decide(combined),
        signals=signals,
    )


def decide(combined: float) -> str:
    """Step 4. The three bands.

    §5.5: "high → auto-merge; low → separate entity; middle band → queue for
    model adjudication". The middle band is meant to be small, cheap, and the
    only place a model adds value — so the two thresholds are set to make it
    narrow, not to make merging easy.
    """
    if combined >= AUTO_MERGE:
        return "merge"
    if combined < SEPARATE_BELOW:
        return "separate"
    return "adjudicate"


async def block(
    sess: AsyncSession,
    name: str,
    node_type: str,
    *,
    embedding: Sequence[float] | None = None,
    expansions: dict[str, str] | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[Candidate]:
    """Step 2. Everything worth scoring against, and nothing else.

    **Same node type, always** (§5.5's first cheap win). Enforced here rather
    than left to the caller, because a resolver that can merge an organisation
    into a place has no threshold safe enough to compensate.

    Two rules, unioned. Name and alias overlap catches the spellings; embedding
    kNN catches the ones that share no words — "the Authority" against "Land
    Transport Authority" survives normalisation with one token in common and
    would be missed by string matching alone.

    Entities that already redirect are excluded: they are not destinations, and
    merging into one would build a chain somebody has to follow.
    """
    normalised = normalise(name, expansions=expansions)
    tokens = [token for token in normalised.split() if len(token) > 2]

    base = select(Entity).where(
        Entity.node_type == node_type,
        Entity.redirects_to.is_(None),
    )

    found: dict[int, str] = {}
    rows: list[Entity] = []

    if tokens:
        # Any shared token is enough to be *considered* — blocking is meant to
        # be generous and cheap, and the scoring step is what is strict.
        like = [Entity.canonical_name.ilike(f"%{token}%") for token in tokens]
        by_name = (await sess.execute(base.where(or_(*like)).limit(limit))).scalars().all()
        for row in by_name:
            found[row.entity_id] = "name"
            rows.append(row)

    if embedding is not None:
        nearest = (
            (
                await sess.execute(
                    base.where(Entity.embedding.is_not(None))
                    .order_by(Entity.embedding.cosine_distance(list(embedding)))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in nearest:
            if row.entity_id not in found:
                found[row.entity_id] = "embedding"
                rows.append(row)

    log.info(
        "resolution candidates",
        # `entity_name`, not `name`: `logging` refuses to let an `extra` key
        # shadow a `LogRecord` attribute and *raises* rather than dropping it,
        # so this line would take down every caller — but only once logging is
        # configured, which is why it passed in isolation and failed in the
        # suite. The handover lists the whole set.
        extra={"entity_name": name, "node_type": node_type, "candidates": len(rows)},
    )
    return [Candidate(entity=row, via=found[row.entity_id]) for row in rows[:limit]]
