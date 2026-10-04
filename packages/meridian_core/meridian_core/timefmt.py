"""Times as people read them: the deployment's display zone (task `B-145`, ADR 0009).

Instants are stored in UTC (`timestamptz`) and sent as ISO 8601; only text a person reads is
converted, here on the server and in `web/src/lib/time.ts` in the browser. The zone is the
`display_timezone` key of the global fetch-policy row, an IANA name, `Asia/Singapore` unless
changed in Admin. See docs/features/operations.md#display-time-zone.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import FetchPolicy

log = get_logger(__name__)

#: The key in `fetch_policy['*'].settings`; stripped from fetch settings by `policy`.
ZONE_KEY = "display_timezone"

#: Mirrors `config/fetch_policy.yaml`; a drift test holds the two together.
DEFAULT_ZONE = "Asia/Singapore"

GLOBAL_DOMAIN = "*"


def parse_zone(raw: object) -> str:
    """An IANA zone name from the stored value, or the default when it is unusable."""
    if isinstance(raw, str) and raw.strip():
        name = raw.strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("display time zone not recognised; using the default", extra={"zone": name})
        else:
            return name
    return DEFAULT_ZONE


def is_zone(name: str) -> bool:
    """Whether ``name`` is an IANA zone this machine knows."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


async def display_zone(sess: AsyncSession) -> str:
    """The deployment's display zone."""
    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    return parse_zone((row.settings or {}).get(ZONE_KEY) if row else None)


def zone_label(zone: str, at: dt.datetime | None = None) -> str:
    """The zone as a reader says it: `GMT+8`, `GMT+5:30`, `GMT`."""
    moment = (at or dt.datetime.now(dt.UTC)).astimezone(ZoneInfo(zone))
    offset = moment.utcoffset() or dt.timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "GMT"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"GMT{sign}{hours}" + (f":{rest:02d}" if rest else "")


def format_instant(moment: dt.datetime, zone: str, *, date_only: bool = False) -> str:
    """An instant in the display zone, labelled: `2026-10-05 08:00 GMT+8`.

    A naive datetime is refused: an instant without an offset is the bug this module exists
    to keep out.
    """
    if moment.tzinfo is None:
        raise ValueError("a naive datetime has no instant to convert")
    local = moment.astimezone(ZoneInfo(zone))
    if date_only:
        return local.strftime("%Y-%m-%d")
    return f"{local:%Y-%m-%d %H:%M} {zone_label(zone, moment)}"
