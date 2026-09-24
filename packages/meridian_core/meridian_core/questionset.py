"""The held-out question set, as data a runner can use (task P2-22, spec §14.1).

Loading `eval/questions.yaml`, saying whether it may be scored at all, asking
each question through :func:`meridian_core.search.search` exactly as typed
(:func:`run_set`), a heuristic *proposal* for each item's grade, the run-file
shape, and a comparison between two runs. `scripts/run_question_set.py` is the
command that writes `eval/runs/<date>.yaml`.

Three rules shape it, all from `eval/README.md`:

- **A proposal is not a score.** An agent (or this heuristic) may propose a
  grade; the operator's grade is the score. The run record keeps them in two
  separate fields, and :func:`compare` reads only the operator's — so a run
  nobody has graded compares as "not graded", never as a number.
- **An unreviewed set says so.** Items drafted with `reviewed: false` are a
  draft, and a run against a draft is labelled as one rather than silently
  treated as the go/no-go set.
- **The questions are never edited here.** The loader reads; nothing writes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import statistics
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path

import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, Edge, Entity, Source
from .search import search

#: What the heuristic calls itself in a run file, so nobody mistakes it for a
#: reader's judgement.
HEURISTIC = "heuristic:terms-and-tiers-in-top-k"

#: Tiers named in an item's `sources` line, matched as words.
_TIER_WORDS = {
    "government": "government",
    "peer-reviewed": "peer_reviewed",
    "peer_reviewed": "peer_reviewed",
    "institutional": "institutional",
    "press": "informal",
}

_WORD = re.compile(r"[a-z][a-z0-9_-]{3,}")

#: Words in a concept line that name the *kind* of thing wanted rather than
#: something a passage would contain. Small on purpose; see `concept_terms`.
_FILLER = frozenset(
    [
        "the",
        "that",
        "this",
        "with",
        "from",
        "their",
        "which",
        "such",
        "other",
        "others",
        "where",
        "whether",
        "what",
        "given",
        "sources",
        "source",
        "e.g.",
        "each",
        "least",
        "more",
        "most",
        "than",
        "into",
        "over",
        "under",
        "about",
        "name",
        "named",
        "date",
        "dates",
    ]
)


class QuestionSetError(ValueError):
    """The file is not a question set this runner can read."""


@dataclasses.dataclass(frozen=True)
class Question:
    id: str
    kind: str
    question: str
    topics: tuple[str, ...]
    concepts: tuple[str, ...]
    expected_tiers: tuple[str, ...]
    reviewed: bool


@dataclasses.dataclass(frozen=True)
class QuestionSet:
    set_version: int
    status: str
    questions: tuple[Question, ...]

    @property
    def unreviewed(self) -> list[str]:
        return [q.id for q in self.questions if not q.reviewed]

    @property
    def is_draft(self) -> bool:
        """True until every item is reviewed *and* the set says so."""
        return bool(self.unreviewed) or self.status == "draft"


def expected_tiers(sources_line: str | None) -> tuple[str, ...]:
    text = (sources_line or "").lower()
    found = []
    for word, tier in _TIER_WORDS.items():
        if word in text and tier not in found:
            found.append(tier)
    return tuple(found)


def load(path: Path) -> QuestionSet:
    """Read the set. Refuses a file missing the fields a run depends on."""
    return loads(path.read_text(), name=str(path))


def loads(text: str, *, name: str = "<questions>") -> QuestionSet:
    """:func:`load` from text — for a run whose process cannot see the repo."""
    path = name
    raw = yaml.safe_load(text)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("questions"), list):
        raise QuestionSetError(f"{path}: no `questions` list")
    if "set_version" not in raw:
        raise QuestionSetError(f"{path}: no `set_version`; scores are only comparable within one")

    items = []
    seen: set[str] = set()
    for entry in raw["questions"]:
        qid = entry.get("id")
        if not qid or not entry.get("question") or not entry.get("kind"):
            raise QuestionSetError(f"{path}: an item lacks id, question or kind: {entry!r:.80}")
        if qid in seen:
            raise QuestionSetError(f"{path}: duplicate id {qid}")
        seen.add(qid)
        good = entry.get("what_a_good_answer_contains") or {}
        items.append(
            Question(
                id=qid,
                kind=entry["kind"],
                question=" ".join(str(entry["question"]).split()),
                topics=tuple(entry.get("topics") or ()),
                concepts=tuple(str(c) for c in good.get("concepts") or ()),
                expected_tiers=expected_tiers(good.get("sources")),
                # Absent is unreviewed. Only an explicit `true` counts.
                reviewed=entry.get("reviewed") is True,
            )
        )
    return QuestionSet(int(raw["set_version"]), str(raw.get("status", "draft")), tuple(items))


def concept_terms(concept: str) -> set[str]:
    """Content words of one concept line, for a crude presence check."""
    return {w for w in _WORD.findall(concept.lower()) if w not in _FILLER}


@dataclasses.dataclass(frozen=True)
class HitView:
    """What the heuristic reads from a search hit."""

    text: str
    source_tier: str
    topic_labels: Sequence[str] | None


@dataclasses.dataclass(frozen=True)
class Proposal:
    grade: int
    method: str
    concepts_found: int
    concepts_total: int
    tier_match: bool
    topic_match: bool
    reason: str


def propose(question: Question, hits: Sequence[HitView]) -> Proposal:
    """A 0–3 grade *proposal* from the top hits. Never the score.

    It can only see presence, not whether an answer is assembled or right, so
    it is deliberately capped at 2: a 3 needs a reader. Gap items are graded
    inversely on topic presence and capped at 1 — the heuristic cannot tell
    "absence is evident" from "the search failed", and a gap item that scores
    well by accident is the failure `eval/README.md` warns about.
    """
    joined = " ".join(h.text.lower() for h in hits)
    found = sum(
        1 for c in question.concepts if (t := concept_terms(c)) and t & set(_WORD.findall(joined))
    )
    total = len(question.concepts)
    tier_match = bool(question.expected_tiers) and any(
        h.source_tier in question.expected_tiers for h in hits
    )
    topic_match = any(set(h.topic_labels or ()) & set(question.topics) for h in hits)
    share = found / total if total else 0.0

    if question.kind == "gap":
        grade = 1 if not hits or not topic_match else 0
        reason = "no on-topic hit at the top" if grade else "on-topic material ranks at the top"
    elif not hits:
        grade, reason = 0, "no hits"
    else:
        grade = 0
        if share >= 0.25 or topic_match:
            grade = 1
        if share >= 0.5 and (tier_match or not question.expected_tiers) and topic_match:
            grade = 2
        tier = "yes" if tier_match else "no"
        topic = "yes" if topic_match else "no"
        reason = f"{found}/{total} concepts present; tier {tier}; topic {topic}"
    return Proposal(grade, HEURISTIC, found, total, tier_match, topic_match, reason)


def item_record(question: Question, proposal: Proposal, hits: Iterable[dict]) -> dict:
    """One item in a run file. `operator` is left empty for the operator to fill."""
    return {
        "id": question.id,
        "kind": question.kind,
        # As asked, so a run file stands alone after the set's next version.
        "question": question.question,
        "topics": list(question.topics),
        "reviewed": question.reviewed,
        "hits": list(hits),
        "proposed": {
            "grade": proposal.grade,
            "method": proposal.method,
            "reason": proposal.reason,
        },
        # The score. Filled by the operator only: {grade, missing, sources}.
        "operator": None,
    }


def operator_mean(items: Sequence[Mapping]) -> float | None:
    grades = [i["operator"]["grade"] for i in items if isinstance(i.get("operator"), Mapping)]
    return statistics.fmean(grades) if grades else None


def compare(before: Mapping, after: Mapping) -> dict:
    """Operator-grade change per item between two runs of the same set version.

    Proposals are ignored on purpose. Refuses runs of different set versions,
    because scores are only comparable within one (`eval/README.md`).
    """
    if before.get("set_version") != after.get("set_version"):
        raise QuestionSetError(
            f"set_version {before.get('set_version')} vs {after.get('set_version')}: not comparable"
        )
    old = {i["id"]: i for i in before.get("items", [])}
    rows = []
    for item in after.get("items", []):
        prior = old.get(item["id"])
        a = (item.get("operator") or {}).get("grade")
        b = ((prior or {}).get("operator") or {}).get("grade")
        rows.append(
            {
                "id": item["id"],
                "before": b,
                "after": a,
                "delta": None if a is None or b is None else a - b,
                # README's proposed regression flag: any single item down by 2.
                "regression": a is not None and b is not None and b - a >= 2,
                # Kept under their own names so nobody reads them as the score.
                "proposed_before": ((prior or {}).get("proposed") or {}).get("grade"),
                "proposed_after": (item.get("proposed") or {}).get("grade"),
            }
        )
    return {
        "set_version": after.get("set_version"),
        "graded": any(r["after"] is not None for r in rows),
        "mean_before": operator_mean(before.get("items", [])),
        "mean_after": operator_mean(after.get("items", [])),
        "items": rows,
    }


# ---------------------------------------------------------------------------
# Reading a run back
# ---------------------------------------------------------------------------

SCALE = (0, 1, 2, 3)


def read_run(path: Path) -> dict:
    """Load a run file, refusing an operator grade outside the 0-3 scale.

    The operator edits these files by hand, so this is the one place a typo
    ("2.5", "three", `true`) would otherwise flow silently into a comparison.
    """
    run = yaml.safe_load(path.read_text())
    if not isinstance(run, Mapping) or not isinstance(run.get("items"), list):
        raise QuestionSetError(f"{path}: not a run file (no `items`)")
    for item in run["items"]:
        op = item.get("operator")
        if op is None:
            continue
        grade = op.get("grade") if isinstance(op, Mapping) else None
        if isinstance(grade, bool) or grade not in SCALE:
            raise QuestionSetError(
                f"{path}: {item.get('id')}: operator grade must be one of {SCALE}, got {op!r}"
            )
    return dict(run)


def previous_run(runs_dir: Path, *, excluding: Path | None = None) -> Path | None:
    """The newest run file in ``runs_dir`` other than ``excluding``.

    Ordered by (date, same-day number), not by name: as strings `<date>-2`
    sorts *before* `<date>` (`-` < `.`), which would compare a run with the
    wrong predecessor.
    """
    skip = excluding.resolve() if excluding is not None else None
    found = sorted((p for p in runs_dir.glob("*.yaml") if p.resolve() != skip), key=_run_order)
    return found[-1] if found else None


def _run_order(path: Path) -> tuple[str, int]:
    """`2026-09-24` → (`2026-09-24`, 1); `2026-09-24-3` → (`2026-09-24`, 3)."""
    rest = path.stem[11:]
    return (path.stem[:10], int(rest) if rest.isdigit() else 1)


def next_run_path(runs_dir: Path, day: dt.date) -> Path:
    """`<date>.yaml`, or `<date>-N.yaml` when that day already has a run."""
    path = runs_dir / f"{day.isoformat()}.yaml"
    n = 2
    while path.exists():
        path = runs_dir / f"{day.isoformat()}-{n}.yaml"
        n += 1
    return path


# ---------------------------------------------------------------------------
# Asking the questions
# ---------------------------------------------------------------------------

#: How many hits a run records and the heuristic reads. The README's reader
#: works from "the top results"; ten is one page of Explore.
TOP_K = 10

#: Characters of passage text kept per hit — enough for the operator to see
#: why it ranked, not the corpus copied into the repository.
EXCERPT = 240

Embed = Callable[[str], Awaitable[Sequence[float] | None]]


def banner(qs: QuestionSet) -> str | None:
    """The line a run carries while the set is a draft; None once it is not."""
    if not qs.is_draft:
        return None
    return (
        f"DRAFT SET: {len(qs.unreviewed)} of {len(qs.questions)} items are unreviewed "
        f"(status: {qs.status}). These results are not the go/no-go (P2-09); the "
        "operator reviews the set (P0-15) before any run counts."
    )


async def corpus_context(sess: AsyncSession) -> dict:
    """The counts §14.1 asks for beside every run, so a change is attributable."""
    live = Chunk.superseded_at.is_(None)
    embedded = Chunk.embedding.is_not(None)
    return {
        "sources": int(await sess.scalar(select(func.count()).select_from(Source)) or 0),
        "chunks_live": int(await sess.scalar(select(func.count(Chunk.chunk_id)).where(live)) or 0),
        "chunks_embedded": int(
            await sess.scalar(select(func.count(Chunk.chunk_id)).where(live, embedded)) or 0
        ),
        "entities": int(await sess.scalar(select(func.count()).select_from(Entity)) or 0),
    }


async def graph_citing(sess: AsyncSession, chunk_ids: Sequence[int]) -> dict:
    """What the graph already says from the hits — where the graph applies.

    The question text is not a node name, so asking the graph the question
    directly would measure string matching. What the graph can honestly add is
    whether the passages search found are already cited: by a claim (an edge,
    where derived evidence lives) or by a node itself (a note).
    """
    if not chunk_ids:
        return {"claims_citing_hits": 0, "entities": []}
    ids = list(chunk_ids)
    edges = (
        await sess.execute(
            select(Edge.from_node, Edge.to_node).where(Edge.supporting_chunk_ids.overlap(ids))
        )
    ).all()
    ends = {n for pair in edges for n in pair}
    rows = await sess.execute(
        select(Entity.entity_id, Entity.canonical_name, Entity.node_type)
        .where(or_(Entity.entity_id.in_(ends), Entity.supporting_chunk_ids.overlap(ids)))
        .order_by(Entity.entity_id)
        .limit(TOP_K)
    )
    return {
        "claims_citing_hits": len(edges),
        "entities": [{"entity_id": e, "name": n, "node_type": t} for e, n, t in rows],
    }


def hit_row(rank: int, hit) -> dict:
    return {
        "rank": rank,
        "chunk_id": hit.chunk_id,
        "source_id": hit.source_id,
        "url": hit.url,
        "title": hit.title,
        "source_tier": hit.source_tier,
        "publication_date": hit.publication_date.isoformat() if hit.publication_date else None,
        "topic_labels": list(hit.topic_labels) if hit.topic_labels is not None else None,
        "lexical_rank": hit.lexical_rank,
        "vector_rank": hit.vector_rank,
        "excerpt": " ".join(hit.text.split())[:EXCERPT],
    }


def proposed_summary(items: Sequence[Mapping]) -> dict:
    """Mean *proposed* grade per kind — labelled, so it is not read as the score."""
    by_kind: dict[str, list[int]] = {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item["proposed"]["grade"])
    return {
        "label": "heuristic proposal, not the operator's score",
        "mean_by_kind": {k: round(statistics.fmean(v), 2) for k, v in sorted(by_kind.items())},
        "items_with_no_hits": sum(1 for i in items if not i["hits"]),
    }


def attach_comparison(run: dict, previous: Mapping | None, previous_name: str | None) -> dict:
    """Put the comparison with the previous run into ``run`` and return it.

    A different set version is recorded as "not comparable" rather than
    raised: the run itself is still worth writing, and the reason is the
    finding.
    """
    if previous is None:
        run["comparison"] = {"previous": None, "note": "first run; nothing to compare against"}
        return run
    try:
        result = compare(previous, run)
    except QuestionSetError as exc:
        run["comparison"] = {"previous": previous_name, "note": str(exc)}
        return run
    result["previous"] = previous_name
    result["previous_mode"] = (previous.get("context") or {}).get("mode")
    if not result["graded"]:
        result["note"] = (
            "No operator grades in this run yet, so there is no score to compare; "
            "proposed_before/proposed_after are the heuristic's, not scores."
        )
    run["comparison"] = result
    return run


LEXICAL_ONLY_NOTE = (
    "The vector arm did not run for every item (no embedder, or it did not answer). "
    "Lexical search ANDs every word of a whole question, so most items return nothing; "
    "that is this run's limitation, not the corpus's."
)


async def run_set(
    sess: AsyncSession,
    qs: QuestionSet,
    *,
    embed: Embed | None = None,
    embedder_model: str | None = None,
    version: str | None = None,
    k: int = TOP_K,
    now: dt.datetime | None = None,
) -> dict:
    """Ask every question as typed and return one run record.

    Read-only: it runs `search()` and a few counts, nothing else. The query is
    the question text verbatim — building queries from an item's concepts
    would tune retrieval to the test, which `eval/README.md` forbids.

    The vector arm runs when ``embed`` returns a vector; otherwise the run is
    lexical-only and says so, per item and in `context.mode`. Lexical-only on a
    whole question is close to useless (`websearch_to_tsquery` ANDs every
    word), and a run that hid that would read as a corpus that knows nothing.
    """
    now = now or dt.datetime.now(dt.UTC)
    items = []
    for q in qs.questions:
        vector = await embed(q.question) if embed is not None else None
        result = await search(sess, q.question, query_vector=vector, limit=k)
        views = [HitView(h.text, h.source_tier, h.topic_labels) for h in result.hits]
        rows = [hit_row(i, h) for i, h in enumerate(result.hits, start=1)]
        record = item_record(q, propose(q, views), rows)
        record["arms"] = sorted(result.arms)
        record["graph"] = await graph_citing(sess, [h.chunk_id for h in result.hits])
        items.append(record)

    hybrid = bool(items) and all(i["arms"] == ["lexical", "vector"] for i in items)
    context = {
        "run_at": now.isoformat(),
        "version": version,
        "set_version": qs.set_version,
        "set_status": qs.status,
        "mode": "hybrid" if hybrid else "lexical-only",
        "mode_note": None if hybrid else LEXICAL_ONLY_NOTE,
        "embedder_model": embedder_model,
        "top_k": k,
        "corpus": await corpus_context(sess),
    }
    return {
        "set_version": qs.set_version,
        "draft": qs.is_draft,
        "banner": banner(qs),
        "unreviewed": qs.unreviewed,
        "grading": {
            "proposed": f"{HEURISTIC}: a proposal, never the score",
            "operator": (
                "the score; fill items[].operator by hand as "
                "{grade: 0-3, missing: '<one line>', sources: [<urls>]}"
            ),
        },
        "context": context,
        "proposed_summary": proposed_summary(items),
        "comparison": None,
        "items": items,
    }
