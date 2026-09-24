"""A small corpus with three separable subjects, for the area tests (P6-30, P6-31, P6-35).

Each subject is a direction in the embedding space and a vocabulary, so the
clusters and their names are known in advance. Sources are labelled with a
topic unique to the test, which is what scopes a build to these rows.
"""

from __future__ import annotations

import uuid

import numpy as np
from sqlalchemy import select

from meridian_core import areabuild
from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.models import Chunk
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.sources import upsert_source

SUBJECTS = {
    0: "shelter canopy shade walkway cooling",
    1: "enzyme protein folding binding assay",
    2: "tariff export trade quota customs",
}
PER_SOURCE = 4
SOURCES = 6


def vector(subject: int, jitter: int) -> list[float]:
    rng = np.random.default_rng(subject * 1000 + jitter)
    v = np.zeros(EMBEDDING_DIM)
    v[700 + subject * 10 : 700 + subject * 10 + 10] = 1.0
    v += rng.standard_normal(EMBEDDING_DIM) * 0.02
    return (v / np.linalg.norm(v)).tolist()


def shrink_levels(monkeypatch) -> None:
    """Cut a 72-passage corpus into 2 regions, about 3 areas and 6 sub-areas."""
    monkeypatch.setattr(areabuild, "PASSAGES_PER_LEAF", 12)
    monkeypatch.setattr(areabuild, "LEAVES_PER_AREA", 2)
    monkeypatch.setattr(areabuild, "MAX_REGIONS", 2)


def a_topic() -> str:
    return f"areas-{uuid.uuid4().hex[:10]}"


async def seed(
    sess, topic: str, *, sources: int = SOURCES, per_source: int = PER_SOURCE
) -> dict[int, int]:
    """``sources`` sources, each with ``per_source`` passages on every subject.

    Returns ``{chunk_id: subject}``.
    """
    chunk_subject: dict[int, int] = {}
    for s in range(sources):
        source, _ = await upsert_source(
            sess,
            f"https://{topic}.test/{s}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            title=f"Source {s}",
        )
        source.topic_labels = [topic]
        writes, subjects = [], []
        for subject, words in SUBJECTS.items():
            for i in range(per_source):
                writes.append(ChunkWrite(text=f"{words} note {s} {i}", chunk_index=len(writes)))
                subjects.append(subject)
        await replace_chunks(sess, source.source_id, writes)
        rows = list(
            await sess.scalars(
                select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_index)
            )
        )
        await store_embeddings(
            sess,
            {c.chunk_id: vector(subj, c.chunk_id) for c, subj in zip(rows, subjects, strict=True)},
        )
        chunk_subject.update({c.chunk_id: subj for c, subj in zip(rows, subjects, strict=True)})
    await sess.flush()
    return chunk_subject
