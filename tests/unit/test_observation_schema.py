"""``ObservationCreate`` must reject at the API boundary what the database
would reject anyway (schemas/graph.py, mirrors models/graph.py ``Observation``).

An agent proposing an observation gets a 422 from these validators before the
insert ever reaches Postgres — the mirrored CHECK constraints
(``ck_observations_has_a_value``, ``ck_observations_period_ordered``) are the
last line of defence, not the first. Without this file, a drift between the
Pydantic validators and the database CHECKs would only surface as a raw
database error surfacing as a 500 (AGENTS.md Testing: "Rejection tests ...
that valid input works is the weaker half").
"""

from __future__ import annotations

import datetime as dt

import pytest
from meridian_core.schemas import ObservationCreate
from pydantic import ValidationError


def _base(**overrides: object) -> dict:
    payload = {
        "subject_entity_id": 1,
        "metric": "av_share_of_taxi_trips",
        "value_numeric": 3.2,
        "supporting_chunk_ids": [1],
    }
    payload.update(overrides)
    return payload


def test_observation_create_accepts_a_valid_payload() -> None:
    """The positive case, so the negative cases below aren't rejecting everything."""
    obs = ObservationCreate(**_base())
    assert obs.value_numeric == 3.2


def test_observation_create_rejects_no_value() -> None:
    """Mirrors ``ck_observations_has_a_value``: a measurement without a value_numeric
    or value_text is not a measurement."""
    with pytest.raises(ValidationError, match="value_numeric or value_text"):
        ObservationCreate(**_base(value_numeric=None, value_text=None))


def test_observation_create_accepts_text_only_value() -> None:
    """The CHECK is an OR, not a requirement for value_numeric specifically."""
    obs = ObservationCreate(**_base(value_numeric=None, value_text="mixed reports"))
    assert obs.value_text == "mixed reports"


def test_observation_create_rejects_inverted_period() -> None:
    """Mirrors ``ck_observations_period_ordered``: period_end before period_start
    describes an interval that never happened."""
    with pytest.raises(ValidationError, match="period_end precedes period_start"):
        ObservationCreate(
            **_base(
                period_start=dt.date(2021, 1, 1),
                period_end=dt.date(2019, 1, 1),
            )
        )


def test_observation_create_accepts_open_ended_period() -> None:
    """Null on either side means open-ended or unknown, not invalid (model docstring)."""
    obs = ObservationCreate(**_base(period_start=dt.date(2019, 1, 1), period_end=None))
    assert obs.period_end is None


def test_observation_create_rejects_empty_supporting_chunk_ids() -> None:
    """An observation without a justifying chunk is not assertable (spec §2
    principle 3) — the same rule ``EdgeCreate`` enforces for edges."""
    with pytest.raises(ValidationError):
        ObservationCreate(**_base(supporting_chunk_ids=[]))


def test_observation_create_rejects_missing_supporting_chunk_ids() -> None:
    with pytest.raises(ValidationError):
        ObservationCreate(**{k: v for k, v in _base().items() if k != "supporting_chunk_ids"})


def test_observation_create_forbids_unknown_fields() -> None:
    """``CreateBase`` forbids extra keys so a model-invented field fails loudly
    rather than being silently dropped (§11.8)."""
    with pytest.raises(ValidationError):
        ObservationCreate(**_base(sample_size=500))
