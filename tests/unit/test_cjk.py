"""Splitting CJK text for lexical search (task `B-217`, docs/features/search.md#cjk)."""

from __future__ import annotations

import pytest

from meridian_core.cjk import bigrams, has_cjk, query_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("数据保护", "数据 据保 保护"),
        ("中", "中"),
        ("GDPR 第三条", "GDPR 第三 三条"),
        ("개인정보 보호법", "개인 인정 정보 보호 호법"),
        ("plain English", "plain English"),
    ],
)
def test_runs_become_overlapping_pairs(text, expected) -> None:
    assert bigrams(text).split() == expected.split()


def test_only_these_scripts_count() -> None:
    assert has_cjk("ひらがな") and has_cjk("漢字") and has_cjk("한글")
    # Latin with accents, Cyrillic, Arabic and full-width punctuation alone are not.
    assert not any(has_cjk(t) for t in ["réglementation", "закон", "قانون", "。、", ""])


def test_a_run_becomes_a_phrase() -> None:
    assert query_text("数据保护").split() == ['"数据', "据保", '保护"']


def test_a_run_inside_the_readers_quotes_joins_their_phrase() -> None:
    out = query_text('"个人 信息" GDPR')
    assert out.count('"') == 2, out
    assert out.split() == ['"', "个人", "信息", '"', "GDPR"]


def test_runs_outside_quotes_are_phrases_each() -> None:
    assert query_text("数据 保护 law").split() == ['"数据"', '"保护"', "law"]
