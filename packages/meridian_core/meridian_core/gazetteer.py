"""Turning gazetteer rows into matcher patterns, and text into gazetteer rows (§5.6).

Two pure halves of task `P5-02`, free of spaCy and the database:
:func:`compile_patterns` turns approved rows into ``EntityRuler`` patterns, which
override statistical NER; :func:`find_acronyms` harvests ``Full Name (ACRONYM)``
definitions, mostly a filter against bracketed noise. See
docs/features/places-and-terms.md#ruler and #harvest.
"""

from __future__ import annotations

import bisect
import dataclasses
import re

#: Words an expansion may contain without contributing a letter to the acronym.
#: "Housing & Development Board (HDB)" and "Department for Transport (DfT)" are
#: both real, and both fail a strict one-word-per-letter rule.
SKIPPABLE = frozenset(
    {"a", "an", "and", "at", "by", "de", "der", "for", "in", "of", "on", "or", "the", "to", "und"}
)

#: A surface form this short and this upper-case is treated as an acronym and
#: matched case-sensitively. Above it, case stops carrying information: nobody
#: writes a sentence that accidentally spells out "Land Transport Authority".
SHORT_FORM_MAX_CHARS = 8

#: An acronym shorter than this is not evidence of anything — "(A)" and "(ii)"
#: are list markers. Longer than this and it is a word in brackets.
MIN_ACRONYM_LETTERS = 2
MAX_ACRONYM_LETTERS = 8

#: How far back an expansion may start. Schwartz & Hearst use ``len + 5``; this
#: is tighter because a long window mostly buys false positives, and a missed
#: definition costs nothing — the next document that defines the term catches it.
_WINDOW = 2

#: Sentence-ish boundaries an expansion may not cross. A single newline is not one
#: (PDF text breaks lines mid-sentence); a blank line is.
_BOUNDARY = re.compile(r"[.!?;:•]|\n[ \t]*\n|\s[-–—]\s")

#: The bracketed candidate. Deliberately permissive — the initialism check below
#: is what decides, and a regex that tries to do both ends up rejecting
#: "Housing & Development Board (HDB)" to keep out "(PDF)".
_CANDIDATE = re.compile(r"\(([^()]{1,20})\)")

#: "Urban Redevelopment Authority's (URA)" defines the Authority, not a thing
#: called "Authority's". The genitive survives tokenisation because it has to —
#: dropping it there would split the word for the matcher too.
_POSSESSIVE = re.compile(r"['’]s$")

#: Tokenisation, close enough to spaCy's to build token patterns with. Letters
#: and digits clump; everything else is its own token, which is how spaCy splits
#: "&" out of "Housing & Development Board".
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?|[^\sA-Za-z0-9]")


def tokenise(text: str) -> list[str]:
    """Split the way the matcher will see it."""
    return _TOKEN.findall(text)


# ---------------------------------------------------------------------------
# The load: rows → EntityRuler patterns
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Withheld:
    """One surface form that will *not* be force-labelled, and why.

    Returned rather than logged, so an approved term that never matches is visible.
    """

    surface: str
    reason: str  # ambiguous | collision
    term_ids: tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class CompiledGazetteer:
    patterns: tuple[dict, ...]
    withheld: tuple[Withheld, ...]

    def __len__(self) -> int:
        return len(self.patterns)


def label_for(entity_type: str) -> str:
    """The ``EntityRuler`` label for one of §5.6's five types.

    Upper-cased and not spaCy's scheme, so a curated match is never mistaken for a
    model's guess.
    """
    return entity_type.upper()


def _token_pattern(surface: str) -> tuple[list[dict], bool]:
    """Token pattern plus whether it is case-sensitive.

    Short all-caps forms match case-sensitively, or they fire on ordinary words. See
    docs/features/places-and-terms.md#ruler.
    """
    tokens = tokenise(surface)
    cased = (
        len(surface) <= SHORT_FORM_MAX_CHARS and surface.upper() == surface and " " not in surface
    )
    if cased:
        return [{"TEXT": token} for token in tokens], True
    return [{"LOWER": token.lower()} for token in tokens], False


def _key(surface: str) -> tuple[bool, str]:
    """How two surface forms are compared for collision.

    Under the case rule above, "ODD" and "odd" are different keys: one is matched
    case-sensitively and cannot be reached by the other.
    """
    _, cased = _token_pattern(surface)
    return (cased, surface if cased else surface.lower())


def surfaces_of(term) -> list[str]:
    """Canonical first, then aliases, deduplicated and order-preserving."""
    out: list[str] = []
    for surface in (term.canonical, *(term.aliases or ())):
        cleaned = (surface or "").strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def compile_patterns(terms) -> CompiledGazetteer:
    """Approved, unambiguous rows → ``EntityRuler`` patterns.

    Unapproved rows do not load. An ambiguous row keeps its canonical form and loses its
    aliases. Surface forms shared by two rows are withheld even when nothing is flagged.
    See docs/features/places-and-terms.md#ruler.
    """
    by_key: dict[tuple[bool, str], list] = {}
    flagged: dict[tuple[bool, str], list] = {}

    for term in terms:
        if not term.approved:
            continue
        for position, surface in enumerate(surfaces_of(term)):
            # Position 0 is the canonical — the form written to identify the
            # term, as opposed to the short forms that are why the row was
            # flagged in the first place.
            bucket = flagged if (term.ambiguous and position > 0) else by_key
            bucket.setdefault(_key(surface), []).append((surface, term))

    withheld: list[Withheld] = []
    patterns: list[dict] = []

    for key, entries in flagged.items():
        withheld.append(
            Withheld(entries[0][0], "ambiguous", tuple(term.term_id for _, term in entries))
        )
        # A flagged row still shadows an unflagged one with the same surface: the
        # collision is real whichever row carries the flag.
        by_key.pop(key, None)

    for entries in by_key.values():
        distinct = {term.term_id for _, term in entries}
        if len(distinct) > 1:
            withheld.append(Withheld(entries[0][0], "collision", tuple(sorted(distinct))))
            continue
        surface, term = entries[0]
        tokens, _ = _token_pattern(surface)
        patterns.append(
            {
                "label": label_for(term.entity_type),
                "pattern": tokens,
                # Surfaces as ``ent.ent_id_``, so an alias arrives carrying its row (§5.5).
                "id": str(term.term_id),
            }
        )

    return CompiledGazetteer(tuple(patterns), tuple(withheld))


@dataclasses.dataclass(frozen=True)
class TermVerdict:
    """What the matcher will do with one row (task P6-13).

    Derived from the whole approved set, never from the row: a surface form two
    rows share is withheld whichever row you are looking at, so "will this
    load" is not a property a single row carries.
    """

    will_load: bool
    #: `unapproved`, `rejected`, `ambiguous`, `collision`, `no_patterns` — or None when
    #: nothing was held back. Present even when ``will_load`` is True, for a withheld alias.
    reason: str | None = None
    collides_with: tuple[int, ...] = ()


def loading_report(terms) -> dict[int, TermVerdict]:
    """Per-row verdicts for an approval screen, so an approved term that will not load is seen."""
    compiled = compile_patterns(terms)
    loaded = {int(pattern["id"]) for pattern in compiled.patterns}

    held: dict[int, tuple[str, tuple[int, ...]]] = {}
    for withheld in compiled.withheld:
        for term_id in withheld.term_ids:
            if term_id not in held:
                others = tuple(other for other in withheld.term_ids if other != term_id)
                held[term_id] = (withheld.reason, others)

    report: dict[int, TermVerdict] = {}
    for term in terms:
        term_id = term.term_id
        if not term.approved:
            rejected = getattr(term, "rejected_at", None) is not None
            report[term_id] = TermVerdict(False, "rejected" if rejected else "unapproved")
            continue
        reason, others = held.get(term_id, (None, ()))
        if term_id not in loaded and reason is None:
            # Approved, nothing withheld it, and it still produced no pattern:
            # the row has no usable surface form at all. Rare, and worth naming
            # rather than reporting as a silent False.
            reason = "no_patterns"
        report[term_id] = TermVerdict(term_id in loaded, reason, others)
    return report


# ---------------------------------------------------------------------------
# The growth: text → acronym definitions
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AcronymDefinition:
    acronym: str
    expansion: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.expansion} ({self.acronym})"


def _looks_like_an_acronym(candidate: str) -> str | None:
    """The bracketed text, if it could be an acronym. Its letters, if so."""
    candidate = candidate.strip()
    if not candidate or " " in candidate:
        return None
    letters = [c for c in candidate if c.isalpha()]
    if not (MIN_ACRONYM_LETTERS <= len(letters) <= MAX_ACRONYM_LETTERS):
        return None
    # At least two capitals, and never all-lowercase: "(ii)", "(cont)" and
    # "(2026)" all reach here otherwise.
    if sum(1 for c in letters if c.isupper()) < 2:
        return None
    if not candidate[0].isupper():
        return None
    return "".join(letters).lower()


def _skippable(token: str) -> bool:
    return token.lower() in SKIPPABLE or not token.isalnum() or len(token) == 1


def _expansion_start(words: list[str], letters: str) -> int | None:
    """Where in ``words`` an expansion of ``letters`` begins, or None.

    Right to left, one word per letter, connectives skippable; the first word must
    carry the first letter and the last word the last.
    """
    letter = len(letters) - 1
    word = len(words) - 1
    while letter >= 0 and word >= 0:
        if words[word][:1].lower() == letters[letter]:
            letter -= 1
            word -= 1
        elif letter < len(letters) - 1 and _skippable(words[word]):
            word -= 1
        else:
            return None
    return word + 1 if letter < 0 else None


#: Tokens that end a name rather than sit inside one.
_BREAKS = frozenset({";", ":", '"', "“", "”", "(", ")", "[", "]"})

#: Rejoins tokens as the document wrote them ("multi-agent"); display only, since
#: `tokenise` splits either spelling the same way.
_GLUED = re.compile(r"\s*([-/‐–’'])\s*")
_BEFORE_COMMA = re.compile(r"\s+,")


def join_tokens(tokens: list[str]) -> str:
    """Tokens back into the text a document wrote.

    Hyphens and slashes glued, no space before a comma.
    """
    return _BEFORE_COMMA.sub(",", _GLUED.sub(r"\1", " ".join(tokens)))


#: How much of a clause is tokenised to find its last words (`B-85`).
_LOOKBACK = 2000


def boundary_ends(text: str) -> list[int]:
    """Where each clause boundary in ``text`` ends, in order.

    Found once per document (`B-85`); same decisions as rescanning each prefix.
    """
    return [hit.end() for hit in _BOUNDARY.finditer(text)]


def clause_words(text: str, end: int, needed: int, ends: list[int] | None = None) -> list[str]:
    """The last ``needed`` tokens of the clause that ends at ``end``.

    Only the last stretch of a long clause is tokenised: tokens are contiguous,
    so a window cut through a word changes only its first token, and when the
    window holds more than ``needed`` the last ones are exact. Otherwise the
    whole clause is read.
    """
    if ends is None:
        ends = boundary_ends(text[:end])
    i = bisect.bisect_right(ends, end)
    boundary = ends[i - 1] if i else 0
    start = max(boundary, end - _LOOKBACK)
    tokens = tokenise(text[start:end])
    if start > boundary and len(tokens) <= needed:
        tokens = tokenise(text[boundary:end])
    return tokens[-needed:]


def find_acronyms(text: str) -> list[AcronymDefinition]:
    """Every ``Full Name Here (ACRONYM)`` this text defines.

    Deduplicated, first definition wins, order preserved. The reverse form is not read.
    """
    found: dict[tuple[str, str], AcronymDefinition] = {}
    ends = boundary_ends(text)

    for match in _CANDIDATE.finditer(text):
        letters = _looks_like_an_acronym(match.group(1))
        if letters is None:
            continue

        # Only the current clause. Text before a full stop is a different
        # sentence, and an expansion stitched across one appeared nowhere.
        words = clause_words(text, match.start(), (len(letters) + _WINDOW) * 2, ends)
        if not words:
            continue

        start = _expansion_start(words, letters)
        if start is None:
            continue

        span = words[start:]
        # A colon, a semicolon or a bracket inside is a clause, not a name.
        # Commas are not: "Agency for Science, Technology and Research" is one.
        if any(token in _BREAKS for token in span):
            continue
        expansion = _POSSESSIVE.sub("", join_tokens(span))
        # An acronym whose expansion is one word is the word itself abbreviated,
        # which the table has no use for, and a runaway span is a parse failure.
        if len(words) - start < 2 or len(expansion) > 90:
            continue

        acronym = match.group(1).strip()
        key = (acronym, expansion.lower())
        found.setdefault(key, AcronymDefinition(acronym, expansion))

    return list(found.values())


# ---------------------------------------------------------------------------
# What kind of thing a harvested name is
# ---------------------------------------------------------------------------

#: A name's head word, by the type it signals. Only heads that say one thing; mixed
#: ones ("Service", "System", "Network", "Law") stay concept.
_AGENCY_HEADS = """
    academy agency alliance administration assembly association authority board bureau
    centre center coalition college commission committee company conference consortium
    corporation council court department directorate federation forum foundation
    inspectorate institute institution laboratory laboratories league ministry office
    organisation organization parliament partnership secretariat society tribunal union
    university
"""
_SCHEME_HEADS = """
    act allowance bill code directive fund grant guideline guidelines initiative
    licence license ordinance pass permit pilot plan policy programme program project
    rebate regulation regulations scheme standard strategy subsidy trial
"""
_INFRASTRUCTURE_HEADS = """
    airport bridge corridor depot expressway highway hospital interchange lane line
    motorway port railway road station terminal tunnel
"""
_METRIC_HEADS = "coefficient index indicator percentage ratio rate score"

HEAD_TYPES: dict[str, str] = {
    **dict.fromkeys(_AGENCY_HEADS.split(), "agency"),
    **dict.fromkeys(_SCHEME_HEADS.split(), "scheme"),
    **dict.fromkeys(_INFRASTRUCTURE_HEADS.split(), "infrastructure"),
    **dict.fromkeys(_METRIC_HEADS.split(), "metric"),
}

_PREPOSITIONS = frozenset({"of", "on", "for", "in", "to", "at", "de", "der", "du"})


#: Types that name one particular thing, so need a capitalised head: "base
#: station" is a kind of thing, "Changi Station" is one. A metric is named in
#: lowercase as often as not ("true positive rate").
_PROPER_TYPES = frozenset({"agency", "scheme", "infrastructure"})


def _head(name: str) -> str | None:
    words = [w for w in re.split(r"[\s\-/]+", name.strip()) if w]
    for index, word in enumerate(words):
        if word.lower() in _PREPOSITIONS and index > 0:
            words = words[:index]
            break
    return words[-1] if words else None


def head_word(name: str) -> str | None:
    """The word a name is about: last before the first preposition, else last."""
    head = _head(name)
    return head.lower() if head else None


def infer_entity_type(name: str, *, default: str = "concept") -> str:
    """The type a harvested name's head word signals, or ``default``.

    Deliberately narrow. A term wrongly filed as an agency reads as a fact
    somebody established, so a head that could mean two things files nothing.
    """
    head = _head(name)
    if not head:
        return default
    kind = HEAD_TYPES.get(head.lower())
    if kind is None or (kind in _PROPER_TYPES and not head[:1].isupper()):
        return default
    return kind
