"""One polite fetch: policy, robots, rate limit, conditional request (P1-04).

:mod:`worker.fetch` knows how to get bytes from a URL safely. It deliberately
does not know whether it *should*, how often, or what it already has — those are
decisions about a domain rather than about a request, and they need the database.
This module is the seam: it resolves the policy, asks robots.txt, waits its turn,
attaches the validators from last time, and only then calls the fetcher.

The order matters and is the cheapest-refusal-first order:

1. **Is the domain blocked?** A policy row lookup. Costs no network at all.
2. **Does robots.txt allow it?** One cached request per origin per day.
3. **Wait for the domain's slot.** Only after the request is known to be one
   worth making — queueing behind a delay for a URL that was going to be refused
   anyway is time the crawl does not get back.
4. **What do we already have?** ETag and Last-Modified from the previous fetch,
   which turns an unchanged page into a 304 and no body.

Every path through :meth:`Crawler.fetch` then ends in the same place: one
``fetch_attempts`` row, and the policy consequence that outcome carries
(`P1-19`, `P1-05`). Recording here rather than in the worker loop is deliberate
— the loop is not the only caller a crawler ever grows, and a log that depends
on each caller remembering to write it is a log with holes in exactly the paths
nobody thought about.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.attempts import record_attempt
from meridian_core.logging import get_logger
from meridian_core.models import Source
from meridian_core.policy import ResolvedPolicy, apply_fetch_outcome, resolve_policy
from meridian_core.tiering import registrable_domain

from .fetch import Fetcher, FetchResult
from .ratelimit import DomainLimiter
from .robots import RobotsCache

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def conditional_headers(source: Source | None, *, enabled: bool = True) -> dict[str, str]:
    """Revalidation headers for a URL already fetched once.

    ``If-None-Match`` is sent alone when an ETag exists. RFC 9110 §13.1.2 has the
    origin ignore ``If-Modified-Since`` whenever ``If-None-Match`` is present, so
    sending both is bytes on the wire that cannot change any answer.
    """
    if not enabled or source is None:
        return {}
    if source.etag:
        return {"If-None-Match": source.etag}
    if source.last_modified:
        return {"If-Modified-Since": source.last_modified}
    return {}


def validators(headers: Mapping[str, str]) -> dict[str, str | None]:
    """The validators worth storing from a response, for the next fetch."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return {"etag": lowered.get("etag"), "last_modified": lowered.get("last-modified")}


class Crawler:
    """Fetches URLs politely, under the policy the database holds for them."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        fetcher: Fetcher,
        limiter: DomainLimiter | None = None,
        robots: RobotsCache | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._fetcher = fetcher
        self._limiter = limiter or DomainLimiter()
        # robots.txt is fetched through the same limiter as everything else. A
        # crawler that honours a domain's delay for its pages and then fetches
        # robots.txt whenever it likes has misread which of the two is the
        # courtesy.
        self._robots = robots or RobotsCache(self._fetch_in_slot)

    @property
    def limiter(self) -> DomainLimiter:
        return self._limiter

    @property
    def robots(self) -> RobotsCache:
        return self._robots

    async def _fetch_in_slot(self, url: str, policy: ResolvedPolicy) -> FetchResult:
        """Fetch statically, holding the domain's rate-limit slot."""
        async with self._limiter.slot(
            registrable_domain(url),
            concurrency=policy.concurrency_per_domain,
            delay_ms=policy.next_delay_ms(),
        ):
            return await self._fetcher.fetch_static(url, policy)

    async def fetch(
        self, url: str, *, task_id: int | None = None, attempt_number: int = 1
    ) -> FetchResult:
        """Fetch ``url`` under its domain's policy, or say why it was not.

        Whatever happens, the attempt is recorded and its consequence applied
        before the result is handed back.
        """
        domain = registrable_domain(url)

        async with self._session_factory() as sess:
            policy = await resolve_policy(sess, domain)
            source = await sess.scalar(select(Source).where(Source.url == url))
            conditional = conditional_headers(source, enabled=policy.conditional_requests)

        result = await self._attempt(url, policy, conditional, task_id=task_id)
        await self._record(result, policy, task_id=task_id, attempt_number=attempt_number)
        return result

    async def _attempt(
        self,
        url: str,
        policy: ResolvedPolicy,
        conditional: dict[str, str],
        *,
        task_id: int | None,
    ) -> FetchResult:
        """Everything between the policy lookup and the result. Records nothing."""
        domain = policy.domain

        if not policy.is_fetchable:
            return _refused(url, "blocked", f"domain is {policy.status}")

        delay_ms = policy.next_delay_ms()

        if policy.respect_robots:
            rules = await self._robots.rules_for(url, policy)
            if not rules.allows(url):
                log.info(
                    "robots.txt refuses this path",
                    extra={"url": url, "domain": domain, "task_id": task_id},
                )
                return _refused(url, "robots_denied", "disallowed by robots.txt")
            if policy.respect_crawl_delay and rules.crawl_delay_s:
                # The site's own figure wins when it is the slower of the two.
                # Taking it as an instruction to speed *up* would be reading a
                # request for restraint as permission (§14.2).
                delay_ms = max(delay_ms, int(rules.crawl_delay_s * 1000))

        async with self._limiter.slot(
            domain, concurrency=policy.concurrency_per_domain, delay_ms=delay_ms
        ) as waited_ms:
            result = await self._fetcher.fetch(url, policy, extra_headers=conditional)

        log.info(
            "fetched",
            extra={
                "url": url,
                "domain": domain,
                "task_id": task_id,
                "outcome": result.outcome,
                "status": result.status_code,
                "bytes": len(result.content),
                "render_mode": result.render_mode,
                "elapsed_ms": result.elapsed_ms,
                "waited_ms": int(waited_ms),
                "conditional": bool(conditional),
            },
        )
        return result

    async def _record(
        self,
        result: FetchResult,
        policy: ResolvedPolicy,
        *,
        task_id: int | None,
        attempt_number: int,
    ) -> None:
        """Write the attempt row and apply what it means for the domain.

        One transaction for both. A log saying a domain failed five times beside
        a policy row that counted none of them is worse than either alone, and
        two commits is how that happens.
        """
        async with self._session_factory() as sess:
            await record_attempt(
                sess,
                domain=policy.domain,
                url=result.requested_url,
                outcome=result.outcome,
                status_code=result.status_code,
                error_detail=result.detail or None,
                duration_ms=result.elapsed_ms,
                bytes_fetched=len(result.content),
                task_id=task_id,
                attempt_number=attempt_number,
            )
            blocked_now = await apply_fetch_outcome(
                sess,
                policy.domain,
                result.outcome,
                status_code=result.status_code,
                blocked_after=policy.blocked_after_failures,
            )
            await sess.commit()

        if blocked_now:
            # The limiter keeps per-domain state forever otherwise, and a domain
            # that will never be fetched again does not need a semaphore.
            self._limiter.forget(policy.domain)
            log.warning(
                "domain auto-blocked after consecutive failures",
                extra={
                    "domain": policy.domain,
                    "url": result.requested_url,
                    "outcome": result.outcome,
                    "status": result.status_code,
                    "threshold": policy.blocked_after_failures,
                },
            )


def _refused(url: str, outcome: str, detail: str) -> FetchResult:
    return FetchResult(requested_url=url, final_url=url, outcome=outcome, detail=detail)
