"""Guessing a URL's topic from its path, mechanically (`P1-28`, §5.6, §10).

A sitemap entry is matched against approved gazetteer terms and topic names rather
than inheriting a topic. Ambiguous terms are excluded; a URL that matches nothing
gets no topic. See docs/features/discovery.md#topic-from-path.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re
from collections.abc import Iterable, Sequence
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.models import GazetteerTerm, TopicConfig

__all__ = [
    "MIN_MATCHABLE_LENGTH",
    "TopicVocabulary",
    "load_topic_vocabulary",
    "normalise_path",
]

#: Below this, a surface form is not matchable against a path: short acronyms
#: collide with ordinary segments and a path cannot disambiguate them.
MIN_MATCHABLE_LENGTH = 5

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Path segments that carry no topical meaning and would otherwise contribute
#: noise to matching. Kept deliberately short: this is not a stopword list, it
#: is the handful of segments every CMS emits.
_STRUCTURAL_SEGMENTS = frozenset(
    {"en", "sg", "index", "html", "htm", "aspx", "php", "page", "pages", "default"}
)


def normalise_path(url: str) -> str:
    """A URL's path as a space-separated, lowercased token string.

    Only the path, percent-decoded; query strings are mostly pagination and tracking.
    """
    path = urlsplit(url).path
    with contextlib.suppress(UnicodeDecodeError, ValueError):
        path = unquote(path)
    tokens = [t for t in _NON_ALNUM.sub(" ", path.lower()).split() if t]
    return " ".join(t for t in tokens if t not in _STRUCTURAL_SEGMENTS)


def _normalise_term(term: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", term.lower()).split())


@dataclasses.dataclass(frozen=True)
class TopicVocabulary:
    """Matchable surface forms, each mapped to the topics it implies.

    Built once per worker, not per URL: the gazetteer is config (§13.1).
    """

    #: Normalised phrase -> the topics it implies, in config order.
    phrases: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.phrases)

    def topics_for(self, url: str) -> tuple[str, ...]:
        """Every topic this URL's path implies, most specific match first.

        On whole tokens, so ``pub`` does not match ``public``.
        """
        haystack = f" {normalise_path(url)} "
        if not haystack.strip():
            return ()

        hits: list[tuple[int, tuple[str, ...]]] = []
        for phrase, topics in self.phrases.items():
            if f" {phrase} " in haystack:
                hits.append((len(phrase), topics))

        if not hits:
            return ()

        # Longest phrase first: "park connector network" is better evidence than
        # "park", and where they disagree the specific one should lead.
        hits.sort(key=lambda pair: pair[0], reverse=True)
        ordered: dict[str, None] = {}
        for _, topics in hits:
            for topic in topics:
                ordered[topic] = None
        return tuple(ordered)

    def best_topic(self, url: str) -> str | None:
        """The single topic to file this URL under, or None if unmatched.

        One topic rather than several because `queue.topic` holds one. The
        first is the longest match's, which is the most specific evidence the
        path offers.
        """
        topics = self.topics_for(url)
        return topics[0] if topics else None

    @classmethod
    def from_terms(
        cls,
        terms: Iterable[GazetteerTerm],
        topics: Iterable[str] = (),
    ) -> TopicVocabulary:
        """Build from the gazetteer, plus the topic names themselves.

        The names let a topic with no curated terms yet match a path segment of its name.
        """
        phrases: dict[str, tuple[str, ...]] = {}

        for topic in topics:
            # Kebab-case slugs normalise to spaced words, so a multi-word topic
            # matches a path segment however it was punctuated — hyphens,
            # underscores or spaces.
            phrase = _normalise_term(topic)
            if len(phrase) >= MIN_MATCHABLE_LENGTH:
                phrases[phrase] = (topic,)

        for term in terms:
            term_topics = tuple(term.topic_labels or ())
            if not term_topics:
                # A term with no topic association cannot answer the question
                # this class exists to answer.
                continue
            if term.ambiguous:
                # §5.5: candidates only, and a path cannot disambiguate.
                continue
            surface_forms: Sequence[str] = [term.canonical, *(term.aliases or ())]
            for form in surface_forms:
                phrase = _normalise_term(form or "")
                if len(phrase) < MIN_MATCHABLE_LENGTH:
                    continue
                existing = phrases.get(phrase)
                if existing is None:
                    phrases[phrase] = term_topics
                else:
                    merged: dict[str, None] = dict.fromkeys(existing)
                    merged.update(dict.fromkeys(term_topics))
                    phrases[phrase] = tuple(merged)
        return cls(phrases=phrases)


async def load_topic_vocabulary(sess: AsyncSession) -> TopicVocabulary:
    """Build the vocabulary from the database — active topics and the gazetteer.

    Approved terms only: an unapproved term is a model's proposal (§5.6). Read once at
    startup.
    """
    topic_stmt = select(TopicConfig.topic).where(TopicConfig.status == "active")
    topics = list((await sess.scalars(topic_stmt)).all())

    term_stmt = select(GazetteerTerm)
    if hasattr(GazetteerTerm, "approved"):
        term_stmt = term_stmt.where(GazetteerTerm.approved.is_(True))
    terms = (await sess.scalars(term_stmt)).all()

    return TopicVocabulary.from_terms(terms, topics)
