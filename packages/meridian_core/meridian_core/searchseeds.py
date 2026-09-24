"""Fresh search queries, so the crawl keeps widening (task `B-51`, spec §7.4).

A corpus built by following links converges: each page links to its own
neighbourhood, and after the first few seeds the crawl was 98% link-following
and 2% search, fetching more of the same few sites. §7.4 reserves a share of
the seed budget for seeds that do not come from the corpus's own links; this
module writes them, mechanically, from what the database says each topic is.

**No model.** Queries are built from each topic's name, description and
approved vocabulary, in a handful of shapes:

- the concept on its own, and beside its topic's name;
- two concepts of one topic together, which reaches material neither finds
  alone;
- evidence-shaped phrasings (``… evaluation``, ``… study``);
- §7.4's counter-seeds (``criticism of …``, ``… problems``), because a crawl
  that only confirms its vocabulary walks toward consensus;
- news, through SearXNG's ``!news`` category, so reporting reaches a corpus
  that link-following from scholarly pages never would.

Every query is new: the generator is handed every query already queued, in any
status, and never repeats one. The order is shuffled by a seed the caller
passes, so a run is reproducible and successive runs differ.
"""

from __future__ import annotations

import dataclasses
import random
import re
from collections.abc import Iterable, Sequence

#: SearXNG's category bang for news engines.
NEWS_BANG = "!news"

#: Evidence-shaped suffixes: what a question about a concept is usually after.
EVIDENCE = ("evaluation", "study", "evidence", "outcomes")

#: §7.4 mechanism 1: phrasings that find the other side.
COUNTER = ("criticism of {}", "{} problems", "why {} failed")

_SPACE = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class TopicSeedInput:
    topic: str
    description: str | None
    terms: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Query:
    topic: str
    text: str
    #: Which shape produced it, for the report and for measuring what works.
    kind: str


def topic_words(topic: str) -> str:
    return _SPACE.sub(" ", topic.replace("-", " ").replace("_", " ")).strip()


def _clean(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def candidates(topic: TopicSeedInput) -> list[Query]:
    """Every query this topic could produce, before de-duplication and sampling."""
    name = topic_words(topic.topic)
    terms = sorted({_clean(t) for t in topic.terms if t and len(_clean(t)) >= 4}, key=str.lower)
    concepts = [name, *[t for t in terms if t.lower() != name.lower()]]
    out: list[Query] = []

    def add(text: str, kind: str) -> None:
        out.append(Query(topic.topic, _clean(text), kind))

    for concept in concepts:
        add(concept, "concept")
        for suffix in EVIDENCE:
            add(f"{concept} {suffix}", "evidence")
        for pattern in COUNTER:
            add(pattern.format(concept), "counter")
        add(f"{NEWS_BANG} {concept}", "news")
    for term in terms:
        if name.lower() not in term.lower():
            add(f"{term} {name}", "with_topic")
    for i, first in enumerate(terms):
        for second in terms[i + 1 :]:
            add(f"{first} {second}", "pair")
    if topic.description:
        # The description's opening words: an outsider's phrasing of the topic,
        # §7.4's mechanism 3 in the operator's own words.
        words = _clean(topic.description).split()[:10]
        if len(words) >= 3:
            add(" ".join(words), "description")
    return out


def plan(
    topics: Sequence[TopicSeedInput],
    *,
    already: Iterable[str],
    per_topic: int,
    seed: int,
    kinds_first: Sequence[str] = ("news", "counter", "concept", "evidence"),
) -> list[Query]:
    """``per_topic`` new queries for each topic.

    Shapes are interleaved rather than drawn in proportion to how many
    candidates each has — pairs vastly outnumber everything else, and a run of
    nothing but pairs would never ask for news or counter-evidence. So each run
    first takes one of each shape in ``kinds_first`` that still has a new query,
    then fills the rest at random.
    """
    if per_topic < 1:
        raise ValueError("per_topic must be at least 1")
    seen = {_clean(q).lower() for q in already}
    rng = random.Random(seed)
    chosen: list[Query] = []
    for topic in topics:
        fresh = [q for q in candidates(topic) if q.text.lower() not in seen]
        rng.shuffle(fresh)
        picked: list[Query] = []
        for kind in kinds_first:
            match = next((q for q in fresh if q.kind == kind and q not in picked), None)
            if match is not None and len(picked) < per_topic:
                picked.append(match)
        for query in fresh:
            if len(picked) >= per_topic:
                break
            if query not in picked:
                picked.append(query)
        for query in picked:
            seen.add(query.text.lower())
        chosen.extend(picked)
    return chosen
