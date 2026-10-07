"""Where a cited paper goes in the queue (task `B-58`).

A DOI is worth what its citing page is worth, by the page's content labels and its
host's standing (:func:`cited_priority`): the floor for an off-topic host or a page
about nothing, the peer-reviewed tier's priority for an on-topic page, and a modest
unjudged rank for a page not yet read, which `worker.requeue_dois` settles later.
Nothing here deletes or calls a model. See docs/features/discovery.md#cited-paper-rank.
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

    Never below :data:`UNJUDGED_PRIORITY`.
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
