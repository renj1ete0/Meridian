"""The gazetteer, loaded into spaCy's ``EntityRuler`` (task P5-02, spec §5.6).

Ruler matches take precedence over statistical NER. The table is read per process, so
each pass sees current approvals. spaCy is the optional ``meridian-worker[ner]`` extra;
patterns come from :mod:`meridian_core.gazetteer`, which does not need it. See
docs/features/places-and-terms.md#ruler.
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.gazetteer import CompiledGazetteer, compile_patterns
from meridian_core.logging import get_logger
from meridian_core.models import GazetteerTerm

log = get_logger(__name__)

#: The component's name in the pipeline. Named so a reload can replace it rather
#: than stacking a second ruler whose patterns silently lose every race.
RULER_NAME = "gazetteer_ruler"

#: ``blank`` means an English tokenizer and the ruler, with no statistical model:
#: an honest configuration where the curated terms are what matter.
DEFAULT_MODEL = os.environ.get("MERIDIAN_SPACY_MODEL", "en_core_web_sm")


class SpacyUnavailable(RuntimeError):
    """spaCy is not installed. Raised rather than degraded to a no-op.

    A pipeline that silently matched nothing would look exactly like a corpus
    whose documents mention no known entities, and the difference is a missing
    package.
    """


def _spacy():
    try:
        import spacy
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the extra
        raise SpacyUnavailable(
            "spaCy is not installed. `uv sync --extra ner`, or set "
            "MERIDIAN_SPACY_MODEL=blank and install spacy alone."
        ) from exc
    return spacy


async def approved_terms(sess: AsyncSession) -> list[GazetteerTerm]:
    """Every row the ruler is allowed to consider.

    Approved only, in SQL; :func:`compile_patterns` re-checks for other callers.
    """
    rows = await sess.scalars(
        select(GazetteerTerm)
        .where(GazetteerTerm.approved.is_(True))
        .order_by(GazetteerTerm.term_id)
    )
    return list(rows)


def attach_ruler(nlp, compiled: CompiledGazetteer):
    """Put the patterns in front of the statistical model.

    ``before="ner"`` when there is an NER component, appended otherwise: a ruler after
    ``ner`` cannot override it.
    """
    if nlp.has_pipe(RULER_NAME):
        nlp.remove_pipe(RULER_NAME)
    config = {"overwrite_ents": True, "validate": True}
    if nlp.has_pipe("ner"):
        ruler = nlp.add_pipe("entity_ruler", name=RULER_NAME, before="ner", config=config)
    else:
        ruler = nlp.add_pipe("entity_ruler", name=RULER_NAME, config=config)
    ruler.add_patterns(list(compiled.patterns))
    return ruler


def blank_or_model(model: str | None = None):
    """Load the pipeline the patterns will hang off."""
    spacy = _spacy()
    name = model or DEFAULT_MODEL
    if name == "blank":
        return spacy.blank("en")
    try:
        return spacy.load(name)
    except OSError as exc:  # pragma: no cover - depends on what is downloaded
        raise SpacyUnavailable(
            f"spaCy model {name!r} is not installed. `python -m spacy download {name}`, "
            "or set MERIDIAN_SPACY_MODEL=blank to run the gazetteer without one."
        ) from exc


async def build_pipeline(sess: AsyncSession, *, model: str | None = None):
    """The whole load: read the table, compile, attach.

    Returns the pipeline. What was *not* loaded is logged rather than discarded —
    a term a curator added and that never matches is the failure nobody reports,
    because nothing breaks and the term is simply absent from the graph.
    """
    compiled = compile_patterns(await approved_terms(sess))
    nlp = blank_or_model(model)
    attach_ruler(nlp, compiled)
    log.info(
        "gazetteer loaded",
        extra={
            "patterns": len(compiled.patterns),
            "withheld": len(compiled.withheld),
            "ambiguous": sum(1 for w in compiled.withheld if w.reason == "ambiguous"),
            "collisions": sum(1 for w in compiled.withheld if w.reason == "collision"),
        },
    )
    return nlp
