"""Which hosts are worth following links into (task `B-48`).

From the share of a host's examined pages that are on a topic: off-topic hosts get no
links followed (government hosts are down-ranked instead), unknown hosts are explored a
few links at a time, unknown subdomains of an off-topic site go last (`B-113`), unknown
hosts that on-topic pages elsewhere link to are explored first (`B-150`), and no host holds
more than :data:`MAX_PENDING` queued links. Nothing deletes or calls a model. See
docs/features/discovery.md#host-scores and docs/features/discovery.md#vouched-hosts.
"""

from __future__ import annotations

import dataclasses
import enum
from collections import Counter
from collections.abc import Mapping

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tld import get_fld

from .boilerplate import host_key
from .logging import get_logger
from .models import HostScore, LinkVouch, QueueTask, Source

log = get_logger(__name__)

#: Pages that must have been examined before a host's share means anything.
#: Below it, one landing page and one privacy notice would condemn a site.
MIN_EXAMINED = 20

#: Under this share of examined pages on a topic, a host is off-topic. Set from a
#: measured crawl; see docs/features/discovery.md#host-scores.
OFFTOPIC_SHARE = 0.05

#: Queued links allowed to a host nobody has judged yet. Was 50 (`B-61`); ten is
#: enough to learn what a host is about.
EXPLORE_PENDING = 10

#: Queued links allowed to any one host.
MAX_PENDING = 500

#: At or above this share an on-topic host's links keep the priority their tier
#: gives them; below it the priority scales down with the share.
FULL_SHARE = 0.25

#: The priority an off-topic *government* link is queued at: above nothing,
#: below everything that earned its place.
DOWNRANKED_PRIORITY = 1

#: Added to a *proven* host's links (`B-115`): judged on a topic at or above
#: :data:`FULL_SHARE`. Larger than any tier priority, so proven links rank above
#: unjudged ones; search keeps its reserved claims.
PROVEN_BOOST = 65

#: Other hosts with an on-topic page linking to an unjudged host before it counts as
#: vouched for (`B-150`). One: measured, a single such site already lifts the host's
#: eventual on-topic share several-fold. See docs/features/discovery.md#vouched-hosts.
VOUCHED_MIN = 1

#: Added to a vouched-for unjudged host's links: above unjudged, below proven.
VOUCHED_BOOST = 30

#: Queued links allowed to a vouched-for host nobody has judged yet.
VOUCHED_PENDING = 25

#: Distinct linked hosts recorded per page; a link farm is not more evidence.
MAX_VOUCHES_PER_PAGE = 200


class Standing(enum.StrEnum):
    ON_TOPIC = "on_topic"
    UNKNOWN = "unknown"
    OFF_TOPIC = "off_topic"


@dataclasses.dataclass(frozen=True)
class Score:
    examined: int = 0
    on_topic: int = 0
    pending: int = 0
    vouched: int = 0

    @property
    def share(self) -> float:
        return self.on_topic / self.examined if self.examined else 0.0

    @property
    def standing(self) -> Standing:
        if self.examined < MIN_EXAMINED:
            return Standing.UNKNOWN
        return Standing.OFF_TOPIC if self.share < OFFTOPIC_SHARE else Standing.ON_TOPIC

    @property
    def is_vouched(self) -> bool:
        """Unjudged, and linked from on-topic pages on enough other hosts (`B-150`)."""
        return self.standing is Standing.UNKNOWN and self.vouched >= VOUCHED_MIN


@dataclasses.dataclass(frozen=True)
class Decision:
    queue: bool
    #: None keeps the priority the caller computed; a number replaces it.
    priority: int | None = None
    reason: str = "ok"
    #: Multiplies the caller's priority when ``priority`` is None.
    weight: float = 1.0
    #: Added to the caller's priority when ``priority`` is None (`B-115`).
    boost: int = 0

    def applied_to(self, computed: int) -> int:
        """The priority to queue at, given the one the tier and urgency gave."""
        if self.priority is not None:
            return self.priority
        if self.weight >= 1.0:
            return computed + self.boost
        return max(DOWNRANKED_PRIORITY + 1, round(computed * self.weight))


def decide(score: Score, *, government: bool, pending: int) -> Decision:
    """Whether to queue one more link to a host, given what is known about it.

    ``pending`` is the host's queued links now — the stored count plus what this
    process has queued since — so the caps hold between recomputations.
    """
    standing = score.standing
    if standing is Standing.OFF_TOPIC:
        if government:
            if pending >= EXPLORE_PENDING:
                return Decision(False, reason="off_topic_capped")
            return Decision(True, DOWNRANKED_PRIORITY, "off_topic_government")
        return Decision(False, reason="off_topic")
    if score.is_vouched:
        if pending >= VOUCHED_PENDING:
            return Decision(False, reason="vouched_capped")
        return Decision(True, reason="vouched", boost=VOUCHED_BOOST)
    cap = EXPLORE_PENDING if standing is Standing.UNKNOWN else MAX_PENDING
    if pending >= cap:
        return Decision(False, reason=f"{standing.value}_capped")
    if standing is Standing.ON_TOPIC and score.share < FULL_SHARE:
        return Decision(True, reason="on_topic_thin", weight=score.share / FULL_SHARE)
    if standing is Standing.ON_TOPIC:
        return Decision(True, reason="proven", boost=PROVEN_BOOST)
    return Decision(True, reason=standing.value)


def site_of(host: str | None) -> str | None:
    """The registrable domain a host belongs to, or None if it is one itself.

    Read from the public-suffix list, so `a.b.gov.uk` belongs to `b.gov.uk`
    and two unrelated sites under a shared suffix are never one site.
    """
    if not host:
        return None
    site = get_fld(f"https://{host}", fail_silently=True)
    return site if site and site != host else None


def decide_on_site(site: Score, *, pending: int) -> Decision | None:
    """The verdict an unjudged host takes from its site, if the site has one.

    Only an off-topic site speaks for its subdomains, and only to order them
    last: an on-topic site says nothing about a subdomain it has never served.
    """
    if site.standing is not Standing.OFF_TOPIC:
        return None
    if pending >= EXPLORE_PENDING:
        return Decision(False, reason="site_off_topic_capped")
    return Decision(True, DOWNRANKED_PRIORITY, "site_off_topic")


def follows_links(score: Score) -> bool:
    """Whether a page on this host should have its own links followed.

    Not for an off-topic host: what an off-topic site links to is, almost
    always, more of itself.
    """
    return score.standing is not Standing.OFF_TOPIC


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


async def recompute(sess: AsyncSession) -> int:
    """Rebuild `host_scores` from labels and the queue. Returns hosts written.

    Does not commit. In Python rather than SQL because the host is `boilerplate.host_key` of the
    final URL — the same function everything else uses — and a second definition in SQL is how two
    passes come to disagree about what a host is.
    """
    examined: Counter[str] = Counter()
    on_topic: Counter[str] = Counter()
    rows = await sess.execute(
        select(Source.url, Source.extra["final_url"].astext, Source.topic_labels).where(
            Source.topic_labels.is_not(None)
        )
    )
    for url, final_url, labels in rows:
        host = host_key(final_url or url)
        if not host:
            continue
        examined[host] += 1
        if labels:
            on_topic[host] += 1

    pending: Counter[str] = Counter()
    queued = await sess.scalars(
        select(QueueTask.url_or_query).where(
            QueueTask.status == "pending", QueueTask.task_type == "url"
        )
    )
    for url in queued:
        host = host_key(url)
        if host:
            pending[host] += 1

    vouched = await vouches_by_host(sess)

    hosts = set(examined) | set(pending) | set(vouched)
    await sess.execute(delete(HostScore))
    if hosts:
        await sess.execute(
            insert(HostScore),
            [
                {
                    "host": h,
                    "examined": examined[h],
                    "on_topic": on_topic[h],
                    "pending": pending[h],
                    "vouched": vouched.get(h, 0),
                }
                for h in sorted(hosts)
            ],
        )
    log.info("host scores recomputed", extra={"hosts": len(hosts)})
    return len(hosts)


async def load(sess: AsyncSession) -> dict[str, Score]:
    rows = await sess.scalars(select(HostScore))
    return {r.host: Score(r.examined, r.on_topic, r.pending, r.vouched) for r in rows}


async def vouches_by_host(sess: AsyncSession) -> dict[str, int]:
    """Per linked host, the other hosts that have an on-topic page linking to it.

    Hosts, not pages: one site's many pages linking to the same place are one voice.
    """
    rows = await sess.execute(
        select(LinkVouch.host, Source.url, Source.extra["final_url"].astext)
        .join(Source, Source.source_id == LinkVouch.source_id)
        .where(Source.topic_labels.is_not(None), Source.topic_labels != [])
    )
    voices: dict[str, set[str]] = {}
    for host, url, final_url in rows:
        voucher = host_key(final_url or url)
        if voucher and voucher != host:
            voices.setdefault(host, set()).add(voucher)
    return {host: len(v) for host, v in voices.items()}


async def record_vouches(sess: AsyncSession, source_id: int, page_url: str, links) -> int:
    """Record which other hosts a page links to. Flushes; does not commit.

    Called with every link the page carries, before any is dropped as already queued, so
    a host keeps the evidence of every page that links to it. Returns hosts recorded.
    """
    page_host = host_key(page_url)
    hosts = sorted({h for h in map(host_key, links) if h and h != page_host})
    hosts = hosts[:MAX_VOUCHES_PER_PAGE]
    if not hosts:
        return 0
    await sess.execute(
        pg_insert(LinkVouch)
        .values([{"host": h, "source_id": source_id} for h in hosts])
        .on_conflict_do_nothing()
    )
    return len(hosts)


class HostPolicy:
    """The fetch loop's view of `host_scores`, with its own queued counts on top.

    The stored ``pending`` is as of the last recomputation; links this process
    queues since are counted here, so a cap is not a suggestion for the hour
    between two passes.
    """

    def __init__(self, scores: Mapping[str, Score] | None = None) -> None:
        self._queued: Counter[str] = Counter()
        self.replace(scores or {})

    def replace(self, scores: Mapping[str, Score]) -> None:
        """New scores from a fresh read; the local counts start again with them."""
        self._scores = dict(scores)
        self._queued.clear()
        # Each site's judged pages, summed over every host under it (`B-113`).
        examined: Counter[str] = Counter()
        on_topic: Counter[str] = Counter()
        for host, score in self._scores.items():
            site = site_of(host) or host
            examined[site] += score.examined
            on_topic[site] += score.on_topic
        self._sites = {s: Score(examined[s], on_topic[s]) for s in examined}

    def site_score(self, host: str | None) -> Score:
        """What the host's whole site has shown, over all its hosts."""
        return self._sites.get(site_of(host) or host or "", Score())

    def score(self, host: str | None) -> Score:
        return self._scores.get(host or "", Score())

    def follows_links_from(self, url: str | None) -> bool:
        return follows_links(self.score(host_key(url)))

    def admit(self, url: str, *, government: bool) -> Decision:
        """Decide one link and, if it is queued, count it against its host."""
        host = host_key(url)
        if not host:
            return Decision(False, reason="no_host")
        score = self.score(host)
        pending = score.pending + self._queued[host]
        decision = None
        # A vouch is evidence about this host; a site's verdict only about its siblings.
        if (
            score.standing is Standing.UNKNOWN
            and not score.is_vouched
            and site_of(host) is not None
        ):
            decision = decide_on_site(self.site_score(host), pending=pending)
        if decision is None:
            decision = decide(score, government=government, pending=pending)
        if decision.queue:
            self._queued[host] += 1
        return decision
