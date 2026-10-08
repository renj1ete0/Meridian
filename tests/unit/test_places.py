"""Which places a source is about — the pure half (task P2-23, spec §7.2, §5.6).

The rules are tested with constructed text and constructed evidence, at their
edges: one mention below a threshold and one at it. Most of these are rejection
tests, because the failure that matters is a document tagged with a place it
only mentions — that puts it in the wrong column of a comparison, and nothing
downstream can tell.

The database half — the queue, the write, staleness, the filters and Gaps — is
`tests/integration/test_places.py`.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from types import SimpleNamespace

import pytest
import yaml

from meridian_core import placenames, places
from meridian_core.placenames import (
    AMBIGUOUS,
    CITIES,
    COUNTRIES,
    LANGUAGE_COUNTRIES,
    NOT_PLACES,
    SUBDIVISIONS,
    TLD_COUNTRY,
)
from meridian_core.places import (
    COUNTRY_CODE,
    DOMAIN,
    ENTITY,
    GAZETTEER,
    GOVERNMENT,
    LANGUAGE,
    MIN_MENTIONS,
    SHARE_OF_BEST,
    TEXT,
    GazetteerPlace,
    Mentions,
    Vocabulary,
    basis_fingerprint,
    comparison_places,
    count_mentions,
    decide,
    domain_country,
    entity_codes,
    examine,
    minimum_mentions,
    valid_code,
    without_references,
)

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def vocab() -> Vocabulary:
    return Vocabulary.build()


def mentions(names: dict[str, int] | None = None, gaz: dict[str, int] | None = None, chars=0):
    out = Mentions(chars=chars)
    out.names.update(names or {})
    out.gazetteer.update(gaz or {})
    return out


def a_source(text_title: str | None = None, url="https://example.com/a", tier="institutional"):
    return SimpleNamespace(source_id=1, url=url, title=text_title, source_tier=tier, language="en")


def one_country(code: str) -> str:
    return COUNTRIES[code][0]


# ---------------------------------------------------------------------------
# The vocabulary is well-formed — drift between the tables and the scheme
# ---------------------------------------------------------------------------


def test_every_country_code_is_two_uppercase_letters() -> None:
    bad = [c for c in COUNTRIES if not re.fullmatch(r"[A-Z]{2}", c)]
    assert not bad, bad


def test_every_city_code_is_a_locode_whose_prefix_is_a_known_country() -> None:
    """The scheme's one structural promise: a city code rolls up to its
    country with ``code[:2]``. A city whose prefix names no country would tag
    a document with a country the filter cannot offer."""
    bad = [c for c in CITIES if not re.fullmatch(r"[A-Z]{5}", c) or c[:2] not in COUNTRIES]
    assert not bad, bad


def test_every_table_that_names_a_country_names_a_known_one() -> None:
    named = {*SUBDIVISIONS, *TLD_COUNTRY.values()} | {
        c for codes in LANGUAGE_COUNTRIES.values() for c in codes
    }
    assert named - set(COUNTRIES) == set()


def test_every_ambiguous_form_is_a_name_the_tables_actually_hold() -> None:
    """An entry here that no table holds is a stale exclusion — or a typo that
    leaves the form it meant to exclude matched."""
    held = {
        n for table in (COUNTRIES, CITIES, SUBDIVISIONS) for names in table.values() for n in names
    }
    assert AMBIGUOUS - held == set()


def test_no_surface_form_names_two_places() -> None:
    """A name under two codes would be counted for whichever the build saw
    last — a silent, order-dependent answer."""
    seen: dict[str, str] = {}
    clashes = []
    for table in (COUNTRIES, CITIES):
        for code, names in table.items():
            for name in names:
                if name in seen and seen[name] != code:
                    clashes.append((name, seen[name], code))
                seen[name] = code
    assert not clashes, clashes


def test_every_code_has_a_display_name() -> None:
    for code in [*COUNTRIES, *CITIES]:
        assert placenames.display_name(code) != code
    assert placenames.display_name("ZZ") == "ZZ"


def test_valid_code_normalises_and_refuses() -> None:
    assert valid_code(" sg ") == "SG"
    assert valid_code("uk") == "GB"
    assert valid_code("JPTYO") == "JPTYO"
    for bad in (None, "", "XX", "GB-LND", "Singapore", "S"):
        assert valid_code(bad) is None, bad


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_names_are_counted_by_code_and_a_city_by_its_own_code(vocab) -> None:
    text = f"{one_country('JP')} and {CITIES['JPTYO'][0]}, then {one_country('JP')} again."
    found = count_mentions(text, vocab)
    assert found.names == {"JP": 2, "JPTYO": 1}


def test_lower_case_is_not_a_place(vocab) -> None:
    """Proper nouns only: the lower-case form of a place name is usually a
    common noun, and counting it would tag every document using the word."""
    name = one_country("JP")
    assert count_mentions(name.lower(), vocab).names == {}
    assert count_mentions(name.upper(), vocab).names == {"JP": 1}


def test_a_name_inside_a_longer_word_is_not_counted(vocab) -> None:
    name = one_country("NG")  # a name another country's name begins with
    assert count_mentions(f"{name}n", vocab).names == {}
    assert count_mentions(f"x{name}", vocab).names == {}


def test_the_longest_name_wins_at_a_position(vocab) -> None:
    """A country whose name contains another's is not also counted as the
    other: the longer alternative is tried first."""
    assert count_mentions("Papua New Guinea", vocab).names == {"PG": 1}
    assert count_mentions("South Sudan", vocab).names == {"SS": 1}
    assert count_mentions("Northern Ireland", vocab).names == {"GB": 1}


def test_names_ending_in_a_full_stop_are_matched(vocab) -> None:
    """``\\b`` after a full stop needs a word character to follow; the
    lookarounds do not, so an abbreviation at a sentence end still counts."""
    assert count_mentions("Policy in the U.S. differs.", vocab).names == {"US": 1}
    assert count_mentions("Policy in the U.S.", vocab).names == {"US": 1}


@pytest.mark.parametrize("form", sorted(AMBIGUOUS))
def test_an_ambiguous_form_is_never_counted(vocab, form: str) -> None:
    """Rejection: a form that is routinely a person, a state or a common noun
    never counts, whatever its table says."""
    assert count_mentions(f"{form} {form} {form} {form} {form}.", vocab).names == {}


@pytest.mark.parametrize("phrase", NOT_PLACES)
def test_a_phrase_named_for_a_place_does_not_count_the_place(vocab, phrase: str) -> None:
    """A treaty named for a city, a newspaper named for its city, a publisher's
    imprint: the place inside is swallowed with the phrase."""
    assert count_mentions(f"{phrase}. {phrase}.", vocab).names == {}


def test_a_city_followed_by_its_own_country_is_one_mention(vocab) -> None:
    city, country = CITIES["JPTYO"][0], one_country("JP")
    assert count_mentions(f"held in {city}, {country}, last year", vocab).names == {"JPTYO": 1}
    assert count_mentions(f"held in {city} ({country})", vocab).names == {"JPTYO": 1}
    # Not the same mention once there is text between them.
    later = count_mentions(f"{city} is large. {country} is larger.", vocab).names
    assert later == {"JPTYO": 1, "JP": 1}
    # And not when the country is another one.
    other = count_mentions(f"{city}, {one_country('KR')}", vocab).names
    assert other == {"JPTYO": 1, "KR": 1}


def test_a_subdivision_counts_toward_its_country(vocab) -> None:
    state = SUBDIVISIONS["US"][0]
    assert count_mentions(f"{state} and {state}", vocab).names == {"US": 2}


def test_gazetteer_terms_count_separately_toward_their_country() -> None:
    vocab = Vocabulary.build([GazetteerPlace("Harbour Transit Office", "SG")])
    found = count_mentions("The Harbour Transit Office said so.", vocab)
    assert found.names == {}
    assert found.gazetteer == {"SG": 1}


def test_a_gazetteer_term_that_is_a_place_name_is_counted_once() -> None:
    vocab = Vocabulary.build([GazetteerPlace(one_country("SG"), "SG")])
    found = count_mentions(one_country("SG"), vocab)
    assert (found.names, found.gazetteer) == ({"SG": 1}, {})


def test_countries_named_folds_cities_and_terms_into_countries(vocab: Vocabulary) -> None:
    """What the answer page asks of a passage (`B-168`): which countries it names."""
    text = "Trials ran in Berlin and Munich, then in Germany, and later in Singapore."
    assert places.countries_named(text, vocab) == {"DE", "SG"}
    terms = Vocabulary.build([GazetteerPlace("Harbour Transit Office", "SG")])
    assert places.countries_named("The Harbour Transit Office said so.", terms) == {"SG"}
    assert places.countries_named("Ridership rose by a fifth.", vocab) == frozenset()
    assert places.countries_named("", vocab) == frozenset()


# ---------------------------------------------------------------------------
# Reference lists
# ---------------------------------------------------------------------------


def test_a_late_reference_list_is_cut() -> None:
    body = "Body text.\n" * 50
    text = body + "References\n- A citation printed in a far city.\n"
    assert without_references(text).strip() == body.strip()


def test_an_early_references_heading_is_not_a_reference_list() -> None:
    text = "## References\n" + "Body text that follows.\n" * 20
    assert "Body text" in without_references(text)


def test_a_citation_line_is_dropped_wherever_it_sits() -> None:
    cite = "- Surname A, Other B (2021) A title. Journal of Things 4(2): 1-9. London."
    body = "A paragraph about the subject."
    kept = without_references(f"{body}\n{cite}\n{body}")
    assert cite not in kept
    assert kept.count(body) == 2


def test_a_long_paragraph_mentioning_a_journal_is_kept() -> None:
    para = "In 2021 a Journal reported " + "that the scheme worked, " * 40
    assert without_references(para) == para


# ---------------------------------------------------------------------------
# The decision — rejections first
# ---------------------------------------------------------------------------


def test_a_single_passing_mention_does_not_tag() -> None:
    """The case the minimum exists for."""
    assert decide(mentions({"JP": 1})).places == []


def test_one_below_the_minimum_does_not_tag_and_the_minimum_does() -> None:
    assert decide(mentions({"JP": MIN_MENTIONS - 1})).places == []
    assert decide(mentions({"JP": MIN_MENTIONS})).places == ["JP"]


def test_a_place_far_below_the_best_does_not_tag() -> None:
    best = 40
    minor = int(best * SHARE_OF_BEST) - 1
    assert minor >= MIN_MENTIONS  # the share, not the minimum, is what refuses it
    decision = decide(mentions({"SG": best, "JP": minor}))
    assert decision.places == ["SG"]


def test_a_comparison_of_several_places_carries_all_of_them() -> None:
    decision = decide(mentions({"SG": 10, "JP": 9, "KR": 8}))
    assert decision.places == ["SG", "JP", "KR"]
    assert decision.decided["KR"] == [TEXT]


def test_a_long_document_needs_proportionally_more_mentions() -> None:
    long = 100_000
    floor = minimum_mentions(long)
    assert floor > MIN_MENTIONS
    assert decide(mentions({"JP": floor - 1}, chars=long)).places == []
    assert decide(mentions({"JP": floor}, chars=long)).places == ["JP"]


def test_a_city_rolls_up_and_is_tagged_beside_its_country() -> None:
    decision = decide(mentions({"JPTYO": 6}))
    assert decision.places == ["JP", "JPTYO"]
    assert decision.country_counts == {"JP": 6}


def test_a_city_in_a_country_the_document_is_not_about_is_not_tagged() -> None:
    """Five mentions of a city in a document about another country, far
    below the share: neither the city nor its country is tagged."""
    decision = decide(mentions({"SG": 60, "GBLON": 5}))
    assert decision.places == ["SG"]


def test_a_gazetteer_term_alone_can_tag_and_says_so() -> None:
    decision = decide(mentions(gaz={"SG": MIN_MENTIONS}))
    assert decision.places == ["SG"]
    assert decision.decided["SG"] == [GAZETTEER]


# ---------------------------------------------------------------------------
# The domain
# ---------------------------------------------------------------------------


def test_a_foreign_publisher_writing_about_another_country_is_not_tagged_with_its_own() -> None:
    """The case the domain gets wrong: where the publisher is is not what the
    page is about. The text names another country; the publisher's is named
    once, below the share."""
    decision = decide(mentions({"US": 20, "SG": 1}), domain=("SG", GOVERNMENT))
    assert decision.places == ["US"]


def test_a_government_page_naming_no_place_is_about_its_jurisdiction() -> None:
    decision = decide(mentions(), domain=("SG", GOVERNMENT))
    assert decision.places == ["SG"]
    assert decision.decided["SG"] == [DOMAIN]


def test_a_country_code_domain_alone_does_not_tag() -> None:
    """Rejection: a company or university on a country-code domain naming no
    place is not thereby about the country."""
    assert decide(mentions(), domain=("DE", COUNTRY_CODE)).places == []


def test_the_domain_lowers_the_bar_for_its_own_country_only() -> None:
    gov = decide(mentions({"SG": 1}), domain=("SG", GOVERNMENT))
    assert gov.places == ["SG"]
    assert gov.decided["SG"] == [TEXT, DOMAIN]
    # A country-code domain needs two, not one.
    assert decide(mentions({"DE": 1}), domain=("DE", COUNTRY_CODE)).places == []
    assert decide(mentions({"DE": 2}), domain=("DE", COUNTRY_CODE)).places == ["DE"]
    # And another country named once is still a passing mention.
    assert decide(mentions({"JP": 1}), domain=("DE", COUNTRY_CODE)).places == []


def test_a_domain_that_agrees_with_the_text_is_recorded() -> None:
    decision = decide(mentions({"SG": 8}), domain=("SG", GOVERNMENT))
    assert decision.decided["SG"] == [TEXT, DOMAIN]


# ---------------------------------------------------------------------------
# Language — weakest, and never alone
# ---------------------------------------------------------------------------


def test_language_alone_never_tags() -> None:
    assert decide(mentions(), language="ja").places == []


def test_language_confirms_a_country_code_domain_on_a_page_naming_no_place() -> None:
    decision = decide(mentions(), domain=("JP", COUNTRY_CODE), language="ja")
    assert decision.places == ["JP"]
    assert decision.decided["JP"] == [DOMAIN, LANGUAGE]


def test_language_that_disagrees_with_the_domain_confirms_nothing() -> None:
    assert decide(mentions(), domain=("JP", COUNTRY_CODE), language="de").places == []
    assert decide(mentions(), domain=("JP", COUNTRY_CODE), language="en").places == []


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def test_a_cited_place_entity_tags_on_its_own_and_brings_its_country() -> None:
    decision = decide(mentions(), entities={"KRSEL": ["a city"]})
    assert set(decision.places) == {"KR", "KRSEL"}
    assert decision.decided["KRSEL"] == [ENTITY]
    assert decision.decided["KR"] == [ENTITY]


def test_entity_codes_prefers_a_city_then_a_country_then_the_jurisdiction() -> None:
    assert entity_codes(CITIES["KRSEL"][0], None, "JP") == "KRSEL"
    assert entity_codes("somewhere", [one_country("TH")], None) == "TH"
    assert entity_codes("an unnamed district", None, "my") == "MY"
    assert entity_codes("an unnamed district", None, None) is None
    # An ambiguous name falls through to the jurisdiction rather than guessing.
    form = sorted(AMBIGUOUS & {n for names in COUNTRIES.values() for n in names})[0]
    assert entity_codes(form, None, None) is None


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "tier", "expected"),
    [
        ("https://www.agency.gov.sg/page", "government", ("SG", GOVERNMENT)),
        ("https://dept.gov.uk/x", "government", ("GB", GOVERNMENT)),
        ("https://agency.gov/x", "government", ("US", GOVERNMENT)),
        ("https://firm.co.jp/x", "institutional", ("JP", COUNTRY_CODE)),
        ("https://school.edu/x", "peer_reviewed", ("US", COUNTRY_CODE)),
        ("https://body.europa.eu/x", "government", ("EU", GOVERNMENT)),
    ],
)
def test_domain_country(url, tier, expected) -> None:
    assert domain_country(url, tier) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://startup.io/",
        "https://thing.ai/",
        "https://x.org/",
        "localhost",
    ],
)
def test_a_generic_domain_names_no_country(url) -> None:
    assert domain_country(url, "government") is None


# ---------------------------------------------------------------------------
# One source, end to end without a database
# ---------------------------------------------------------------------------


def test_examine_counts_the_title_and_records_every_input(vocab) -> None:
    title = f"Buses in {CITIES['KRSEL'][0]}"
    text = f"{CITIES['KRSEL'][0]} runs buses."
    examined = examine(a_source(title), text, vocab)
    assert examined.decision.places == ["KR", "KRSEL"]
    evidence = examined.evidence
    assert evidence["names"] == {"KRSEL": 1 + places.TITLE_WEIGHT}
    assert set(evidence) == {"names", "gazetteer", "entities", "domain", "language", "decided"}
    assert evidence["decided"] == {"KR": [TEXT], "KRSEL": [TEXT]}


def test_examine_ignores_places_in_the_reference_list(vocab) -> None:
    body = f"{one_country('SG')} is the subject. " * 6 + "\n"
    refs = "References\n" + f"- A book. {CITIES['GBLON'][0]}.\n" * 30
    examined = examine(a_source(), body * 10 + refs, vocab)
    assert examined.decision.places == ["SG"]


# ---------------------------------------------------------------------------
# The basis
# ---------------------------------------------------------------------------


def test_the_basis_changes_with_the_vocabulary_and_the_thresholds(monkeypatch) -> None:
    plain = basis_fingerprint(Vocabulary.build())
    assert plain == basis_fingerprint(Vocabulary.build())
    with_term = basis_fingerprint(Vocabulary.build([GazetteerPlace("Harbour Office", "SG")]))
    assert with_term != plain
    monkeypatch.setattr(places, "SHARE_OF_BEST", SHARE_OF_BEST + 0.01)
    assert basis_fingerprint(Vocabulary.build()) != plain


# ---------------------------------------------------------------------------
# The comparison set
# ---------------------------------------------------------------------------


def test_the_comparison_set_comes_from_government_suffixes_and_jurisdictions() -> None:
    tiers = yaml.safe_load((REPO / "config" / "source_tiers.yaml").read_text())
    got = comparison_places(tiers, ["SG", "uk", None, "nowhere"])
    codes = {p.code for p in got}
    # Every government pattern with a national suffix contributes its country.
    for pattern in tiers["patterns"]["government"]:
        code = places.pattern_country(pattern)
        if code:
            assert code in codes, pattern
    assert "GB" in codes  # from a jurisdiction written the common way
    assert all(len(c) == 2 for c in codes)
    assert [p.name for p in got] == sorted(p.name for p in got)
    assert all(dataclasses.asdict(p)["name"] for p in got)


def test_an_empty_configuration_has_no_comparison_set() -> None:
    assert comparison_places({}, []) == []
    assert comparison_places({"patterns": {"government": ["*.org", "*.int"]}}, []) == []


# ---------------------------------------------------------------------------
# The timetable row — the migration and the seed file agree
# ---------------------------------------------------------------------------


def test_the_migration_schedules_the_pass_exactly_as_the_seed_file_does() -> None:
    """`scripts/seed.py` is insert-only, so an existing database gets the row
    from the migration and a new one from the file. Two copies of one row:
    this is the test that keeps them one."""
    jobs = yaml.safe_load((REPO / "config" / "schedule.yaml").read_text())["jobs"]
    seeded = next(j for j in jobs if j["name"] == "places")
    migration = next((REPO / "migrations" / "versions").glob("*_which_places_a_source_is_about.py"))
    match = re.search(
        r"VALUES \('places', '([\w.]+)', ARRAY\[([^\]]*)\], (\d+), (true|false)\)",
        migration.read_text(),
    )
    assert match, "the migration no longer inserts the row in the expected form"
    module, args, interval, enabled = match.groups()
    assert module == seeded["module"]
    assert [a.strip().strip("'") for a in args.split(",")] == seeded["args"]
    assert int(interval) == seeded["interval_seconds"]
    assert (enabled == "true") == seeded["enabled"]
    # And it writes — a report-only row on a timer would examine everything
    # every hour and record nothing.
    assert "--apply" in seeded["args"]
