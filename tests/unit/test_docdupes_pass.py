"""The daily duplicate pass stays within Postgres's parameter limit (`B-84`).

It bound every non-junk source id in one IN list and failed every day once the
corpus passed 32,767 such sources. These hold the two statements that touch
many ids to a bound count that does not grow with the corpus.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from worker.docdupes import MAX_BOUND_IDS, batches, live_passages

#: Postgres's hard ceiling on bind parameters in one statement.
PG_MAX_PARAMS = 32767


def test_reading_passages_binds_no_source_ids():
    compiled = live_passages().compile(dialect=postgresql.asyncpg.dialect())
    # The junk filter is the only value; nothing scales with the corpus.
    assert len(compiled.params) <= 2
    assert " IN " not in str(compiled).upper()


def test_a_batch_stays_under_the_postgres_limit():
    assert MAX_BOUND_IDS < PG_MAX_PARAMS


@pytest.mark.parametrize("n", [0, 1, MAX_BOUND_IDS, MAX_BOUND_IDS + 1, 40_000])
def test_batches_cover_every_id_once_in_order(n):
    ids = list(range(n))
    parts = batches(ids)
    assert [i for part in parts for i in part] == ids
    assert all(0 < len(part) <= MAX_BOUND_IDS for part in parts)


@pytest.mark.parametrize("size", [0, -1])
def test_an_empty_batch_size_is_refused(size):
    with pytest.raises(ValueError):
        batches([1, 2, 3], size)
