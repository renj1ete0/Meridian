"""The retention sweep (task P1-31, spec §5.4).

§5.4 splits raw retention three ways — primary keeps the file, background keeps
metadata and a snapshot *if cited*, junk and near-duplicates are dropped after
the novelty gate — and until `P2-03` there was no gate, so there was nothing to
act on. The gate exists now. This is what spends its verdict.

**It reconciles rather than deletes.** Measuring the dev corpus before writing
this changed what it is: there were no junk files to reclaim and no orphans, and
three sources whose ``raw_file_path`` pointed at nothing. The reason for the
first is structural — `P1-11` never *writes* the files §5.4 says to drop, and
`rawstore.retention_for` only ever moves a tier up, so a file that exists was
written under a tier that keeps files and cannot have been demoted beneath it.
So the sweep's reclaim arm is correct, necessary, and currently a no-op by
construction; saying that out loud is more useful than a report implying work
was done.

The dangling arm is the one that found something, and it is the one that
matters, because a source claiming a file it does not have is a citation that
will not open. Nothing else in the system notices: extraction already happened,
the chunks are real, and the row looks complete from every angle except the
disk.

**Three verdicts, and only one of them deletes:**

``droppable``
    A file whose source says it should not be kept. Deleted with ``--apply``.

``orphaned``
    A file no source row points at. Deleted with ``--apply``. These are real:
    ``path_for`` includes the media type's extension, so a URL that was served
    as HTML and is later served as a PDF gets a *different* path, and the old
    file is left behind with nothing referring to it.

``dangling``
    A row whose file is missing. **Never deleted** — the row is the only record
    that the fetch happened, and the text extracted from it is still in the
    corpus. Reported, and that is all.

**Primary is never swept.** Enforced when the plan is built and asserted again
when it is applied. §5.4 makes link rot the binding reason for raw retention —
government URLs reorganise constantly — so a primary file is frequently the only
remaining copy of what a citation refers to, and deleting one is not recoverable
by re-crawling.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

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

    ``raw_root`` is why this is two situations rather than one (`P1-45`). A row
    that records a *different* root is not missing a file — it is a file this
    sweep is not looking at. A row that records no root at all was written
    before the column existed and genuinely cannot be told apart from a loss.
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
    #: Rows whose file is absent because it lives under a *different* recorded
    #: root. Not a problem, and separated so that it stops being reported as
    #: one — a multi-root corpus otherwise produces a dangling list long enough
    #: that a real loss inside it would never be noticed.
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
    """Sources with at least one chunk cited by an edge (§5.4's "snapshot if cited").

    Empty until the graph exists, which makes every background file droppable in
    the meantime — correct per §5.4, and harmless only because none are written.
    The query is here rather than deferred because the day edges appear is the
    day a sweep without it starts deleting evidence, and that is not a day
    anyone will connect to this function.
    """
    rows = await sess.execute(
        text(
            "SELECT DISTINCT c.source_id FROM chunks c "
            "WHERE EXISTS (SELECT 1 FROM edges e WHERE c.chunk_id = ANY(e.supporting_chunk_ids))"
        )
    )
    return {row[0] for row in rows}


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

    # The split `P1-45` exists for. A row that names a different store is not
    # missing its file; it is a file this sweep is not looking at. Folding the
    # two together is what made a multi-root corpus report a dangling list long
    # enough to hide a real loss inside.
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

    ``dry_run`` defaults to True because this is the only operation in the
    system that destroys something a re-crawl cannot reproduce: the web moves
    on, and a page fetched last month is not re-fetchable, only re-visitable.

    The protected-tier check is repeated here rather than trusted from
    :func:`plan_sweep`. A plan is data and can be constructed, filtered or
    replayed by a caller that did not build it, and "never delete a primary
    file" is worth costing a second lookup.
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
