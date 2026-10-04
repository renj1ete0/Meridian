"""Other-language names from Wikipedia's interlanguage links (task B-52)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from meridian_core.searchseeds import NEWS_BANG, TopicSeedInput, candidates, plan
from meridian_core.translations import clean_title, parse_langlinks
from worker.translate import title_forms

REPO = Path(__file__).resolve().parents[2]


def page(title: str, links: list[tuple[str, str]], **extra) -> dict:
    return {
        "query": {
            "pages": {
                "1": {
                    "title": title,
                    "langlinks": [{"lang": lang, "*": t} for lang, t in links],
                    **extra,
                }
            }
        }
    }


def test_a_missing_article_gives_nothing() -> None:
    payload = {"query": {"pages": {"-1": {"title": "Nope", "missing": ""}}}}
    assert parse_langlinks(payload, ["de"]) == (None, {})


def test_only_the_asked_languages_are_kept() -> None:
    article, found = parse_langlinks(
        page("Walkability", [("de", "Fußgängerfreundlichkeit"), ("sv", "x")]), ["de"]
    )
    assert (article, found) == ("Walkability", {"de": "Fußgängerfreundlichkeit"})


def test_a_title_identical_to_the_english_one_is_not_a_translation() -> None:
    _, found = parse_langlinks(
        page("Transit-oriented development", [("fr", "Transit-oriented development")]), ["fr"]
    )
    assert found == {}


def test_a_qualifier_in_parentheses_is_dropped() -> None:
    assert clean_title("Peloton (routier)") == "Peloton"
    _, found = parse_langlinks(page("Platoon (automobile)", [("fr", "Peloton (routier)")]), ["fr"])
    assert found == {"fr": "Peloton"}


@pytest.mark.parametrize("title", [None, "", "  ", "(only a qualifier)"])
def test_an_empty_title_is_nothing(title) -> None:
    assert clean_title(title) is None


def test_a_phrase_is_tried_as_written_then_in_sentence_case() -> None:
    assert title_forms("Mass Rapid Transit") == ["Mass Rapid Transit", "Mass rapid transit"]
    assert title_forms("walkability") == ["walkability", "Walkability"]


def test_translations_become_language_and_language_news_queries() -> None:
    topic = TopicSeedInput("walkability", "d e f", (), (("ja", "ウォーカビリティ"),))
    queries = {(q.kind, q.text) for q in candidates(topic)}
    assert ("language", ":ja ウォーカビリティ") in queries
    assert ("language_news", f"{NEWS_BANG} :ja ウォーカビリティ") in queries


def test_every_run_leads_with_a_non_english_query_when_one_exists() -> None:
    topic = TopicSeedInput(
        "walkability", "d e f", ("footpath",), (("de", "Fußgängerfreundlichkeit"),)
    )
    assert "language" in {q.kind for q in plan([topic], already=[], per_topic=5, seed=0)}


def test_the_migration_and_the_config_list_the_same_languages() -> None:
    config = yaml.safe_load((REPO / "config" / "fetch_policy.yaml").read_text())
    path = next((REPO / "migrations" / "versions").glob("*_92cba253bcb9_*.py"))
    spec = importlib.util.spec_from_file_location("mig", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert config["search_languages"] == module.SEARCH_LANGUAGES
