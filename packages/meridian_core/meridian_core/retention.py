"""The retention sweep (task P1-31, spec §5.4).

Three verdicts: ``droppable`` and ``orphaned`` files are deleted with ``--apply``;
``dangling`` rows (a file missing) are only reported. A primary file is never swept,
checked when the plan is built and again when it is applied. See
docs/features/source-quality.md#the-retention-sweep.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chunks
from .logging import get_logger
from .models import Source

log = get_logger(__name__)

#: The one tier whose files are never touched (§5.4).
PROTECTED_TIER = "primary"


@dataclasses.dataclass(frozen=True)
class DropCandidate:
    """A file the policy says should not be kept, and why."""

    path: str
    size: int
    reason: str
    source_id: int | None = None


@dataclasses.dataclass(frozen=True)
class Dangling:
    """A source whose raw file is absent from the root being swept.

    A row recording a *different* ``raw_root`` is reported apart (`P1-45`); one with
    no root predates the column and cannot be told apart from a loss.
    """

    source_id: int
    url: str
    path: str
    raw_root: str | None = None


@dataclasses.dataclass(frozen=True)
class RetentionPlan:
    droppable: tuple[DropCandidate, ...] = ()
    orphaned: tuple[DropCandidate, ...] = ()
    #: Rows whose file is absent and whose recorded root is this one, or unknown.
    dangling: tuple[Dangling, ...] = ()
    #: Rows whose file lives under a *different* recorded root: not a loss.
    elsewhere: tuple[Dangling, ...] = ()
    protected: int = 0
    files_seen: int = 0

    @property
    def reclaimable_bytes(self) -> int:
        return sum(c.size for c in (*self.droppable, *self.orphaned))

    @property
    def is_empty(self) -> bool:
        return not (self.droppable or self.orphaned)


async def cited_source_ids(sess: AsyncSession) -> set[int]:
    """Sources with at least one cited chunk (§5.4's "snapshot if cited").

    Delegates to `chunks.cited_source_ids`, which covers every table with
    provenance (`B-46`). This one asked only about edges, so a raw file whose
    only citation was an entity's could be swept.
    """
    return await chunks.cited_source_ids(sess)


def _walk(root: Path) -> Iterable[tuple[str, int]]:
    for path in root.rglob("*"):
        if path.is_file():
            yield str(path.relative_to(root)), path.stat().st_size


async def plan_sweep(sess: AsyncSession, raw_root: str | os.PathLike[str]) -> RetentionPlan:
    """What a sweep of ``raw_root`` would do. Reads only."""
    root = Path(raw_root)
    if not root.is_dir():
        log.warning("retention: no raw store to sweep", extra={"raw_root": str(root)})
        return RetentionPlan()

    rows = (
        await sess.execute(
            select(
                Source.source_id,
                Source.url,
                Source.raw_file_path,
                Source.retention_tier,
                Source.raw_root,
            ).where(Source.raw_file_path.is_not(None))
        )
    ).all()
    by_path = {row.raw_file_path: row for row in rows}
    cited = await cited_source_ids(sess)

    droppable: list[DropCandidate] = []
    orphaned: list[DropCandidate] = []
    protected = 0
    seen = 0

    for relative, size in _walk(root):
        seen += 1
        row = by_path.get(relative)
        if row is None:
            orphaned.append(DropCandidate(relative, size, "no source row refers to this file"))
            continue
        if row.retention_tier == PROTECTED_TIER:
            protected += 1
            continue
        if row.source_id in cited:
            # §5.4: background keeps a snapshot *if cited*. A cited source whose
            # file is deleted turns a checkable citation into a dead one, which
            # is the specific failure raw retention exists to prevent.
            protected += 1
            continue
        droppable.append(
            DropCandidate(relative, size, f"retention_tier={row.retention_tier}", row.source_id)
        )

    on_disk = {relative for relative, _ in _walk(root)}
    absent = [
        Dangling(row.source_id, row.url, row.raw_file_path, row.raw_root)
        for row in rows
        if row.raw_file_path not in on_disk
    ]

    # The `P1-45` split: a row naming a different store is not missing its file.
    # See docs/features/source-quality.md#the-retention-sweep.
    here = str(root)
    elsewhere = tuple(d for d in absent if d.raw_root is not None and d.raw_root != here)
    dangling = tuple(d for d in absent if d not in elsewhere)

    return RetentionPlan(
        droppable=tuple(droppable),
        orphaned=tuple(orphaned),
        dangling=dangling,
        elsewhere=elsewhere,
        protected=protected,
        files_seen=seen,
    )


def apply_sweep(
    plan: RetentionPlan, raw_root: str | os.PathLike[str], *, dry_run: bool = True
) -> int:
    """Delete what the plan says to delete. Returns bytes reclaimed.

    ``dry_run`` defaults to True: this destroys what a re-crawl cannot reproduce. The
    protected-tier check is repeated here rather than trusted from :func:`plan_sweep`,
    since a plan is data a caller can construct.
    """
    root = Path(raw_root)
    reclaimed = 0

    for candidate in (*plan.droppable, *plan.orphaned):
        if candidate.reason.endswith(PROTECTED_TIER):
            raise ValueError(f"refusing to sweep a {PROTECTED_TIER} file: {candidate.path}")
        target = root / candidate.path
        if dry_run:
            reclaimed += candidate.size
            continue
        try:
            target.unlink()
        except FileNotFoundError:
            # Swept twice, or removed underneath us. Not an error: the intended
            # end state is that the file is absent, and it is.
            continue
        reclaimed += candidate.size
        log.info(
            "retention: dropped raw file",
            extra={"path": candidate.path, "bytes": candidate.size, "reason": candidate.reason},
        )

    return reclaimed
