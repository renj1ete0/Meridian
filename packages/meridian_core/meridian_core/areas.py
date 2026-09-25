"""Areas: the corpus as nested clusters of passages (task P6-30) — pure core.

The arithmetic only: clustering passage embeddings into three nested levels
and naming each cluster by its most distinctive terms.
:mod:`meridian_core.areabuild` runs it over the corpus and writes a build.

**An area is a cluster, not a topic.** Nothing here reads topic labels: a
cluster is where passages sit close together in the embedding space, and its
name is the words that set its passages apart from the rest of the corpus.
Topics remain a filter over areas, never their boundaries.

**Deterministic.** Seeded k-means with a k-means++ start, nested top down,
so the same vectors give the same areas; a recomputation moves only when the
corpus does.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

import numpy as np

from .embedtext import embedding_view

_SEED = 0

#: Words too common in any prose to say anything about a cluster.
_STOP_TEXT = """a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each few
    for from further had has have having he her here hers him his how i if in into is it its
    itself just may might more most must my no nor not now of off on once only or other our
    ours out over own per same she should so some such than that the their theirs them then
    there these they this those through to too under until up upon us very was we were what
    when where which while who whom why will with within without would you your yours one two
    new use used using well however therefore thus page pages click here http https www com
    org html pdf"""
#: What documents are made of rather than about (`B-71`): repository and
#: citation furniture, table placeholders, section labels. Found naming live
#: areas — "model · arxiv · title", "nan · arxiv · cross-list", "university ·
#: doi · research", "volume number · virus · measles".
_FURNITURE_TEXT = """nan null none arxiv doi isbn issn title abstract volume vol issue
    number pp preprint cross-list crossref pubmed pmc scholar google html pdf retrieved
    accessed available online copyright rights reserved license licence cookie cookies
    menu navigation login sign skip content figure fig table section chapter appendix
    et al ibid journal proceedings conference university press author authors editor
    submitted revised accepted published version download view full text cite citation
    references"""
FURNITURE = frozenset(_FURNITURE_TEXT.split())

STOPWORDS = frozenset(_STOP_TEXT.split()) | FURNITURE

_WORD = re.compile(r"[^\W\d_][\w\-]*[^\W_]|[^\W\d_]{3,}", re.UNICODE)


def _normalise_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def kmeans(
    vectors: np.ndarray, k: int, *, iterations: int = 25, seed: int = _SEED
) -> tuple[np.ndarray, np.ndarray]:
    """Spherical k-means: ``(labels, centroids)`` with unit-length centroids.

    Cosine is the similarity the search's vector arm uses, so clusters are cut
    by the same notion of "close". ``k`` larger than the number of vectors is
    clamped, and an empty cluster is re-seeded at the vector worst served by
    its centroid rather than left as a centroid nobody belongs to.
    """
    if vectors.ndim != 2 or len(vectors) == 0:
        raise ValueError("kmeans needs a non-empty 2D array")
    if k < 1:
        raise ValueError("k must be at least 1")
    x = _normalise_rows(vectors.astype(np.float32))
    n = len(x)
    k = min(k, n)
    rng = np.random.default_rng(seed)

    # k-means++ start, on cosine distance.
    centroids = np.empty((k, x.shape[1]), dtype=np.float32)
    centroids[0] = x[rng.integers(n)]
    closest = 1.0 - x @ centroids[0]
    for i in range(1, k):
        weights = np.clip(closest, 0, None)
        total = float(weights.sum())
        pick = int(rng.integers(n)) if total <= 0 else int(rng.choice(n, p=weights / total))
        centroids[i] = x[pick]
        closest = np.minimum(closest, 1.0 - x @ centroids[i])

    labels = np.zeros(n, dtype=np.int64)
    for _ in range(iterations):
        sims = x @ centroids.T
        new = sims.argmax(axis=1)
        for c in range(k):
            members = new == c
            if not members.any():
                worst = int(sims[np.arange(n), new].argmin())
                new[worst] = c
                members = new == c
            mean = x[members].sum(axis=0)
            norm = float(np.linalg.norm(mean))
            centroids[c] = mean / norm if norm else x[members][0]
        if np.array_equal(new, labels):
            break
        labels = new
    return labels, centroids


def nest(
    vectors: np.ndarray, regions: int, areas: int, leaves: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Three nested levels by k-means inside k-means, top down.

    Regions first over every vector; then each region is cut into areas, and
    each area into leaves, in proportion to its share of the vectors (at least
    one each). Returns ``(leaf_of_vector, leaf_centroids, area_of_leaf,
    region_of_area)``, numbered consecutively.

    **Top down, not bottom up.** Grouping leaf centroids upward (centroid
    linkage) was tried first and chained on a real corpus: one region took 97%
    of the passages, because a dense mass merges with each neighbour in turn.
    Splitting from the top asks each level the same question — where does this
    set of passages divide — so every level is balanced by the same measure.
    """
    x = _normalise_rows(np.asarray(vectors, dtype=np.float32))
    n = len(x)
    if n == 0:
        raise ValueError("nest needs at least one vector")
    if min(regions, areas, leaves) < 1:
        raise ValueError("every level needs at least one cluster")
    leaf_of = np.empty(n, dtype=np.int64)
    centroids: list[np.ndarray] = []
    area_of_leaf: list[int] = []
    region_of_area: list[int] = []

    region_labels, _ = kmeans(x, regions)
    for region_no, r in enumerate(np.unique(region_labels)):
        in_region = np.flatnonzero(region_labels == r)
        k_areas = max(1, round(areas * len(in_region) / n))
        area_labels, _ = kmeans(x[in_region], k_areas)
        for a in np.unique(area_labels):
            in_area = in_region[area_labels == a]
            area_no = len(region_of_area)
            region_of_area.append(region_no)
            k_leaves = max(1, round(leaves * len(in_area) / n))
            leaf_labels, leaf_centroids = kmeans(x[in_area], k_leaves)
            for leaf in np.unique(leaf_labels):
                leaf_of[in_area[leaf_labels == leaf]] = len(centroids)
                centroids.append(leaf_centroids[leaf])
                area_of_leaf.append(area_no)
    return (
        leaf_of,
        np.stack(centroids),
        np.asarray(area_of_leaf, dtype=np.int64),
        np.asarray(region_of_area, dtype=np.int64),
    )


#: Whitespace-delimited tokens that are addresses, not words: a host with a
#: known suffix, or anything with a path in it. Chunk text carries links, and
#: a cluster named by the sites it cites ("edu · cornell · arxiv") says where
#: its passages came from, not what they are about.
_ADDRESS = re.compile(
    r"\S*\w\.(?:com|org|edu|gov|net|int|io|ac|co|uk|sg|au|de|fr|jp|info)\b\S*|\S*\w/\S*",
    re.IGNORECASE,
)


def readable(text: str) -> str:
    """The words of a passage: link syntax reduced to its text, addresses dropped."""
    return _ADDRESS.sub(" ", embedding_view(text))


def tokens(text: str) -> list[str]:
    """Lower-cased words worth counting: no stopwords, no numbers, three letters up."""
    words = (m.group(0).lower() for m in _WORD.finditer(readable(text)))
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def _stems(term: str) -> set[str]:
    """Each word of a term, with a plain English plural ``-s`` taken off."""
    return {
        w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
        for w in term.split()
    }


def distinctive_terms(
    texts_by_cluster: Sequence[Sequence[str]],
    *,
    top: int = 3,
    sources_by_cluster: Sequence[Sequence[int]] | None = None,
    min_sources: int = 2,
) -> list[list[str]]:
    """Each cluster's most distinctive terms, by class-based TF-IDF.

    ``tf(t, c) · log(1 + A / f(t))`` where ``A`` is the mean word count per
    cluster and ``f(t)`` the term's count across all clusters: frequent here,
    rare elsewhere. Unigrams and bigrams both compete.

    With ``sources_by_cluster`` (the source id of each text), a term must
    appear in passages from ``min_sources`` different sources in the cluster —
    otherwise one long document's own phrasing names the whole area. A
    cluster with fewer sources than that is held to as many as it has, so it
    still gets a name (and its source count says how much to trust it).

    Source spread is counted as a document frequency — each source's text
    contributes each term once — rather than a set of sources per term, which
    on a real corpus held millions of sets and took gigabytes.
    """
    counts: list[Counter[str]] = []
    spread: list[Counter[str]] = []
    needed: list[int] = []
    for c, texts in enumerate(texts_by_cluster):
        counter: Counter[str] = Counter()
        by_source: dict[int, set[str]] = {}
        for i, text in enumerate(texts):
            words = tokens(text)
            grams = words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
            counter.update(grams)
            if sources_by_cluster is not None:
                by_source.setdefault(sources_by_cluster[c][i], set()).update(grams)
        df: Counter[str] = Counter()
        for grams in by_source.values():
            df.update(grams)
        counts.append(counter)
        spread.append(df)
        needed.append(min(min_sources, max(1, len(by_source))))
    if not counts:
        return []
    corpus: Counter[str] = Counter()
    for counter in counts:
        corpus.update(counter)
    mean_words = sum(sum(c.values()) for c in counts) / len(counts) or 1.0

    names: list[list[str]] = []
    for c, counter in enumerate(counts):
        scored = []
        for term, tf in counter.items():
            if sources_by_cluster is not None and spread[c][term] < needed[c]:
                continue
            scored.append((tf * math.log(1 + mean_words / corpus[term]), term))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        chosen: list[str] = []
        for _, term in scored:
            # A bigram and its own words, or a word and its plural, say the
            # same thing twice.
            if any(_stems(term) & _stems(other) for other in chosen):
                continue
            chosen.append(term)
            if len(chosen) == top:
                break
        names.append(chosen)
    return names
