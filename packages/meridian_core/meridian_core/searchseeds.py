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
  that link-following from scholarly pages never would;
- §7.4's mechanism 5, forced non-English seeds: a concept in another language's
  own words (`translations`, from Wikipedia), under SearXNG's ``:lang``
  prefix, and as news in that language.

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

#: SearXNG's category bang for scholarly engines (`B-111`). They answered, and on
#: topic (28–30 of 30), while every general web engine refused this client.
SCIENCE_BANG = "!science"

#: Evidence-shaped suffixes: what a question about a concept is usually after.
EVIDENCE = ("evaluation", "study", "evidence", "outcomes")

#: §7.4 mechanism 1: phrasings that find the other side.
COUNTER = ("criticism of {}", "{} problems", "why {} failed")

#: Which §7.4 mechanism each shape is an instance of (`P5-05`), written to
#: `queue.seed_mechanism` so a query's yield can be read per mechanism. The
#: counter-phrasings are mechanism 1 at *topic* level: they fire for every
#: topic, not because a node's sources were seen to agree (that needs stance,
#: which is a model's output and does not exist yet — see `diversity`).
MECHANISM_BY_KIND = {
    "counter": "counter_seed",
    "description": "naive_phrasing",
    "language": "non_english",
    "language_news": "non_english",
}

#: Shapes that widen a topic without answering one of the five failure modes.
#: Listed rather than implied, so a new shape has to be put in one set or the
#: other (a test fails otherwise).
WIDENING_KINDS = frozenset(
    {"concept", "evidence", "news", "with_topic", "pair", "facet", "science"}
)

_SPACE = re.compile(r"\s+")

#: Words a description phrase starts with that carry no subject ("the", "their").
_LEADING = frozenset(
    [
        "a",
        "an",
        "the",
        "their",
        "its",
        "his",
        "her",
        "our",
        "of",
        "in",
        "on",
        "to",
        "for",
        "with",
        "and",
        "or",
        "including",
        "such",
        "as",
        "around",
        "used",
    ]
)
#: A phrase starting with one of these is a clause ("how they are regulated"),
#: which says what the operator wants to know, not what to search for.
_CLAUSE = frozenset(["how", "where", "what", "when", "why", "which", "who", "whose", "whether"])
_FACET_SPLIT = re.compile(r"[,;:.()]|\band\b|\bor\b")
#: Facets longer than this are sentences, not subjects.
MAX_FACET_WORDS = 5


def description_facets(description: str | None) -> list[str]:
    """The subjects a topic's description lists (`B-103`), in order, once each.

    A description written for people lists what the topic covers — "costs,
    pricing, fares, funding and financing". Each item is a subject a search can
    ask for. Split on punctuation and on "and"/"or", leading function words
    dropped, clauses ("how they are regulated") and single short words left
    out. Deterministic, and nothing here is a model's output (§2.1).
    """
    out: dict[str, None] = {}
    for piece in _FACET_SPLIT.split((description or "").lower()):
        words = [w for w in re.findall(r"[a-z][a-z'-]*", piece)]
        while words and words[0] in _LEADING:
            words = words[1:]
        if not words or words[0] in _CLAUSE or len(words) > MAX_FACET_WORDS:
            continue
        facet = " ".join(words)
        if len(facet) >= 4:
            out.setdefault(facet, None)
    return list(out)


@dataclasses.dataclass(frozen=True)
class TopicSeedInput:
    topic: str
    description: str | None
    terms: tuple[str, ...]
    #: ``(language, words)`` for this topic's concepts in other languages.
    translations: tuple[tuple[str, str], ...] = ()


@dataclasses.dataclass(frozen=True)
class Query:
    topic: str
    text: str
    #: Which shape produced it, for the report and for measuring what works.
    kind: str

    @property
    def mechanism(self) -> str | None:
        """The §7.4 mechanism this query answers, or None for plain widening."""
        return MECHANISM_BY_KIND.get(self.kind)


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
        add(f"{SCIENCE_BANG} {concept}", "science")
    for term in terms:
        if name.lower() not in term.lower():
            add(f"{term} {name}", "with_topic")
    for i, first in enumerate(terms):
        for second in terms[i + 1 :]:
            add(f"{first} {second}", "pair")
    for lang, words in sorted(set(topic.translations)):
        add(f":{lang} {words}", "language")
        add(f"{NEWS_BANG} :{lang} {words}", "language_news")
    # The description's own list of subjects (`B-103`): a topic with no approved
    # vocabulary had only its name to search with, and ran out of queries.
    facets = description_facets(topic.description)
    for facet in facets:
        if facet != name.lower() and name.lower() not in facet:
            add(f"{facet} {name}", "facet")
            add(f"{SCIENCE_BANG} {facet} {name}", "science")
    for i, first in enumerate(facets):
        for second in facets[i + 1 :]:
            add(f"{first} {second}", "facet")
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
    kinds_first: Sequence[str] = ("news", "science", "counter", "language", "concept", "evidence"),
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
