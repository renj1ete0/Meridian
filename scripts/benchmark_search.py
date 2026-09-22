#!/usr/bin/env python
"""Measure the retrieval stack (task P2-04's second half, spec §12.5).

`P2-04` is two jobs wearing one task id: build the HNSW index, and *measure
recall and latency at corpus size*. The index shipped in `v0.30.0`; this is the
measurement, and it is deliberately a script rather than a test because its
answers are numbers to read rather than assertions to pass.

What it measures, and what it refuses to pretend to measure:

**Index recall — yes, today.** Whether HNSW returns the same neighbours an exact
scan would is pure mechanics: no question set, no judgement, no corpus quality.
Query vectors are sampled from the corpus itself, which is legitimate here
because the question is "does the index find what brute force finds", and any
vector answers it.

**Latency — yes, today**, though the number only means something at corpus size.

**Arm overlap — yes, today.** How often the lexical and vector arms return the
same chunks *for the same question*. This is the cheapest evidence about
whether hybrid search is earning its second query: perfect agreement means
fusion is decoration, and no agreement at all usually means one arm is
misconfigured rather than that the two are beautifully complementary.

**The pairing is the whole measurement, and it was wrong.** Until `B-29` this
asked the vector arm to find neighbours of a randomly sampled chunk while
asking the lexical arm for an unrelated frequent word — two different
questions, so the arms could not agree and the figure was 0% by construction.
It then printed the warning about a misconfigured arm, which sends somebody
hunting a text-search bug that does not exist. Both arms now answer the same
probe: a chunk's own distinctive terms against its own vector, with the probe
chunk itself excluded, because a chunk trivially matching itself in both arms
would inflate agreement rather than measure it.

**Retrieval quality — no.** Does hybrid actually answer questions better? That
needs `P0-15`'s held-out questions, and it needs them written *before* the
results are visible, or the question set is a description of the results. Pass
`--questions` once they exist.

**The honesty check.** At a small corpus Postgres will sequentially scan rather
than use the index, because a seq scan over a few hundred vectors is genuinely
cheaper. Recall then compares exact search against exact search and is 1.0 by
construction, which looks like a perfect index and is not evidence of anything.
So every recall figure is reported alongside whether the planner actually used
the index, and the script says so loudly when it did not.

Usage:
    make bench-search
    python scripts/benchmark_search.py --k 10 --trials 50
    python scripts/benchmark_search.py --questions docs/held_out_questions.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from meridian_core.db import normalize_url
from meridian_core.models import Chunk
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.search import DEFAULT_CANDIDATES, search

#: `ef_search` values to sweep. pgvector's default is 40; the tradeoff is recall
#: against latency, and the useful output is the point where recall stops
#: improving faster than latency degrades.
EF_SEARCH_SWEEP = (40, 100, 200)


@dataclass
class Census:
    chunks: int
    embedded: int
    duplicates: int
    sources: int

    @property
    def searchable(self) -> int:
        return self.embedded - self.duplicates


async def census(sess: AsyncSession) -> Census:
    async def n(sql: str) -> int:
        return int(await sess.scalar(text(sql)) or 0)

    return Census(
        chunks=await n("SELECT count(*) FROM chunks"),
        embedded=await n("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL"),
        duplicates=await n("SELECT count(*) FROM chunks WHERE duplicate_of IS NOT NULL"),
        sources=await n("SELECT count(*) FROM sources"),
    )


async def sample_vectors(sess: AsyncSession, trials: int) -> list[list[float]]:
    """Query vectors drawn from the corpus.

    Sampling real vectors rather than random ones matters: a random 1024-dim
    vector is near-orthogonal to everything, so every candidate is equidistant
    and the index has no structure to exploit. That measures nothing except the
    scan rate.
    """
    # Through the mapped column, not a raw `text()` query. A raw select returns
    # the vector as its text representation — asyncpg has no reason to know the
    # type — and `list()` of that is a list of single characters, which fails
    # confusingly much later, at the next bind rather than here.
    rows = await sess.execute(
        select(Chunk.embedding)
        .where(Chunk.embedding.is_not(None))
        .order_by(func.random())
        .limit(trials)
    )
    return [[float(x) for x in row[0]] for row in rows]


async def _neighbours(
    sess: AsyncSession, vector: Sequence[float], k: int, exact: bool
) -> list[int]:
    if exact:
        # The only reliable way to force a brute-force scan. pgvector has no
        # "ignore the index" hint, so the planner is told the alternatives are
        # unavailable and it falls back to what is exact by definition.
        await sess.execute(text("SET LOCAL enable_indexscan = off"))
        await sess.execute(text("SET LOCAL enable_bitmapscan = off"))

    rows = await sess.execute(_nearest_sql(vector, k))
    return [row[0] for row in rows]


#: The vector has to be bound through pgvector's own type. Passed as a plain
#: string to a `CAST(:v AS vector)` the driver expands it character by
#: character, and Postgres rejects a "vector" whose first element is "[".
def _nearest_sql(vector: Sequence[float], k: int, *, explain: bool = False):
    prefix = "EXPLAIN " if explain else ""
    return text(
        prefix + "SELECT chunk_id FROM chunks WHERE embedding IS NOT NULL "
        "ORDER BY embedding <=> :v LIMIT :k"
    ).bindparams(
        bindparam("v", value=list(vector), type_=Vector(EMBEDDING_DIM)),
        bindparam("k", value=k),
    )


async def index_is_used(sess: AsyncSession, vector: Sequence[float], k: int) -> bool:
    plan = await sess.execute(_nearest_sql(vector, k, explain=True))
    return "ix_chunks_embedding_hnsw" in "\n".join(row[0] for row in plan)


async def recall_at_k(sess: AsyncSession, vectors: list[list[float]], k: int, ef: int) -> float:
    """Mean |ANN ∩ exact| / k over the sampled queries."""
    hits = []
    for vector in vectors:
        await sess.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef)}"))
        approx = set(await _neighbours(sess, vector, k, exact=False))
        await sess.rollback()  # drop the SET LOCAL before measuring the exact side

        truth = set(await _neighbours(sess, vector, k, exact=True))
        await sess.rollback()

        hits.append(len(approx & truth) / max(1, len(truth)))
    return statistics.fmean(hits) if hits else 0.0


async def latency(sess: AsyncSession, vectors: list[list[float]], terms: list[str], k: int):
    """Wall time per method. Milliseconds, p50 and p95."""
    timings: dict[str, list[float]] = {"lexical": [], "vector": [], "hybrid": []}

    for i, vector in enumerate(vectors):
        term = terms[i % len(terms)] if terms else ""
        for label, query, vec in (
            ("lexical", term, None),
            ("vector", "", vector),
            ("hybrid", term, vector),
        ):
            start = time.perf_counter()
            await search(sess, query, query_vector=vec, limit=k)
            timings[label].append((time.perf_counter() - start) * 1000)

    return timings


#: Words too short or too common to distinguish a passage. Deliberately tiny —
#: `websearch_to_tsquery` ANDs its terms, so every word added to a probe makes
#: the lexical arm stricter, and a list long enough to be principled would
#: leave probes with nothing to search for.
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "that",
        "this",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "as",
        "at",
        "from",
        "it",
        "its",
        "their",
        "have",
        "has",
        "had",
        "not",
        "but",
        "which",
        "we",
        "our",
        "can",
        "may",
    ]
)


def probe_terms(text_value: str, *, count: int = 3) -> str:
    """A few distinctive words from a chunk, as a lexical query.

    Longest-first rather than TF-IDF: the ranking only has to be better than
    "the first three words", and a scoring pass over the corpus to choose probe
    terms would be a second retrieval system inside the benchmark for it.

    **Joined with `or`, and that is not a detail.** `websearch_to_tsquery` ANDs
    bare terms, so three rare words co-occur in exactly one chunk — the probe
    itself, which the overlap count excludes. The first version of this did
    that and reported the lexical arm finding *nothing*, 100% vector-only,
    which looks like a broken tsvector and was a broken probe. `or` asks the
    lexical arm the question the vector arm is being asked: passages about any
    of these things, not the one passage about all of them.
    """
    seen: list[str] = []
    for word in sorted(set(text_value.lower().split()), key=len, reverse=True):
        stripped = "".join(ch for ch in word if ch.isalnum())
        if len(stripped) > 4 and stripped not in _STOPWORDS:
            seen.append(stripped)
        if len(seen) >= count:
            break
    return " or ".join(seen)


async def sample_probes(sess: AsyncSession, trials: int) -> list[tuple[int, str, list[float]]]:
    """Chunks to ask both arms about: id, distinctive terms, and vector.

    One row gives all three, which is the point — the arms have to be answering
    the same question or their overlap is a coincidence rather than a finding.
    """
    rows = await sess.execute(
        select(Chunk.chunk_id, Chunk.text, Chunk.embedding)
        .where(Chunk.embedding.is_not(None))
        .order_by(func.random())
        .limit(trials)
    )
    probes = []
    for chunk_id, text_value, vector in rows:
        terms = probe_terms(text_value or "")
        if terms:
            probes.append((chunk_id, terms, [float(x) for x in vector]))
    return probes


async def arm_overlap(sess: AsyncSession, probes, k: int) -> dict[str, float]:
    """How often the two arms agree, and how often each finds something alone.

    The number that matters is the middle one. Total agreement means the second
    query is buying nothing; total disagreement is more often a misconfigured
    arm than genuine complementarity.
    """
    both = only_lexical = only_vector = 0
    for chunk_id, terms, vector in probes:
        result = await search(sess, terms, query_vector=vector, limit=k)
        for hit in result.hits:
            if hit.chunk_id == chunk_id:
                # The probe finds itself in both arms, always. Counting it
                # would report agreement the corpus did not produce.
                continue
            if hit.lexical_rank and hit.vector_rank:
                both += 1
            elif hit.lexical_rank:
                only_lexical += 1
            else:
                only_vector += 1

    total = both + only_lexical + only_vector
    if not total:
        return {}
    return {
        "found by both": both / total,
        "lexical only": only_lexical / total,
        "vector only": only_vector / total,
    }


async def frequent_terms(sess: AsyncSession, n: int = 12) -> list[str]:
    """Real terms from the corpus, so the lexical arm has something to find.

    A benchmark whose queries match nothing measures an empty result path and
    reports excellent latency for it.
    """
    rows = await sess.execute(
        text(
            "SELECT word FROM ts_stat($$SELECT search_vector FROM chunks$$) "
            "WHERE length(word) > 4 ORDER BY ndoc DESC LIMIT :n"
        ),
        {"n": n},
    )
    return [row[0] for row in rows]


async def score_questions(sess: AsyncSession, path: Path, k: int) -> None:
    """Recall@k and MRR per method against a held-out question set (`P0-15`).

    Format: [{"question": "...", "relevant_urls": ["https://..."]}, ...]
    URLs rather than chunk ids, because chunk ids change on every re-crawl
    (`replace_chunks`) and a question set has to outlive the corpus it was
    written against.
    """
    questions = json.loads(path.read_text())
    print(f"\n=== Held-out questions ({len(questions)}) — {path} ===")
    print("  NOTE: judged without a query embedder, so `vector` and `hybrid` are")
    print("  omitted unless this script is extended to embed the questions.\n")

    per_method: dict[str, list[float]] = {"lexical": []}
    for item in questions:
        relevant = set(item.get("relevant_urls") or [])
        if not relevant:
            continue
        result = await search(sess, item["question"], limit=k)
        found = [h.url in relevant for h in result.hits]
        per_method["lexical"].append(1.0 if any(found) else 0.0)

    for method, scores in per_method.items():
        if scores:
            print(f"  {method:8} recall@{k}: {statistics.fmean(scores):.3f}  (n={len(scores)})")


def percentiles(values: list[float]) -> str:
    if not values:
        return "no samples"
    ordered = sorted(values)
    p50 = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return f"p50 {p50:7.2f}ms   p95 {p95:7.2f}ms   n={len(values)}"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=10, help="neighbours per query")
    parser.add_argument("--trials", type=int, default=30, help="sampled query vectors")
    parser.add_argument("--questions", type=Path, help="held-out question set (P0-15)")
    args = parser.parse_args()

    url = os.environ.get("PG_RO_URL") or os.environ.get("PG_RW_URL")
    if not url:
        raise SystemExit(
            "PG_RO_URL or PG_RW_URL must be set — `make bench-search` exports .env.dev"
        )

    engine = create_async_engine(normalize_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as sess:
        counts = await census(sess)
        print("=== Corpus ===")
        print(
            f"  sources {counts.sources}   chunks {counts.chunks}   "
            f"embedded {counts.embedded}   near-duplicates {counts.duplicates}"
        )
        print(f"  searchable by default: {counts.searchable}")

        if counts.embedded == 0:
            raise SystemExit("\nNothing is embedded. Run `python -m worker.embed` first.")

        vectors = await sample_vectors(sess, args.trials)
        terms = await frequent_terms(sess)
        print(f"  query terms drawn from the corpus: {', '.join(terms[:8])}")

        used = await index_is_used(sess, vectors[0], args.k)
        print("\n=== Index ===")
        print(f"  HNSW used by the planner: {'yes' if used else 'NO'}")
        if not used:
            print("  ! At this corpus size Postgres prefers a sequential scan, which is")
            print("  ! exact. Every recall figure below is therefore 1.0 by construction")
            print("  ! and is NOT evidence that the index works. Re-run after `P1-16`.")

        print("\n=== Recall@k vs exact search ===")
        for ef in EF_SEARCH_SWEEP:
            value = await recall_at_k(sess, vectors, args.k, ef)
            print(f"  ef_search {ef:4}   recall@{args.k}: {value:.4f}")

        print("\n=== Latency ===")
        for method, samples in (await latency(sess, vectors, terms, args.k)).items():
            print(f"  {method:8} {percentiles(samples)}")

        # Probes rather than the sampled vectors and frequent terms used
        # above: those are deliberately unrelated to each other, which is right
        # for latency and meaningless for agreement (`B-29`).
        probes = await sample_probes(sess, args.trials)
        overlap = await arm_overlap(sess, probes, args.k)
        if overlap:
            print("\n=== Arm agreement ===")
            for label, share in overlap.items():
                print(f"  {label:16} {share:6.1%}")

            # The same honesty problem the index check has, in a different
            # place. The vector arm takes `DEFAULT_CANDIDATES` neighbours; when
            # the corpus is smaller than that, it returns *everything*, so every
            # lexical hit is necessarily also a vector hit and agreement is 100%
            # by arithmetic. Reporting that as "fusion is buying nothing" would
            # be a confident conclusion drawn from a corpus that cannot support
            # one.
            saturated = counts.searchable <= DEFAULT_CANDIDATES
            if saturated:
                print(f"  ! Only {counts.searchable} searchable chunks against a candidate pool")
                print(f"  ! of {DEFAULT_CANDIDATES}: the vector arm returns the entire corpus, so")
                print("  ! agreement is 100% by arithmetic. This figure means nothing yet.")
            elif overlap.get("found by both", 0) > 0.95:
                print("  ! The arms almost always agree — fusion is buying very little here.")
            elif overlap.get("found by both", 0) < 0.02:
                print("  ! The arms almost never agree. Two explanations needing")
                print("  ! different responses: a misconfigured text-search arm, or a")
                print("  ! vector arm answering a narrower question than it looks.")
                print("  ! On this corpus it was the second — 42% of the vector arm's")
                print("  ! top ten came from the probe's own document, about four")
                print("  ! distinct sources per ten hits (`B-30`). Measure the source")
                print("  ! concentration before changing the tsvector configuration.")

        if args.questions:
            await score_questions(sess, args.questions, args.k)
        else:
            print("\n=== Retrieval quality ===")
            print("  Not measured. Needs `P0-15`'s held-out questions, written before")
            print("  the results are visible. Pass --questions once they exist.")

    await engine.dispose()


if __name__ == "__main__":
    random.seed(0)
    asyncio.run(main())
