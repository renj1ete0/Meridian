"""Pytest plugin: run every test with Python's clock moved forward ``SHIFT_DAYS`` days.

Loaded by ``scripts/clockshift/run.sh`` with ``-p shiftclock``; needs ``time-machine``,
which the script supplies with ``uv run --with``. See docs/reference/commands.md#clock-check.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator

import pytest
import time_machine


@pytest.fixture(autouse=True)
def _shift_clock() -> Iterator[None]:
    """Travel ``SHIFT_DAYS`` days ahead for the test, with the clock still ticking."""
    days = int(os.environ.get("SHIFT_DAYS", "0"))
    with time_machine.travel(dt.datetime.now(dt.UTC) + dt.timedelta(days=days), tick=True):
        yield
