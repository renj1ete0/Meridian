"""Naming a real build's areas by field of work (tasks B-74, B-157), against Postgres.

The embedder is a fake that puts each label on its own axis, so which field an
area gets is decided by its centroid alone — the thing under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from meridian_core.fields import load_fields
from meridian_core.models import Area, AreaBuild
from meridian_core.models.source import EMBEDDING_DIM
from worker.areas import name_build

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


class AxisEmbedder:
    """Each distinct label text on its own axis; anything else on the last."""

    def __init__(self) -> None:
        self.axes: dict[str, int] = {}

    async def embed(self, texts):
        out = []
        for text in texts:
            axis = self.axes.setdefault(text, len(self.axes))
            v = np.zeros(EMBEDDING_DIM)
            v[axis] = 1.0
            out.append(v.tolist())
        return out


def toward(embedder: AxisEmbedder, text: str) -> list[float]:
    v = np.zeros(EMBEDDING_DIM)
    v[embedder.axes[text]] = 1.0
    v[EMBEDDING_DIM - 1] = 0.2  # near, not identical
    return (v / np.linalg.norm(v)).tolist()


async def a_build(sess, centroids: list[tuple[int, list[float]]]) -> tuple[AreaBuild, list[Area]]:
    build = AreaBuild(passages=10, params={})
    sess.add(build)
    await sess.flush()
    areas = []
    for level, centroid in centroids:
        area = Area(
            build_id=build.build_id,
            level=level,
            terms=["bync", "free article"],
            passages=5,
            sources=3,
            tier_mix={},
            centroid=centroid,
            x=0.0,
            y=0.0,
        )
        sess.add(area)
        areas.append(area)
    await sess.flush()
    return build, areas


async def test_a_build_is_named_from_the_list(sess) -> None:
    fields, subfields = load_fields()
    embedder = AxisEmbedder()
    await embedder.embed([f.text() for f in fields] + [s.text() for s in subfields])
    transport = next(s for s in subfields if s.name == "Transportation")
    social = next(f for f in fields if f.field == transport.field)

    orthogonal = np.zeros(EMBEDDING_DIM)
    orthogonal[EMBEDDING_DIM - 1] = 1.0
    build, (region, area, lost) = await a_build(
        sess,
        [
            (1, toward(embedder, social.text())),
            (2, toward(embedder, transport.text())),
            (2, orthogonal.tolist()),
        ],
    )

    area.parent_id = lost.parent_id = region.area_id
    await sess.flush()

    named = await name_build(sess, build.build_id, embedder)

    # The region is named from its areas (`B-157`): half its passages are Transportation,
    # though its own centroid points at the field above it.
    assert named == 2
    assert (region.field, area.field, lost.field) == ("Transportation", "Transportation", None)


async def test_the_api_reads_the_field_as_the_name(sess) -> None:
    from meridian_core.areaview import area_name

    fields, subfields = load_fields()
    embedder = AxisEmbedder()
    await embedder.embed([f.text() for f in fields] + [s.text() for s in subfields])
    transport = next(s for s in subfields if s.name == "Transportation")
    build, (area,) = await a_build(sess, [(2, toward(embedder, transport.text()))])

    await name_build(sess, build.build_id, embedder)

    assert area_name(area.terms, area.field) == "Transportation"
