"""Lexical search over CJK passages (task `B-217`, docs/features/search.md#cjk).

Measured before: a three-character term taken from inside a Chinese passage found that
passage lexically 10% of the time, Japanese 6%, Korean 30%, because the `english` parser
keeps a whole run of these characters as one token. Each test is scoped to its own
language code, since the dev corpus holds crawled CJK text too.
"""

from __future__ import annotations

import importlib.util
import pathlib
import random
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.dialects import postgresql

from meridian_core import cjk
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Source
from meridian_core.search import SearchFilters, lexical_cjk_statement, search
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

_path = next(
    (pathlib.Path(__file__).resolve().parents[2] / "migrations/versions").glob("*_b217c1a2d3e4_*")
)
_spec = importlib.util.spec_from_file_location("cjk_migration", _path)
MIGRATION = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MIGRATION)

CHINESE = "个人数据保护法规定了数据处理者的义务。"
JAPANESE = "個人情報の保護に関する法律が改正されました。"
KOREAN = "개인정보 보호법은 정보주체의 권리를 규정한다."


@pytest.fixture
def scope() -> str:
    return f"zz-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def passages(session_for, scope):
    sess = await session_for("rw")
    source, _ = await upsert_source(
        sess,
        f"https://c{uuid.uuid4().hex[:12]}.test/a",
        checksum=f"sha256:{uuid.uuid4().hex}",
        language=scope,
        trust_state="cleared",
    )
    texts = [CHINESE, JAPANESE, KOREAN, "An English passage about data protection law."]
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)]
    )
    await sess.commit()
    yield sess
    await sess.rollback()
    await sess.execute(delete(Source).where(Source.language == scope))
    await sess.commit()


async def found(sess, query: str, scope: str) -> list[str]:
    result = await search(sess, query, filters=SearchFilters(languages=[scope]))
    return [h.text for h in result.hits]


@pytest.mark.parametrize(
    ("query", "passage"),
    [
        ("数据保护", CHINESE),
        ("处理者", CHINESE),
        ("個人情報", JAPANESE),
        ("保護", JAPANESE),
        ("보호법", KOREAN),
        ("정보주체", KOREAN),
    ],
)
async def test_a_word_inside_a_run_finds_its_passage(passages, scope, query, passage) -> None:
    assert await found(passages, query, scope) == [passage]


async def test_the_pairs_must_stand_together(passages, scope) -> None:
    """了数, 数据 and 据保 are each in the Chinese passage, but never as 了数据保."""
    assert await found(passages, "了数据保", scope) == []


async def test_the_readers_own_quotes_still_make_a_phrase(passages, scope) -> None:
    assert await found(passages, '"数据 处理者"', scope) == []
    assert await found(passages, '"数据处理者"', scope) == [CHINESE]


async def test_an_english_query_is_unchanged(passages, scope) -> None:
    """The English arm still stems: "protecting" finds "protection"'s passage."""
    assert await found(passages, "protecting", scope) == [
        "An English passage about data protection law."
    ]


async def test_the_arm_reads_the_partial_index(passages) -> None:
    """A bound parameter in the expression or the predicate would not match the index,
    and the arm would scan every passage for each query."""
    stmt = lexical_cjk_statement("数据保护", SearchFilters(), 100)
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    await passages.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(await passages.scalars(text(f"EXPLAIN {sql}")))
    assert MIGRATION.INDEX in plan, plan


def test_the_code_and_the_migration_name_the_same_characters() -> None:
    assert cjk.CJK_CLASS == MIGRATION.CJK_CLASS


_PARTS = ["数据保护", "個人情報の", "개인정보", "a", " ", "GDPR", "2024", "。", "x", "中"]


async def test_python_and_postgres_split_alike(session_for) -> None:
    """The query is split in Python and the passage in SQL; a difference is a miss."""
    pick = random.Random(217)
    samples = ["".join(pick.choices(_PARTS, k=pick.randint(0, 8))) for _ in range(200)]
    sess = await session_for("ro")
    rows = await sess.execute(
        text(
            "SELECT s, to_tsvector('simple', p)::text, "
            "to_tsvector('simple', meridian_cjk_bigrams(s))::text "
            "FROM unnest(CAST(:raw AS text[]), CAST(:py AS text[])) AS t(s, p)"
        ),
        {"raw": samples, "py": [cjk.bigrams(x) for x in samples]},
    )
    differ = [sample for sample, ours, theirs in rows if ours != theirs]
    assert differ == []
