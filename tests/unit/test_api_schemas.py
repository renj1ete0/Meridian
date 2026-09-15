"""The retrieval DTOs against the dataclasses they mirror (task P2-07).

No database. These are drift tests in the sense AGENTS.md means: two sources of
truth that must not disagree. `meridian_core.search` returns frozen dataclasses
because its callers are Python; `meridian_core.schemas.search` is the same shape
at an HTTP boundary. Nothing makes them agree, and the failure when they stop is
silent in the worst direction — a field added to a `SearchHit` and forgotten in
the DTO is simply dropped on the way out, and the caller sees a well-formed
response missing something it never knew to look for.

Also the paging guard, which is arithmetic and belongs nowhere near a fixture.
"""

from __future__ import annotations

import dataclasses

import pytest

from api.search_service import NO_EMBEDDER, NO_QUERY, WindowTooDeep, _degraded_reason, check_window
from meridian_core.schemas.search import CorpusStatsRead, SearchHitRead
from meridian_core.search import SearchHit
from meridian_core.stats import CorpusStats

# --------------------------------------------------------------------------
# Dataclass ↔ DTO parity
# --------------------------------------------------------------------------


def test_the_hit_dto_exposes_every_dataclass_field() -> None:
    """A field on the library's hit that the DTO lacks is dropped silently.

    Not hardcoded: the expected set is read off the dataclass, so a legitimate
    new field fails here once and is fixed in one place, rather than needing
    this test edited every time retrieval grows.
    """
    on_dataclass = {f.name for f in dataclasses.fields(SearchHit)}
    on_dto = set(SearchHitRead.model_fields)

    assert not (on_dataclass - on_dto), (
        f"SearchHit fields missing from SearchHitRead: {sorted(on_dataclass - on_dto)}"
    )


def test_the_hit_dto_invents_no_fields() -> None:
    """The reverse: a DTO field with nothing behind it never populates, and
    `from_attributes` would raise on validation rather than default it."""
    on_dataclass = {f.name for f in dataclasses.fields(SearchHit)}
    invented = set(SearchHitRead.model_fields) - on_dataclass

    assert not invented, f"SearchHitRead fields with no backing attribute: {sorted(invented)}"


def test_the_stats_dto_exposes_every_count_including_the_derived_one() -> None:
    """`searchable_chunks` is a property rather than a field, and a completeness
    check that only looked at `dataclasses.fields` would miss it — which would
    be the wrong kind of pass, because it is the count §12.5 actually turns on."""
    on_dataclass = {f.name for f in dataclasses.fields(CorpusStats)}
    on_dto = set(CorpusStatsRead.model_fields)

    assert not (on_dataclass - on_dto), sorted(on_dataclass - on_dto)
    assert "searchable_chunks" in on_dto


def test_a_hit_validates_from_the_dataclass() -> None:
    """`from_attributes` is the mechanism the routes rely on; that it works for
    this pair is worth one direct check rather than being inferred from the
    field-name comparisons above."""
    hit = SearchHit(
        chunk_id=1,
        source_id=2,
        text="some text",
        page_or_offset=3,
        chunk_index=0,
        url="https://example.test/a",
        title="A title",
        source_tier="government",
        publication_date=None,
        language="en",
        page_unit="page",
        media_type="application/pdf",
        duplicate_of=None,
        score=0.5,
        lexical_rank=1,
        vector_rank=None,
    )

    dto = SearchHitRead.model_validate(hit)

    assert dto.chunk_id == 1
    assert dto.source_tier == "government"
    # `P2-18`: the unit rides with the number, so a citation can be labelled
    # rather than guessed at.
    assert dto.page_unit == "page"
    assert dto.vector_rank is None


def test_the_hit_dto_rejects_a_tier_the_database_would_reject() -> None:
    """`source_tier` is the enum Literal, not a bare str. An API that emitted a
    tier the database cannot hold would be a boundary bug of exactly the kind
    `schemas/enums.py` exists to prevent."""
    with pytest.raises(ValueError):
        SearchHitRead.model_validate(
            {
                "chunk_id": 1,
                "source_id": 1,
                "text": "t",
                "page_or_offset": None,
                "chunk_index": 0,
                "url": "https://example.test/a",
                "title": None,
                "source_tier": "excellent",
                "publication_date": None,
                "language": None,
                "duplicate_of": None,
                "score": 0.1,
                "lexical_rank": 1,
                "vector_rank": None,
            }
        )


# --------------------------------------------------------------------------
# Paging a fused ranking
# --------------------------------------------------------------------------


def test_a_window_inside_the_pool_is_allowed() -> None:
    check_window(limit=20, offset=0, candidates=100)
    check_window(limit=20, offset=80, candidates=100)


def test_a_window_past_the_pool_is_refused() -> None:
    """RRF orders only what the arms handed it, so a hit beyond `candidates`
    was never a candidate. Returning an empty page there is indistinguishable
    from "you have reached the end", and a client would stop paging believing
    it had seen everything."""
    with pytest.raises(WindowTooDeep):
        check_window(limit=20, offset=90, candidates=100)


def test_the_refusal_names_both_remedies() -> None:
    """A 422 that does not say what to do instead is a dead end. Either widening
    the pool or narrowing the query resolves this, and only the caller can
    choose."""
    with pytest.raises(WindowTooDeep) as caught:
        check_window(limit=50, offset=100, candidates=100)

    message = str(caught.value)
    assert "candidates" in message
    assert "narrow" in message


# --------------------------------------------------------------------------
# Saying which arms ran
# --------------------------------------------------------------------------


def test_both_arms_running_is_not_degraded() -> None:
    assert _degraded_reason(frozenset({"lexical", "vector"}), has_vector=True) is None


def test_a_lexical_only_search_explains_the_missing_embedder() -> None:
    """§12.5 asks for hybrid search. A response that delivered half of one and
    said nothing would make the corpus look thinner than it is, and the reader
    would conclude something false about the corpus rather than about the API."""
    assert _degraded_reason(frozenset({"lexical"}), has_vector=False) == NO_EMBEDDER


def test_no_arms_at_all_says_so_separately() -> None:
    """ "Nothing was asked" and "half the search ran" are different answers and
    need different responses from the caller — one is its own empty search box,
    the other is a deployment limitation."""
    assert _degraded_reason(frozenset(), has_vector=False) == NO_QUERY


# --------------------------------------------------------------------------
# The uvicorn log config
# --------------------------------------------------------------------------


def test_the_log_config_names_a_formatter_that_exists() -> None:
    """A renamed formatter breaks the container at startup, which is the worst
    place to find out.

    `log_config.json` is handed to uvicorn with `--log-config` so that its
    startup banner is JSON like everything else — `meridian_core/logging.py`
    makes stdout the only collection point and promises one object per line, and
    uvicorn writes its first lines before the app's lifespan runs. The cost of
    that is a class path in a JSON file, which no import checks.
    """
    import importlib
    import json
    from pathlib import Path

    config = json.loads(
        (Path(__file__).resolve().parents[2] / "services/api/log_config.json").read_text()
    )
    dotted = config["formatters"]["json"]["()"]
    module_name, _, class_name = dotted.rpartition(".")

    module = importlib.import_module(module_name)
    assert hasattr(module, class_name), f"{dotted} does not exist"


def test_the_log_config_silences_uvicorns_access_logger() -> None:
    """Belt and braces with `--no-access-log` in the CMD.

    Uvicorn's access log is plain text and would interleave with the JSON
    stream. The flag alone would do it, but a flag is easy to drop while editing
    a CMD, and the app's middleware already logs every request structurally —
    so the config says it too.
    """
    import json
    from pathlib import Path

    config = json.loads(
        (Path(__file__).resolve().parents[2] / "services/api/log_config.json").read_text()
    )
    assert config["loggers"]["uvicorn.access"]["handlers"] == []
