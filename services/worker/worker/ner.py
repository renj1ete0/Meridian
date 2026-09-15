"""The gazetteer, loaded into spaCy's ``EntityRuler`` (task P5-02, spec §5.6).

§5.6 asks for this in one sentence — "loaded into spaCy's ``EntityRuler`` at
worker startup, so gazetteer matches take precedence over statistical NER" — and
the precedence is the whole point. A pretrained model resolves "Land Transport
Authority" as an ORG if you are lucky and makes nothing at all of "Electronic
Road Pricing" or "farebox recovery ratio". The ruler is what turns those into
entities on day one.

**Per process, not per machine.** "At worker startup" is the spec's shorthand for
"not per document"; taken literally it would mean a term approved at ten in the
morning does nothing until somebody restarts a container. Every consumer here is
a ``python -m`` pass that exits, so each run reads the table as it stands, and
:func:`build_pipeline` is cached only for the life of the process.

**spaCy is optional.** It is an extra (``meridian-worker[ner]``) rather than a
dependency because the fast loop does not yet run it: `P5-01` is the task that
introduces NER, and until it lands, shipping thinc, blis and a model file into
the image that fetches web pages costs the Pi disk and build minutes for
something nothing calls. The patterns themselves are built by
:mod:`meridian_core.gazetteer`, which has no such dependency, so what loads and
what is withheld is testable without any of it.
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

#: ``blank`` means an English tokenizer and the ruler, with no statistical model
#: at all. Not a degraded mode — on a machine where the curated terms are the
#: ones that matter it is the honest configuration, and it makes the gazetteer
#: usable without downloading a model.
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

    Approved only, filtered in SQL. :func:`compile_patterns` re-checks it, and
    that duplication is deliberate: this is the query the index
    ``ix_gazetteer_approved_type`` exists for, and the re-check is what keeps the
    rule true for callers that build patterns from a list they assembled
    themselves.
    """
    rows = await sess.scalars(
        select(GazetteerTerm).where(GazetteerTerm.approved.is_(True)).order_by(GazetteerTerm.term_id)
    )
    return list(rows)


def attach_ruler(nlp, compiled: CompiledGazetteer):
    """Put the patterns in front of the statistical model.

    ``before="ner"`` when there is an NER component, and appended when there is
    not. Order is the precedence §5.6 asks for: a ruler placed *after* ``ner``
    cannot override it, because spaCy's entity spans do not overlap and the
    first component to claim a span keeps it. The two placements look identical
    in a smoke test — every curated term still matches on text the model has no
    opinion about — and differ on exactly the terms the gazetteer exists for.
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
