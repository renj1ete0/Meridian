"""Turning gazetteer rows into matcher patterns, and text into gazetteer rows (§5.6).

Two pure halves of task `P5-02`, both deliberately free of spaCy and of the
database.

:func:`compile_patterns` is the load: approved rows in, ``EntityRuler`` patterns
out. §5.6 says ruler matches take precedence over statistical NER, which makes
every pattern here an *override* — whatever it matches stops being a question the
model gets to answer. A bad pattern therefore does not degrade extraction, it
silently replaces it, and the corpus has no way to show that it happened.

:func:`find_acronyms` is the growth: §5.6's step 2, "auto-harvest acronym
definitions", which is one regex over text that has already been extracted and
is by far the highest-yield way to fill this table. The regex is the easy part.
Everything below it is the filter, because ``(PDF)``, ``(see Figure 3)`` and
``(USD)`` also match ``Full Name Here (ACRONYM)`` and every one of them admitted
is a permanent piece of noise in the thing entity resolution trusts.
"""

from __future__ import annotations

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

#: Sentence-ish boundaries. An expansion may not cross one: text before a full
#: stop is a different claim, and stitching across it invents a term that
#: appeared nowhere.
#:
#: A *single* newline is not one. Extracted PDF text breaks lines mid-sentence
#: constantly — "the Land Transport\nAuthority (LTA)" is the ordinary shape of a
#: definition in a two-column report — and treating every line break as a
#: sentence end would reject most real definitions in exactly the documents this
#: pattern is high-yield in. A blank line is a paragraph break and does count.
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

    Returned rather than logged and dropped. A term a curator added and that
    never matches anything is the kind of failure nobody reports, because
    nothing breaks: extraction still runs, the graph still fills, and the term
    is simply absent from it.
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

    Upper-cased, and deliberately *not* spaCy's own scheme. If a gazetteer match
    were labelled ``ORG`` it would be indistinguishable downstream from a match
    the statistical model guessed — and these two carry completely different
    confidence. One was written by a person; the other is a model's opinion
    about a capitalised word.
    """
    return entity_type.upper()


def _token_pattern(surface: str) -> tuple[list[dict], bool]:
    """Token pattern plus whether it is case-sensitive.

    The case rule is the single most consequential line in this module. Matching
    a short all-caps form case-insensitively fires on the ordinary English word:
    "ODD" matches *odd*, "ERP" matches nothing but "TOD" matches *tod*, and each
    hit becomes a curated, high-precedence entity in a research corpus. Matching
    a long form case-sensitively is the mirror failure and much cheaper — it
    just misses "the land transport authority" in lower-cased prose.
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

    Three rules, each of which exists because the ruler *overrides* the model.

    **Unapproved rows do not load.** ``approved=false`` is where auto-harvested
    and model-proposed terms wait (§5.6 steps 2–4). Loading them would make the
    approval queue decorative and let a regex's mistake become a curated entity.

    **Rows flagged ambiguous do not load.** The column's own comment is explicit:
    where context is insufficient a mention must be left *unresolved* rather than
    guessed, because a wrong resolution corrupts the graph invisibly and an
    unresolved mention stays visible and fixable. A high-precedence pattern is
    precisely a guess made without context, so the flag has to mean "not here" —
    these belong to §5.5's resolver, which can see the rest of the document.

    **Surface forms that collide are withheld even when nothing is flagged.**
    ``ambiguous`` is hand-maintained and will drift; two rows sharing a surface
    is the same fact, observed rather than declared. Without this the ruler keeps
    whichever pattern it saw first and the choice between two jurisdictions is
    made by row order.
    """
    by_key: dict[tuple[bool, str], list] = {}
    flagged: dict[tuple[bool, str], list] = {}

    for term in terms:
        if not term.approved:
            continue
        for surface in surfaces_of(term):
            bucket = flagged if term.ambiguous else by_key
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
                # What makes a match resolvable without matching the string
                # again: spaCy surfaces this as ``ent.ent_id_``, so "the
                # Authority" arrives already carrying the row it came from —
                # which is exactly what §5.5 needs and cannot recover from the
                # span alone.
                "id": str(term.term_id),
            }
        )

    return CompiledGazetteer(tuple(patterns), tuple(withheld))


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

    Right to left, one word per letter, with connectives skippable in between.
    The last word must carry the last letter and the first word the first: an
    expansion that starts mid-word ("Annual Land Transport Authority" for LTA)
    is how a plausible-looking wrong expansion gets in, and both anchors are
    free to check.
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


def find_acronyms(text: str) -> list[AcronymDefinition]:
    """Every ``Full Name Here (ACRONYM)`` this text defines.

    Deduplicated, first definition wins, order preserved. The reverse form —
    ``ACRONYM (Full Name Here)`` — is not read: it is far rarer in the documents
    this corpus fetches, and the same bracket shape is also how those documents
    gloss anything at all, so accepting it would mean accepting every
    parenthetical as an expansion.
    """
    found: dict[tuple[str, str], AcronymDefinition] = {}

    for match in _CANDIDATE.finditer(text):
        letters = _looks_like_an_acronym(match.group(1))
        if letters is None:
            continue

        # Only the current clause. Text before a full stop is a different
        # sentence, and an expansion stitched across one appeared nowhere.
        before = text[: match.start()]
        boundary = 0
        for hit in _BOUNDARY.finditer(before):
            boundary = hit.end()
        words = tokenise(before[boundary:])[-(len(letters) + _WINDOW) * 2 :]
        if not words:
            continue

        start = _expansion_start(words, letters)
        if start is None:
            continue

        expansion = _POSSESSIVE.sub("", " ".join(words[start:]))
        # An acronym whose expansion is one word is the word itself abbreviated,
        # which the table has no use for, and a runaway span is a parse failure.
        if len(words) - start < 2 or len(expansion) > 90:
            continue

        acronym = match.group(1).strip()
        key = (acronym, expansion.lower())
        found.setdefault(key, AcronymDefinition(acronym, expansion))

    return list(found.values())
