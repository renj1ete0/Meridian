"""The worker loop: claim, fetch, record, repeat (task P1-15, spec §6.1, §13.4).

Concurrency is N independent claim-fetch-settle lanes over one shared `Crawler`; the
database is the queue and `FOR UPDATE SKIP LOCKED` the dispatcher. Lanes catch and settle
every error except cancellation, which is the shutdown path. See
docs/features/crawling.md#the-loop.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import datetime as dt
import os
import signal
import socket
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.ageing import HALF_LIFE_DAYS
from meridian_core.attempts import DEFAULT_RETENTION_DAYS, fetch_health, prune_attempts
from meridian_core.bibliography import FROM_KEY
from meridian_core.boilerplate import host_key
from meridian_core.chunks import (
    ChunkWrite,
    as_writes,
    chunk_count,
    embedding_backlog,
    replace_chunks,
)
from meridian_core.citedpapers import FLOOR_PRIORITY, cited_priority
from meridian_core.crawlhealth import DEFAULT_MAX_EMBED_BACKLOG, EMBED_BACKLOG_ENV, RESUME_SHARE
from meridian_core.db import dispose_engines, session
from meridian_core.embedder import RemoteEmbedder
from meridian_core.figures import FigureWrite, replace_figures
from meridian_core.hostscores import Decision, HostPolicy, Standing, record_vouches
from meridian_core.hostscores import load as load_host_scores
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, Source, Upload, Watch
from meridian_core.novelty import novelty_health
from meridian_core.policy import frontier_settings, source_tier_map
from meridian_core.queueing import (
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_RETRIES,
    abandon,
    advance,
    already_queued,
    claim_next,
    enqueue,
    enqueue_dois,
    fail,
    queue_depth,
    queue_disposition,
    reclaim_expired,
    record_search_yield,
    release_worker_claims,
    retry_floor_s,
)
from meridian_core.schemaready import wait_before_start
from meridian_core.sources import get_source, touch_source, upsert_source
from meridian_core.steering import draw_shares, draw_topic
from meridian_core.steering import topics as steering_topics
from meridian_core.stubs import is_stub, replace_stubs, set_stub
from meridian_core.tiering import (
    document_tier,
    is_tier_mapped,
    link_tier,
    priority_with_urgency,
    resolve_tier,
)
from meridian_core.titles import clean_title, title_from_text
from meridian_core.trust import page_state, record_novel_fetch, record_screening
from meridian_core.uploads import (
    NO_COPY,
    inbox_root,
    read_upload,
    settle_upload,
    source_tier_for,
    source_url_for,
    upload_id_from_task,
)

from . import rawstore
from .cleancut import clean_cut
from .crawl import Crawler, validators
from .extract import ExtractedDocument, extract_html
from .extract.dockind import Evidence, classify, evidence_from, record_doc_kind
from .extract.document import extract_document
from .extract.document import supports as supports_document
from .extract.errorpage import error_page_reason
from .extract.injection import Screening, screen
from .extract.language import detect_language
from .extract.paywall import Stub, paywall_stub
from .extract.pdf import PdftotextMissing, extract_pdf
from .fetch import Crawl4aiClient, Fetcher, FetchResult
from .headlines import HeadlineFilter
from .listingpage import parse_listing
from .liveness import beat
from .ocr_queue import enqueue_ocr, mark_scanned
from .prefilter import Prefilter, normalise_url
from .ratelimit import DomainLimiter
from .rawstore import StoredRaw
from .resolve_doi import (
    DoiError,
    DoiResolver,
    OpenAccessCopy,
    ResolutionThrottled,
    ResolutionUnavailable,
    ResolverSettings,
    normalise_doi,
)
from .search import GdeltClient, NewsSearch, SearchError, SearchResults, SearxClient
from .sitemaps import ParsedSitemap, SitemapError, parse_sitemap
from .topicmatch import TopicVocabulary, load_topic_vocabulary
from .watch import WATCH_PRIORITY

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class NotKept(RuntimeError):
    """A fetch succeeded and could not be persisted; the task is retried, not advanced.

    See docs/features/crawling.md#keeping-a-fetch.
    """


#: Task types this loop can actually process. `query` and `doi` are claimed only by
#: a worker with a search backend or resolver; see `_claimable_task_types`.
HANDLED_TASK_TYPES = ["url", "sitemap", "query", "doi", "upload"]

#: Which dependency each conditional task type needs, by attribute name. An `upload`
#: needs the connector's inbox mounted (`B-264`).
CONDITIONAL_TASK_TYPES = {"query": "_search", "doi": "_resolver", "upload": "_inbox"}

#: §6.6's format routing table, as far as it is built. Everything not listed is
#: fetched, stored and left metadata-only until its extractor exists —
#: MarkItDown for Office formats is `P1-08`.
HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
PDF_MEDIA_TYPES = frozenset({"application/pdf"})

#: Where a sitemap URL that matched no topic goes in the queue: below every tier,
#: so it is drained only when nothing else waits. See docs/features/discovery.md#sitemaps.
UNMATCHED_SITEMAP_PRIORITY = -10

#: A watched outlet's new entries (`B-249`): just behind an operator's seed, ahead of
#: the crawl's own work, because news ages by the hour.
WATCH_ENTRY_PRIORITY = 90


def watch_topic(
    watch: Watch,
    url: str,
    title: str | None,
    vocabulary: TopicVocabulary,
    embedded: tuple[str, ...] | None = None,
) -> str | None:
    """The topic a watched entry is filed under, or None when it is not queued (`B-249`).

    Without a headline filter, the watch's topic. With one, the topics the headline's
    embedding found (``embedded``, `B-251`), or when it was not embedded, the topics the
    headline and the path name in the vocabulary; a watch with a topic keeps only entries
    matching it.
    """
    if not watch.headline_filter:
        return watch.topic or vocabulary.best_topic(url)
    if embedded is not None:
        found = embedded
    else:
        found = (*vocabulary.topics_for_text(title or ""), *vocabulary.topics_for(url))
    if watch.topic is not None:
        return watch.topic if watch.topic in found else None
    return found[0] if found else None


#: Added to a declared English version's priority (`B-57`), so it is fetched
#: ahead of the other links on the page that declared it.
ENGLISH_ALTERNATE_BONUS = 10

#: How long a followed link waits for the labeller to read the page that carried it
#: (`B-242`), so `requeue_links` can raise or lower it first. Three hours: labelling
#: lags a fetch by under 1.5 h for three pages in four, and the re-ranking pass runs
#: hourly. ``verdict_wait_hours`` in the global policy's ``frontier`` block overrides
#: it; 0 turns the wait off. See docs/features/discovery.md#verdict-wait.
DEFAULT_VERDICT_WAIT_S = 3 * 3600.0

#: Added to a search result's tier priority (`B-51`): at the same tier, an answer
#: to a question about a topic goes before a followed link.
SEARCH_RESULT_BONUS = 5

#: A query whose engines were all throttled waits at least this long before it
#: is asked again, and is retried this many times before it is given up (`B-107`).
THROTTLED_QUERY_FLOOR_S = 1800
THROTTLED_QUERY_RETRIES = 6

#: Task types that look something up rather than fetch a page (`B-68`). They are
#: claimed in their own turn of the directed slot and never in a topic's
#: ordinary draw, which is for pages.
LOOKUP_TASK_TYPES = frozenset({"doi"})

#: Retries for a DOI held back only by a throttled provider (`B-67`). Each waits
#: out the provider's cooldown, which doubles to half an hour, so this spans
#: hours rather than the seconds the ordinary budget spans.
THROTTLED_MAX_RETRIES = 8

#: How many of one page's citations become `doi` rows (`P1-14`, §6.1), in the
#: order the document listed them. See docs/features/discovery.md#cited-papers.
MAX_CITATIONS_PER_PAGE = 30

#: The least a resolved paper's copy is queued at: the floor a DOI waits at
#: when nothing recommends it. A DOI ranked higher by the page that cited it
#: (`B-58`, :mod:`meridian_core.citedpapers`) passes its own rank on instead.
RESOLVED_PAPER_PRIORITY = FLOOR_PRIORITY

#: What a sitemap fetch overrides on its domain's resolved policy (`P1-28`): the
#: content-type allowlist (most sitemaps are `text/xml`) and the browser.
#: `parse_sitemap` is the real gate.
SITEMAP_POLICY_OVERRIDES = {
    "allowed_content_types": [],
    "render_js": "never",
}

DEFAULT_CONCURRENCY = 4
DEFAULT_IDLE_SLEEP_S = 5.0
DEFAULT_HOUSEKEEPING_S = 3600.0

#: One claim in this many goes to directed work when any is pending (`B-61`, `B-86`).
#: See docs/features/crawling.md#directed-slots.
DEFAULT_DIRECTED_EVERY = 2

#: Within the directed slots, cited-paper lookups go first in one of this many
#: (`B-86`); search results, seeds and queries take the rest.
LOOKUP_EVERY = 3

#: The crawl pauses while this many live chunks wait for a vector (`B-61`).

#: How often the backlog is re-counted while claiming.
BACKLOG_CHECK_S = 60.0

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
    #: One claim in this many is *directed* (`B-61`): a search result, query,
    #: seed or cited paper, when any is waiting. 0 turns the reservation off.
    directed_every: int = DEFAULT_DIRECTED_EVERY
    #: Stop claiming while more live chunks than this wait for a vector
    #: (`B-61`); resume below 80% of it. 0 turns backpressure off.
    max_embed_backlog: int = DEFAULT_MAX_EMBED_BACKLOG
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
            directed_every=_env_int(
                "MERIDIAN_WORKER_DIRECTED_EVERY", DEFAULT_DIRECTED_EVERY, minimum=0
            ),
            max_embed_backlog=_env_int(EMBED_BACKLOG_ENV, DEFAULT_MAX_EMBED_BACKLOG, minimum=0),
        )


@dataclasses.dataclass(frozen=True)
class Claim:
    """The claimed task's data, detached from the session that claimed it.

    Plain values, because the claim's session closes before any fetching starts.
    """

    task_id: int
    url: str
    attempts: int
    topic: str | None
    #: `url` or `sitemap`. The loop dispatches on it, because a sitemap is not a
    #: page — it is neither stored, extracted, chunked nor embedded, and putting
    #: one through the page path would file XML in the corpus as a document.
    task_type: str = "url"
    #: What the row was queued at. A resolved DOI's open-access copy inherits
    #: it (`B-58`): the copy is the paper the DOI was ranked for.
    priority: int = 0


@dataclasses.dataclass(frozen=True)
class Kept:
    """What persisting one fetch produced.

    ``changed`` is whether the checksum differs from the one already on the source
    row; an unchanged page skips extraction and embedding.
    """

    stored: StoredRaw
    changed: bool
    #: None when the format has no extractor yet — Office documents (`P1-08`).
    document: ExtractedDocument | None = None
    chunks: int = 0
    queued: int = 0
    source_id: int | None = None


@dataclasses.dataclass(frozen=True)
class HandedIn:
    """What a document handed in through the connector changes on the fetched path (`B-264`).

    Its tier, title and DOI are the sender's (validated by the connector), its text is
    already extracted, and `provenance` rides in `sources.extra["upload"]`.
    """

    document: ExtractedDocument
    tier: str
    title: str | None = None
    doi: str | None = None
    provenance: dict[str, object] = dataclasses.field(default_factory=dict)

    def applied_to(self, fields: dict[str, object]) -> dict[str, object]:
        """`_bibliography`'s columns with the sender's title and DOI in place."""
        out = dict(fields)
        extra = dict(out.get("extra") or {})  # type: ignore[call-overload]
        if self.title:
            out["title"] = self.title
            extra.pop("title_from", None)
        if self.doi:
            out["doi"] = self.doi
        extra["upload"] = self.provenance
        out["extra"] = extra
        return out


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
        search: SearxClient | NewsSearch | None = None,
        resolver: DoiResolver | None = None,
        hosts: HostPolicy | None = None,
        inbox: Path | None = None,
        headlines: HeadlineFilter | None = None,
        verdict_wait_s: float = DEFAULT_VERDICT_WAIT_S,
    ) -> None:
        if verdict_wait_s < 0:
            raise ValueError("a verdict wait cannot be negative")
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
        # `B-48`: which hosts are worth following links into, from content
        # labels a scheduled pass turned into per-host scores. None follows
        # every link, which is what a one-shot fetch or a test wants.
        self._hosts = hosts
        # `B-264`: where the connector keeps handed-in files. None when it is not
        # mounted, and then `upload` rows wait for a worker that has it.
        self._inbox = inbox
        # `B-251`: None filters watched headlines by the vocabulary alone.
        self._headlines = headlines
        # `B-242`: followed links wait this long for their page's verdict.
        self._verdict_wait_s = verdict_wait_s
        self._stopping = asyncio.Event()
        self._claims = 0
        self._directed_claims = 0
        self._backlog_checked = -1e9
        self._paused = False
        self._stats = WorkerStats()
        self._reserved = 0

    @property
    def settings(self) -> WorkerSettings:
        return self._settings

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    def _crawled_for(self, claim: object) -> list[str]:
        """Why this URL was fetched: the claim's topic (tasks P2-14, P2-21).

        Provenance only; what the page is about is `topic_labels`. A list, never
        None: empty means the claim carried no topic. See
        docs/features/crawling.md#keeping-a-fetch.
        """
        topic = getattr(claim, "topic", None)
        return [topic] if topic else []

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
            # `gather` returns when one lane raises and leaves the others running;
            # cancel them so their leases are not released from under them.
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
            # `P5-08`. Every iteration, before the work rather than after: a
            # lane that wedges *inside* a fetch should stop beating, and a beat
            # at the end of the loop would only stop once the wedge cleared.
            beat()
            try:
                paused = await self.backpressured()
            except Exception:
                log.exception("could not read the embedding backlog; claiming anyway")
                paused = False
            if paused:
                await self._sleep(self._settings.idle_sleep_s)
                continue
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

        A `query` row needs a search backend; leaving it unclaimed lets it wait
        for one rather than fail.
        """
        return [
            name
            for name in HANDLED_TASK_TYPES
            if name not in CONDITIONAL_TASK_TYPES
            or getattr(self, CONDITIONAL_TASK_TYPES[name]) is not None
        ]

    async def _shares(self, sess) -> dict[str, float]:
        """What fraction of claims each topic should get right now (§10).

        Read per claim, not cached, so a steering change applies at once.
        """
        try:
            return draw_shares(await steering_topics(sess), now=dt.datetime.now(dt.UTC))
        except Exception:
            # Steering is a preference, not a precondition. A worker that
            # stopped claiming because it could not read the weight vector
            # would trade the whole crawl for the shape of it.
            log.exception("could not read the steering vector; claiming unfiltered")
            return {}

    async def backpressured(self) -> bool:
        """Whether the embedding backlog says to stop claiming for now (`B-61`).

        Re-counted at most every :data:`BACKLOG_CHECK_S`. Hysteresis: pause above
        the ceiling, resume only below 80% of it, so the crawl does not flap on
        the line.
        """
        ceiling = self._settings.max_embed_backlog
        if ceiling <= 0:
            return False
        now = time.monotonic()
        if now - self._backlog_checked >= BACKLOG_CHECK_S:
            self._backlog_checked = now
            async with self._session_factory() as sess:
                backlog = await embedding_backlog(sess, valuable_only=True)
            was = self._paused
            self._paused = backlog > ceiling if not was else backlog > int(ceiling * RESUME_SHARE)
            if self._paused != was:
                log.info(
                    "crawl paused for embedding" if self._paused else "crawl resumed",
                    extra={"backlog": backlog, "ceiling": ceiling},
                )
        return self._paused

    async def _claim(self) -> Claim | None:
        """One task, preferring the topics the attention vector prefers (§10).

        A drawn topic with nothing claimable is dropped and another drawn; only an
        exhausted pool claims unfiltered. See docs/features/crawling.md#claiming.
        """
        async with self._session_factory() as sess:
            if self._settings.topics:
                # An operator said what this worker is for. The vector does not
                # get to widen that.
                topics = list(self._settings.topics)
                return await self._claim_within(sess, topics) or await self._claim_within(
                    sess, topics, include_held=True
                )

            shares = await self._shares(sess)

            self._claims += 1
            every = self._settings.directed_every
            if every and self._claims % every == 0:
                # The directed slot (`B-61`), drawn through the same attention
                # vector; lookups take their own turn (`B-68`). Falls through to
                # the ordinary draw when nothing directed is waiting.
                self._directed_claims += 1
                first = self._directed_claims % LOOKUP_EVERY == 0
                for lookups in (first, not first):
                    pool = dict(shares)
                    while pool:
                        topic = draw_topic(pool)
                        if topic is None:
                            break
                        claim = await self._claim_within(
                            sess, [topic], directed=True, lookups=lookups
                        )
                        if claim is not None:
                            return claim
                        pool.pop(topic, None)
                    if not shares:
                        claim = await self._claim_within(sess, None, directed=True, lookups=lookups)
                        if claim is not None:
                            return claim

            # The topic's share is spent on pages, never on lookups (`B-68`):
            # measured on a live run, a topic whose best rows were cited-paper
            # DOIs got no page fetches at all for two hours.
            while shares:
                topic = draw_topic(shares)
                if topic is None:
                    break
                claim = await self._claim_within(sess, [topic], lookups=False)
                if claim is not None:
                    return claim
                # Nothing claimable under it *right now* — in backoff, held by
                # another worker, or genuinely empty. Either way another draw
                # spent on it is a draw wasted.
                shares.pop(topic, None)

            # `B-242`: a link still waiting for its page's verdict goes only when nothing
            # else is ready, so the wait reorders the crawl and never idles it.
            return await self._claim_within(sess, None) or await self._claim_within(
                sess, None, include_held=True
            )

    async def _claim_within(
        self,
        sess,
        topics: list[str] | None,
        *,
        directed: bool = False,
        lookups: bool | None = None,
        include_held: bool = False,
    ) -> Claim | None:
        """One claim.

        ``lookups`` True claims only lookup tasks, False only page work, None either (`B-68`).
        ``include_held`` also takes links waiting for their page's verdict (`B-242`).
        """
        task_types = self._claimable_task_types()
        if lookups is not None:
            task_types = [t for t in task_types if (t in LOOKUP_TASK_TYPES) == lookups]
        if not task_types:
            # Nothing of that kind this worker can do — never an unfiltered claim.
            return None
        task = await claim_next(
            sess,
            worker_id=self._settings.worker_id,
            lease_seconds=self._settings.lease_seconds,
            topics=topics,
            task_types=task_types,
            directed=directed,
            # `B-112`: not a page on a host this worker is already waiting on.
            skip_domains=self._busy_domains(),
            include_held=include_held,
        )
        if task is None:
            return None
        return Claim(
            task_id=task.task_id,
            url=task.url_or_query,
            attempts=task.attempts,
            topic=task.topic,
            task_type=task.task_type,
            priority=task.priority,
        )

    def _busy_domains(self) -> set[str]:
        """Hosts to leave for later (`B-112`).

        An optimisation, so it can never stop a claim: anything wrong here means no host is skipped.
        """
        busy = getattr(getattr(self._crawler, "limiter", None), "busy", None)
        if not callable(busy):
            return set()
        try:
            return set(busy())
        except Exception:
            log.exception("could not read busy hosts; claiming without skipping any")
            return set()

    async def _process(self, claim: Claim) -> None:
        """Fetch one claimed task and settle it.

        ``attempt_number`` is ``attempts + 1``: the row counts finished attempts.
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
        if claim.task_type == "upload":
            await self._process_upload(claim)
            return

        result = await self._crawler.fetch(
            claim.url, task_id=claim.task_id, attempt_number=claim.attempts + 1
        )
        self._stats.outcomes[result.outcome] += 1
        disposition = queue_disposition(result.outcome, result.status_code)
        detail = f"{result.outcome}: {result.detail}" if result.detail else result.outcome

        # Kept before the settle, because `result.content` lives only in memory.
        # The loop keeps; the crawler logs. See docs/features/crawling.md#keeping-a-fetch.
        kept = None
        if disposition == "fetched":
            try:
                kept = await self._keep(claim, result)
            except NotKept as exc:
                # The fetch worked; keeping it did not. Retried: the cause is
                # almost always local and transient.
                disposition, detail = "retry", f"storage_error: {exc}"
        elif disposition == "done":
            await self._record_freshness(claim)

        await self._settle(claim, disposition, detail, floor_s=retry_floor_s(result.outcome))

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
        floor_s: float = 0.0,
        max_retries: int | None = None,
        note: str | None = None,
    ) -> None:
        """Apply one disposition to the queue row and drop the lease.

        ``fetched_status`` is where a successful fetch leaves the task: ``fetched``
        for a page, ``done`` for a sitemap, whose reading is all its work. ``note`` is
        kept on a ``done`` row as its answer, with the time it was given (`B-264`).
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
                if note is not None:
                    task.error = note
                    task.fetched_at = dt.datetime.now(dt.UTC)
                await advance(sess, task, "done")
                self._stats.unchanged += 1
            elif disposition == "retry":
                retrying = await fail(
                    sess,
                    task,
                    detail,
                    max_retries=self._settings.max_retries if max_retries is None else max_retries,
                    backoff_base_s=self._settings.backoff_base_s,
                    floor_s=floor_s,
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
                if await self._watch_kind(claim.url) == "listing":
                    # A watched latest-articles page (`B-255`): its links, not XML.
                    parsed = parse_listing(result.content, base_url=base)
                else:
                    parsed = parse_sitemap(result.content, base_url=base)
            except SitemapError as exc:
                # Unreadable is a refusal, not a failure: it reads the same next time.
                disposition = "abandon"
                detail = f"sitemap_unreadable: {exc}"
            else:
                queued = await self._queue_sitemap_entries(claim, parsed, base)
                self._stats.queued += queued

        await self._settle(
            claim,
            disposition,
            detail,
            fetched_status="done",
            floor_s=retry_floor_s(result.outcome),
        )

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

    async def _watch_kind(self, url: str) -> str | None:
        """The kind of the watch on ``url``, or None when nobody watches it."""
        async with self._session_factory() as sess:
            return await sess.scalar(select(Watch.kind).where(Watch.url == url))

    async def _queue_sitemap_entries(self, claim: Claim, parsed: ParsedSitemap, base: str) -> int:
        """Turn a parsed sitemap's URLs into queue rows.

        An index's entries become `sitemap` tasks, a urlset's `url` tasks. The prefilter
        runs on a urlset only, since it would drop `.gz` sitemaps from an index.
        """
        if not parsed.urls:
            return 0

        async with self._session_factory() as sess:
            watch = await sess.scalar(select(Watch).where(Watch.url == claim.url))
            if watch is not None:
                return await self._queue_watch_entries(sess, claim, watch, parsed)
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
                    topic, priority = claim.topic, priority_with_urgency(url, tiers, HALF_LIFE_DAYS)
                else:
                    decision = self._admit(url, tiers)
                    if not decision.queue:
                        continue
                    topic = self._topics.best_topic(url)
                    if topic is not None:
                        matched += 1
                        # Through the host's decision, so a proven host's pages
                        # carry its boost (`B-115`, `B-116`) and a thin one's
                        # its scaling, exactly as a followed link would.
                        priority = decision.applied_to(
                            priority_with_urgency(url, tiers, HALF_LIFE_DAYS)
                        )
                    else:
                        # Not dropped: an unmatched path is not known to be irrelevant (§7.4).
                        # Queued below every tier instead.
                        priority = UNMATCHED_SITEMAP_PRIORITY

                await enqueue(
                    sess,
                    url,
                    topic=topic,
                    # Distinguishable from `frontier` on purpose: a site's list is a different
                    # answer to "how did this URL get here" (§5.2).
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

    async def _queue_watch_entries(
        self, sess: AsyncSession, claim: Claim, watch: Watch, parsed: ParsedSitemap
    ) -> int:
        """A watched sitemap's or feed's entries (`B-249`); commits.

        Unlike a discovered sitemap: an index's files are read again unless a read is
        waiting (a news index keeps its file names and changes their contents); entries
        skip the followed-link host cap, since the operator chose the site; and with a
        headline filter only entries whose headline or path matches a topic are queued.
        See docs/features/discovery.md#watched-sources.
        """
        if parsed.is_index:
            waiting = set(
                (
                    await sess.scalars(
                        select(QueueTask.url_or_query).where(
                            QueueTask.url_or_query.in_(list(parsed.urls)),
                            QueueTask.task_type == "sitemap",
                            QueueTask.status == "pending",
                        )
                    )
                ).all()
            )
            files = [url for url in parsed.urls if url not in waiting]
            for url in files:
                await enqueue(
                    sess,
                    url,
                    topic=watch.topic,
                    seed_source="watch",
                    task_type="sitemap",
                    priority=WATCH_PRIORITY,
                )
            await sess.commit()
            return len(files)

        if self._prefilter is None:
            return 0
        verdict = await self._prefilter.keep(sess, parsed.urls)
        titles = {normalise_url(url): title for url, title in parsed.titles.items()}
        embedded, by = await self._embedded_topics(watch, verdict.kept, titles)
        queued = skipped = 0
        for url in verdict.kept:
            topic = watch_topic(watch, url, titles.get(url), self._topics, embedded.get(url))
            if topic is None:
                skipped += 1
                continue
            await enqueue(
                sess,
                url,
                topic=topic,
                seed_source="watch",
                task_type="url",
                priority=WATCH_ENTRY_PRIORITY,
            )
            queued += 1
        await sess.commit()
        log.info(
            "watch entries queued",
            extra={
                "url": claim.url,
                "watch_id": watch.watch_id,
                "entries": len(parsed.urls),
                "new": len(verdict.kept),
                "queued": queued,
                "off_topic": skipped,
                "filter": by,
            },
        )
        return queued

    async def _embedded_topics(
        self, watch: Watch, urls: Sequence[str], titles: dict[str, str]
    ) -> tuple[dict[str, tuple[str, ...]], str | None]:
        """Each titled entry's topics by embedding (`B-251`), and which rule judged the read.

        Empty when the watch has no headline filter, and when embedding is unavailable, so
        every entry falls back to the vocabulary. See docs/features/discovery.md#headline-filter.
        """
        if not watch.headline_filter:
            return {}, None
        titled = [url for url in urls if titles.get(url)]
        if self._headlines is None or not titled:
            return {}, "vocabulary"
        found = await self._headlines.topics_for([titles[url] for url in titled])
        if found is None:
            log.warning(
                "headline filter fell back to the vocabulary",
                extra={"url": watch.url, "watch_id": watch.watch_id, "entries": len(titled)},
            )
            return {}, "vocabulary"
        return dict(zip(titled, found, strict=True)), "embedding"

    async def _process_query(self, claim: Claim) -> None:
        """Run one search and turn its results into queue rows (`P1-34`, §6.4).

        Nothing is fetched or stored, and no `fetch_attempts` row is written; the task
        settles ``done``. See docs/features/discovery.md#search.
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
            if results.throttled:
                # `B-107`: nothing because the engines refused, not because the
                # web has nothing. Suspensions last minutes to hours, so the
                # retry waits at least THROTTLED_QUERY_FLOOR_S, a few times over.
                await self._settle(
                    claim,
                    "retry",
                    f"search_throttled: {', '.join(results.unresponsive)[:300]}",
                    floor_s=THROTTLED_QUERY_FLOOR_S,
                    max_retries=THROTTLED_QUERY_RETRIES,
                )
                return
            # Answered, including with nothing: done, not retried (§6.4).
            disposition, detail = "done", "search_ok"
            queued = await self._queue_search_results(claim, results)
            # `queued` counts rows this run added to the frontier, wherever they
            # came from. A discovery channel missing from it would make the run
            # summary understate exactly the thing the run was for.
            self._stats.queued += queued
            async with self._session_factory() as sess:
                # `B-56`: the yield stays on the row, so a question that found
                # nothing is a fact the database holds, not a log line.
                await record_search_yield(
                    sess, claim.task_id, results=len(results.urls), queued=queued
                )
                await sess.commit()

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

        Produces one ordinary `url` task for the copy, which then goes through the whole
        fetch stack; nothing here fetches it.
        """
        if self._resolver is None:  # pragma: no cover - _claimable_task_types prevents it
            log.warning("claimed a DOI with no resolver", extra={"task_id": claim.task_id})
            await self._settle(claim, "retry", "no_doi_resolver")
            return

        copy = None
        queued = 0
        floor_s, max_retries = 0.0, None
        try:
            copy = await self._resolver.resolve(claim.url)
        except DoiError as exc:
            # The row is bad, not the network. Retrying re-parses the same
            # string to the same error, so it is abandoned rather than retried.
            disposition, detail = "abandon", f"invalid_doi: {exc}"
        except ResolutionThrottled as exc:
            # Back after the provider's cooldown, not the queue's seconds, and
            # with room for several: a shared anonymous quota is busy for hours
            # at a time, and the paper is not at fault (`B-67`).
            disposition, detail = "retry", f"resolution_throttled: {exc}"
            floor_s, max_retries = exc.retry_after_s, THROTTLED_MAX_RETRIES
        except ResolutionUnavailable as exc:
            disposition, detail = "retry", f"resolution_unavailable: {exc}"
        else:
            # Including "no open-access copy exists", which is an answer (§6.5): done.
            disposition = "done"
            detail = f"resolved_by: {copy.provider}" if copy else NO_COPY
            if copy is not None:
                queued = await self._queue_resolved_copy(claim, copy)
                self._stats.queued += queued

        await self._settle(
            claim,
            disposition,
            detail,
            floor_s=floor_s,
            max_retries=max_retries,
            # The connector's wanted list reads this (`B-264`).
            note=NO_COPY if disposition == "done" and copy is None else None,
        )

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
                # `B-58`: the copy is what the DOI was ranked for. Queued at
                # the floor, a DOI claimed because an on-topic page cited it
                # would resolve and then wait behind every link in the queue.
                priority=max(claim.priority, RESOLVED_PAPER_PRIORITY),
            )
            await sess.commit()
        return 1

    async def _process_upload(self, claim: Claim) -> None:
        """Turn one document handed in through the connector into a source (`B-264`).

        The file is read from the inbox and goes through `_extract` and `_keep`, the path a
        fetched document takes. Nothing is fetched, so no `fetch_attempts` row is written.
        See docs/features/connector-api.md#how-an-upload-becomes-a-source.
        """
        try:
            upload_id = upload_id_from_task(claim.url)
        except ValueError as exc:
            await self._settle(claim, "abandon", f"invalid_upload_task: {exc}")
            return
        async with self._session_factory() as sess:
            upload = await sess.get(Upload, upload_id)
        if upload is None:
            await self._settle(claim, "abandon", "upload_missing")
            return

        try:
            content = read_upload(upload, self._inbox)
        except (OSError, ValueError) as exc:
            await self._fail_upload(claim, upload_id, f"inbox_unreadable: {exc}")
            return

        url = source_url_for(upload)
        result = FetchResult(
            requested_url=url,
            final_url=url,
            outcome="success",
            content=content,
            media_type=upload.media_type,
        )
        target = dataclasses.replace(claim, url=url, topic=upload.topic or claim.topic)
        document = await self._extract(target, result)
        reason = _unusable(document, upload.media_type)
        if reason is not None:
            await self._fail_upload(claim, upload_id, reason)
            return

        handed_in = HandedIn(
            document=document,  # type: ignore[arg-type]  # _unusable refused None
            tier=source_tier_for(upload),
            title=upload.title,
            doi=upload.doi,
            provenance=_upload_provenance(upload),
        )
        try:
            kept = await self._keep(target, result, handed_in=handed_in)
        except NotKept as exc:
            await self._settle(claim, "retry", f"storage_error: {exc}")
            return

        async with self._session_factory() as sess:
            await settle_upload(sess, upload_id, status="done", source_id=kept.source_id)
            if upload.source_url:
                # `B-256`: the whole article, handed in for a page kept as a stub.
                await replace_stubs(
                    sess,
                    [upload.source_url, normalise_url(upload.source_url) or ""],
                    by_source_id=kept.source_id,
                )
            await sess.commit()
        await self._settle(claim, "fetched", "upload_ok", fetched_status="done")
        log.info(
            "upload settled",
            extra={
                "task_id": claim.task_id,
                "upload_id": upload_id,
                "url": url,
                "source_id": kept.source_id,
                "chunks": kept.chunks,
                "queued": kept.queued,
                "needs_ocr": handed_in.document.needs_ocr,
            },
        )

    async def _fail_upload(self, claim: Claim, upload_id: int, reason: str) -> None:
        """Mark the upload failed and give up its task; the same bytes would fail again."""
        async with self._session_factory() as sess:
            await settle_upload(sess, upload_id, status="failed", error=reason[:500])
            await sess.commit()
        await self._settle(claim, "abandon", reason[:500])
        log.warning(
            "upload failed",
            extra={"task_id": claim.task_id, "upload_id": upload_id, "reason": reason},
        )

    async def _queue_search_results(self, claim: Claim, results: SearchResults) -> int:
        """Prefilter a query's results and enqueue what survives.

        Not optional here: a search result is the least trustworthy way a URL reaches
        the queue (§6.4).
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
                    # The query's topic, not the URL's path: the query was written for it.
                    topic=claim.topic,
                    seed_source="search",
                    # `B-51`: an answer to a question outranks a link a page
                    # happened to carry, at the same tier. Small on purpose —
                    # tier still decides order across tiers.
                    priority=priority_with_urgency(url, tiers, HALF_LIFE_DAYS)
                    + SEARCH_RESULT_BONUS,
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

        On the raw HTML (hidden text, HTML only) and on the extracted text (any
        format). See docs/features/crawling.md#loop-stages.
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

    async def _keep(
        self, claim: Claim, result: FetchResult, *, handed_in: HandedIn | None = None
    ) -> Kept:
        """Keep what came back: the bytes, the checksum, and the validators.

        Raises :class:`NotKept` if it could not, so the task is retried rather than
        advanced to `fetched` with no source row. ``handed_in`` is a document from the
        connector (`B-264`): see docs/features/connector-api.md#how-an-upload-becomes-a-source.
        """
        try:
            async with self._session_factory() as sess:
                tiers = await source_tier_map(sess)
                # Before extraction there is only the domain to go on, so an
                # academic institution's page is stored as its own
                # publication until the document shows otherwise (`B-50`).
                tier = link_tier(result.domain, tiers) if handed_in is None else handed_in.tier
                existing = await get_source(sess, claim.url)
                current_retention = existing.retention_tier if existing else None
            if handed_in is not None:
                # A handed-in file exists nowhere to fetch again, so its bytes are kept.
                current_retention = "primary"

            stored = rawstore.store(
                claim.url,
                result.content,
                source_tier=tier,
                media_type=result.media_type,
                current_retention=current_retention,
            )
            if handed_in is None:
                document = await self._extract(claim, result)
                # `B-50`: the document's own DOI is the evidence that confirms a
                # scholarly tier the domain alone could only guess at.
                tier = document_tier(
                    result.domain, tiers, scholarly=bool(document is not None and document.doi)
                )
            else:
                document = handed_in.document
            # `B-256`: a walled article is kept as what the page shows everyone. Never a
            # handed-in document: whoever handed it in could read the whole of it.
            stub = (
                paywall_stub(_decoded(result.content), document)
                if handed_in is None and result.media_type in HTML_MEDIA_TYPES
                else None
            )
            if stub is not None and document is not None:
                if existing is not None and (existing.extra or {}).get("stub_replaced_by"):
                    # The whole article was handed in; a teaser beside it adds nothing.
                    document = dataclasses.replace(document, text="", pages=(), figures=())
                    stub = None
                else:
                    document = stub.as_document(document)
            screening = self._screen(claim, result, document)
            if screening is not None and screening.suspicious:
                self._stats.flagged += 1
            # `B-45`: a page answering 200 that says it is not there. Stored as
            # junk with no chunks and no links followed — its text is the
            # site's template, and its links are the site's menu.
            missing = (
                error_page_reason(document.title, document.text) if document is not None else None
            )

            flagged = bool(screening and screening.suspicious)
            fields = _bibliography(document, screening, url=claim.url)
            if stub is not None:
                # Its one passage is text, though short of the floor an article must clear.
                fields["text_available"] = True
            if handed_in is not None:
                fields = handed_in.applied_to(fields)

            async with self._session_factory() as sess:
                if handed_in is None:
                    # `P4-14`: fold this fetch into the domain's verdict before the
                    # page is written, so the page is stored under the updated verdict.
                    domain_trust = await record_screening(
                        sess,
                        result.domain,
                        flagged=flagged,
                        tier_mapped=is_tier_mapped(result.domain, await source_tier_map(sess)),
                    )
                    trust_state = page_state(domain_trust, flagged=flagged)
                else:
                    # Chosen by whoever holds the connector's token, as a curated domain
                    # is chosen; the screen can still quarantine it.
                    trust_state = "quarantined" if flagged else "cleared"
                source, changed = await upsert_source(
                    sess,
                    claim.url,
                    checksum=stored.checksum,
                    raw_file_path=stored.path,
                    raw_root=stored.root,
                    source_tier=tier,
                    trust_state=trust_state,
                    retention_tier="junk" if missing else stored.retention_tier,
                    media_type=result.media_type,
                    final_url=result.final_url,
                    crawled_for=self._crawled_for(claim),
                    **fields,
                    **validators(result.headers),
                )
                # Set or cleared on every fetch: a later fetch that finds the whole
                # article, or a connector handing it in, replaces the stub.
                was_stub = is_stub(source)
                set_stub(source, stub.mark() if stub is not None and not missing else None)
                if document is not None and document.needs_ocr:
                    # After the upsert, so a scan's OCR columns survive
                    # `_bibliography`'s view that it simply had no text.
                    await mark_scanned(sess, source)
                    await enqueue_ocr(sess, source.source_id)
                    # `B-47` follow-up: a document that had text and is now a
                    # scan (or a garbled layer) has none. What it said before
                    # is superseded, not left live beside `text_available=False`.
                    await replace_chunks(sess, source.source_id, [])
                    self._stats.scanned += 1
                if missing:
                    log.info(
                        "page says it is not there; stored as junk",
                        extra={"url": claim.url, "reason": missing, "title": document.title},
                    )
                    # A page that was real and is now an error page: what it
                    # said before is superseded, not left live under a title
                    # that says there is nothing here.
                    await replace_chunks(sess, source.source_id, [])
                    await sess.commit()
                    self._stats.stored += 1
                    return Kept(
                        stored=stored,
                        changed=changed,
                        document=document,
                        chunks=0,
                        queued=0,
                        source_id=source.source_id,
                    )
                # In the same transaction as the source row, so chunks always come
                # from the bytes the checksum describes. `B-59`: a listing is
                # followed, not chunked.
                chunks_written = await self._chunk(
                    sess,
                    source,
                    document,
                    # A page first kept whole and now found walled is re-cut even
                    # when its bytes have not changed.
                    changed=changed or (stub is not None and not was_stub),
                    stub=stub,
                    evidence=evidence_from(
                        result.final_url or claim.url,
                        document,
                        media_type=result.media_type,
                        tier=tier,
                    ),
                )
                if changed and handed_in is None:
                    # `P4-12`: a domain earns its seeding allowance on *novel*
                    # documents, so a site cannot approve itself on volume.
                    await record_novel_fetch(sess, result.domain)
                queued = await self._expand_frontier(sess, claim, document, source)
                queued += await self._seed_citations(sess, claim, document, source)
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
            source_id=source.source_id,
        )

    async def _expand_frontier(
        self,
        sess: AsyncSession,
        claim: Claim,
        document: ExtractedDocument | None,
        source: Source | None = None,
    ) -> int:
        """Turn this page's outbound links into queue rows (`P1-06`, §6.1).

        In the fetch pass, because the link list lives only in memory. Each link
        inherits this page's topic. See docs/features/discovery.md#followed-links.
        """
        if document is None or not document.links or self._prefilter is None:
            return 0
        if source is not None:
            # `B-150`: before the prefilter drops links already queued, which is where a
            # host's second and later vouches used to be lost.
            page_url = (source.extra or {}).get("final_url") or source.url or claim.url
            await record_vouches(sess, source.source_id, page_url, document.links)
        if self._hosts is not None and not self._hosts.follows_links_from(claim.url):
            # `B-48`: what an off-topic site links to is, almost always, more
            # of itself — and a page's links inherit its topic, so following
            # them is how one detour becomes a subtree.
            log.info("frontier not expanded: off-topic host", extra={"url": claim.url})
            return 0

        alternate = await self._queue_english_alternate(sess, claim, document, source)
        verdict = await self._prefilter.keep(sess, document.links)

        # Identifiers first, and as `doi` tasks (`B-23`), so they reach the resolver
        # rather than a publisher's redirect. See docs/features/discovery.md#identifier-hosts.
        dois = await self._queue_dois(sess, claim, source, verdict.dois, seed_source="frontier")

        if not verdict.kept:
            log.debug(
                "frontier expansion queued nothing",
                extra={"url": claim.url, "dropped": verdict.dropped, "dois": dois},
            )
            return dois

        tiers = await source_tier_map(sess)
        queued = 0
        held: collections.Counter[str] = collections.Counter()
        # `B-242`: wait for this page's verdict, which `requeue_links` reads through the
        # parent recorded below; a link with no parent has no verdict coming.
        hold_until = None
        if source is not None and self._verdict_wait_s > 0:
            hold_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=self._verdict_wait_s)
        for url in verdict.kept:
            # §5.2: ranked by tier; `P2-20` brings forward a page whose kind rots fast.
            priority = priority_with_urgency(url, tiers, HALF_LIFE_DAYS)
            decision = self._admit(url, tiers)
            if not decision.queue:
                held[decision.reason] += 1
                continue
            await enqueue(
                sess,
                url,
                topic=claim.topic,
                seed_source="frontier",
                priority=decision.applied_to(priority),
                # `B-91`: which page carried the link, as a cited DOI already
                # records. Without it nobody can ask whether an on-topic page's
                # links are on-topic more often than an off-topic page's.
                parent_source_id=source.source_id if source is not None else None,
                hold_until=hold_until,
            )
            queued += 1

        log.info(
            "frontier expanded",
            extra={
                "url": claim.url,
                "task_id": claim.task_id,
                "considered": verdict.considered,
                "queued": queued,
                "dois": dois,
                "dropped": verdict.dropped,
                "held_by_host": dict(held),
                "english_alternate": alternate,
                "verdict_wait_s": self._verdict_wait_s if hold_until is not None else 0,
            },
        )
        return queued + dois + alternate

    async def _queue_english_alternate(
        self,
        sess: AsyncSession,
        claim: Claim,
        document: ExtractedDocument,
        source: Source | None = None,
    ) -> int:
        """Queue a non-English page's declared English version first (`B-57`).

        The original is kept; once both are stored `worker.docdupes` hides it behind its
        English version.
        """
        url = document.english_alternate
        language = (document.language or "").lower()
        if not url or language.startswith("en") or await already_queued(sess, [url]):
            return 0
        tiers = await source_tier_map(sess)
        decision = self._admit(url, tiers)
        if not decision.queue:
            return 0
        await enqueue(
            sess,
            url,
            topic=claim.topic,
            seed_source="frontier",
            priority=decision.applied_to(priority_with_urgency(url, tiers, HALF_LIFE_DAYS))
            + ENGLISH_ALTERNATE_BONUS,
            parent_source_id=source.source_id if source is not None else None,
        )
        return 1

    def _admit(self, url: str, tiers: dict) -> Decision:
        """Whether one more link to ``url``'s host is worth queueing (`B-48`)."""
        if self._hosts is None:
            return Decision(True)
        hostname = urlsplit(url).hostname or ""
        return self._hosts.admit(url, government=resolve_tier(hostname, tiers) == "government")

    async def refresh_hosts(self) -> None:
        """Re-read `host_scores`, which a scheduled pass rewrites hourly."""
        if self._hosts is None:
            return
        async with self._session_factory() as sess:
            self._hosts.replace(await load_host_scores(sess))

    async def _seed_citations(
        self,
        sess: AsyncSession,
        claim: Claim,
        document: ExtractedDocument | None,
        source: Source | None = None,
    ) -> int:
        """Turn this page's reference list into `doi` rows (`P1-14`, §6.1).

        Only `doi` citations; other identifiers stay in `sources.extra`. The prefilter
        does not run, only its `already_queued` check. See
        docs/features/discovery.md#cited-papers.
        """
        if document is None or not document.citations or self._resolver is None:
            return 0
        if self._hosts is not None and not self._hosts.follows_links_from(claim.url):
            # `B-48`, as for links: an off-topic page's reference list is
            # references about something else.
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

        fresh = await self._queue_dois(sess, claim, source, list(dois), seed_source="citation")
        if fresh:
            log.info(
                "citations queued for resolution",
                extra={
                    "url": claim.url,
                    "task_id": claim.task_id,
                    "cited": len(document.citations),
                    "queued": fresh,
                    "already_known": len(dois) - fresh,
                },
            )
        return fresh

    async def _queue_dois(
        self,
        sess: AsyncSession,
        claim: Claim,
        source: Source | None,
        identifiers: Sequence[str],
        *,
        seed_source: str,
    ) -> int:
        """Queue DOIs this page named, ranked by the page (`B-58`). Returns how many are new.

        An unlabelled page queues them at the unjudged rank, for `worker.requeue_dois` to
        settle; DOIs are normalised first. See docs/features/discovery.md#cited-paper-rank.
        """
        dois: dict[str, None] = {}
        for raw in identifiers:
            try:
                dois[normalise_doi(raw)] = None
            except DoiError:
                continue
        if not dois:
            return 0
        standing = (
            self._hosts.score(host_key(claim.url)).standing
            if self._hosts is not None
            else Standing.UNKNOWN
        )
        priority = cited_priority(
            source.topic_labels if source is not None else None,
            standing,
            await source_tier_map(sess),
        )
        return await enqueue_dois(
            sess,
            list(dois),
            topic=claim.topic,
            seed_source=seed_source,
            priority=priority,
            parent_source_id=source.source_id if source is not None else None,
        )

    async def _chunk(
        self,
        sess: AsyncSession,
        source: Source,
        document: ExtractedDocument | None,
        *,
        changed: bool,
        evidence: Evidence | None = None,
        stub: Stub | None = None,
    ) -> int:
        """Cut the extracted text into chunks and make them the source's set.

        Skipped when the checksum is unchanged. Done in the fetch pass because a
        `background` source keeps no raw file (§5.4). A stub (`B-256`) is one passage,
        never cleaned or cut. See docs/features/crawling.md#loop-stages.
        """
        if stub is not None and document is not None:
            return await self._chunk_stub(
                sess, source, document, changed=changed, evidence=evidence
            )
        if document is None or not document.has_text:
            if evidence is not None:
                record_doc_kind(source, classify(evidence))
            return 0
        if not changed and await chunk_count(sess, source.source_id):
            # The kind is left alone too (`B-59`): it was read off these same
            # bytes, or, for a source stored before kinds, `worker.dockind`
            # reads it from these chunks.
            log.debug(
                "content unchanged; chunks left alone",
                extra={"url": source.url, "source_id": source.source_id},
            )
            return 0

        # §5.3: page number for a paginated document, character offset otherwise;
        # `is_paginated` says which. `B-43`: furniture is left out of the chunks,
        # never cut from the text. The host is the one the page was served from.
        host = host_key((source.extra or {}).get("final_url") or source.url)
        if document.is_paginated:
            cut = await clean_cut(sess, source.source_id, host, pages=document.pages)
        else:
            cut = await clean_cut(sess, source.source_id, host, text=document.text)

        # `B-59`: classified from the text that would be chunked, as
        # `worker.dockind` reads it back from stored chunks.
        if evidence is not None:
            verdict = classify(
                dataclasses.replace(evidence, text="\n".join(c.text for c in cut.chunks))
            )
            record_doc_kind(source, verdict)
            if verdict.kind == "listing":
                # A listing's value is its links. Not chunked at all, so no
                # downstream pass can forget to filter it; earlier chunks are
                # superseded, never deleted.
                _, retired = await replace_chunks(sess, source.source_id, [])
                log.info(
                    "listing: links followed, text not chunked",
                    extra={
                        "url": source.url,
                        "source_id": source.source_id,
                        "rule": verdict.rule,
                        "retired": retired,
                    },
                )
                return 0
        written, deleted = await replace_chunks(sess, source.source_id, as_writes(cut.chunks))

        # In the same transaction as the chunks and the source row (`P1-10`), so
        # text and figures come from the same fetch.
        figures_written, _ = await replace_figures(
            sess,
            source.source_id,
            [
                FigureWrite(
                    caption=figure.caption,
                    alt_text=figure.alt_text,
                    image_url=figure.image_url,
                    page=figure.page,
                )
                for figure in document.figures
            ],
        )
        log.info(
            "chunked",
            extra={
                "url": source.url,
                "source_id": source.source_id,
                "chunks": written,
                "figures": figures_written,
                "replaced": deleted,
                "chars": document.char_count,
                "paginated": document.is_paginated,
                "lines_removed": cut.lines_removed,
                "kept_whole": cut.kept_original,
                "unreadable": cut.unreadable,
            },
        )
        return written

    async def _chunk_stub(
        self,
        sess: AsyncSession,
        source: Source,
        document: ExtractedDocument,
        *,
        changed: bool,
        evidence: Evidence | None,
    ) -> int:
        """Make a stub's one passage the source's set (`B-256`).

        Its kind is read from the address and the head alone: a teaser is not the text
        the link-density rules were measured on, and a stub is never a listing.
        """
        if not changed and await chunk_count(sess, source.source_id):
            return 0
        if evidence is not None:
            record_doc_kind(source, classify(dataclasses.replace(evidence, text="")))
        written, _ = await replace_chunks(
            sess,
            source.source_id,
            [ChunkWrite(text=document.text, chunk_index=0, page_or_offset=0)],
        )
        await replace_figures(sess, source.source_id, [])
        log.info(
            "paywalled: kept as a stub",
            extra={
                "url": source.url,
                "source_id": source.source_id,
                "chars": document.char_count,
                "rule": (source.extra or {}).get("stub", {}).get("rule"),
            },
        )
        return written

    async def _extract(self, claim: Claim, result: FetchResult) -> ExtractedDocument | None:
        """Turn the bytes into text, routed by media type (§6.6).

        Returns None when the format has no extractor, rather than raising: the
        source is metadata-only (§6.5). Extraction failing is not `NotKept`; the
        bytes are stored and can be re-extracted.
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
            # A deployment fault, not a property of this document: logged loudly,
            # once per document, so a worker that lost poppler is noticed.
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
        """Give a task back after an exception the loop did not expect, as a retry."""
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

        Cancelled rather than stopped on shutdown; the next tick finishes a prune.
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

        `unreachable` is the one to act on: query rows stop being claimed and the
        crawl winds down to an idle. See docs/features/crawling.md#loop-stages.
        """
        if self._search is None:
            return "absent"
        return "configured" if await self._search.healthy() else "unreachable"

    async def browser_health(self) -> str:
        """`configured` / `unreachable` / `absent` — for the health line (`P1-26`).

        `unreachable` is the silent failure: the fetcher degrades to static and
        nothing else notices.
        """
        browser = self._crawler.fetcher.browser
        if browser is None:
            return "absent"
        return "configured" if await browser.healthy() else "unreachable"

    async def housekeep(self) -> None:
        """One housekeeping pass. Public so a test — or an operator — can run it."""
        await self.refresh_hosts()
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
                # §12.5's daily health line, less `edges added` (the orchestrator's),
                # plus the novelty pass rate. See docs/features/crawling.md#loop-stages.
                "queue_depth": depth,
                "pending": depth.get("pending", 0),
                "fetch_attempts": health.attempts,
                "fetch_success_rate": health.success_rate,
                "by_outcome": health.by_outcome,
                "attempts_pruned": pruned,
                "tracked_domains": self._crawler.limiter.tracked_domains,
                "browser": browser,
                "search": search,
                "inbox": "configured" if self._inbox is not None else "absent",
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

    The second signal cancels every task in the loop. Where signal handlers cannot
    be installed (not POSIX, not the main thread) the worker runs without them.
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

    A worker that cannot read it starts with an empty blocklist rather than
    refusing to run.
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


def verdict_wait_from(frontier: dict) -> float:
    """Seconds a followed link waits for its page's verdict, from the ``frontier`` block.

    ``verdict_wait_hours`` (`B-242`); absent means the default. A value that is not a
    non-negative number is refused, never read as 0, which would quietly turn the wait off.
    """
    raw = frontier.get("verdict_wait_hours")
    if raw is None:
        return DEFAULT_VERDICT_WAIT_S
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw < 0:
        raise ValueError(f"frontier.verdict_wait_hours must be hours >= 0, got {raw!r}")
    return float(raw) * 3600.0


async def build_verdict_wait() -> float:
    """Read the followed-link verdict wait once, at startup (`B-242`, §13.1).

    An unreadable or malformed value is logged and the default used: a crawl that will
    not start is worse than one that waits three hours.
    """
    try:
        async with session() as sess:
            frontier = await frontier_settings(sess)
        wait = verdict_wait_from(frontier)
    except Exception:
        log.exception("could not read the verdict wait; using the default")
        return DEFAULT_VERDICT_WAIT_S
    log.info("followed links wait for their page's verdict", extra={"verdict_wait_s": wait})
    return wait


async def build_topic_vocabulary() -> TopicVocabulary:
    """Read the topic vocabulary once, at startup (`P1-28`).

    A worker that cannot read it crawls with none (every sitemap URL lands
    unmatched) rather than refusing to start.
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
    verdict_wait_s = await build_verdict_wait()
    hosts = HostPolicy()

    search = SearxClient.from_env()
    if search is not None:
        # `B-267`: GDELT beside SearXNG for news queries; off with MERIDIAN_GDELT=false.
        search = NewsSearch(search, GdeltClient.from_env())
    if search is None:
        # Louder than the browser's equivalent: without search the crawl drains
        # its frontier and idles like a finished one (`P1-34`, §6.4).
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

    # `B-264`: absent is a deployment without the connector, not a fault.
    inbox = inbox_root() if inbox_root().is_dir() else None
    if inbox is None:
        log.info("no connector inbox; upload rows will not be claimed")
    embedder = RemoteEmbedder.from_env()
    if embedder is None:
        # `B-251`: never the local model here; the crawl loop cannot spare the memory.
        log.warning("no MERIDIAN_EMBEDDER_URL; watched headlines are filtered by vocabulary")
    headlines = HeadlineFilter(embedder, session_factory=session)

    async with Fetcher(browser=browser) as fetcher:
        crawler = Crawler(session, fetcher=fetcher, limiter=DomainLimiter())
        worker = Worker(
            crawler,
            settings=settings,
            prefilter=prefilter,
            topics=topics,
            search=search,
            resolver=resolver,
            hosts=hosts,
            inbox=inbox,
            headlines=headlines,
            verdict_wait_s=verdict_wait_s,
        )
        await worker.refresh_hosts()
        install_signal_handlers(worker)
        try:
            return await worker.run()
        finally:
            await dispose_engines()
            if search is not None:
                await search.aclose()
            await resolver.aclose()
            if embedder is not None:
                await embedder.aclose()


def main() -> None:
    """Entry point: `python -m worker.main`."""
    configure_logging("worker")
    wait_before_start()  # `B-263`: never new code on the old schema
    settings = WorkerSettings.from_env()
    # One run_id for the process, on every record it emits. Ctrl-C and a second
    # signal are ordinary endings and print no traceback.
    quiet_exits = (KeyboardInterrupt, asyncio.CancelledError)
    with bind_run_id(f"worker-{settings.worker_id}"), contextlib.suppress(*quiet_exits):
        asyncio.run(run_worker(settings))


def _bibliography(
    document: ExtractedDocument | None,
    screening: Screening | None = None,
    *,
    url: str | None = None,
) -> dict[str, object]:
    """The `sources` columns an extracted document can fill (§5.2).

    Empty when there is no document, so nothing a previous fetch established is
    overwritten with nulls. Citations ride in `extra` (`P1-14`).
    """
    extra: dict[str, object] = {}
    if screening is not None and screening.findings:
        # §2.5's rule that nothing is destroyed applies here: the page is stored,
        # extracted and chunked exactly as normal, and this is a record beside
        # it. `P4-06` is what eventually acts on it.
        extra["injection"] = screening.as_record()

    if document is None:
        return {"extra": extra} if extra else {}
    # `B-69`: what the document declared is often a placeholder or the site's
    # name. Cleaned; else a heading-like first line, marked as such; else None,
    # which leaves whatever a previous fetch found. The declared one is kept.
    title = clean_title(
        document.title, publisher=document.publisher, host=urlsplit(url).hostname if url else None
    )
    if title is None and (title := title_from_text(document.text)) is not None:
        extra["title_from"] = "text"
    elif title is not None and (title_source := document.metadata_from.get("title")):
        extra["title_from"] = title_source
    if document.title and title != document.title:
        extra["declared_title"] = document.title
    # `B-234`: where the author, publisher and date came from, and what a rule refused.
    for field in ("author", "publisher", "publication_date"):
        if getattr(document, field) is not None and field in document.metadata_from:
            extra[FROM_KEY[field]] = document.metadata_from[field]
    if document.metadata_rejected:
        extra["rejected_metadata"] = dict(document.metadata_rejected)
    # `B-153`: an undeclared language is read from the text, and marked as such.
    language = document.language
    if language is None and (language := detect_language(document.text)) is not None:
        extra["language_from"] = "text"
    fields: dict[str, object] = {
        "title": title,
        "author": document.author,
        "publisher": document.publisher,
        "publication_date": document.publication_date,
        "language": language,
        "doi": document.doi,
        # Which tool read this, including the failure names (`P1-44`), so
        # `pdftotext-failed` is distinguishable from a document with no text.
        "extractor": document.extractor,
        # A scan has no text and is not merely empty: `mark_scanned` records
        # why and what would fix it, and must not be undone by this.
        "text_available": document.has_text,
    }
    if document.citations:
        extra["citations"] = [{"kind": c.kind, "value": c.value} for c in document.citations]
    if document.english_alternate:
        # `B-57`: what `worker.docdupes` reads to hide this page behind its
        # English version once that is in the corpus.
        extra["english_alternate"] = document.english_alternate
    if extra:
        fields["extra"] = extra
    return fields


def _decoded(content: bytes | str) -> str:
    """Page bytes as text, for reading its markup; undecodable bytes replaced."""
    return content.decode("utf-8", "replace") if isinstance(content, bytes) else content


def _unusable(document: ExtractedDocument | None, media_type: str) -> str | None:
    """Why a handed-in document cannot become a source, or None when it can (`B-264`).

    A scan is usable: it is stored and queued for OCR, as a fetched one is.
    """
    if document is None:
        return f"no_text: nothing could read this {media_type}"
    if document.needs_ocr:
        return None
    if not document.has_text:
        return f"no_text: {document.extractor or 'extraction'} found no text"
    if (missing := error_page_reason(document.title, document.text)) is not None:
        return f"error_page: {missing}"
    return None


def _upload_provenance(upload: Upload) -> dict[str, object]:
    """Who handed the document in and what they said, for `sources.extra["upload"]`."""
    fields = {
        "upload_id": upload.upload_id,
        "sha256": upload.sha256,
        "filename": upload.filename,
        "description": upload.description,
        "source_url": upload.source_url,
        "client_address": upload.client_address,
        "submitted_at": upload.created_at.isoformat() if upload.created_at else None,
    }
    return {key: value for key, value in fields.items() if value is not None}


def _backoff_for(consecutive_errors: int) -> float:
    return ERROR_BACKOFF_S[min(consecutive_errors, len(ERROR_BACKOFF_S)) - 1]


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
