"""The gazetteer's two pure halves (task P5-02, spec §5.6).

Both halves have the same failure mode and it is the reason this file is long:
**nothing here fails loudly.** A pattern that is wrong does not raise, it
overrides the statistical model on every document that mentions the term. A
regex that is too loose does not crash, it fills the table entity resolution
trusts with `(PDF)` and `(USD)`. Neither shows up in a smoke test, because in
both cases the pipeline runs and produces entities.

So the tests are mostly about what must *not* happen: which rows do not load,
which brackets are not definitions, and which matches must not be made
case-insensitively.
"""

from __future__ import annotations

import dataclasses

import pytest

from meridian_core.gazetteer import (
    MAX_ACRONYM_LETTERS,
    compile_patterns,
    find_acronyms,
    label_for,
    surfaces_of,
    tokenise,
)


@dataclasses.dataclass
class Row:
    """A gazetteer row, as much of one as `compile_patterns` reads."""

    term_id: int
    canonical: str
    aliases: list[str] | None = None
    entity_type: str = "agency"
    approved: bool = True
    ambiguous: bool = False


def surfaces(compiled) -> list[str]:
    """Every pattern's text, rejoined, for readable assertions."""
    out = []
    for pattern in compiled.patterns:
        out.append(" ".join(token.get("TEXT") or token["LOWER"] for token in pattern["pattern"]))
    return out


# --------------------------------------------------------------------------
# What loads
# --------------------------------------------------------------------------


def test_canonical_and_every_alias_become_patterns() -> None:
    compiled = compile_patterns([Row(1, "Land Transport Authority", ["Transport Authority"])])

    assert surfaces(compiled) == ["land transport authority", "transport authority"]


def test_a_pattern_carries_the_row_it_came_from() -> None:
    # `ent.ent_id_` is what makes a match resolvable without matching the string
    # again. Without it "the Authority" arrives as a span and §5.5 has to
    # re-derive which of several rows produced it — from the text that was
    # ambiguous enough to need a gazetteer in the first place.
    compiled = compile_patterns([Row(77, "Electronic Road Pricing", ["ERP"])])

    assert {pattern["id"] for pattern in compiled.patterns} == {"77"}


def test_the_label_is_meridians_ontology_not_spacys() -> None:
    # If a curated match were labelled ORG it would be indistinguishable
    # downstream from a word the model guessed was an organisation, and the two
    # carry completely different confidence.
    compiled = compile_patterns([Row(1, "Land Transport Authority", entity_type="agency")])

    assert {pattern["label"] for pattern in compiled.patterns} == {"AGENCY"}
    assert "ORG" not in {pattern["label"] for pattern in compiled.patterns}


@pytest.mark.parametrize(
    "entity_type", ["agency", "scheme", "infrastructure", "metric", "concept"]
)
def test_every_type_in_the_schema_has_a_label(entity_type: str) -> None:
    # Drift: `gazetteer_entity_type` is a CHECK constraint, and a type added
    # there with no label here loads as an empty string nothing downstream reads.
    assert label_for(entity_type).isupper()


def test_blank_and_repeated_aliases_are_dropped() -> None:
    compiled = compile_patterns(
        [Row(1, "Land Transport Authority", ["  ", "", "Land Transport Authority"])]
    )

    assert surfaces(compiled) == ["land transport authority"]


def test_surfaces_put_the_canonical_first() -> None:
    assert surfaces_of(Row(1, "Canonical", ["Alias"])) == ["Canonical", "Alias"]


# --------------------------------------------------------------------------
# What does not load — each one an override that must not happen
# --------------------------------------------------------------------------


def test_an_unapproved_row_loads_nothing() -> None:
    # §5.6 files auto-harvested and model-proposed terms as approved=false. If
    # those loaded, the approval queue would be decorative and a regex's mistake
    # would take the model's say away on every document mentioning the term.
    compiled = compile_patterns([Row(1, "Something Proposed", ["SP"], approved=False)])

    assert compiled.patterns == ()


def test_a_row_flagged_ambiguous_is_withheld_with_its_reason() -> None:
    compiled = compile_patterns([Row(1, "Operational Design Domain", ["ODD"], ambiguous=True)])

    assert compiled.patterns == ()
    assert [(w.reason, w.term_ids) for w in compiled.withheld] == [
        ("ambiguous", (1,)),
        ("ambiguous", (1,)),
    ]


def test_two_rows_sharing_a_surface_are_withheld_though_neither_is_flagged() -> None:
    # The drift case, and the reason the flag alone is not enough. `ambiguous`
    # is hand-maintained; a collision is the same fact observed rather than
    # declared. Without this the ruler keeps whichever pattern it saw first and
    # the choice between two jurisdictions is made by row order.
    compiled = compile_patterns(
        [
            Row(1, "Building and Construction Authority", ["BCA"]),
            Row(2, "Bicycle Coalition of America", ["BCA"]),
        ]
    )

    assert "BCA" not in surfaces(compiled)
    assert [w for w in compiled.withheld if w.reason == "collision"][0].term_ids == (1, 2)


def test_a_flagged_row_shadows_an_unflagged_one_with_the_same_surface() -> None:
    # The collision is real whichever row carries the flag: if the unflagged one
    # still loaded, the surface would resolve to it every time and the flag on
    # the other row would have made the wrong reading the only reading.
    compiled = compile_patterns(
        [Row(1, "Some Agency", ["SA"], ambiguous=True), Row(2, "South Australia", ["SA"])]
    )

    assert "SA" not in surfaces(compiled)


def test_withholding_one_surface_keeps_the_others() -> None:
    # A collision on an alias must not cost the canonical, which is unambiguous.
    compiled = compile_patterns(
        [Row(1, "Land Transport Authority", ["LTA"]), Row(2, "Lake Tahoe Airport", ["LTA"])]
    )

    assert "land transport authority" in surfaces(compiled)
    assert "lake tahoe airport" in surfaces(compiled)
    assert "LTA" not in surfaces(compiled)


# --------------------------------------------------------------------------
# The case rule — the most consequential line in the module
# --------------------------------------------------------------------------


def test_a_short_all_caps_form_matches_case_sensitively() -> None:
    # "ODD" matched case-insensitively fires on the ordinary English word, and
    # every hit becomes a curated, high-precedence entity in a research corpus.
    compiled = compile_patterns([Row(1, "Operational Design Domain", ["ODD"])])

    acronym = [p for p in compiled.patterns if p["pattern"][0].get("TEXT") == "ODD"]
    assert acronym, "an acronym must be matched on TEXT, not LOWER"


def test_a_long_form_matches_regardless_of_case() -> None:
    # The mirror failure, and much cheaper: matching case-sensitively here just
    # misses the term in lower-cased prose. Nobody writes a sentence that
    # accidentally spells out "Land Transport Authority".
    compiled = compile_patterns([Row(1, "Land Transport Authority")])

    assert all("LOWER" in token for token in compiled.patterns[0]["pattern"])


def test_a_short_mixed_case_form_is_not_treated_as_an_acronym() -> None:
    compiled = compile_patterns([Row(1, "Silver Zone")])

    assert all("LOWER" in token for token in compiled.patterns[0]["pattern"])


def test_two_surfaces_differing_only_in_case_do_not_collide() -> None:
    # Under the case rule they are different patterns and one cannot be reached
    # by the other, so treating them as a collision would withhold a term for a
    # clash that cannot happen.
    compiled = compile_patterns([Row(1, "ODD", ["Operational Design Domain"]), Row(2, "odd")])

    assert [w for w in compiled.withheld if w.reason == "collision"] == []


# --------------------------------------------------------------------------
# Tokenisation — the patterns have to survive the matcher
# --------------------------------------------------------------------------


def test_an_ampersand_is_its_own_token() -> None:
    # spaCy splits it out. A pattern that kept "& Development" as one token
    # would never match anything, silently.
    assert tokenise("Housing & Development Board") == ["Housing", "&", "Development", "Board"]


def test_a_possessive_stays_attached() -> None:
    assert tokenise("Authority's plan") == ["Authority's", "plan"]


# --------------------------------------------------------------------------
# The harvest — what counts as a definition
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "acronym", "expansion"),
    [
        ("The Land Transport Authority (LTA) said.", "LTA", "Land Transport Authority"),
        ("the Housing & Development Board (HDB) rules", "HDB", "Housing & Development Board"),
        ("a Department for Transport (DfT) report", "DfT", "Department for Transport"),
        ("the farebox recovery ratio (FRR) fell", "FRR", "farebox recovery ratio"),
        ("Transit Oriented Development (TOD) works", "TOD", "Transit Oriented Development"),
    ],
)
def test_a_real_definition_is_read(text: str, acronym: str, expansion: str) -> None:
    found = find_acronyms(text)

    assert [(d.acronym, d.expansion) for d in found] == [(acronym, expansion)]


@pytest.mark.parametrize(
    "text",
    [
        "Download the report (PDF) here.",
        "as shown in the figure (see Figure 3) above",
        "priced in dollars (USD) per trip",
        "a list item (ii) then another",
        "in the year (2026) the plan",
        "the third quarter (Q3) result",
        "nothing at all relevant (XYZ) here",
        "an empty bracket () and text",
    ],
)
def test_a_bracket_that_is_not_a_definition_is_refused(text: str) -> None:
    # Each of these matches `Full Name Here (ACRONYM)` on shape alone. Admitting
    # one is not a transient error: it is a permanent row in the table entity
    # resolution consults, and nothing downstream can tell it from a real term.
    assert find_acronyms(text) == []


def test_an_expansion_may_not_cross_a_sentence_boundary() -> None:
    # "It was odd." and the next sentence are different claims. An expansion
    # stitched across the full stop appeared in no document.
    assert find_acronyms("It was odd. Matters here (ODD) apply.") == []


def test_a_line_wrap_is_not_a_boundary() -> None:
    # Extracted PDF text breaks lines mid-sentence constantly, so a two-column
    # report defines most of its acronyms across a newline. Treating every line
    # break as a sentence end would reject them — in exactly the documents this
    # pattern is supposed to be high-yield in.
    found = find_acronyms("as set out by the Land Transport\nAuthority (LTA) today")

    assert [d.expansion for d in found] == ["Land Transport Authority"]


def test_a_blank_line_is_a_boundary() -> None:
    assert find_acronyms("A heading\n\nTransport Authority (LTA) today") == []


def test_a_definition_after_a_boundary_is_still_read() -> None:
    # The converse, which is what makes the test above mean anything: cutting at
    # the boundary must not cut off definitions that begin after it.
    found = find_acronyms("Some earlier sentence. Operational Design Domain (ODD) applies.")

    assert [d.expansion for d in found] == ["Operational Design Domain"]


def test_the_first_expansion_word_must_carry_the_first_letter() -> None:
    # Without the anchor the match runs backwards until it finds the letters
    # somewhere, and "Annual Land Transport Authority" becomes the expansion of
    # LTA — plausible, wrong, and indistinguishable once written.
    assert find_acronyms("the Annual Report of Land Transport (LTA) rules") == []


def test_a_possessive_is_not_part_of_the_expansion() -> None:
    found = find_acronyms("the Urban Redevelopment Authority's (URA) plan")

    assert [d.expansion for d in found] == ["Urban Redevelopment Authority"]


def test_a_single_word_expansion_is_refused() -> None:
    # A two-letter acronym needs two words. One word means the "expansion" is
    # the word itself abbreviated, which the table has no use for.
    assert find_acronyms("Walkability (WA) matters") == []


def test_the_reverse_form_is_not_read() -> None:
    # `ACRONYM (Full Name)` is far rarer, and the same bracket shape is how these
    # documents gloss anything at all — accepting it means accepting every
    # parenthetical as an expansion.
    assert find_acronyms("ERP (Electronic Road Pricing) charges") == []


def test_the_same_definition_twice_is_one_definition() -> None:
    text = "The Land Transport Authority (LTA) said. The Land Transport Authority (LTA) added."

    assert len(find_acronyms(text)) == 1


def test_two_expansions_of_one_acronym_are_both_returned() -> None:
    # The harvest has to see both to flag the acronym ambiguous. Collapsing them
    # here would hide the disagreement and let whichever came first become the
    # single reading.
    text = "Land Transport Authority (LTA). Later, the Lake Tahoe Airport (LTA) opened."

    assert {d.expansion for d in find_acronyms(text)} == {
        "Land Transport Authority",
        "Lake Tahoe Airport",
    }


def test_an_acronym_longer_than_the_cap_is_refused() -> None:
    letters = "A" * (MAX_ACRONYM_LETTERS + 1)
    words = " ".join("Alpha" for _ in letters)

    assert find_acronyms(f"the {words} ({letters}) thing") == []


def test_definitions_keep_the_order_they_appear_in() -> None:
    text = "Electronic Road Pricing (ERP) and the Land Transport Authority (LTA)."

    assert [d.acronym for d in find_acronyms(text)] == ["ERP", "LTA"]


def test_text_with_no_brackets_costs_nothing_and_finds_nothing() -> None:
    assert find_acronyms("A paragraph of ordinary prose with no definitions in it.") == []
