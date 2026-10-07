"""Text Postgres can store (task `B-158`).

Postgres keeps no NUL character in `text` or `jsonb`, and one NUL in a page's extracted text
failed the whole write, losing a page that was fetched. Removed at the two places extracted text
is written: a source's fields and its passages.
"""

from __future__ import annotations

from typing import Any


def storable(value: Any) -> Any:
    """``value`` with NUL characters removed from every string in it, however nested."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {storable(k): storable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(storable(v) for v in value)
    return value
