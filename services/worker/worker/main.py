"""The worker loop: claim, fetch, record, repeat (task P1-15, spec §6.1, §13.4).

Everything this module composes already existed and was unused. `queueing.py`
knows how to hand out a task without handing it out twice; `Crawler.fetch` knows
how to get one URL politely and leave a record of it; `attempts.py` knows what
the last day of crawling looked like. What was missing is the thing that runs
them without anybody watching, which is the only mode this system is ever in.

**Lanes, not a loop.** Concurrency is N independent claim-fetch-settle lanes over
one shared `Crawler`. There is no dispatcher and no in-process queue: the
database is the queue, `FOR UPDATE SKIP LOCKED` is the dispatcher, and two lanes
that both go looking at the same moment get different rows. That also means the
unit of concurrency is the same whether it is four lanes in one process or two
processes of two, so scaling out later needs no coordination to be invented.

**Politeness is not the lane's business.** A lane claims whatever is next, which
may be the fourth URL in a row from one domain. `DomainLimiter` is what stops
that becoming four simultaneous requests to one host — per-domain, shared across
lanes, and already enforced inside `Crawler.fetch`. A loop that tried to schedule
around domains itself would be duplicating that, badly.

**Nothing here raises to the top.** A worker that dies on an unexpected exception
is a worker that stopped crawling on Saturday and gets noticed on Monday. Every
lane catches, logs, settles the task it was holding, and goes back for the next
one; a database that has gone away backs the lane off rather than ending it,
because the outage that matters is the one that outlasts the retry. What is
*not* caught is cancellation — that is the shutdown path, and swallowing it
would turn `SIGTERM` into a process that has to be killed.

**Shutdown is graceful once and immediate twice.** The first signal stops the
lanes claiming and lets the fetches in flight finish, so a task is never
abandoned mid-request. Held leases are dropped on the way out rather than left
to expire, because fifteen minutes of a queue that looks busy and is doing
nothing on every deploy is a bad way to learn about lease expiry. A second
signal cancels, for the case where a fetch is wedged and the operator has
stopped being patient.

Restart supervision itself lives outside the process: `Restart=always` in the
systemd unit (§13.4). Nothing in here tries to be its own supervisor.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import signal
import socket
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.attempts import DEFAULT_RETENTION_DAYS, fetch_health, prune_attempts
from meridian_core.chunks import as_writes, chunk_count, replace_chunks
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, Source
from meridian_core.novelty import novelty_health
from meridian_core.policy import frontier_settings, resolve_source_tier, source_tier_map
from meridian_core.queueing import (
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_RETRIES,
    abandon,
    advance,
    already_queued,
    claim_next,
    enqueue,
    fail,
    queue_depth,
    queue_disposition,
    reclaim_expired,
    release_worker_claims,
)
from meridian_core.sources import get_source, touch_source, upsert_source
from meridian_core.tiering import priority_for_domain

from . import rawstore
from .crawl import Crawler, validators
from .extract import ExtractedDocument, extract_html
from .extract.chunk import chunk_pages, chunk_text
from .extract.document import extract_document
from .extract.document import supports as supports_document
from .extract.injection import Screening, screen
from .extract.pdf import PdftotextMissing, extract_pdf
from .fetch import Crawl4aiClient, Fetcher, FetchResult
from .ocr_queue import enqueue_ocr, mark_scanned
from .prefilter import Prefilter
from .ratelimit import DomainLimiter
from .rawstore import StoredRaw
from .resolve_doi import (
    DoiError,
    DoiResolver,
    OpenAccessCopy,
    ResolutionUnavailable,
    ResolverSettings,
    normalise_doi,
)
from .search import SearchError, SearchResults, SearxClient
from .sitemaps import ParsedSitemap, SitemapError, parse_sitemap
from .topicmatch import TopicVocabulary, load_topic_vocabulary

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class NotKept(RuntimeError):
    """A fetch succeeded and could not be persisted.

    Its own type so `_process` can tell it from any other failure and settle the
    task as a retry rather than as a success — the fetch is not the thing that
    went wrong, but advancing anyway would drop the URL out of the corpus with
    the queue insisting it had been fetched.
    """


#: Task types this loop can actually process.
#:
#: `query` (`P1-34`) and `doi` (`P1-14`) are conditional at claim time rather
#: than absent from this list: a worker with no search backend, or no way to
#: reach the resolution APIs, must not claim rows it cannot answer, or a stack
#: whose SearXNG is briefly down would fail every seed query once and abandon
#: it. See `_claimable_task_types`.
HANDLED_TASK_TYPES = ["url", "sitemap", "query", "doi"]

#: Which dependency each conditional task type needs, by attribute name.
CONDITIONAL_TASK_TYPES = {"query": "_search", "doi": "_resolver"}

#: §6.6's format routing table, as far as it is built. Everything not listed is
#: fetched, stored and left metadata-only until its extractor exists —
#: MarkItDown for Office formats is `P1-08`.
HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
PDF_MEDIA_TYPES = frozenset({"application/pdf"})

#: What a sitemap fetch overrides on its domain's resolved policy (`P1-28`).
#:
#: The content-type allowlist goes for the same reason `RobotsCache` drops it:
#: it is the corpus's list of what can be *read as a document*, and a sitemap is
#: not one. `text/xml` is not on it — which is what most sitemaps are served as —
#: so leaving the allowlist in place would refuse the majority of them while
#: looking like a network problem. `parse_sitemap` is the real gate here, and it
#: refuses anything whose root element is not a urlset or a sitemapindex.
#:
#: `render_js` goes because a sitemap is XML and a browser would only add a
#: rendering pass to a document with nothing to render.
#: Where a sitemap URL that matched no topic goes in the queue.
#:
#: Below every tier — the lowest is `informal` at 5 — so these are drained only
#: when nothing else is pending, and never at the expense of a URL something
#: actually pointed at. Negative rather than zero so that a future tier of 0
#: still outranks them.
UNMATCHED_SITEMAP_PRIORITY = -10

#: How many of one page's citations become `doi` rows (`P1-14`, §6.1).
#:
#: A reference list is the densest frontier signal there is — §6.4 notes the
#: citation graph alone sustains a full queue for weeks — but a review article
#: cites three hundred works, and letting one page put three hundred rows in
#: ahead of everything already waiting is how a crawl ends up depth-first
#: through one literature. Capped, in the order the document listed them.
MAX_CITATIONS_PER_PAGE = 30

#: Where a resolved paper goes in the queue.
#:
#: Above the frontier's default of 0 and below the tier ranking: an
#: open-access copy of a work something already cited is more likely to be
#: worth reading than an arbitrary outbound link, and less likely than a
#: government publication.
RESOLVED_PAPER_PRIORITY = 3

SITEMAP_POLICY_OVERRIDES = {
    "allowed_content_types": [],
    "render_js": "never",
}

DEFAULT_CONCURRENCY = 4
DEFAULT_IDLE_SLEEP_S = 5.0
DEFAULT_HOUSEKEEPING_S = 3600.0

#: How long a lane waits after an error it did not expect. Capped low: the
#: failure this exists for is Postgres restarting, and coming back a minute
#: later is the difference between a blip and an outage that needed a human.
ERROR_BACKOFF_S = (1.0, 5.0, 15.0, 30.0, 60.0)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value < 0:
        raise RuntimeError(f"{name} must not be negative, got {value}")
    return value


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}, got {value}")
    return value


def default_worker_id() -> str:
    """Host plus a per-process suffix.

    The hostname alone would collide the moment two workers run on one box, and
    a bare uuid would make `claimed_by` useless for the question it is actually
    asked — *which machine* is holding this.
    """
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


@dataclasses.dataclass(frozen=True)
class WorkerSettings:
    """Everything the loop's shape depends on, resolved once at startup."""

    worker_id: str = dataclasses.field(default_factory=default_worker_id)
    concurrency: int = DEFAULT_CONCURRENCY
    idle_sleep_s: float = DEFAULT_IDLE_SLEEP_S
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_s: int = DEFAULT_BACKOFF_BASE_S
    housekeeping_interval_s: float = DEFAULT_HOUSEKEEPING_S
    attempt_retention_days: int = DEFAULT_RETENTION_DAYS
    topics: tuple[str, ...] | None = None
    #: Stop after this many claims. None runs until signalled; a number gives a
    #: bounded run for tests and for `--once`-style smoke checks.
    max_tasks: int | None = None

    @classmethod
    def from_env(cls) -> WorkerSettings:
        topics = [t.strip() for t in os.environ.get("MERIDIAN_WORKER_TOPICS", "").split(",")]
        topics = [t for t in topics if t]
        max_tasks = _env_int("MERIDIAN_WORKER_MAX_TASKS", 0, minimum=0)
        return cls(
            worker_id=os.environ.get("MERIDIAN_WORKER_ID") or default_worker_id(),
            concurrency=_env_int("MERIDIAN_WORKER_CONCURRENCY", DEFAULT_CONCURRENCY, minimum=1),
            idle_sleep_s=_env_float("MERIDIAN_WORKER_IDLE_SLEEP_S", DEFAULT_IDLE_SLEEP_S),
            lease_seconds=_env_int(
                "MERIDIAN_WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS, minimum=1
            ),
            housekeeping_interval_s=_env_float(
                "MERIDIAN_WORKER_HOUSEKEEPING_S", DEFAULT_HOUSEKEEPING_S
            ),
            attempt_retention_days=_env_int(
                "MERIDIAN_ATTEMPT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS, minimum=0
            ),
            topics=tuple(topics) or None,
            max_tasks=max_tasks or None,
        )


@dataclasses.dataclass(frozen=True)
class Claim:
    """The claimed task's data, detached from the session that claimed it.

    Carried as plain values rather than as the ORM instance because the claim
    commits and its session closes before any fetching starts — holding a
    connection open across a network request would pin one for the whole fetch,
    and there are more lanes than there are pool slots to spare.
    """

    task_id: int
    url: str
    attempts: int
    topic: str | None
    #: `url` or `sitemap`. The loop dispatches on it, because a sitemap is not a
    #: page — it is neither stored, extracted, chunked nor embedded, and putting
    #: one through the page path would file XML in the corpus as a document.
    task_type: str = "url"


@dataclasses.dataclass(frozen=True)
class Kept:
    """What persisting one fetch produced.

    ``changed`` is whether the checksum differs from the one already on the
    source row. A 200 that returns byte-identical content is a page that has not
    changed — a cheaper and more common fact than a 304, since most origins do
    not implement conditional requests — and it is what lets extraction and
    embedding be skipped on a re-crawl.
    """

    stored: StoredRaw
    changed: bool
    #: None when the format has no extractor yet — Office documents (`P1-08`).
    document: ExtractedDocument | None = None
    chunks: int = 0
    queued: int = 0


@dataclasses.dataclass
class WorkerStats:
    """What one run of the loop did. Logged on the way out."""

    claimed: int = 0
    fetched: int = 0
    unchanged: int = 0
    retried: int = 0
    abandoned: int = 0
    errored: int = 0
    stored: int = 0
    bytes_stored: int = 0
    extracted: int = 0
    chunks: int = 0
    queued: int = 0
    #: Scanned PDFs filed for OCR rather than extracted (§6.6).
    scanned: int = 0
    #: Pages the injection pre-screen flagged (`P1-23`). Nothing is blocked yet.
    flagged: int = 0
    outcomes: Counter[str] = dataclasses.field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {
            "claimed": self.claimed,
            "fetched": self.fetched,
            "unchanged": self.unchanged,
            "retried": self.retried,
            "abandoned": self.abandoned,
            "errored": self.errored,
            "stored": self.stored,
            "bytes_stored": self.bytes_stored,
            "extracted": self.extracted,
            "chunks": self.chunks,
            "queued": self.queued,
            "scanned": self.scanned,
            "flagged": self.flagged,
            "outcomes": dict(self.outcomes),
        }


class Worker:
    """Drains the queue through a `Crawler` until told to stop."""

    def __init__(
        self,
        crawler: Crawler,
        *,
        settings: WorkerSettings | None = None,
        session_factory: SessionFactory = session,
        prefilter: Prefilter | None = None,
        topics: TopicVocabulary | None = None,
        search: SearxClient | None = None,
        resolver: DoiResolver | None = None,
    ) -> None:
        self._crawler = crawler
        self._settings = settings or WorkerSettings()
        self._session_factory = session_factory
        # None disables frontier expansion entirely, which is what a one-shot
        # refetch or a test of the fetch path wants — and is a different thing
        # from a prefilter that drops everything.
        self._prefilter = prefilter
        # Empty rather than None when absent: an unmatched URL is the normal
        # case, so "no vocabulary" and "nothing matched" take the same path and
        # there is no second branch to get wrong.
        self._topics = topics or TopicVocabulary()
        # None means "this deployment has no search backend", which is a
        # supported state and not an error — the loop simply stops claiming
        # `query` rows so they wait for a worker that can answer them.
        self._search = search
        # As with `search`: absent is a supported state, and the loop simply
        # stops claiming the rows it could not answer.
        self._resolver = resolver
        self._stopping = asyncio.Event()
        self._stats = WorkerStats()
        self._reserved = 0

    @property
    def settings(self) -> WorkerSettings:
        return self._settings

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    def stop(self) -> None:
        """Ask the loop to finish what it is holding and come back."""
        self._stopping.set()

    # -- the run ------------------------------------------------------------

    async def run(self) -> WorkerStats:
        """Claim and fetch until stopped, then hand back what was left."""
        settings = self._settings
        log.info(
            "worker starting",
            extra={
                "worker_id": settings.worker_id,
                "concurrency": settings.concurrency,
                "topics": list(settings.topics or []),
                "max_tasks": settings.max_tasks,
            },
        )

        # Not required — claim_next already ignores an expired lease — but it
        # makes work abandoned by a crash visible in the queue on startup
        # rather than only implicit in a timestamp comparison.
        try:
            async with self._session_factory() as sess:
                reclaimed = await reclaim_expired(sess, lease_seconds=settings.lease_seconds)
            if reclaimed:
                log.info("reclaimed expired leases", extra={"count": reclaimed})
        except Exception:
            # A worker that cannot reach the database at startup should back off
            # in its lanes like any other outage, not refuse to start.
            log.exception("could not reclaim expired leases at startup")

        lanes = [
            asyncio.create_task(self._lane(i), name=f"lane-{i}")
            for i in range(settings.concurrency)
        ]
        keeper = asyncio.create_task(self._housekeeping(), name="housekeeping")
        try:
            await asyncio.gather(*lanes)
        finally:
            # `gather` returns the moment one lane raises, leaving the rest of
            # them running — a caller that got an exception from `run()` would
            # then have three lanes still claiming and fetching behind its back,
            # and would release their leases out from under them. Cancelling is
            # a no-op on the lanes that finished normally.
            for lane in lanes:
                lane.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
            await self._release_claims()
            log.info(
                "worker stopped",
                extra={"worker_id": settings.worker_id, **self._stats.as_dict()},
            )
        return self._stats

    async def _lane(self, index: int) -> None:
        """One claim-fetch-settle loop. Ends only when stopped or out of budget."""
        consecutive_errors = 0
        while not self._stopping.is_set():
            if not self._reserve():
                return
            try:
                claim = await self._claim()
            except Exception:
                self._release_reservation()
                consecutive_errors += 1
                self._stats.errored += 1
                log.exception(
                    "could not claim a task",
                    extra={"lane": index, "consecutive_errors": consecutive_errors},
                )
                await self._sleep(_backoff_for(consecutive_errors))
                continue

            consecutive_errors = 0
            if claim is None:
                self._release_reservation()
                await self._sleep(self._settings.idle_sleep_s)
                continue

            self._stats.claimed += 1
            try:
                await self._process(claim)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._stats.errored += 1
                log.exception(
                    "task failed unexpectedly",
                    extra={"lane": index, "task_id": claim.task_id, "url": claim.url},
                )
                await self._settle_error(claim)

    def _claimable_task_types(self) -> list[str]:
        """What this particular worker can answer, not what the loop supports.

        A `query` row needs a search backend, and a worker without one that
        claimed it would fail a task that is not broken — the row is fine, this
        process just cannot do it. Leaving it unclaimed hands it to a worker
        that can, which on a single-worker stack means it waits for SearXNG to
        come back rather than being abandoned while it is down.
        """
        return [
            name
            for name in HANDLED_TASK_TYPES
            if name not in CONDITIONAL_TASK_TYPES
            or getattr(self, CONDITIONAL_TASK_TYPES[name]) is not None
        ]

    async def _claim(self) -> Claim | None:
        async with self._session_factory() as sess:
            task = await claim_next(
                sess,
                worker_id=self._settings.worker_id,
                lease_seconds=self._settings.lease_seconds,
                topics=list(self._settings.topics) if self._settings.topics else None,
                task_types=self._claimable_task_types(),
            )
            if task is None:
                return None
            return Claim(
                task_id=task.task_id,
                url=task.url_or_query,
                attempts=task.attempts,
                topic=task.topic,
                task_type=task.task_type,
            )

    async def _process(self, claim: Claim) -> None:
        """Fetch one claimed task and settle it.

        ``attempt_number`` is ``attempts + 1`` and not ``attempts``: the counter
        on the row is how many attempts have *finished*, so passing it straight
        through would file every retry in the attempt log as a first try, and
        the log's whole purpose is telling a URL that failed once from one that
        has been failing all week.
        """
        if claim.task_type == "sitemap":
            await self._process_sitemap(claim)
            return
        if claim.task_type == "query":
            await self._process_query(claim)
            return
        if claim.task_type == "doi":
            await self._process_doi(claim)
            return

        result = await self._crawler.fetch(
            claim.url, task_id=claim.task_id, attempt_number=claim.attempts + 1
        )
        self._stats.outcomes[result.outcome] += 1
        disposition = queue_disposition(result.outcome, result.status_code)
        detail = f"{result.outcome}: {result.detail}" if result.detail else result.outcome

        # Before the settle, because `result.content` lives only in memory and
        # the settle is the last thing that happens to this fetch. The attempt
        # log is written inside `Crawler.fetch` for the opposite reason — every
        # caller wants an attempt recorded, and a log with holes in the paths
        # nobody thought about is worthless — but the corpus is not something a
        # liveness probe or an ad-hoc refetch should write to. So this lives in
        # the loop, which is the caller whose job is keeping what it fetched.
        kept = None
        if disposition == "fetched":
            try:
                kept = await self._keep(claim, result)
            except NotKept as exc:
                # The fetch worked; keeping it did not. Retrying is right — the
                # cause is almost always local and transient (a full disk,
                # Postgres restarting) and the alternative is a URL the queue
                # believes was fetched and the corpus has never heard of.
                disposition, detail = "retry", f"storage_error: {exc}"
        elif disposition == "done":
            await self._record_freshness(claim)

        await self._settle(claim, disposition, detail)

        log.info(
            "task settled",
            extra={
                "task_id": claim.task_id,
                "url": claim.url,
                "topic": claim.topic,
                "outcome": result.outcome,
                "status": result.status_code,
                "disposition": disposition,
                "attempt_number": claim.attempts + 1,
                "stored": kept.stored.path if kept else None,
                "content_changed": kept.changed if kept else None,
                "chars": kept.document.char_count if kept and kept.document else None,
                "chunks": kept.chunks if kept else None,
                "queued": kept.queued if kept else None,
            },
        )

    async def _settle(
        self,
        claim: Claim,
        disposition: str,
        detail: str,
        *,
        fetched_status: str = "fetched",
    ) -> None:
        """Apply one disposition to the queue row and drop the lease.

        ``fetched_status`` is where a successful fetch leaves the task. A page
        stops at ``fetched`` because extraction and embedding are still ahead of
        it in the status flow; a sitemap goes straight to ``done``, because
        reading it *is* the whole of its work and leaving it at ``fetched`` would
        advertise a source row that will never exist.
        """
        async with self._session_factory() as sess:
            task = await sess.get(QueueTask, claim.task_id)
            if task is None:
                # Deleted underneath us — a bulk queue purge from Admin, say.
                # The fetch_attempts row survives it (task_id is ON DELETE SET
                # NULL), so the request is still on the health line.
                log.warning(
                    "claimed task vanished before it settled",
                    extra={"task_id": claim.task_id},
                )
                return

            if disposition == "fetched":
                await advance(sess, task, fetched_status)
                self._stats.fetched += 1
            elif disposition == "done":
                await advance(sess, task, "done")
                self._stats.unchanged += 1
            elif disposition == "retry":
                retrying = await fail(
                    sess,
                    task,
                    detail,
                    max_retries=self._settings.max_retries,
                    backoff_base_s=self._settings.backoff_base_s,
                )
                if retrying:
                    self._stats.retried += 1
                else:
                    self._stats.abandoned += 1
            else:
                await abandon(sess, task, detail)
                self._stats.abandoned += 1

    async def _process_sitemap(self, claim: Claim) -> None:
        """Read one sitemap and turn it into queue rows (`P1-28`, §6.4).

        Nothing here is stored, extracted, chunked or embedded. A sitemap is not
        a source — it carries no claim anything could cite — so it produces queue
        rows and a `fetch_attempts` row and nothing else.
        """
        result = await self._crawler.fetch(
            claim.url,
            task_id=claim.task_id,
            attempt_number=claim.attempts + 1,
            policy_overrides=SITEMAP_POLICY_OVERRIDES,
        )
        self._stats.outcomes[result.outcome] += 1
        disposition = queue_disposition(result.outcome, result.status_code)
        detail = f"{result.outcome}: {result.detail}" if result.detail else result.outcome

        parsed = None
        queued = 0
        if disposition == "fetched":
            base = result.final_url or claim.url
            try:
                parsed = parse_sitemap(result.content, base_url=base)
            except SitemapError as exc:
                # Unreadable is a refusal, not a failure: re-fetching an HTML
                # error page or a document with a DTD three more times gets the
                # same answer, and `queue_disposition` reserves retries for
                # things that might succeed later.
                disposition = "abandon"
                detail = f"sitemap_unreadable: {exc}"
            else:
                queued = await self._queue_sitemap_entries(claim, parsed, base)
                self._stats.queued += queued

        await self._settle(claim, disposition, detail, fetched_status="done")

        log.info(
            "sitemap settled",
            extra={
                "task_id": claim.task_id,
                "url": claim.url,
                "topic": claim.topic,
                "outcome": result.outcome,
                "status": result.status_code,
                "disposition": disposition,
                "attempt_number": claim.attempts + 1,
                "kind": parsed.kind if parsed else None,
                "entries": len(parsed.urls) if parsed else 0,
                "truncated": parsed.truncated if parsed else False,
                "dropped": dict(parsed.dropped) if parsed else {},
                "queued": queued,
            },
        )

    async def _queue_sitemap_entries(self, claim: Claim, parsed: ParsedSitemap, base: str) -> int:
        """Turn a parsed sitemap's URLs into queue rows.

        An index's entries become further `sitemap` tasks and a urlset's become
        `url` tasks. The prefilter runs over the urlset case for the same reason
        it runs over frontier links — a sitemap lists every page a site has,
        including the several thousand already in the corpus — but *not* over the
        index case, because `SKIP_EXTENSIONS` drops `.gz` and most large sites
        publish `sitemap.xml.gz`, so filtering there would discard exactly the
        indexes worth following.
        """
        if not parsed.urls:
            return 0

        async with self._session_factory() as sess:
            if parsed.is_index:
                # Deduplicated against the queue by hand, since the prefilter is
                # the wrong tool here and re-enqueueing an index on every crawl
                # of the domain would multiply it.
                known = await already_queued(sess, list(parsed.urls))
                candidates = [url for url in parsed.urls if url not in known]
                task_type = "sitemap"
            elif self._prefilter is None:
                return 0
            else:
                verdict = await self._prefilter.keep(sess, parsed.urls)
                candidates = list(verdict.kept)
                task_type = "url"

            if not candidates:
                return 0

            tiers = await source_tier_map(sess)
            matched = 0
            for url in candidates:
                if task_type == "sitemap":
                    # An index is not a page. Its topic is irrelevant — nothing
                    # reads it — so it keeps the claim's and is fetched promptly,
                    # because it is the thing that reveals the actual URLs.
                    topic, priority = claim.topic, priority_for_domain(url, tiers)
                else:
                    topic = self._topics.best_topic(url)
                    if topic is not None:
                        matched += 1
                        priority = priority_for_domain(url, tiers)
                    else:
                        # Not dropped. A sitemap URL that matches no topic is
                        # not known to be irrelevant — the path may simply be
                        # opaque, and §7.4 warns that a corpus which only ever
                        # confirms its own vocabulary is its own bias. It is
                        # queued below every tier so it is crawled when the
                        # frontier has nothing better, which is exactly when
                        # incidental discovery is worth paying for.
                        priority = UNMATCHED_SITEMAP_PRIORITY

                await enqueue(
                    sess,
                    url,
                    topic=topic,
                    # Distinguishable from `frontier` on purpose: "how did this
                    # URL get here" is the question §5.2's seed provenance exists
                    # to answer, and a sitemap is a different kind of answer from
                    # a link someone chose to place on a page.
                    seed_source="sitemap",
                    task_type=task_type,
                    priority=priority,
                )
            await sess.commit()

        if task_type == "url":
            log.info(
                "sitemap entries queued",
                extra={
                    "url": claim.url,
                    "queued": len(candidates),
                    "topic_matched": matched,
                    "unmatched": len(candidates) - matched,
                },
            )
        return len(candidates)

    async def _process_query(self, claim: Claim) -> None:
        """Run one search and turn its results into queue rows (`P1-34`, §6.4).

        Nothing is fetched, stored or extracted here. A query is not a document
        — `url_or_query` holds the query text, not a URL — so it produces queue
        rows and nothing else, and it settles to ``done`` because running it
        *is* the whole of its work.

        No `fetch_attempts` row either, and that is not an oversight. The log is
        keyed by domain and answers "is this host refusing us"; a search asks
        one internal service about many hosts, so a row there would file
        SearXNG's availability under a domain nobody crawled.
        """
        if self._search is None:  # pragma: no cover - _claimable_task_types prevents it
            log.warning("claimed a query with no search backend", extra={"task_id": claim.task_id})
            await self._settle(claim, "retry", "no_search_backend")
            return

        results = None
        queued = 0
        try:
            results = await self._search.search(claim.url)
        except SearchError as exc:
            # Transient and local: the backend is down or misconfigured, and the
            # query itself is fine. Retrying is right, and the backoff is what
            # stops a dead backend from spinning the queue.
            disposition, detail = "retry", f"search_unavailable: {exc}"
        else:
            # Answered — including answered with nothing. §6.4 says engine
            # failure is routine, so a query that returns no usable results is
            # done rather than retried: the same engines will be just as broken
            # tomorrow, and the queue slot is better spent elsewhere.
            disposition, detail = "done", "search_ok"
            queued = await self._queue_search_results(claim, results)
            # `queued` counts rows this run added to the frontier, wherever they
            # came from. A discovery channel missing from it would make the run
            # summary understate exactly the thing the run was for.
            self._stats.queued += queued

        await self._settle(claim, disposition, detail)

        log.info(
            "query settled",
            extra={
                "task_id": claim.task_id,
                "query": claim.url,
                "topic": claim.topic,
                "disposition": disposition,
                "detail": detail,
                "results": len(results.urls) if results else 0,
                "dropped": dict(results.dropped) if results else {},
                # §6.4 expects individual engines to break constantly. Logged
                # rather than acted on: it is what explains a thin result set
                # to whoever reads this line a week later.
                "unresponsive_engines": list(results.unresponsive) if results else [],
                "queued": queued,
            },
        )

    async def _process_doi(self, claim: Claim) -> None:
        """Resolve one DOI to a legally available copy and queue it (`P1-14`, §6.5).

        The row's `url_or_query` is a DOI, not a URL. What this produces is one
        ordinary `url` task pointing at an open-access copy — which then goes
        through the whole fetch stack, robots and `netguard` included. That is
        what makes it safe for a hostile page to put any DOI it likes in its
        reference list: nothing here fetches the answer, it only queues it.
        """
        if self._resolver is None:  # pragma: no cover - _claimable_task_types prevents it
            log.warning("claimed a DOI with no resolver", extra={"task_id": claim.task_id})
            await self._settle(claim, "retry", "no_doi_resolver")
            return

        copy = None
        queued = 0
        try:
            copy = await self._resolver.resolve(claim.url)
        except DoiError as exc:
            # The row is bad, not the network. Retrying re-parses the same
            # string to the same error, so it is abandoned rather than retried.
            disposition, detail = "abandon", f"invalid_doi: {exc}"
        except ResolutionUnavailable as exc:
            disposition, detail = "retry", f"resolution_unavailable: {exc}"
        else:
            # Including "no open-access copy exists", which is an answer. §6.5
            # is explicit that a metadata-only work still participates in the
            # graph — the paper is paywalled today and will be tomorrow, so the
            # task is done rather than retried forever.
            disposition = "done"
            detail = f"resolved_by: {copy.provider}" if copy else "no_open_access_copy"
            if copy is not None:
                queued = await self._queue_resolved_copy(claim, copy)
                self._stats.queued += queued

        await self._settle(claim, disposition, detail)

        log.info(
            "doi settled",
            extra={
                "task_id": claim.task_id,
                "doi": claim.url,
                "topic": claim.topic,
                "disposition": disposition,
                "detail": detail,
                "provider": copy.provider if copy else None,
                "version": copy.version if copy else None,
                "license": copy.license if copy else None,
                "queued": queued,
            },
        )

    async def _queue_resolved_copy(self, claim: Claim, copy: OpenAccessCopy) -> int:
        """Put the open-access copy in the queue, if it is not already there."""
        if self._prefilter is None:
            return 0

        async with self._session_factory() as sess:
            verdict = await self._prefilter.keep(sess, [copy.url])
            if not verdict.kept:
                log.debug(
                    "a resolved copy was not worth queueing",
                    extra={"doi": claim.url, "url": copy.url, "dropped": verdict.dropped},
                )
                return 0
            await enqueue(
                sess,
                verdict.kept[0],
                topic=claim.topic,
                seed_source="doi",
                priority=RESOLVED_PAPER_PRIORITY,
            )
            await sess.commit()
        return 1

    async def _queue_search_results(self, claim: Claim, results: SearchResults) -> int:
        """Prefilter a query's results and enqueue what survives.

        The prefilter is not optional here the way it is for frontier links.
        §6.4 is explicit that SearXNG returns a lot of content-farm and SEO
        junk, and a search result is a URL nobody chose — no page pointed at it
        and no site listed it — so it is the *least* trustworthy way a URL can
        reach this queue and the one most worth filtering.
        """
        if not results.urls or self._prefilter is None:
            return 0

        async with self._session_factory() as sess:
            verdict = await self._prefilter.keep(sess, list(results.urls))
            if not verdict.kept:
                return 0

            tiers = await source_tier_map(sess)
            for url in verdict.kept:
                await enqueue(
                    sess,
                    url,
                    # The query's topic, not the URL's path. Unlike a sitemap
                    # entry — where the triggering page's topic says nothing
                    # about what the site lists — a query was written *for* a
                    # topic by a person, so every result is an answer to that
                    # question and carries it.
                    topic=claim.topic,
                    seed_source="search",
                    priority=priority_for_domain(url, tiers),
                )
            await sess.commit()

        log.info(
            "search results queued",
            extra={
                "query": claim.url,
                "considered": verdict.considered,
                "queued": len(verdict.kept),
                "dropped": dict(verdict.dropped),
            },
        )
        return len(verdict.kept)

    def _screen(
        self, claim: Claim, result: FetchResult, document: ExtractedDocument | None
    ) -> Screening | None:
        """Look for prompt injection before any of this reaches a model (`P1-23`).

        On the raw HTML *and* the extracted text, because they answer different
        halves: hiddenness is a DOM property extraction has already discarded,
        and what survived extraction is what a model would actually read.

        The DOM half is HTML-only — a PDF or a .docx hides text by other means,
        and the equivalents need a different screen than this one, worth having
        and not worth pretending this is it. But the *text* half runs on
        anything that extracted, because a tool directive in a spreadsheet
        reaches a model exactly as well as one in a web page.
        """
        is_html = result.media_type in HTML_MEDIA_TYPES
        text = document.text if document else ""
        if not is_html and not text:
            return None
        try:
            screening = screen(
                result.content.decode("utf-8", "replace") if is_html else "",
                text,
            )
        except Exception:
            log.exception("injection screening failed", extra={"url": claim.url})
            return None

        if screening.suspicious:
            # WARNING, not INFO. Nothing is blocked — quarantine is `P4-06` —
            # so the log line is the entire mechanism until then, and it has to
            # be visible on a health check that greps for severity.
            log.warning(
                "page flagged by the injection pre-screen",
                extra={
                    "url": claim.url,
                    "task_id": claim.task_id,
                    "domain": result.domain,
                    "kinds": screening.kinds,
                    "evidence": screening.findings[0].evidence if screening.findings else None,
                },
            )
        elif screening.findings:
            log.info(
                "injection pre-screen noted something",
                extra={"url": claim.url, "kinds": screening.kinds},
            )
        return screening

    async def _record_freshness(self, claim: Claim) -> None:
        """The 304 path: nothing about the content is new, but the check happened.

        Failures here are swallowed, unlike in :meth:`_keep`. There is no
        content at risk — a revalidation that succeeded is not worth throwing
        away in order to record a timestamp about it.
        """
        try:
            async with self._session_factory() as sess:
                await touch_source(sess, claim.url)
                await sess.commit()
        except Exception:
            log.exception("could not record a freshness check", extra={"url": claim.url})

    async def _keep(self, claim: Claim, result: FetchResult) -> Kept:
        """Keep what came back: the bytes, the checksum, and the validators.

        Raises :class:`NotKept` if it could not, and the raise is the point. A
        full disk that let the task advance to `fetched` anyway would lose the
        URL from the corpus permanently: the queue would say the page was
        fetched, no source row would exist, and nothing would ever ask for it
        again. Failing the task instead means a backoff, two more tries, and —
        if the disk is still full — a `failed` row carrying the reason, which is
        a problem somebody can see.
        """
        try:
            async with self._session_factory() as sess:
                tier = await resolve_source_tier(sess, result.domain)
                existing = await get_source(sess, claim.url)
                current_retention = existing.retention_tier if existing else None

            stored = rawstore.store(
                claim.url,
                result.content,
                source_tier=tier,
                media_type=result.media_type,
                current_retention=current_retention,
            )
            document = await self._extract(claim, result)
            screening = self._screen(claim, result, document)
            if screening is not None and screening.suspicious:
                self._stats.flagged += 1

            async with self._session_factory() as sess:
                source, changed = await upsert_source(
                    sess,
                    claim.url,
                    checksum=stored.checksum,
                    raw_file_path=stored.path,
                    source_tier=tier,
                    retention_tier=stored.retention_tier,
                    media_type=result.media_type,
                    final_url=result.final_url,
                    **_bibliography(document, screening),
                    **validators(result.headers),
                )
                if document is not None and document.needs_ocr:
                    # After the upsert, so `text_available=False` and the OCR
                    # columns survive `_bibliography`'s view that this document
                    # simply had no text. It had none *because* it is a scan,
                    # which is a different thing needing a different follow-up.
                    await mark_scanned(sess, source)
                    await enqueue_ocr(sess, source.source_id)
                    self._stats.scanned += 1
                # In the same transaction as the source row. A source whose
                # checksum says one thing and whose chunks were cut from another
                # is a corpus that cites text it does not hold.
                chunks_written = await self._chunk(sess, source, document, changed=changed)
                queued = await self._expand_frontier(sess, claim, document)
                queued += await self._seed_citations(sess, claim, document)
                await sess.commit()
        except Exception as exc:
            log.exception(
                "could not keep what was fetched",
                extra={"url": claim.url, "task_id": claim.task_id, "bytes": len(result.content)},
            )
            raise NotKept(f"{type(exc).__name__}: {exc}") from exc

        self._stats.stored += 1
        if stored.kept:
            self._stats.bytes_stored += stored.bytes_written
        if document is not None and document.has_text:
            self._stats.extracted += 1
        self._stats.chunks += chunks_written
        self._stats.queued += queued
        return Kept(
            stored=stored,
            changed=changed,
            document=document,
            chunks=chunks_written,
            queued=queued,
        )

    async def _expand_frontier(
        self, sess: AsyncSession, claim: Claim, document: ExtractedDocument | None
    ) -> int:
        """Turn this page's outbound links into queue rows (`P1-06`, §6.1).

        This is what makes the crawl a crawl. Without it the worker drains its
        seed list once and then idles forever, which is a fetcher.

        In the same pass as the fetch, not a later sweep, and for the same
        reason chunking is: the link list lives only in memory. It is
        deliberately not stored on the source row — 500 URLs is ~40KB of JSONB,
        ~2GB across a 50k corpus, and the right home for a URL worth fetching is
        a queue row, not a column.

        The topic is inherited from the page that linked here. It is the only
        signal available without a model, it is usually right — a page about a
        subject tends to link to pages about that subject — and §10's steering acts
        on topics, so a frontier that produced untopiced rows would be a
        frontier steering cannot reach.
        """
        if document is None or not document.links or self._prefilter is None:
            return 0

        verdict = await self._prefilter.keep(sess, document.links)
        if not verdict.kept:
            log.debug(
                "frontier expansion queued nothing",
                extra={"url": claim.url, "dropped": verdict.dropped},
            )
            return 0

        tiers = await source_tier_map(sess)
        for url in verdict.kept:
            await enqueue(
                sess,
                url,
                topic=claim.topic,
                seed_source="frontier",
                # §5.2: a government link outranks a blog without anyone
                # curating a seed list. `priority_for_domain` has existed since
                # P1-17 with no caller; this is it.
                priority=priority_for_domain(url, tiers),
            )

        log.info(
            "frontier expanded",
            extra={
                "url": claim.url,
                "task_id": claim.task_id,
                "considered": verdict.considered,
                "queued": len(verdict.kept),
                "dropped": verdict.dropped,
            },
        )
        return len(verdict.kept)

    async def _seed_citations(
        self, sess: AsyncSession, claim: Claim, document: ExtractedDocument | None
    ) -> int:
        """Turn this page's reference list into `doi` rows (`P1-14`, §6.1).

        §6.1 lists citations beside outbound links as frontier expansion, and
        §6.4 says the citation graph alone sustains a full queue for weeks. It
        is also a better signal than a link: a reference is a claim that this
        work matters to that one, which is exactly the judgement a link in a
        navigation bar is not making.

        Only `doi` citations, for now. `arxiv`, `pmid` and `handle` identifiers
        are extracted (`extract/base.py`) and each needs its own resolution
        route — a `pmid` is not a DOI, and guessing a URL for one would queue
        rows that 404. They stay in `sources.extra` until there is somewhere
        for them to go.

        The prefilter deliberately does not run here. It answers "is this URL
        worth a request", and a DOI is not a URL — `already_queued` is the part
        of it that applies, and it is applied directly.
        """
        if document is None or not document.citations or self._resolver is None:
            return 0

        dois: dict[str, None] = {}
        for citation in document.citations:
            if citation.kind != "doi":
                continue
            try:
                dois[normalise_doi(citation.value)] = None
            except DoiError:
                # A reference list is OCR'd, hand-typed and frequently wrong.
                # One malformed DOI is not worth a log line per page.
                continue
            if len(dois) >= MAX_CITATIONS_PER_PAGE:
                break

        if not dois:
            return 0

        known = await already_queued(sess, list(dois))
        fresh = [doi for doi in dois if doi not in known]
        for doi in fresh:
            await enqueue(
                sess,
                doi,
                topic=claim.topic,
                seed_source="citation",
                task_type="doi",
                priority=RESOLVED_PAPER_PRIORITY,
            )

        if fresh:
            log.info(
                "citations queued for resolution",
                extra={
                    "url": claim.url,
                    "task_id": claim.task_id,
                    "cited": len(document.citations),
                    "queued": len(fresh),
                    "already_known": len(dois) - len(fresh),
                },
            )
        return len(fresh)

    async def _chunk(
        self,
        sess: AsyncSession,
        source: Source,
        document: ExtractedDocument | None,
        *,
        changed: bool,
    ) -> int:
        """Cut the extracted text into chunks and make them the source's set.

        Skipped entirely when the checksum says the content did not change: the
        chunks already stored were cut from these exact bytes, and rewriting
        them would hand the slow loop a day of "new" material it has already
        read (§6.3's high-water mark is a chunk id).

        This has to happen here, in the fetch pass, and not in a later
        re-extraction sweep — a `background` source keeps no raw file (§5.4), so
        text not chunked now is text that needs the page fetched again.
        """
        if document is None or not document.has_text:
            return 0
        if not changed and await chunk_count(sess, source.source_id):
            log.debug(
                "content unchanged; chunks left alone",
                extra={"url": source.url, "source_id": source.source_id},
            )
            return 0

        # §5.3: page number for a paginated document, character offset otherwise.
        # The two are the same column, and `is_paginated` is what says which
        # reading applies — not which extractor happened to run.
        cut = chunk_pages(document.pages) if document.is_paginated else chunk_text(document.text)
        written, deleted = await replace_chunks(sess, source.source_id, as_writes(cut))
        log.info(
            "chunked",
            extra={
                "url": source.url,
                "source_id": source.source_id,
                "chunks": written,
                "replaced": deleted,
                "chars": document.char_count,
                "paginated": document.is_paginated,
            },
        )
        return written

    async def _extract(self, claim: Claim, result: FetchResult) -> ExtractedDocument | None:
        """Turn the bytes into text, routed by media type (§6.6).

        Returns None when the format has no extractor yet — `P1-08` brings
        MarkItDown for Office documents — rather than raising. A source with no
        extractor is metadata-only (§6.5), which is a resting state the schema
        already has a word for, not a failure.

        Extraction failing is not `NotKept`. The bytes are safely stored and can
        be re-extracted whenever the extractor improves (§11.12); refetching the
        page to try again would be spending a request to solve a local problem.
        """
        url = result.final_url or claim.url
        try:
            if result.media_type in HTML_MEDIA_TYPES:
                document = extract_html(result.content, url, browser_payload=result.browser_payload)
            elif result.media_type in PDF_MEDIA_TYPES:
                document = await extract_pdf(result.content)
            elif supports_document(result.media_type):
                # Asked rather than matched against a set of our own: the
                # module's `_normalise` already handles `; charset=…` and
                # casing, and a second copy of that list here would drift.
                document = await extract_document(
                    result.content,
                    media_type=result.media_type,
                    filename_hint=PurePosixPath(urlsplit(url).path).name or None,
                )
            else:
                return None
        except PdftotextMissing:
            # A deployment fault, not a property of this document: every PDF in
            # the corpus is affected and none of them should read as "no text".
            # Loud, and once per document, because a worker that has quietly
            # lost poppler stops growing the corpus with no other symptom.
            log.error(
                "poppler is not installed; this PDF and every other one cannot be read",
                extra={"url": claim.url, "task_id": claim.task_id},
            )
            return None
        except Exception:
            log.exception("extraction failed", extra={"url": claim.url, "task_id": claim.task_id})
            return None

        log.info(
            "extracted",
            extra={
                "url": claim.url,
                "task_id": claim.task_id,
                "extractor": document.extractor,
                "chars": document.char_count,
                "has_text": document.has_text,
                "pages": len(document.pages) or None,
                "needs_ocr": document.needs_ocr,
                "links": len(document.links),
                "citations": len(document.citations),
                "title": document.title,
            },
        )
        return document

    async def _settle_error(self, claim: Claim) -> None:
        """Give a task back after an exception the loop did not expect.

        Left claimed, it would sit out its whole lease before anyone could try
        it again. Failed with a retry, it comes back after a backoff — which is
        the right answer when nobody yet knows whether the bug was in the task
        or in us.
        """
        try:
            async with self._session_factory() as sess:
                task = await sess.get(QueueTask, claim.task_id)
                if task is not None:
                    await fail(
                        sess,
                        task,
                        "worker error",
                        max_retries=self._settings.max_retries,
                        backoff_base_s=self._settings.backoff_base_s,
                    )
        except Exception:
            # The database is where the failure probably was. The lease expiry
            # is the backstop for exactly this.
            log.exception("could not record a failed task", extra={"task_id": claim.task_id})

    # -- housekeeping -------------------------------------------------------

    async def _housekeeping(self) -> None:
        """Prune the attempt log and log the health line, on a slow tick.

        Runs here because there is nowhere else: `fetch_attempts` gains a row
        per request and nothing else in the system is awake often enough to
        bound it. Cancelled rather than stopped on shutdown — a prune half done
        is a prune, and the next tick finishes it.
        """
        interval = self._settings.housekeeping_interval_s
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await self.housekeep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("housekeeping tick failed")

    async def search_health(self) -> str:
        """`configured` / `unreachable` / `absent` — for the health line (`P1-34`).

        Three states for the reason `browser_health` has three. `absent` is a
        deployment that never intended to search and is fine. `unreachable` is
        the one worth waking up for: query rows stop being claimed, the frontier
        stops widening, and the crawl winds down to an idle that reads as
        success on every other number on this line.
        """
        if self._search is None:
            return "absent"
        return "configured" if await self._search.healthy() else "unreachable"

    async def browser_health(self) -> str:
        """`configured` / `unreachable` / `absent` — for the health line (`P1-26`).

        Three states, not two, because they need different responses. `absent`
        means no `CRAWL4AI_URL`, which is a deployment that never intended to
        render and is fine. `unreachable` means one was configured and is not
        answering, which is the silent failure this exists to surface: the
        fetcher degrades to static and keeps working, so nothing else in the
        system ever notices that JS-dependent pages stopped being rendered.
        """
        browser = self._crawler.fetcher.browser
        if browser is None:
            return "absent"
        return "configured" if await browser.healthy() else "unreachable"

    async def housekeep(self) -> None:
        """One housekeeping pass. Public so a test — or an operator — can run it."""
        async with self._session_factory() as sess:
            pruned = await prune_attempts(
                sess, older_than_days=self._settings.attempt_retention_days
            )
            health = await fetch_health(sess)
            depth = await queue_depth(sess)
            novelty = await novelty_health(sess)

        browser = await self.browser_health()
        search = await self.search_health()
        if search == "unreachable":
            log.warning(
                "search backend configured but not answering; query rows are not being "
                "claimed and the frontier cannot widen",
                extra={"search": search},
            )
        if browser == "unreachable":
            # WARNING rather than INFO: this is the one health-line value that
            # means something is wrong right now and is invisible everywhere
            # else, since the crawl carries on and simply extracts worse.
            log.warning(
                "browser configured but not answering; pages needing JS are being "
                "fetched statically",
                extra={"browser": browser},
            )

        log.info(
            "health",
            extra={
                # §12.5's daily health line, less `edges added` — that one
                # belongs to the orchestrator and is not this process's to
                # report. The novelty pass rate is, since `P2-03` made the gate
                # a worker pass: a rate that collapses means the crawl has
                # found a mirror or a site that serves one page under every
                # URL, which looks healthy in every other number here.
                "queue_depth": depth,
                "pending": depth.get("pending", 0),
                "fetch_attempts": health.attempts,
                "fetch_success_rate": health.success_rate,
                "by_outcome": health.by_outcome,
                "attempts_pruned": pruned,
                "tracked_domains": self._crawler.limiter.tracked_domains,
                "browser": browser,
                "search": search,
                **novelty.as_dict(),
            },
        )

    # -- odds and ends ------------------------------------------------------

    def _reserve(self) -> bool:
        """Take one slot of the ``max_tasks`` budget, if there is one.

        Reserved before the claim rather than counted after it, so N lanes
        racing on the last slot cannot between them claim N+1 tasks.
        """
        if self._settings.max_tasks is None:
            return True
        if self._reserved >= self._settings.max_tasks:
            return False
        self._reserved += 1
        return True

    def _release_reservation(self) -> None:
        if self._settings.max_tasks is not None:
            self._reserved -= 1

    async def _sleep(self, seconds: float) -> None:
        """Wait, but wake immediately if the worker has been asked to stop.

        A plain sleep would make shutdown take up to a full idle interval per
        lane, which is the difference between a deploy that feels instant and
        one that looks hung.
        """
        if seconds <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    async def _release_claims(self) -> None:
        try:
            async with self._session_factory() as sess:
                released = await release_worker_claims(sess, self._settings.worker_id)
            if released:
                log.info("released held leases", extra={"count": released})
        except Exception:
            log.exception("could not release held leases; they will expire instead")


def install_signal_handlers(worker: Worker) -> None:
    """Wire SIGINT/SIGTERM: first asks, second insists.

    The second signal cancels every task in the loop, unwinding `run()` through
    the cancellation path — deliberately harsher than the first, because by the
    time an operator sends it they have already waited once.

    `add_signal_handler` is POSIX-only and raises on Windows and inside a thread
    that is not the main one; suppressed rather than required, since a worker
    that cannot install handlers should still crawl.
    """
    loop = asyncio.get_running_loop()
    state = {"signalled": False}

    def handle(signame: str) -> None:
        if state["signalled"]:
            log.warning("second signal; stopping now", extra={"signal": signame})
            for task in asyncio.all_tasks(loop):
                task.cancel()
            return
        state["signalled"] = True
        log.info("shutting down; finishing fetches in flight", extra={"signal": signame})
        worker.stop()

    for signame in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signame), handle, signame)


async def build_prefilter() -> Prefilter:
    """Read the seeded frontier blocklist once, at startup (§13.1).

    Config, so it lives in the database and changes when someone edits it in
    Admin — not between two pages of one crawl. A worker that could not read it
    starts with an empty blocklist rather than refusing to run: crawling a few
    social links is a waste, and not crawling at all is an outage.
    """
    try:
        async with session() as sess:
            frontier = await frontier_settings(sess)
    except Exception:
        log.exception("could not read the frontier blocklist; continuing without one")
        return Prefilter()

    blocked = frontier.get("blocked_domains") or []
    log.info("frontier prefilter ready", extra={"blocked_domains": len(blocked)})
    return Prefilter(blocked)


async def build_topic_vocabulary() -> TopicVocabulary:
    """Read the topic vocabulary once, at startup (`P1-28`).

    Degrades the same way the prefilter does, and for the same reason: a worker
    that cannot read its vocabulary should crawl with none — every sitemap URL
    lands unmatched and deprioritised — rather than refuse to start. Crawling
    with worse topic labels is a bad day; not crawling is an outage.
    """
    try:
        async with session() as sess:
            vocabulary = await load_topic_vocabulary(sess)
    except Exception:
        log.exception("could not read the topic vocabulary; sitemap URLs will be untopiced")
        return TopicVocabulary()

    log.info("topic vocabulary ready", extra={"phrases": len(vocabulary.phrases)})
    return vocabulary


async def run_worker(settings: WorkerSettings | None = None) -> WorkerStats:
    """Build the whole fetch stack from the environment and run it."""
    settings = settings or WorkerSettings.from_env()
    browser = Crawl4aiClient.from_env()
    if browser is None:
        # Correct — the static path is most of the corpus — but worth saying out
        # loud, because a worker that has quietly lost its browser for a week
        # extracts worse and reports nothing (P1-26).
        log.warning("no CRAWL4AI_URL; JS-dependent pages will be fetched statically only")

    prefilter = await build_prefilter()
    topics = await build_topic_vocabulary()

    search = SearxClient.from_env()
    if search is None:
        # Louder than the browser's equivalent, because the consequence is
        # worse. Without a browser the crawl extracts JS-heavy pages badly;
        # without search it drains its frontier and then idles, and an idle
        # crawler looks exactly like a finished one (`P1-34`, §6.4).
        log.warning("no SEARXNG_URL; query rows will not be claimed and the frontier cannot widen")

    resolver_settings = ResolverSettings.from_env()
    if not resolver_settings.contact_email:
        # Unpaywall requires a contact address and refuses requests without
        # one, so this is not cosmetic: the chain drops to OpenAlex and the
        # preprint rule, which is materially worse coverage (`P1-14`, §6.5).
        log.warning(
            "no MERIDIAN_CONTACT_EMAIL; Unpaywall will be skipped and DOI "
            "resolution will find fewer open-access copies"
        )
    resolver = DoiResolver(resolver_settings)

    async with Fetcher(browser=browser) as fetcher:
        crawler = Crawler(session, fetcher=fetcher, limiter=DomainLimiter())
        worker = Worker(
            crawler,
            settings=settings,
            prefilter=prefilter,
            topics=topics,
            search=search,
            resolver=resolver,
        )
        install_signal_handlers(worker)
        try:
            return await worker.run()
        finally:
            await dispose_engines()
            if search is not None:
                await search.aclose()
            await resolver.aclose()


def main() -> None:
    """Entry point: `python -m worker.main`."""
    configure_logging("worker")
    settings = WorkerSettings.from_env()
    # One run_id for the process, on every record it emits. A crawl that ran for
    # six hours is one thing to grep for, not a timestamp range to guess at.
    # Both endings of the shutdown path are ordinary here, not errors: Ctrl-C
    # raises KeyboardInterrupt, and a second signal cancels the run. Neither
    # should print a traceback on a worker that did what it was asked.
    quiet_exits = (KeyboardInterrupt, asyncio.CancelledError)
    with bind_run_id(f"worker-{settings.worker_id}"), contextlib.suppress(*quiet_exits):
        asyncio.run(run_worker(settings))


def _bibliography(
    document: ExtractedDocument | None, screening: Screening | None = None
) -> dict[str, object]:
    """The `sources` columns an extracted document can fill (§5.2).

    Empty when there is no document, so a format with no extractor writes
    nothing rather than writing nulls over what a previous fetch established.
    Citations ride in `extra` — they are a list, `sources` has no column for
    them, and `P1-14` is what turns them into queue rows.
    """
    extra: dict[str, object] = {}
    if screening is not None and screening.findings:
        # §2.5's rule that nothing is destroyed applies here: the page is stored,
        # extracted and chunked exactly as normal, and this is a record beside
        # it. `P4-06` is what eventually acts on it.
        extra["injection"] = screening.as_record()

    if document is None:
        return {"extra": extra} if extra else {}
    fields: dict[str, object] = {
        "title": document.title,
        "author": document.author,
        "publisher": document.publisher,
        "publication_date": document.publication_date,
        "language": document.language,
        "doi": document.doi,
        # A scan has no text and is not merely empty: `mark_scanned` records
        # why and what would fix it, and must not be undone by this.
        "text_available": document.has_text,
    }
    if document.citations:
        extra["citations"] = [{"kind": c.kind, "value": c.value} for c in document.citations]
    if extra:
        fields["extra"] = extra
    return fields


def _backoff_for(consecutive_errors: int) -> float:
    return ERROR_BACKOFF_S[min(consecutive_errors, len(ERROR_BACKOFF_S)) - 1]


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
