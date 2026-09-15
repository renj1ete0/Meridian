"""Loading the gazetteer into spaCy (task P5-02, spec §5.6).

§5.6 asks for the patterns to be loaded "at worker startup, so gazetteer matches
take precedence over statistical NER", and the precedence is the requirement.
Two placements of the same component look identical in a smoke test — every
curated term still matches text the model has no opinion about — and differ on
exactly the terms the gazetteer exists for, which are the ones the model *does*
have an opinion about and gets wrong.

The pattern-shape tests run only where the ``ner`` extra is installed. What runs
everywhere is the one that matters more often: that none of the fast loop needs
spaCy to be there at all.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys

import pytest

from meridian_core.gazetteer import compile_patterns
from worker.ner import RULER_NAME, SpacyUnavailable, attach_ruler, blank_or_model

HAVE_SPACY = importlib.util.find_spec("spacy") is not None
needs_spacy = pytest.mark.skipif(not HAVE_SPACY, reason="needs the `ner` extra")


@dataclasses.dataclass
class Row:
    term_id: int
    canonical: str
    aliases: list[str] | None = None
    entity_type: str = "agency"
    approved: bool = True
    ambiguous: bool = False


TERMS = [
    Row(1, "Land Transport Authority", ["LTA"]),
    Row(2, "Operational Design Domain", ["ODD"], entity_type="concept"),
]


# --------------------------------------------------------------------------
# The fast loop does not depend on the extra
# --------------------------------------------------------------------------


def test_the_harvest_does_not_pull_spacy_in() -> None:
    # §5.6's auto-harvest is a regex over extracted text and needs no model. If
    # importing it dragged spaCy in, the pass that grows the gazetteer could only
    # run on a machine carrying thinc, blis and a model file — which is the fast
    # loop's whole image, on a Pi, for a dependency nothing in it calls.
    assert "spacy" not in sys.modules or HAVE_SPACY
    module = importlib.import_module("worker.harvest")

    assert not hasattr(module, "spacy")


def test_compiling_patterns_needs_nothing_installed() -> None:
    # The decisions worth testing — what loads, what is withheld, how case is
    # handled — are all in this call, and it is why they are testable at all.
    assert len(compile_patterns(TERMS).patterns) == 4


@pytest.mark.skipif(HAVE_SPACY, reason="spaCy is installed here")
def test_a_missing_spacy_refuses_rather_than_matching_nothing() -> None:
    # A pipeline that silently matched nothing would look exactly like a corpus
    # whose documents mention no known entities, and the difference is a missing
    # package. The message has to name the fix, because the symptom names nothing.
    with pytest.raises(SpacyUnavailable) as raised:
        blank_or_model("blank")

    assert "ner" in str(raised.value)


# --------------------------------------------------------------------------
# Placement and pattern shape, against the real matcher
# --------------------------------------------------------------------------


@needs_spacy
def test_the_ruler_goes_in_front_of_the_statistical_model() -> None:
    import spacy

    nlp = spacy.blank("en")
    nlp.add_pipe("ner")
    attach_ruler(nlp, compile_patterns(TERMS))

    assert nlp.pipe_names.index(RULER_NAME) < nlp.pipe_names.index("ner")


@needs_spacy
def test_attaching_twice_replaces_rather_than_stacks() -> None:
    # A reload that appended would leave a second ruler whose patterns lose every
    # race to the first, so removing a term would appear to do nothing.
    nlp = blank_or_model("blank")
    attach_ruler(nlp, compile_patterns(TERMS))
    attach_ruler(nlp, compile_patterns(TERMS[:1]))

    assert nlp.pipe_names.count(RULER_NAME) == 1


@needs_spacy
def test_a_match_carries_the_row_it_came_from() -> None:
    nlp = blank_or_model("blank")
    attach_ruler(nlp, compile_patterns(TERMS))

    ents = [(e.text, e.label_, e.ent_id_) for e in nlp("The Land Transport Authority said.").ents]

    assert ents == [("Land Transport Authority", "AGENCY", "1")]


@needs_spacy
def test_a_long_form_still_matches_in_lower_case() -> None:
    nlp = blank_or_model("blank")
    attach_ruler(nlp, compile_patterns(TERMS))

    assert [e.label_ for e in nlp("the land transport authority said.").ents] == ["AGENCY"]


@needs_spacy
def test_an_acronym_does_not_match_its_lower_case_homograph() -> None:
    # The case rule, end to end. "ODD" matched case-insensitively would make
    # every occurrence of the ordinary English word a curated, high-precedence
    # entity — and because the ruler overrides the model, there would be no
    # second opinion anywhere in the pipeline.
    nlp = blank_or_model("blank")
    attach_ruler(nlp, compile_patterns(TERMS))

    assert nlp("That was an odd result.").ents == ()
    assert [e.ent_id_ for e in nlp("The ODD is defined per vehicle.").ents] == ["2"]
