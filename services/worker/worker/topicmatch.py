"""Guessing a URL's topic from its path, mechanically (`P1-28`, §5.6, §10).

Frontier expansion inherits the linking page's topic, and for a link that is a
fair guess: a page about a subject tends to link to pages about that subject. A
sitemap breaks the assumption completely. It is the site's whole index — a large
institutional one runs to several thousand URLs spanning everything the
organisation publishes — and tagging all of it with whichever topic the page
that triggered discovery happened to carry would be wrong about nearly every
row.

Wrong in a way that spreads, too. Every URL that gets crawled runs frontier
expansion of its own and passes its topic on, so a bad label is not a bad row —
it is a bad subtree. And coverage scoring (§5.3) counts sources per topic to
report where evidence is *thin*, which is the one thing this system promises to
be honest about. Nine thousand mislabelled rows make a thin topic look
comprehensively covered.

So each URL is matched against the gazetteer instead, which already carries
``topic_labels`` on every term precisely because §5.6 needs the association. A
URL whose path names a term belonging to a topic is about that topic no matter
which page led here, and a URL that matches nothing gets no topic at all rather
than a borrowed one.

**Ambiguous terms are excluded**, and that is the whole reason the gazetteer
carries the flag. A short acronym routinely expands to two or three unrelated
things, one of them from an entirely different field. §5.5 resolves those from
document context — co-occurring entities, the document's topic, whether a full
form appears nearby. A URL path has no context whatsoever, so the honest answer
is not to guess: an unmatched URL stays unmatched and visible, which is §5.5's
middle band applied to a path.
"""

from __future__ import annotations

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

#: Below this, a surface form is not matchable against a path. Short acronyms
#: collide with ordinary path segments, and a path offers nothing to
#: disambiguate them with. The `ambiguous` flag catches the ones a human
#: noticed; this catches the rest by construction.
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

    Only the path. Query strings on government sites are almost entirely
    pagination, session and tracking parameters, and matching against them
    produces confident nonsense. Percent-escapes are decoded first so that a
    path written as ``%2Dbus`` matches the same way ``-bus`` does.
    """
    path = urlsplit(url).path
    try:
        path = unquote(path)
    except (UnicodeDecodeError, ValueError):
        pass
    tokens = [t for t in _NON_ALNUM.sub(" ", path.lower()).split() if t]
    return " ".join(t for t in tokens if t not in _STRUCTURAL_SEGMENTS)


def _normalise_term(term: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", term.lower()).split())


@dataclasses.dataclass(frozen=True)
class TopicVocabulary:
    """Matchable surface forms, each mapped to the topics it implies.

    Built once per worker rather than per URL: the gazetteer is config (§13.1),
    it changes when someone approves a term in Admin, and a sitemap of nine
    thousand entries would otherwise be nine thousand queries for an answer that
    does not move.
    """

    #: Normalised phrase -> the topics it implies, in config order.
    phrases: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.phrases)

    def topics_for(self, url: str) -> tuple[str, ...]:
        """Every topic this URL's path implies, most specific match first.

        Matching is on whole tokens — ``" phrase "`` inside ``" path "`` — so
        ``pub`` does not match ``public`` and ``bus`` does not match ``business``.
        Substring matching without that boundary is the classic way a matcher
        looks like it works and tags half the corpus.
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

        The topic names matter more than they look. A gazetteer starts thin and
        grows — `P5-02` harvests it from text the crawl has already read — so a
        vocabulary sourced only from it would assign nothing at all for a topic
        nobody has hand-written terms for yet, and a newly added topic would sit
        inert while every URL that mentions it by name went unmatched. A topic
        name matching a path segment of the same name needs no curation and no
        model, and it is right far more often than it is wrong.

        Both sources are read from the database, so neither is hardcoded: topics
        come from `topic_config` and terms from `gazetteer`, and both grow
        through Admin and through harvesting without any code change.
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

    Both sources are rows, not literals: topics come from `topic_config` and
    terms from `gazetteer`, so adding a topic in Admin or approving a harvested
    term changes what the crawler recognises with no code change and no
    redeploy. Nothing about the vocabulary is written down in this file.

    Approved terms only. An unapproved row is a model's proposal awaiting a
    human (§5.6), and letting one steer what gets crawled would be the model
    writing to the frontier through the back door.

    Read once at startup rather than per URL. It is config (§13.1) — it changes
    when someone edits it, not between two pages of one crawl — and a sitemap of
    nine thousand entries would otherwise be nine thousand queries.
    """
    topic_stmt = select(TopicConfig.topic).where(TopicConfig.status == "active")
    topics = list((await sess.scalars(topic_stmt)).all())

    term_stmt = select(GazetteerTerm)
    if hasattr(GazetteerTerm, "approved"):
        term_stmt = term_stmt.where(GazetteerTerm.approved.is_(True))
    terms = (await sess.scalars(term_stmt)).all()

    return TopicVocabulary.from_terms(terms, topics)
