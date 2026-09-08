"""The novelty gate (task P2-03, spec §6.1, §5.4, §12.5).

§6.1 draws one line — ``novelty gate: cosine vs existing vectors; drop if
>0.95`` — and three decisions hide inside it.

**Which of two identical chunks survives.** Compared naively, each is the
other's nearest neighbour, both clear the threshold, and both are dropped: the
corpus loses the text entirely rather than deduplicating it. So a chunk is only
ever compared against chunks written *before* it (``chunk_id <``). Ids are
monotonic, so the first copy to arrive is the one that stays, and the answer
does not depend on which order a batch happened to be read in.

**Nothing is deleted.** §5.4 says a near-duplicate loses its raw file, and
§12.5 wants a novelty pass rate on the daily health line — a gate that deleted
could report neither, and could not be re-run when the threshold moves. So the
verdict is three columns on ``chunks`` and the retention sweep (`P1-31`) is
what spends it. This is the same shape as §2.5's rule for steering: adjust what
is generated, never destroy what was recorded.

**A duplicate points at a survivor, never at another duplicate.** Chunks that
are already marked are excluded from the candidate set, and a verdict that
still lands on one — because both were judged in the same batch — is followed
through to its target. Otherwise ``duplicate_of`` is a chain, and every
consumer has to walk it.

**No model is involved.** The gate needs vectors, not the thing that made them,
so it runs against Postgres alone — on a machine with no 2.3GB download, beside
the embedder or hours behind it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
from collections.abc import Mapping, Sequence

from sqlalchemy import Select, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .logging import get_logger
from .models import Chunk, Source

log = get_logger(__name__)

#: §6.1's number. Above this similarity a chunk is a near-duplicate of one the
#: corpus already has. Tuned by measurement rather than taste — see
#: `MERIDIAN_NOVELTY_THRESHOLD` — but the default is the spec's.
DEFAULT_THRESHOLD = 0.95

#: How much of a source has to be duplicated before the *source* is junk (§5.4).
#: Not 1.0: a page republished across three sites differs in its header, its
#: date line and its boilerplate, so demanding every chunk match would demote
#: almost nothing. Not 0.5 either — half a page of new material is a source.
DEFAULT_SOURCE_FRACTION = 0.9

#: Rows read and written per transaction.
DEFAULT_BATCH = 256


@dataclasses.dataclass(frozen=True)
class NoveltySettings:
    """Deployment rather than code."""

    threshold: float = DEFAULT_THRESHOLD
    source_fraction: float = DEFAULT_SOURCE_FRACTION
    batch_size: int = DEFAULT_BATCH

    def __post_init__(self) -> None:
        # A threshold outside the cosine range is not a strict gate, it is a
        # gate that never fires — and it would look like a clean corpus.
        if not -1.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be a cosine similarity, got {self.threshold}")
        if not 0.0 < self.source_fraction <= 1.0:
            raise ValueError(f"source_fraction must be in (0, 1], got {self.source_fraction}")
        if self.batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {self.batch_size}")

    @classmethod
    def from_env(cls) -> NoveltySettings:
        return cls(
            threshold=_float_env("MERIDIAN_NOVELTY_THRESHOLD", DEFAULT_THRESHOLD),
            source_fraction=_float_env("MERIDIAN_NOVELTY_SOURCE_FRACTION", DEFAULT_SOURCE_FRACTION),
            batch_size=_int_env("MERIDIAN_NOVELTY_BATCH", DEFAULT_BATCH),
        )


@dataclasses.dataclass(frozen=True)
class Neighbour:
    """The closest earlier chunk, and how close it was."""

    chunk_id: int
    similarity: float


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What the gate decided about one chunk."""

    chunk_id: int
    #: NULL-able all the way to the column: an empty corpus has no neighbour to
    #: report and must not be recorded as "similarity zero".
    nearest_similarity: float | None
    duplicate_of: int | None

    @property
    def novel(self) -> bool:
        return self.duplicate_of is None


@dataclasses.dataclass(frozen=True)
class NoveltyHealth:
    """§12.5's novelty pass rate, and the backlog behind it."""

    judged: int
    duplicates: int
    pending: int

    @property
    def pass_rate(self) -> float | None:
        """Fraction of judged chunks that were novel. None before anything is judged."""
        if not self.judged:
            return None
        return round((self.judged - self.duplicates) / self.judged, 4)

    def as_dict(self) -> dict[str, object]:
        return {
            "novelty_judged": self.judged,
            "novelty_duplicates": self.duplicates,
            "novelty_pending": self.pending,
            "novelty_pass_rate": self.pass_rate,
        }


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


def _pending() -> Select:
    """Embedded and not yet judged, in id order."""
    return (
        select(Chunk.chunk_id, Chunk.source_id)
        .where(Chunk.embedding.is_not(None), Chunk.novelty_checked_at.is_(None))
        .order_by(Chunk.chunk_id)
    )


async def chunks_awaiting_novelty(
    sess: AsyncSession, *, limit: int = DEFAULT_BATCH, after_id: int = 0
) -> list[tuple[int, int]]:
    """The next ``(chunk_id, source_id)`` batch to judge.

    Paged by id rather than by OFFSET for the reason the embedding backfill is:
    the crawl writes new chunks underneath a long pass, and an offset page would
    both re-scan what it has read and skip what shifted past it.

    ``embedding IS NOT NULL`` is half the predicate because the gate compares
    vectors and a chunk without one cannot be compared — it is not novel, it is
    unjudgeable, and it waits for the embedder rather than being marked.
    """
    rows = await sess.execute(_pending().where(Chunk.chunk_id > after_id).limit(limit))
    return [(chunk_id, source_id) for chunk_id, source_id in rows]


async def novelty_backlog(sess: AsyncSession) -> int:
    """How many embedded chunks are still unjudged."""
    return await sess.scalar(select(func.count()).select_from(_pending().subquery())) or 0


async def novelty_health(sess: AsyncSession) -> NoveltyHealth:
    """The three numbers §12.5's health line needs, in one round trip.

    A pass rate that collapses means the crawl has found a mirror, a paginated
    view of one document, or a site that serves the same boilerplate under
    every URL — all of which look like a healthy crawl from every other number
    on the line.
    """
    row = (
        await sess.execute(
            select(
                func.count().filter(Chunk.novelty_checked_at.is_not(None)),
                func.count().filter(Chunk.duplicate_of.is_not(None)),
                func.count().filter(
                    Chunk.embedding.is_not(None), Chunk.novelty_checked_at.is_(None)
                ),
            ).select_from(Chunk)
        )
    ).one()
    return NoveltyHealth(judged=row[0], duplicates=row[1], pending=row[2])


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------


async def nearest_earlier_neighbours(
    sess: AsyncSession, chunk_ids: Sequence[int]
) -> dict[int, Neighbour]:
    """For each id, the closest chunk written before it. Missing = nothing to compare.

    One statement for the whole batch, through a LATERAL join, rather than a
    query per chunk: the round trips dominate at batch sizes that make the scan
    worth doing at all.

    Two filters on the candidate side carry the design:

    - ``chunk_id <`` is what makes the *first* copy the survivor, and what stops
      two identical chunks from deleting each other (see the module docstring).
    - ``duplicate_of IS NULL`` keeps a verdict pointing at something that is
      still in the corpus, rather than at another duplicate.

    Note for `P2-04`: the ``chunk_id <`` predicate is applied *after* the
    HNSW scan, so an index will speed this up without making it exact. That is
    acceptable — a missed near-duplicate is a chunk that stays, not one that is
    wrongly dropped.
    """
    if not chunk_ids:
        return {}

    candidate = aliased(Chunk, name="candidate")
    subject = aliased(Chunk, name="subject")
    distance = candidate.embedding.cosine_distance(subject.embedding)

    nearest = (
        select(candidate.chunk_id.label("neighbour_id"), distance.label("distance"))
        .where(
            candidate.embedding.is_not(None),
            candidate.duplicate_of.is_(None),
            candidate.chunk_id < subject.chunk_id,
        )
        .order_by(distance)
        .limit(1)
        .lateral("nearest")
    )

    rows = await sess.execute(
        select(subject.chunk_id, nearest.c.neighbour_id, nearest.c.distance)
        .select_from(subject)
        .outerjoin(nearest, true())
        .where(subject.chunk_id.in_(list(chunk_ids)))
    )
    return {
        chunk_id: Neighbour(chunk_id=neighbour_id, similarity=1.0 - float(distance))
        for chunk_id, neighbour_id, distance in rows
        if neighbour_id is not None
    }


def judge(
    chunk_ids: Sequence[int],
    neighbours: Mapping[int, Neighbour],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Verdict]:
    """Turn nearest neighbours into verdicts. Pure — no database, no clock.

    Strictly greater than the threshold, as §6.1 writes it: a chunk sitting
    exactly on the line is kept, because the cheap error is keeping a duplicate
    and the expensive one is dropping the only copy of something.

    Chains are resolved here rather than in SQL. Two chunks judged in the same
    batch can both be duplicates — B of A, A of some older Z — and the
    candidate filter cannot see a verdict that has not been written yet. So a
    target that this batch is itself marking is followed through to whatever it
    points at.
    """
    marked: dict[int, int] = {}
    verdicts: list[Verdict] = []

    for chunk_id in chunk_ids:
        neighbour = neighbours.get(chunk_id)
        if neighbour is None:
            verdicts.append(Verdict(chunk_id, nearest_similarity=None, duplicate_of=None))
            continue
        if neighbour.similarity <= threshold:
            verdicts.append(
                Verdict(chunk_id, nearest_similarity=neighbour.similarity, duplicate_of=None)
            )
            continue

        target = neighbour.chunk_id
        # Bounded by the number of ids in this batch, and every hop strictly
        # decreases the id, so this cannot cycle however the batch is ordered.
        seen = 0
        while target in marked and seen <= len(chunk_ids):
            target = marked[target]
            seen += 1
        marked[chunk_id] = target
        verdicts.append(
            Verdict(chunk_id, nearest_similarity=neighbour.similarity, duplicate_of=target)
        )

    return verdicts


# --------------------------------------------------------------------------
# Recording it
# --------------------------------------------------------------------------


async def record_verdicts(
    sess: AsyncSession, verdicts: Sequence[Verdict], *, now: dt.datetime | None = None
) -> int:
    """Write the gate's decisions. Returns how many landed. Flushes, does not commit.

    Only touches rows that are still unjudged, so a second pass racing the first
    over the same batch cannot overwrite a verdict — and cannot re-time one,
    which would make ``novelty_checked_at`` say the corpus was bigger than it
    was when the call was made.
    """
    if not verdicts:
        return 0

    checked_at = now or dt.datetime.now(dt.UTC)
    written = 0
    # One statement per verdict rather than one UPDATE ... FROM (VALUES ...)
    # for the batch. The batched form is faster and returns a rowcount that
    # cannot say *which* rows it skipped; this pass is offline and bounded by
    # the vector scan beside it, so the readable version wins until a profile
    # says otherwise.
    for verdict in verdicts:
        result = await sess.execute(
            update(Chunk)
            .where(Chunk.chunk_id == verdict.chunk_id, Chunk.novelty_checked_at.is_(None))
            .values(
                novelty_checked_at=checked_at,
                nearest_similarity=verdict.nearest_similarity,
                duplicate_of=verdict.duplicate_of,
            )
        )
        written += result.rowcount or 0

    await sess.flush()
    return written


async def demote_duplicate_sources(
    sess: AsyncSession,
    source_ids: Sequence[int],
    *,
    fraction: float = DEFAULT_SOURCE_FRACTION,
) -> list[int]:
    """Mark fully-judged, mostly-duplicate sources ``junk`` (§5.4). Returns the ids.

    The gate's only source-level act, and the input to `P1-31`'s sweep: §5.4
    keeps a raw file for primary sources, extracted text for background ones,
    and nothing for junk. Nothing is deleted here — the tier is the decision and
    the sweep is the deletion.

    **A primary source is never demoted**, whatever its chunks say. §5.4 keeps
    the raw file for government documents and papers precisely because link rot
    makes them unrecoverable, and a mirror that happens to be crawled second is
    still the citable copy of a real document. The asymmetry is deliberate: the
    cost of keeping a duplicate government PDF is a few megabytes, and the cost
    of dropping the only local copy of a page that has since been reorganised
    away is a citation that can never be checked again.

    A source is only considered once every chunk it has is judged. Demoting on a
    partial view would junk a long document because its first page happened to
    be boilerplate.
    """
    if not source_ids:
        return []

    rows = await sess.execute(
        select(
            Chunk.source_id,
            func.count(),
            func.count().filter(Chunk.duplicate_of.is_not(None)),
            func.count().filter(or_(Chunk.embedding.is_(None), Chunk.novelty_checked_at.is_(None))),
        )
        .where(Chunk.source_id.in_(list(source_ids)))
        .group_by(Chunk.source_id)
    )

    candidates = [
        source_id
        for source_id, total, duplicates, unjudged in rows
        if total and not unjudged and duplicates / total >= fraction
    ]
    if not candidates:
        return []

    demoted = await sess.execute(
        update(Source)
        .where(Source.source_id.in_(candidates), Source.retention_tier == "background")
        .values(retention_tier="junk")
        .returning(Source.source_id)
    )
    ids = sorted(demoted.scalars())
    await sess.flush()
    if ids:
        log.info("sources demoted to junk by the novelty gate", extra={"sources": ids})
    return ids


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
