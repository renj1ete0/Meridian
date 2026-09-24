"""Where a cited paper goes in the queue (task `B-58`).

Every `doi` row used to be queued at one fixed priority below every link and
search result, so on a live crawl none was ever claimed. Raising them all would
not have fixed it: many were named by pages from hosts the crawl has since
found to be off-topic (`B-48`), and a paper cited by an off-topic page is about
something else.

So a DOI is worth what the page citing it is worth, judged by the two things
the corpus already knows about that page:

- **its content** — ``sources.topic_labels`` (`P2-21`): non-empty is on a
  topic, empty is examined and about none, NULL is not examined yet;
- **its host** — :class:`~meridian_core.hostscores.Standing`, from the share of
  that host's examined pages that are on a topic.

The rule, as :func:`cited_priority`:

- off-topic host, or a page examined and about nothing → :data:`FLOOR_PRIORITY`,
  where every DOI was before: kept, claimed only when nothing else waits;
- on-topic page on a host that is not off-topic → :func:`on_topic_priority`,
  the peer-reviewed tier's priority. A resolved DOI *is* a scholarly work, and
  one that an on-topic page's author chose to cite is at least as good a
  candidate as a peer-reviewed page the crawl followed a link to, so it sits
  level with those. It stays below a peer-reviewed *search result* (tier + 5)
  and below the queries themselves (70): a search result answers a question
  somebody asked about a topic, and a query is how the crawl finds new ground
  rather than deepening ground it already has. It sits above every other
  tier's search results, which is what "competes with search" means here —
  without that, a steady supply of search results would starve it the way the
  old floor did;
- not examined yet → :data:`UNJUDGED_PRIORITY`, modest: above informal and
  press links, below institutional ones. The page was fetched for a topic, so
  its references are more likely useful than an arbitrary link; but nothing
  has confirmed it, and `worker.requeue_dois` re-ranks the row once the page is
  labelled — which it usually is not at the moment its references are queued,
  because labels come from vectors that arrive after the fetch.

Several pages can cite one DOI; the best of them decides (`worker.requeue_dois`).
A paper one on-topic page cites is not made worse by an off-topic page citing
it as well.

Nothing here deletes, and nothing calls a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .hostscores import Standing
from .tiering import priority_for_tier

#: Where a DOI with nothing to recommend it waits. The priority every `doi` row
#: had before `B-58`, above the unmatched-sitemap floor and the host gate's
#: held links, below every tier.
FLOOR_PRIORITY = 3

#: A DOI whose citing page is not labelled yet.
UNJUDGED_PRIORITY = 20

#: The tier whose priority an on-topic page's citation takes.
CITED_TIER = "peer_reviewed"


def on_topic_priority(tiers: dict[str, Any]) -> int:
    """The priority of a DOI cited by an on-topic page, from the tier map.

    Read from the map rather than fixed, so that re-weighting the tiers moves
    cited papers with the peer-reviewed links they stand beside. Never below
    :data:`UNJUDGED_PRIORITY`: a map that ranked scholarship low must not rank
    a confirmed citation under an unconfirmed one.
    """
    return max(priority_for_tier(CITED_TIER, tiers), UNJUDGED_PRIORITY + 1)


def cited_priority(labels: Sequence[str] | None, standing: Standing, tiers: dict[str, Any]) -> int:
    """The priority of a DOI cited by one page with these labels on this host."""
    if standing is Standing.OFF_TOPIC:
        return FLOOR_PRIORITY
    if labels is None:
        return UNJUDGED_PRIORITY
    if not labels:
        return FLOOR_PRIORITY
    return on_topic_priority(tiers)
