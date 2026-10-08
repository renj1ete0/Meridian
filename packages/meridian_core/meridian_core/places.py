"""Which places a source is about (task P2-23, spec §7.2, §5.5, §5.6).

Four mechanical signals: names in the text, place entities cited from its chunks, the
publisher's domain (which lowers the bar for its own country and never overrides the
text), and the document's language (confirmation only). What decided is recorded in
``sources.place_evidence``. NULL is unread, ``{}`` read and about no place. Tags go
stale by a basis fingerprint, as topic labels do. See docs/features/places-and-terms.md#places.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk, Edge, Entity, GazetteerTerm, Source
from .placenames import (
    AMBIGUOUS,
    CITIES,
    COUNTRIES,
    GENERIC_CCTLDS,
    LANGUAGE_COUNTRIES,
    NOT_PLACES,
    SUBDIVISIONS,
    TLD_COUNTRY,
    country_of,
    display_name,
    is_city,
)
from .tiering import registrable_domain

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------
# Calibrated on a real corpus; see docs/features/places-and-terms.md#place-calibration.

#: The fewest mentions that tag a place from the text alone. Three let a
#: short biography through on one line naming a country and a city in it.
MIN_MENTIONS = 4

#: And at least one mention per this many characters of text, so a long
#: document needs proportionally more: a report of a hundred pages that names
#: a country four times has mentioned it, not studied it.
CHARS_PER_MENTION = 10_000

#: How often a place must be named relative to the most-named country. A
#: comparison of several places names each of them often; a document about
#: one place that mentions another in passing names the second far less.
SHARE_OF_BEST = 0.25

#: Where a reference list starts: a heading line naming one. Text after the first
#: such heading past :data:`REFERENCES_FROM` is left out; it names publishers' cities.
REFERENCES_HEADING = re.compile(
    r"(?im)^[#*\s\d.]*(?:references|bibliography|works cited|literature cited|reference list)"
    r"[\s:*]*$"
)

#: A reference heading counts only past this fraction of the text; earlier, it
#: is a table of contents or a section titled that way.
REFERENCES_FROM = 0.4

#: A line that reads as one entry of a reference list, wherever it sits: a year,
#: and one of the words citations are made of.
CITATION_YEAR = re.compile(r"\b(?:19|20)\d\d\b")
CITATION_WORDS = re.compile(
    r"et al\.|\bpp\.|\bdoi\b|doi\.org|\b[Vv]ol\.|Proceedings|Journal|Conference|Symposium"
    r"|\bPress\b|Publish|Retrieved from|ISBN|ISSN|arXiv|Workshop|Transactions"
)
CITATION_MAX_LINE = 600

#: A place named in the title counts this many times. A title is what a
#: document says it is about, and it is one line.
TITLE_WEIGHT = 3

#: The publisher's own country needs only this many mentions, by how strongly the
#: domain says where the publisher is: once for a government, twice otherwise.
DOMAIN_MIN_MENTIONS = {"government": 1, "country_code": 2}

#: Gazetteer surface forms shorter than this are left out — mostly acronyms,
#: and an acronym is one country's name for a thing other countries also
#: have. The same reasoning and number as `topiclabels.MIN_ALIAS_LENGTH`.
MIN_TERM_LENGTH = 5

#: Bumped when the method changes in a way the constants above do not capture.
PLACES_VERSION = 1

#: The signals, as recorded in ``place_evidence['decided']``.
SIGNALS = ("text", "gazetteer", "entity", "domain", "language")

#: A tag that only a document's own title can supply is still a text tag.
TEXT, GAZETTEER, ENTITY, DOMAIN, LANGUAGE = SIGNALS

#: The domain's strength: a national government, or only a country code.
GOVERNMENT, COUNTRY_CODE = "government", "country_code"


def valid_code(code: str | None) -> str | None:
    """A code as stored, or None when it is not one this scheme knows."""
    if not code:
        return None
    code = code.strip().upper()
    if code == "UK":  # the one alpha-2 people write that ISO does not use
        code = "GB"
    if code in COUNTRIES or code in CITIES:
        return code
    return None


# ---------------------------------------------------------------------------
# The vocabulary and the matcher
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GazetteerPlace:
    """One gazetteer surface form that points at a country."""

    surface: str
    country: str


@dataclasses.dataclass(frozen=True)
class Vocabulary:
    """Every surface form the matcher knows, and what it counts toward.

    ``codes`` maps a surface form to the code it names, or to None for a
    phrase in :data:`NOT_PLACES` — consumed so the name inside it is not
    counted. ``gazetteer`` maps a gazetteer surface form to its country.
    """

    codes: Mapping[str, str | None]
    gazetteer: Mapping[str, str]
    pattern: re.Pattern[str]

    @classmethod
    def build(cls, gazetteer: Iterable[GazetteerPlace] = ()) -> Vocabulary:
        codes: dict[str, str | None] = {}
        for table in (COUNTRIES, CITIES):
            for code, names in table.items():
                for name in names:
                    if name in AMBIGUOUS:
                        continue
                    codes[name] = code
                    codes.setdefault(name.upper(), code)
        for country, names in SUBDIVISIONS.items():
            for name in names:
                if name not in AMBIGUOUS:
                    codes[name] = country
                    codes.setdefault(name.upper(), country)
        for phrase in NOT_PLACES:
            codes[phrase] = None
            codes[phrase.upper()] = None

        gaz: dict[str, str] = {}
        for term in gazetteer:
            # A place name is counted as a place name; a gazetteer term that
            # *is* one would count it twice.
            if term.surface in codes:
                continue
            gaz[term.surface] = term.country

        surfaces = sorted({*codes, *gaz}, key=lambda s: (-len(s), s))
        # Longest first, so a containing name or a NOT_PLACES phrase wins. Lookarounds
        # rather than \b, because several names end in a full stop.
        alternation = "|".join(re.escape(s) for s in surfaces) or r"(?!x)x"
        pattern = re.compile(rf"(?<![\w])(?:{alternation})(?![\w])")
        return cls(codes=codes, gazetteer=gaz, pattern=pattern)

    def fingerprint_payload(self) -> list[list[str]]:
        return sorted([s, c or ""] for s, c in {**self.codes, **self.gazetteer}.items())


@dataclasses.dataclass
class Mentions:
    """Counts from one document: place names by code, gazetteer terms by country."""

    names: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    gazetteer: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: How much text was read, for the density floor.
    chars: int = 0

    def add(self, other: Mentions, weight: int = 1) -> None:
        for code, n in other.names.items():
            self.names[code] += n * weight
        for code, n in other.gazetteer.items():
            self.gazetteer[code] += n * weight


def is_citation(line: str) -> bool:
    """Whether a line reads as one entry of a reference list."""
    return (
        len(line) <= CITATION_MAX_LINE
        and CITATION_YEAR.search(line) is not None
        and CITATION_WORDS.search(line) is not None
    )


def without_references(text: str) -> str:
    """``text`` without its reference list and without citation lines.

    Everything after the first reference heading late enough to be one, and
    every line elsewhere that reads as a citation.
    """
    cut = None
    for match in REFERENCES_HEADING.finditer(text):
        if match.start() >= REFERENCES_FROM * len(text):
            cut = match.start()
            break
    body = text if cut is None else text[:cut]
    return "\n".join(line for line in body.split("\n") if not is_citation(line))


#: What may sit between a city and its own country for the pair to be one
#: mention: "a city, its country" and "a city (its country)" name one place.
_SAME_PLACE_GAP = re.compile(r"\s*[,(]?\s*")


def count_mentions(text: str, vocab: Vocabulary) -> Mentions:
    """Every place name and jurisdictional gazetteer term in ``text``, counted."""
    out = Mentions(chars=len(text or ""))
    if not text:
        return out
    city_end, city_country = -1, None
    for match in vocab.pattern.finditer(text):
        surface = match.group(0)
        if surface in vocab.codes:
            code = vocab.codes[surface]
            if code is None:
                continue
            if (
                not is_city(code)
                and code == city_country
                and _SAME_PLACE_GAP.fullmatch(text, city_end, match.start())
            ):
                # The country after its own city is the same mention twice.
                city_end, city_country = -1, None
                continue
            out.names[code] += 1
            if is_city(code):
                city_end, city_country = match.end(), country_of(code)
        elif surface in vocab.gazetteer:
            out.gazetteer[vocab.gazetteer[surface]] += 1
    return out


def countries_named(text: str, vocab: Vocabulary) -> frozenset[str]:
    """The countries ``text`` names, by place name or jurisdictional term; cities fold in."""
    found = count_mentions(text, vocab)
    return frozenset(country_of(str(code)).upper() for code in (*found.names, *found.gazetteer))


# ---------------------------------------------------------------------------
# The other signals
# ---------------------------------------------------------------------------


def domain_country(url: str, source_tier: str | None = None) -> tuple[str, str] | None:
    """Where the publisher is, from its domain, and how strongly it says so.

    ``(country, strength)`` or None. Strength is :data:`GOVERNMENT` when the
    source was tiered ``government`` and its suffix names a country, else
    :data:`COUNTRY_CODE`. A generic top-level domain, or a country code sold
    as a generic name, says nothing.
    """
    host = registrable_domain(url)
    labels = [part for part in host.split(".") if part]
    if len(labels) < 2:
        return None
    tld = labels[-1]
    country: str | None
    if tld in TLD_COUNTRY:
        country = TLD_COUNTRY[tld]
    elif len(tld) == 2 and tld not in GENERIC_CCTLDS:
        country = valid_code(tld)
        if country is None or is_city(country):
            return None
    else:
        return None
    strength = GOVERNMENT if source_tier == "government" else COUNTRY_CODE
    return country, strength


def entity_codes(name: str, aliases: Sequence[str] | None, jurisdiction: str | None) -> str | None:
    """The code a ``place`` entity stands for: a city, else a country, else its jurisdiction.

    None when none of those is a place this scheme knows.
    """
    names = [name, *(aliases or ())]
    for table in (CITIES, COUNTRIES):
        for candidate in names:
            if candidate in AMBIGUOUS:
                continue
            for code, known in table.items():
                if candidate in known:
                    return code
    return valid_code(jurisdiction)


def language_countries(language: str | None) -> frozenset[str]:
    if not language:
        return frozenset()
    lang = language.strip().lower()
    return LANGUAGE_COUNTRIES.get(lang) or LANGUAGE_COUNTRIES.get(lang.split("-")[0], frozenset())


# ---------------------------------------------------------------------------
# The decision — pure, so every rule can be tested with constructed evidence
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Decision:
    places: list[str]
    #: code → the signals that tagged it, in :data:`SIGNALS` order.
    decided: dict[str, list[str]]
    #: country → its count: place names, cities rolled up, gazetteer terms.
    country_counts: dict[str, int]


def minimum_mentions(chars: int) -> int:
    """The text-alone minimum for a document of ``chars`` characters."""
    return max(MIN_MENTIONS, -(-chars // CHARS_PER_MENTION))


def _passes(n: int, best: int, minimum: int) -> bool:
    return n >= minimum and n >= SHARE_OF_BEST * best


def decide(
    mentions: Mentions,
    *,
    entities: Mapping[str, Sequence[str]] | None = None,
    domain: tuple[str, str] | None = None,
    language: str | None = None,
) -> Decision:
    """The places a document's evidence earns, most-evidenced first."""
    entities = entities or {}
    country_counts: collections.Counter = collections.Counter()
    from_names: collections.Counter = collections.Counter()
    for code, n in mentions.names.items():
        country_counts[country_of(code)] += n
        from_names[country_of(code)] += n
    for code, n in mentions.gazetteer.items():
        country_counts[code] += n
    best = max(country_counts.values(), default=0)
    floor = minimum_mentions(mentions.chars)

    decided: dict[str, set[str]] = collections.defaultdict(set)

    def signals_for_text(country: str) -> set[str]:
        out = set()
        if from_names[country]:
            out.add(TEXT)
        if mentions.gazetteer[country]:
            out.add(GAZETTEER)
        return out

    for country, n in country_counts.items():
        if _passes(n, best, floor):
            decided[country] |= signals_for_text(country)

    for code in entities:
        decided[code].add(ENTITY)
        if is_city(code):
            decided[country_of(code)].add(ENTITY)

    if domain is not None:
        country, strength = domain
        n = country_counts[country]
        if country in decided:
            decided[country].add(DOMAIN)
        elif _passes(n, best, DOMAIN_MIN_MENTIONS[strength]):
            decided[country] |= signals_for_text(country) | {DOMAIN}
        elif not decided:
            if strength == GOVERNMENT:
                decided[country].add(DOMAIN)
            elif country in language_countries(language):
                decided[country] |= {DOMAIN, LANGUAGE}

    # Cities: named often enough on the same scale as countries, and only in a
    # country the document is about.
    for code, n in mentions.names.items():
        if is_city(code) and country_of(code) in decided and _passes(n, best, floor):
            decided[code].add(TEXT)

    def weight(code: str) -> int:
        return mentions.names[code] if is_city(code) else country_counts[code]

    places = sorted(decided, key=lambda c: (-weight(c), c))
    order = {s: i for i, s in enumerate(SIGNALS)}
    return Decision(
        places=places,
        decided={c: sorted(decided[c], key=order.__getitem__) for c in places},
        country_counts=dict(country_counts),
    )


def evidence_record(
    decision: Decision,
    mentions: Mentions,
    *,
    entities: Mapping[str, Sequence[str]],
    domain: tuple[str, str] | None,
    language: str | None,
) -> dict:
    """What ``place_evidence`` holds: the inputs and which of them decided."""
    return {
        "names": dict(sorted(mentions.names.items())),
        "gazetteer": dict(sorted(mentions.gazetteer.items())),
        "entities": {code: sorted(names) for code, names in sorted(entities.items())},
        "domain": None if domain is None else {"country": domain[0], "strength": domain[1]},
        "language": language,
        "decided": decision.decided,
    }


# ---------------------------------------------------------------------------
# Basis
# ---------------------------------------------------------------------------


def basis_fingerprint(vocab: Vocabulary) -> str:
    """What a tag was computed under, as a short stable hash."""
    payload = {
        "version": PLACES_VERSION,
        "min": MIN_MENTIONS,
        "density": CHARS_PER_MENTION,
        "references": [REFERENCES_HEADING.pattern, REFERENCES_FROM],
        "share": SHARE_OF_BEST,
        "title": TITLE_WEIGHT,
        "domain_min": sorted(DOMAIN_MIN_MENTIONS.items()),
        "term_length": MIN_TERM_LENGTH,
        "vocabulary": vocab.fingerprint_payload(),
        "tld": sorted(TLD_COUNTRY.items()),
        "generic": sorted(GENERIC_CCTLDS),
        "languages": sorted((k, sorted(v)) for k, v in LANGUAGE_COUNTRIES.items()),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"p{PLACES_VERSION}:{digest[:16]}"


async def gazetteer_places(sess: AsyncSession) -> list[GazetteerPlace]:
    """Approved, unrejected, unambiguous gazetteer terms with a jurisdiction.

    An ambiguous term is left out entirely, canonical form too.
    """
    rows = await sess.scalars(
        select(GazetteerTerm).where(
            GazetteerTerm.approved.is_(True),
            GazetteerTerm.rejected_at.is_(None),
            GazetteerTerm.ambiguous.is_(False),
            GazetteerTerm.jurisdiction.is_not(None),
        )
    )
    out: dict[str, GazetteerPlace] = {}
    for term in rows:
        country = valid_code(term.jurisdiction)
        if country is None or is_city(country):
            continue
        for surface in (term.canonical, *(term.aliases or ())):
            surface = (surface or "").strip()
            if len(surface) >= MIN_TERM_LENGTH:
                out.setdefault(surface, GazetteerPlace(surface, country))
    return sorted(out.values(), key=lambda t: t.surface)


async def load_vocabulary(sess: AsyncSession) -> Vocabulary:
    return Vocabulary.build(await gazetteer_places(sess))


# ---------------------------------------------------------------------------
# The comparison set
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Place:
    code: str
    name: str


def pattern_country(pattern: str) -> str | None:
    """The country a domain pattern names by its suffix, or None."""
    labels = [part for part in pattern.lower().lstrip("*.").split(".") if part]
    if not labels:
        return None
    tld = labels[-1]
    if tld in TLD_COUNTRY:
        return TLD_COUNTRY[tld]
    if len(tld) == 2 and tld not in GENERIC_CCTLDS:
        code = valid_code(tld)
        return code if code and not is_city(code) else None
    return None


def comparison_places(tier_map: Mapping, jurisdictions: Iterable[str | None]) -> list[Place]:
    """The places the corpus is set up to compare. Pure.

    The union of the tier map's government suffixes and the gazetteer's
    jurisdictions, sorted by name (§7.2). No corpus scan.
    """
    codes: set[str] = set()
    for pattern in ((tier_map or {}).get("patterns") or {}).get("government") or ():
        code = pattern_country(str(pattern))
        if code:
            codes.add(code)
    for jurisdiction in jurisdictions:
        code = valid_code(jurisdiction)
        if code and not is_city(code):
            codes.add(code)
    return sorted((Place(c, display_name(c)) for c in codes), key=lambda p: (p.name, p.code))


async def comparison_set(sess: AsyncSession) -> list[Place]:
    """:func:`comparison_places` from the database's tier map and gazetteer."""
    from .policy import source_tier_map

    tier_map = await source_tier_map(sess)
    jurisdictions = await sess.scalars(
        select(GazetteerTerm.jurisdiction)
        .where(
            GazetteerTerm.approved.is_(True),
            GazetteerTerm.rejected_at.is_(None),
            GazetteerTerm.jurisdiction.is_not(None),
        )
        .distinct()
    )
    return comparison_places(tier_map, list(jurisdictions))


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def _live(chunk=Chunk):
    return chunk.superseded_at.is_(None)


def _place_node_ids():
    return select(Entity.entity_id).where(
        Entity.node_type == "place", Entity.redirects_to.is_(None)
    )


def cited_since_examined():
    """Source ids cited by a place edge written after they were last examined.

    Uncorrelated, so Postgres computes it once per query rather than once per
    source: the edges table is small next to the sources it cites.
    """
    examined = Source.__table__.alias("examined")
    return (
        select(Chunk.source_id)
        .select_from(Edge)
        .join(Chunk, Chunk.chunk_id == Edge.supporting_chunk_ids.any_())
        .join(examined, examined.c.source_id == Chunk.source_id)
        .where(
            or_(Edge.from_node.in_(_place_node_ids()), Edge.to_node.in_(_place_node_ids())),
            Edge.created_at > examined.c.places_examined_at,
        )
    )


def awaiting_places(fingerprint: str):
    """The predicate for "this source needs its places (re-)examined".

    Has live text, and is unexamined, examined under another basis, rewritten
    since (a live chunk newer than the examination), or newly cited by a place
    edge. Unlike topics, no vector is needed: this reads words.
    """
    has_text = exists().where(Chunk.source_id == Source.source_id, _live())
    rewritten = exists().where(
        Chunk.source_id == Source.source_id,
        _live(),
        Chunk.created_at > Source.places_examined_at,
    )
    return and_(
        has_text,
        or_(
            Source.places_examined_at.is_(None),
            Source.place_basis.is_distinct_from(fingerprint),
            rewritten,
            Source.source_id.in_(cited_since_examined()),
        ),
    )


async def sources_awaiting(
    sess: AsyncSession, fingerprint: str, *, limit: int, after: int = 0
) -> list[int]:
    """The next ``limit`` source ids needing places, past ``after``.

    A cursor, so a report-only pass that writes nothing still moves forward.
    """
    rows = await sess.scalars(
        select(Source.source_id)
        .where(Source.source_id > after, awaiting_places(fingerprint))
        .order_by(Source.source_id)
        .limit(limit)
    )
    return list(rows)


async def source_texts(sess: AsyncSession, source_ids: Sequence[int]) -> dict[int, str]:
    """Each source's live text, in document order.

    Non-duplicate chunks where a source has any, for the reason `topiclabels.source_vectors` gives:
    a chunk the novelty gate marked a duplicate is mostly navigation and footer repeated across a
    site, and a footer naming the publisher's city on every page would otherwise vote on every page.
    """
    if not source_ids:
        return {}
    rows = await sess.execute(
        select(Chunk.source_id, Chunk.text, Chunk.duplicate_of.is_(None))
        .where(Chunk.source_id.in_(list(source_ids)), _live())
        .order_by(Chunk.source_id, Chunk.chunk_index)
    )
    unique: dict[int, list[str]] = collections.defaultdict(list)
    everything: dict[int, list[str]] = collections.defaultdict(list)
    for source_id, text, original in rows:
        everything[source_id].append(text)
        if original:
            unique[source_id].append(text)
    return {sid: "\n".join(unique.get(sid) or parts) for sid, parts in everything.items()}


async def cited_places(
    sess: AsyncSession, source_ids: Sequence[int]
) -> dict[int, dict[str, list[str]]]:
    """Per source, the place codes its chunks are cited for, with the entity names."""
    if not source_ids:
        return {}
    rows = await sess.execute(
        select(Chunk.source_id, Entity.canonical_name, Entity.aliases, Entity.jurisdiction)
        .select_from(Edge)
        .join(Chunk, Chunk.chunk_id == Edge.supporting_chunk_ids.any_())
        .join(
            Entity,
            or_(Entity.entity_id == Edge.from_node, Entity.entity_id == Edge.to_node),
        )
        .where(
            Chunk.source_id.in_(list(source_ids)),
            Entity.node_type == "place",
            Entity.redirects_to.is_(None),
        )
    )
    out: dict[int, dict[str, set[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    for source_id, name, aliases, jurisdiction in rows:
        code = entity_codes(name, aliases, jurisdiction)
        if code is not None:
            out[source_id][code].add(name)
    return {sid: {c: sorted(n) for c, n in codes.items()} for sid, codes in out.items()}


@dataclasses.dataclass(frozen=True)
class Examined:
    source_id: int
    decision: Decision
    evidence: dict


def examine(
    source: Source,
    text: str,
    vocab: Vocabulary,
    entities: Mapping[str, Sequence[str]] | None = None,
) -> Examined:
    """One source's places, from its text, title, entities, domain and language."""
    entities = entities or {}
    mentions = count_mentions(without_references(text), vocab)
    if source.title:
        mentions.add(count_mentions(source.title, vocab), weight=TITLE_WEIGHT)
    domain = domain_country(source.url, source.source_tier)
    decision = decide(mentions, entities=entities, domain=domain, language=source.language)
    evidence = evidence_record(
        decision, mentions, entities=entities, domain=domain, language=source.language
    )
    return Examined(source.source_id, decision, evidence)


async def record_places(
    sess: AsyncSession, examined: Examined, *, fingerprint: str, now: dt.datetime
) -> None:
    """Write one source's places, evidence and basis.

    Replaces, never accumulates: a tag is a claim about the text as it is now.
    """
    await sess.execute(
        update(Source)
        .where(Source.source_id == examined.source_id)
        .values(
            places=examined.decision.places,
            place_evidence=examined.evidence,
            place_basis=fingerprint,
            places_examined_at=now,
        )
    )
