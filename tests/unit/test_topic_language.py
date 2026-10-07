"""Non-English scores are read as their English version's would be (`B-53`, ADR 0014).

Translation pulls topic scores toward a point, keeping their order, so a page that is on
a topic in English can miss the floor in translation. :func:`comparable` stretches a
non-English page's scores back out; these tests hold it to the shape that was measured.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import numpy as np
import pytest

from meridian_core.topiclabels import (
    LABEL_FLOOR,
    LANGUAGE_PIVOT,
    LANGUAGE_STRETCH,
    OFFTOPIC_FLOOR,
    Basis,
    Prototype,
    basis_fingerprint,
    comparable,
    decide,
    is_english,
    is_offtopic,
)

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("language", ["en", "EN", "en-GB", "en_US", None, "", "  "])
def test_english_and_unknown_are_unchanged(language: str | None) -> None:
    scores = {"a": 0.47, "b": 0.12}
    assert comparable(scores, language) == scores


@pytest.mark.parametrize(("code", "english"), [("en", True), ("en-AU", True), ("de", False)])
def test_language_codes_are_read_by_their_base(code: str, english: bool) -> None:
    assert is_english(code) is english


def test_unknown_is_not_a_language() -> None:
    assert is_english(None) is None and is_english("") is None


def test_the_pivot_is_a_fixed_point() -> None:
    assert comparable({"a": LANGUAGE_PIVOT}, "de")["a"] == pytest.approx(LANGUAGE_PIVOT)


def test_the_stretch_keeps_order_and_moves_away_from_the_pivot() -> None:
    raw = np.linspace(-0.2, 0.9, 50)
    out = [comparable({"t": float(s)}, "de")["t"] for s in raw]
    assert all(b >= a for a, b in zip(out, out[1:], strict=False))
    for s, o in zip(raw, out, strict=True):
        assert abs(o - LANGUAGE_PIVOT) >= abs(s - LANGUAGE_PIVOT) - 1e-12 or o == 1.0


def test_a_score_is_never_above_one() -> None:
    assert comparable({"a": 0.99}, "de")["a"] == 1.0


def test_a_translation_of_a_labelled_page_is_labelled_again() -> None:
    """The measured case: an English page at 0.52 lands around 0.47 in translation."""
    assert decide({"a": 0.47}) == []
    assert decide(comparable({"a": 0.47}, "de")) == ["a"]


def test_an_off_topic_translation_stays_off_topic() -> None:
    """Off-topic pages lost nothing in translation, so nothing lifts them over a floor."""
    raw = {"a": OFFTOPIC_FLOOR - 0.05}
    assert is_offtopic(comparable(raw, "de"))


def test_the_stretch_is_modest() -> None:
    """A large stretch would turn noise into labels; the fitted one is near the measured gap."""
    assert 1.0 < LANGUAGE_STRETCH <= 1.5
    assert 0.0 <= LANGUAGE_PIVOT < OFFTOPIC_FLOOR < LABEL_FLOOR


def test_the_constants_are_part_of_the_basis(monkeypatch) -> None:
    """Changing either relabels every source, as any change to how a label is decided must."""
    prototypes = [Prototype("a", "a")]
    before = basis_fingerprint(prototypes, "m")
    monkeypatch.setattr("meridian_core.topiclabels.LANGUAGE_STRETCH", LANGUAGE_STRETCH + 0.1)
    assert basis_fingerprint(prototypes, "m") != before


def test_scores_need_a_language() -> None:
    """Keyword-only with no default, so no caller can score without saying the language."""
    parameter = inspect.signature(Basis.scores).parameters["language"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_basis_scores_apply_the_stretch() -> None:
    reference = np.zeros(4)
    basis = Basis.build({"a": [1.0, 0.0, 0.0, 0.0]}, [reference.tolist()], "fp")
    vector = [0.47, (1 - 0.47**2) ** 0.5, 0.0, 0.0]
    assert basis.scores(vector, language="en")["a"] == pytest.approx(0.47, abs=1e-4)
    assert basis.scores(vector, language="de")["a"] == pytest.approx(
        comparable({"a": 0.47}, "de")["a"], abs=1e-4
    )


def test_every_scoring_call_names_a_language() -> None:
    """Every `.scores(` call in the code passes ``language=``; one that did not would raise."""
    missing = []
    for path in [*REPO.glob("packages/**/*.py"), *REPO.glob("services/**/*.py")]:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "scores"
                and not any(k.arg == "language" for k in node.keywords)
            ):
                missing.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not missing
