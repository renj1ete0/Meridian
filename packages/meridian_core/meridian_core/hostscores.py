"""Which hosts are worth following links into (task `B-48`).

The frontier queued every link a fetched page carried, each inheriting the
topic of the page that linked to it, ranked by the linked domain's tier. On a
real crawl that turned a handful of on-topic seeds into a crawl of whatever
large sites they happened to link to: a law school's statute library, a
hospital's condition pages, a university's human-resources pages — each
labelled with a transport topic and ranked at the top of the queue, because an
academic domain ranked as scholarly. Measured by content (`P2-21`), 94% of what
the crawl had fetched was about none of its topics.

The fix uses what the corpus already knows. `P2-21` labels every source by its
content; a host whose examined pages are almost never about any topic is a host
whose *next* page is unlikely to be either. So:

- **off-topic host** — at least :data:`MIN_EXAMINED` pages examined and under
  :data:`OFFTOPIC_SHARE` of them on a topic. A link to it is not queued, and a
  page *on* it does not have its links followed. A government host is the
  exception: it is down-ranked, never dropped, because an official source is
  the one most often served as landing pages that label poorly, and missing it
  is worse than fetching a few pages too many.
- **unknown host** — fewer pages examined than that. It is explored, but only
  up to :data:`EXPLORE_PENDING` queued links, so a site nobody has judged yet
  cannot fill the queue before anyone has.
- **any host** — never more than :data:`MAX_PENDING` queued links, however
  relevant. Diversity is a property of the queue, not only of each link.
- **unknown host on an off-topic site** (`B-113`) — a subdomain nobody has
  judged, whose registrable domain is off-topic across its judged siblings. A
  site that serves every office, county or blog from its own subdomain would
  otherwise be explored ten links at a time per subdomain, and a loop run found
  dozens of such siblings taking a third of the followed-link fetches. It is
  queued at :data:`DOWNRANKED_PRIORITY`, capped as unknown, and never dropped:
  a hospital and a law school can share a registrable domain and nothing else,
  so the sibling verdict orders the subdomain last rather than excluding it,
  and its own verdict replaces the site's once it has one.

Nothing here deletes, and nothing here calls a model: the scores are written by
a scheduled pass from labels a separate pass wrote, and read here as numbers.
"""

from __future__ import annotations

import dataclasses
import enum
from collections import Counter
from collections.abc import Mapping

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from tld import get_fld

from .boilerplate import host_key
from .logging import get_logger
from .models import HostScore, QueueTask, Source

log = get_logger(__name__)

#: Pages that must have been examined before a host's share means anything.
#: Below it, one landing page and one privacy notice would condemn a site.
MIN_EXAMINED = 20

#: Under this share of examined pages on a topic, a host is off-topic. Measured
#: on a real crawl, the sites that dominated the drift sat at 0–1%, general
#: university hosts at 0–5%, and the useful scholarly and research-centre hosts
#: at 18–46%.
OFFTOPIC_SHARE = 0.05

#: Queued links allowed to a host nobody has judged yet. Was 50: a 12-hour run
#: showed that thousands of unjudged hosts at 50 links each is a breadth-first
#: crawl of every large institutional website the crawl brushes against, long
#: before labelling can judge any of them (`B-61`). Ten is enough to learn what a
#: host is about.
EXPLORE_PENDING = 10

#: Queued links allowed to any one host.
MAX_PENDING = 500

#: At or above this share an on-topic host's links keep the priority their
#: tier gives them; below it the priority scales down with the share. A host
#: just over :data:`OFFTOPIC_SHARE` — an institutional repository of
#: everything, a preprint server's listings for every field — is not off-topic,
#: but one page in twenty is not a reason to rank its links beside a research
#: centre's one in four.
FULL_SHARE = 0.25

#: The priority an off-topic *government* link is queued at: above nothing,
#: below everything that earned its place.
DOWNRANKED_PRIORITY = 1

#: Added to a *proven* host's links (`B-115`): judged on a topic at or above
#: :data:`FULL_SHARE`. Larger than any tier priority, so every proven link
#: ranks above every link to a host nobody has judged. The tier ranks a
#: source's authority, not whether its next page is worth fetching: measured
#: over three loop runs, a proven host's next page was on a topic about half the
#: time and an unjudged host's about one time in seven, yet proven hosts in a
#: low tier queued below unjudged government links and took a thirtieth of the
#: fetches. Search keeps its reserved claims, so this orders followed links
#: among themselves and never starves discovery.
PROVEN_BOOST = 65


class Standing(enum.StrEnum):
    ON_TOPIC = "on_topic"
    UNKNOWN = "unknown"
    OFF_TOPIC = "off_topic"


@dataclasses.dataclass(frozen=True)
class Score:
    examined: int = 0
    on_topic: int = 0
    pending: int = 0

    @property
    def share(self) -> float:
        return self.on_topic / self.examined if self.examined else 0.0

    @property
    def standing(self) -> Standing:
        if self.examined < MIN_EXAMINED:
            return Standing.UNKNOWN
        return Standing.OFF_TOPIC if self.share < OFFTOPIC_SHARE else Standing.ON_TOPIC


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

    hosts = set(examined) | set(pending)
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
                }
                for h in sorted(hosts)
            ],
        )
    log.info("host scores recomputed", extra={"hosts": len(hosts)})
    return len(hosts)


async def load(sess: AsyncSession) -> dict[str, Score]:
    rows = await sess.scalars(select(HostScore))
    return {r.host: Score(r.examined, r.on_topic, r.pending) for r in rows}


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
        if score.standing is Standing.UNKNOWN and site_of(host) is not None:
            decision = decide_on_site(self.site_score(host), pending=pending)
        if decision is None:
            decision = decide(score, government=government, pending=pending)
        if decision.queue:
            self._queued[host] += 1
        return decision
