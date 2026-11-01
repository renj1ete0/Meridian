"""What the stored-metadata pass changes on one source (task B-234).

`decide` is the precedence rule: declared beats inferred or unknown, inferred never
replaces anything, and a stored value a rule refuses is cleared.
"""

from __future__ import annotations

import collections
import datetime as dt

from worker.extract.declared import Declared, Found
from worker.remeta import Stored, decide, declared_in_pdf

FETCHED = dt.date(2026, 6, 15)
FIRST = dt.date(2026, 5, 2)


def stored(**fields) -> Stored:
    base = dict(
        title="Ferry waits fall",
        author=None,
        publisher=None,
        publication_date=None,
        language="en",
        extra={},
        url="https://news.velde.example/a",
        media_type="text/html",
        fetched=FETCHED,
        first_fetched=FIRST,
    )
    base.update(fields)
    return Stored(**base)


def by_field(changes):
    return {c.field: c for c in changes}


def test_a_declared_date_replaces_one_of_unknown_provenance() -> None:
    changes = by_field(
        decide(
            stored(publication_date=dt.date(2025, 3, 2)),
            Declared(publication_date=Found(dt.date(2021, 3, 9), "jsonld")),
        )
    )
    change = changes["publication_date"]
    assert (change.old, change.new, change.source) == (
        dt.date(2025, 3, 2),
        dt.date(2021, 3, 9),
        "jsonld",
    )


def test_a_value_already_from_a_declared_source_is_not_flipped() -> None:
    source = stored(author="Mira Castellan", extra={"author_from": "citation"})
    assert decide(source, Declared(author=Found("M. Castellan", "meta"))) == []


def test_a_matching_value_only_gains_its_provenance() -> None:
    changes = by_field(
        decide(
            stored(publisher="Velde Gazette"), Declared(publisher=Found("Velde Gazette", "meta"))
        )
    )
    change = changes["publisher"]
    assert change.old == change.new == "Velde Gazette"
    assert change.source == "meta"


def test_nothing_inferred_replaces_anything() -> None:
    """With nothing declared, a stored value that passes the rules is left alone."""
    source = stored(
        author="Mira Castellan", publisher="velde.example", publication_date=dt.date(2021, 3, 9)
    )
    assert decide(source, Declared()) == []
    assert decide(source, None) == []


def test_a_stored_copyright_year_is_cleared() -> None:
    change = by_field(decide(stored(publication_date=dt.date(2026, 1, 1)), None))[
        "publication_date"
    ]
    assert (change.new, change.rule) == (None, "year only")


def test_a_stored_server_clock_date_is_cleared() -> None:
    change = by_field(decide(stored(publication_date=FIRST), None))["publication_date"]
    assert (change.new, change.rule) == (None, "fetch day")


def test_a_stored_date_marked_declared_is_judged_as_declared() -> None:
    """1 January from JSON-LD is a full day the page declared, not a completed year."""
    source = stored(publication_date=dt.date(2019, 1, 1), extra={"date_from": "jsonld"})
    assert decide(source, None) == []


def test_a_placeholder_author_and_a_platform_publisher_are_cleared() -> None:
    changes = by_field(decide(stored(author="Admin", publisher="WordPress"), None))
    assert changes["author"].rule == "placeholder"
    assert changes["publisher"].rule == "platform"


def test_a_declared_date_a_rule_refuses_does_not_replace_and_is_counted() -> None:
    refused: collections.Counter = collections.Counter()
    changes = decide(
        stored(publication_date=dt.date(2021, 3, 9)),
        Declared(publication_date=Found(dt.date(2027, 1, 5), "citation")),
        refused,
    )
    assert changes == []
    assert refused[("publication_date", "citation: future")] == 1


def test_title_and_language_are_only_filled_never_replaced() -> None:
    declared = Declared(
        title=Found("Wait times on a harbour ferry", "citation"), language=Found("ms", "meta")
    )
    assert decide(stored(), declared) == []
    changes = by_field(decide(stored(title=None, language=None), declared))
    assert changes["title"].new == "Wait times on a harbour ferry"
    assert changes["language"].new == "ms"


def test_a_declared_title_that_is_a_placeholder_does_not_fill() -> None:
    assert decide(stored(title=None), Declared(title=Found("untitled", "dc"))) == []


def test_a_pdfs_creation_date_is_judged_against_its_own_fetch_days() -> None:
    pdf = stored(media_type="application/pdf")
    on_request = declared_in_pdf({"creationdate": f"{FIRST.isoformat()}T08:00:00Z"})
    assert decide(pdf, on_request) == []

    real = declared_in_pdf({"creationdate": "2019-03-14T08:00:00Z", "author": "Admin"})
    changes = by_field(decide(pdf, real))
    assert changes["publication_date"].new == dt.date(2019, 3, 14)
    assert changes["publication_date"].source == "pdfinfo"
    assert "author" not in changes
