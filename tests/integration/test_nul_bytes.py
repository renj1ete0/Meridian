"""A NUL byte in extracted text does not cost the page (task B-158).

Postgres stores no NUL in text or JSONB. A PDF whose text layer carried one failed the whole
write in a crawl run, so the page was fetched and lost.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Chunk
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def test_a_page_whose_text_carries_nul_bytes_is_kept(sess) -> None:
    source, _ = await upsert_source(
        sess,
        f"https://z{uuid.uuid4().hex[:10]}.test/a.pdf",
        checksum=f"sha256:{uuid.uuid4().hex}",
        title="Trustworthy\x00 retrieval",
        author="A.\x00 Author",
        extra={"declared_title": "x\x00y", "pages": ["a\x00", 2]},
    )
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text="Ridership\x00 rose.\x00", chunk_index=0)]
    )
    await sess.refresh(source)

    assert source.title == "Trustworthy retrieval" and source.author == "A. Author"
    assert source.extra == {"declared_title": "xy", "pages": ["a", 2]}
    text = await sess.scalar(select(Chunk.text).where(Chunk.source_id == source.source_id))
    assert text == "Ridership rose."
