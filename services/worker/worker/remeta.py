"""Re-read the metadata stored sources declare, and drop what cannot be right (task B-234).

``python -m worker.remeta`` — reports by default, writes only with ``--apply``. For each
source it re-reads the stored raw file (an HTML page's meta tags and JSON-LD; a PDF's
`pdfinfo`) and applies :func:`decide`: a declared value replaces one of unknown or inferred
provenance, a stored value a rule refuses is cleared, and nothing inferred ever replaces
anything. A value it replaces or clears is kept in ``extra['replaced_metadata']``, and the
source of each value it writes in ``extra['<field>_from']``. See
docs/features/extraction.md#source-metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import datetime as dt
import time
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from meridian_core.bibliography import (
    FROM_KEY,
    is_declared,
    judge_author,
    judge_date,
    judge_publisher,
)
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Source
from meridian_core.titles import clean_title
from meridian_core.topiclabels import is_english

from . import rawstore
from .extract.declared import Declared, Found, read_declared
from .extract.pdf import pdf_date, pdf_info
from .relanguage import _stale_passages

log = get_logger(__name__)

#: Sources per transaction.
BATCH = 200
HTML = "text/html"
PDF = "application/pdf"
#: Fields this pass may change.
FIELDS = ("publication_date", "author", "publisher", "language", "title")


@dataclasses.dataclass(frozen=True)
class Stored:
    """A source's metadata as stored, with the provenance its `extra` records."""

    title: str | None
    author: str | None
    publisher: str | None
    publication_date: dt.date | None
    language: str | None
    extra: dict[str, Any]
    url: str = ""
    media_type: str | None = None
    fetched: dt.date | None = None
    first_fetched: dt.date | None = None

    def source_of(self, field: str) -> str | None:
        return self.extra.get(FROM_KEY[field])


@dataclasses.dataclass(frozen=True)
class Change:
    """One field's new value, and why: the source it came from, or the rule that cleared it."""

    field: str
    old: Any
    new: Any
    source: str | None = None
    rule: str | None = None


def decide(
    stored: Stored, declared: Declared | None, refused: collections.Counter | None = None
) -> list[Change]:
    """What to change on one source, given what its raw file declares.

    Title and language are only filled where none is stored: `worker.retitle` and
    `worker.relanguage` own replacing them. A declared date a rule refuses is counted in
    ``refused`` by (field, rule).
    """
    declared = declared or Declared()
    changes: list[Change] = []
    for field in FIELDS:
        old = getattr(stored, field)
        found: Found | None = getattr(declared, field)
        if found is not None and field == "publication_date":
            verdict = judge_date(
                found.value,
                source=found.source,
                fetched=stored.fetched,
                also_fetched=stored.first_fetched,
            )
            if verdict.rule is not None and refused is not None:
                refused[(field, f"{found.source}: {verdict.rule}")] += 1
            found = Found(verdict.value, found.source) if verdict.value is not None else None
        if found is not None and field == "title":
            host = urlsplit(stored.url).hostname
            cleaned = clean_title(found.value, publisher=stored.publisher, host=host)
            found = Found(cleaned, found.source) if cleaned else None

        if field in ("title", "language"):
            if old is None and found is not None:
                changes.append(Change(field, None, found.value, source=found.source))
            continue

        if found is not None:
            if old == found.value:
                if stored.source_of(field) != found.source:
                    changes.append(Change(field, old, old, source=found.source))
                continue
            if old is not None and is_declared(stored.source_of(field)):
                continue  # declared already; another declared reading does not flip it
            changes.append(Change(field, old, found.value, source=found.source))
            continue

        if old is None:
            continue
        rule = _refused(field, old, stored, declared)
        if rule is not None:
            changes.append(Change(field, old, None, rule=rule))
    return changes


def _refused(field: str, value: Any, stored: Stored, declared: Declared) -> str | None:
    """The rule a stored value fails, judged as what it is known to be."""
    if field == "publication_date":
        source = stored.source_of(field)
        if source is None:
            # Stored before provenance was kept: trafilatura's reading of an HTML page,
            # which this pass found nowhere declared; for a PDF, its creation date.
            source = "pdfinfo" if stored.media_type == PDF else "inferred"
        return judge_date(
            value, source=source, fetched=stored.fetched, also_fetched=stored.first_fetched
        ).rule
    if field == "author":
        return judge_author(value).rule
    if field == "publisher":
        return judge_publisher(value, generator=declared.generator).rule
    return None


def declared_in_pdf(fields: dict[str, str]) -> Declared:
    """What a PDF's `pdfinfo` declares, in the shape :func:`decide` reads.

    The date is judged by :func:`decide`, against the days the source was fetched.
    """
    author = judge_author(fields.get("author")).value
    date = pdf_date(fields.get("creationdate"))
    return Declared(
        author=Found(author, "pdfinfo") if author else None,
        publication_date=Found(date, "pdfinfo") if date else None,
    )


@dataclasses.dataclass
class RemetaStats:
    examined: int = 0
    read: int = 0
    changed_sources: int = 0
    relabel: int = 0
    skipped: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: (field, "filled"|"replaced"|"cleared"|"provenance") -> count.
    outcomes: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Declared values a rule refused: (field, "source: rule") -> count.
    refused: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: (field, source or rule) -> count.
    reasons: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Fill before and after, by field: [before, after].
    filled: dict[str, list[int]] = dataclasses.field(
        default_factory=lambda: {f: [0, 0] for f in FIELDS}
    )
    samples: dict[str, list[tuple[str, Any, Any, str]]] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(list)
    )


def _outcome(change: Change) -> str:
    if change.new is None:
        return "cleared"
    if change.old is None:
        return "filled"
    return "provenance" if change.old == change.new else "replaced"


async def _read_declared(source: Source, stored: Stored, root: str | None) -> Declared | str:
    """What the raw file declares, or why it cannot be read."""
    if not source.raw_file_path:
        return "no raw file"
    if stored.media_type not in (HTML, PDF):
        return "not html or pdf"
    try:
        content = await asyncio.to_thread(
            rawstore.resolve(source.raw_file_path, root or source.raw_root).read_bytes
        )
    except rawstore.UnsafeRawPath:
        return "unsafe path"
    except OSError:
        return "file missing"
    if stored.media_type == PDF:
        fields = await pdf_info(content)
        return declared_in_pdf(fields) if fields else "pdfinfo refused"
    return await asyncio.to_thread(read_declared, content.decode("utf-8", "replace"))


def _stored(source: Source) -> Stored:
    extra = dict(source.extra or {})
    return Stored(
        title=source.title,
        author=source.author,
        publisher=source.publisher,
        publication_date=source.publication_date,
        language=source.language,
        extra=extra,
        url=source.url,
        media_type=(extra.get("media_type") or "").split(";")[0].strip() or None,
        fetched=source.accessed_at.date() if source.accessed_at else None,
        first_fetched=source.created_at.date() if source.created_at else None,
    )


def apply_changes(source: Source, changes: list[Change]) -> bool:
    """Write ``changes`` to ``source``; whether its topic labels must be redone."""
    extra = dict(source.extra or {})
    replaced = dict(extra.get("replaced_metadata") or {})
    relabel = False
    for change in changes:
        key = FROM_KEY[change.field]
        if change.old is not None and change.old != change.new:
            old = change.old.isoformat() if isinstance(change.old, dt.date) else change.old
            replaced.setdefault(change.field, old)
        if change.new is None:
            extra.pop(key, None)
        else:
            extra[key] = change.source
        if change.field == "language":
            relabel = is_english(change.new) is False and source.topic_basis is not None
        setattr(source, change.field, change.new)
    if replaced:
        extra["replaced_metadata"] = replaced
    source.extra = extra
    return relabel


async def run_pass(
    *,
    apply: bool,
    session_factory=session,
    root: str | None = None,
    limit: int | None = None,
    start_after: int = 0,
    sample: int = 8,
) -> RemetaStats:
    """Walk stored sources in id order. Commits per batch when applying."""
    stats = RemetaStats()
    after = start_after
    while limit is None or stats.examined < limit:
        async with session_factory() as sess:
            size = BATCH if limit is None else min(BATCH, limit - stats.examined)
            rows = list(
                await sess.scalars(
                    select(Source)
                    .where(Source.source_id > after, Source.retention_tier != "junk")
                    .order_by(Source.source_id)
                    .limit(size)
                )
            )
            if not rows:
                break
            after = rows[-1].source_id
            relabelled: list[int] = []
            for source in rows:
                stats.examined += 1
                stored = _stored(source)
                declared = await _read_declared(source, stored, root)
                if isinstance(declared, str):
                    stats.skipped[declared] += 1
                    declared = None
                else:
                    stats.read += 1
                changes = decide(stored, declared, stats.refused)
                for field in FIELDS:
                    stats.filled[field][0] += getattr(stored, field) is not None
                after_values = {c.field: c.new for c in changes}
                for field in FIELDS:
                    value = after_values.get(field, getattr(stored, field))
                    stats.filled[field][1] += value is not None
                if not changes:
                    continue
                stats.changed_sources += any(c.old != c.new for c in changes)
                for change in changes:
                    outcome = _outcome(change)
                    stats.outcomes[(change.field, outcome)] += 1
                    stats.reasons[(change.field, change.rule or change.source or "")] += 1
                    bucket = stats.samples[f"{change.field} {outcome}"]
                    if outcome != "provenance" and len(bucket) < sample:
                        bucket.append(
                            (source.url, change.old, change.new, change.rule or change.source)
                        )
                if apply and apply_changes(source, changes):
                    relabelled.append(source.source_id)
                    source.topic_basis = None
            stats.relabel += len(relabelled)
            if apply:
                if relabelled:
                    await _stale_passages(sess, relabelled)
                await sess.commit()
        log.info("remeta batch", extra={"after_id": after, "examined": stats.examined})
    return stats


def render(stats: RemetaStats, apply: bool) -> None:
    """Print the report."""
    print("=== Re-read stored source metadata (B-234) ===")
    print(f"  sources examined   {stats.examined}   raw file read {stats.read}")
    print(f"  {'changed' if apply else 'would change':18} {stats.changed_sources}")
    for reason, count in stats.skipped.most_common():
        print(f"  not read: {reason:16} {count}")
    print("\n  field             filled before -> after")
    for field, (before, after) in stats.filled.items():
        n = max(stats.examined, 1)
        print(f"  {field:17} {before:7} ({before / n:6.1%}) -> {after:7} ({after / n:6.1%})")
    print("\n  field             outcome      count")
    for (field, outcome), count in sorted(stats.outcomes.items()):
        print(f"  {field:17} {outcome:12} {count}")
    print("\n  field             source or rule   count")
    for (field, reason), count in sorted(stats.reasons.items()):
        print(f"  {field:17} {reason:16} {count}")
    for (field, why), count in sorted(stats.refused.items()):
        print(f"  {field:17} refused {why:24} {count}")
    for key, rows in sorted(stats.samples.items()):
        print(f"\n  {key}:")
        for url, old, new, why in rows:
            print(f"    {url[:80]}\n      {old!r} -> {new!r}  ({why})")
    if not apply:
        print("\n  Report only. --apply writes; old values go to extra.replaced_metadata.")


def main() -> None:
    """Entry point: ``python -m worker.remeta``."""
    parser = argparse.ArgumentParser(description="Re-read stored source metadata (B-234).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    parser.add_argument("--limit", type=int, help="at most this many sources")
    parser.add_argument("--after", type=int, default=0, help="start after this source id")
    args = parser.parse_args()
    configure_logging("remeta")

    async def go() -> RemetaStats:
        try:
            return await run_pass(apply=args.apply, limit=args.limit, start_after=args.after)
        finally:
            await dispose_engines()

    with bind_run_id(f"remeta-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    render(stats, args.apply)


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
