"""Writing figures (task P1-10, spec §6.6).

Against a real Postgres because replacement is the subject and it is a
constraint question: a re-crawl has to leave a source's figures describing the
content it now has, and a cascade has to take them when the source goes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.figures import FigureWrite, delete_figures, figure_count, replace_figures
from meridian_core.models import Figure, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def url() -> str:
    return f"https://f{uuid.uuid4().hex[:12]}.test/a"


@pytest.fixture
async def cleanup(session_for, url):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.url == url))
    await sess.commit()


async def a_source(sess, url: str) -> Source:
    source, _ = await upsert_source(sess, url, checksum=f"sha256:{uuid.uuid4().hex}")
    await sess.flush()
    return source


async def figures_for(sess, source_id: int) -> list[Figure]:
    rows = await sess.execute(
        select(Figure).where(Figure.source_id == source_id).order_by(Figure.figure_id)
    )
    return list(rows.scalars())


async def test_figures_are_written_with_everything_extraction_knew(
    session_for, url, cleanup
) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)

    written, deleted = await replace_figures(
        sess,
        source.source_id,
        [
            FigureWrite(
                caption="Figure 1: modal share",
                alt_text="A stacked bar chart",
                image_url="https://example.test/chart.png",
                page=4,
            )
        ],
    )

    assert (written, deleted) == (1, 0)
    figure = (await figures_for(sess, source.source_id))[0]
    assert figure.caption == "Figure 1: modal share"
    assert figure.image_url == "https://example.test/chart.png"
    assert figure.page == 4
    # Deferred enrichment (§6.6) — never filled at ingestion.
    assert figure.vlm_description is None
    assert figure.ocr_text is None
    assert figure.file_path is None, "nothing downloads images; a path here would be a lie"


async def test_a_recrawl_replaces_rather_than_accumulates(session_for, url, cleanup) -> None:
    """A caption describing a diagram the page no longer contains is worse than
    no figure at all: it is a citation that resolves to the wrong thing.

    Replaced wholesale rather than reconciled, because figures have no stable
    identity across a re-crawl — no id in the markup, captions get edited,
    positions move. Matching them up would be guesswork dressed as bookkeeping.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_figures(sess, source.source_id, [FigureWrite(caption="Old figure")])

    written, deleted = await replace_figures(
        sess, source.source_id, [FigureWrite(caption="New figure")]
    )

    assert (written, deleted) == (1, 1)
    captions = [f.caption for f in await figures_for(sess, source.source_id)]
    assert captions == ["New figure"]


async def test_a_page_that_lost_its_figures_keeps_none(session_for, url, cleanup) -> None:
    """The empty case, which a replacement that only ever inserted would get
    wrong — and which looks exactly like a working one until a page drops a
    diagram."""
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_figures(sess, source.source_id, [FigureWrite(caption="Figure 1: a chart")])

    written, deleted = await replace_figures(sess, source.source_id, [])

    assert (written, deleted) == (0, 1)
    assert await figure_count(sess, source.source_id) == 0


async def test_figures_go_when_their_source_does(session_for, url, cleanup) -> None:
    """`ON DELETE CASCADE`. A figure whose source is gone cites nothing."""
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_figures(sess, source.source_id, [FigureWrite(caption="Figure 1: a chart")])
    source_id = source.source_id

    await sess.execute(delete(Source).where(Source.source_id == source_id))
    await sess.flush()

    assert await figure_count(sess, source_id) == 0


async def test_one_sources_figures_are_not_another_s(session_for, url, cleanup) -> None:
    """A replacement scoped too widely would silently delete a neighbour's
    figures, and look like a working replacement while doing it."""
    sess = await session_for("rw")
    first = await a_source(sess, url)
    second = await a_source(sess, f"{url}-other")
    await replace_figures(sess, first.source_id, [FigureWrite(caption="Figure 1: mine")])
    await replace_figures(sess, second.source_id, [FigureWrite(caption="Figure 1: theirs")])

    await replace_figures(sess, first.source_id, [])

    assert await figure_count(sess, second.source_id) == 1
    await sess.execute(delete(Source).where(Source.source_id == second.source_id))
    await sess.commit()


async def test_deleting_figures_for_a_source_with_none_is_not_an_error(
    session_for, url, cleanup
) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)

    assert await delete_figures(sess, source.source_id) == 0
