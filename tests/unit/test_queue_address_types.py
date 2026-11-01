"""Every queue task type is classified as an address or not (`B-149`).

`enqueue` records a domain's first sighting only for task types whose text is an address.
A new task type added to the queue's CHECK without a decision here would silently fall on
the "not an address" side, and its domains would never be recorded.
"""

from __future__ import annotations

from meridian_core.models.queue import TASK_TYPE
from meridian_core.queueing import ADDRESS_TASK_TYPES

#: Task types whose text names no host: search words, DOIs before they resolve, and an
#: `upload:<id>` naming a handed-in document (`B-264`).
NOT_ADDRESSES = frozenset({"query", "doi", "upload"})


def test_every_task_type_is_classified() -> None:
    assert set(TASK_TYPE.enums) == ADDRESS_TASK_TYPES | NOT_ADDRESSES


def test_no_task_type_is_both() -> None:
    assert not ADDRESS_TASK_TYPES & NOT_ADDRESSES
