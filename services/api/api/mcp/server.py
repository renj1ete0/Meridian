"""The MCP read surface (tasks P3-01, P3-02; spec §11.1, §11.6).

§11.1's middle column: an external agent connects *inward* and pulls evidence.
The model does the reasoning; Meridian holds the corpus and the provenance, and
never generates anything.

**The instructions are load-bearing, not documentation.** They are the only
thing an external model reads before deciding how to treat what these tools
return, and three of the mistakes it would otherwise make are ones this whole
system exists to prevent:

- concluding the corpus lacks a topic when the search was word-matching
- treating `source_tier` as a credibility score, which §8 explicitly refuses
  to compute
- reporting a claim without the citation that justifies it

So the guidance is written for a model to *act on*, and the same warnings ride
on every individual result rather than only at connection time — an assistant
summarising one tool call will not go back and re-read the server instructions.

**Nothing here writes.** Every tool takes `session_ro()`, so the read-only
guarantee is Postgres's rather than this module's (`P3-07` narrows it further
for guests). Write tools are §11.6's second group and belong to the orchestrator
scope; they arrive with the graph in phase 4.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from meridian_core.db import session_ro
from meridian_core.logging import get_logger
from meridian_core.models import Chunk, Source
from meridian_core.search import SearchFilters, search
from meridian_core.stats import corpus_stats

log = get_logger(__name__)

#: What a model is told before it uses anything here.
INSTRUCTIONS = """\
Meridian is a research corpus with provenance on every passage. It retrieves and
cites; it does not answer. You do the reasoning.

Rules for using it well:

1. CITE EVERYTHING. Every chunk carries `url`, `title` and `page_or_offset`.
   A claim you take from this corpus must name where it came from, so the reader
   can open it. Do not paraphrase away the source.

2. NO RESULTS DOES NOT MEAN NO EVIDENCE. Read the `retrieval` field on every
   search. When it says word-matching, the corpus may hold documents on the
   topic that use different vocabulary, and they were not searched. Try
   alternative wordings before concluding anything is absent.

3. `source_tier` IS NOT A CREDIBILITY SCORE. It records what kind of document
   this is — peer-reviewed, government, institutional, press, informal — and
   nothing more. Meridian deliberately does not score reliability. Report the
   tier; do not convert it into a ranking or a confidence, and do not dismiss an
   informal source: for some subjects it is the only record that exists.

4. NEAR-DUPLICATES ARE EXCLUDED by default. `duplicate_of` on a result tells you
   a passage repeats one already returned.

5. SAY WHAT IS THIN. If the evidence you found is sparse, stale, or all from one
   source, say so in your answer. A confident summary of two press articles is
   worse than an honest report that only two exist.
"""

#: Wording that travels with a degraded search. Phrased for a model to repeat,
#: and to act on — a client told only that a flag is true will not think to try
#: synonyms, which is the compensating behaviour that makes lexical-only
#: retrieval usable at all.
LEXICAL_ONLY = (
    "Matched on words only. Semantic search is unavailable on this deployment, "
    "so passages about this topic phrased in different words were NOT searched. "
    "Do not conclude the corpus lacks this topic — retry with alternative "
    "wordings, synonyms, and narrower or broader terms before saying so."
)

HYBRID = (
    "Matched on both wording and meaning, then fused by reciprocal rank. "
    "Passages using different vocabulary were searched."
)


def _cite(chunk: Any) -> dict[str, Any]:
    """One passage, with what makes it checkable.

    §2 principle 3: nothing is assertable without a citation you can follow back
    to a file. A tool that returned text alone would make this a RAG endpoint
    over somebody's documents, which the README is explicit it is not.
    """
    return {
        "text": chunk.text,
        "url": chunk.url,
        "title": chunk.title,
        "source_tier": chunk.source_tier,
        "published": chunk.publication_date.isoformat() if chunk.publication_date else None,
        "page_or_offset": chunk.page_or_offset,
        "source_id": chunk.source_id,
        "chunk_id": chunk.chunk_id,
        "duplicate_of": chunk.duplicate_of,
    }


def build_mcp(*, version: str = "") -> MCPServer:
    """The server, assembled by a factory for the same reason the app is.

    A module-level instance would connect to the database at import, which makes
    it impossible to build in a test that has no Postgres and impossible to
    mount twice with different scopes when `P3-03` lands.
    """
    mcp = MCPServer(
        name="meridian",
        title="Meridian research corpus",
        instructions=INSTRUCTIONS,
        version=version,
    )

    @mcp.tool()
    async def search_chunks(
        query: str,
        limit: int = 10,
        source_tier: list[str] | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        include_duplicates: bool = False,
    ) -> dict[str, Any]:
        """Search the corpus for passages, with the source of each.

        Returns a `retrieval` field describing how the match was made — read it.
        When it says word-matching, absence of results is not evidence of
        absence, and you should retry with different wording.
        """
        filters = SearchFilters(
            source_tiers=source_tier or None,
            published_after=dt.date.fromisoformat(published_after) if published_after else None,
            published_before=dt.date.fromisoformat(published_before) if published_before else None,
            include_duplicates=include_duplicates,
        )
        async with session_ro() as sess:
            # No query vector: this deployment has no embedder (`P2-07`,
            # `P2-17`). The honest consequence rides in `retrieval` rather than
            # being left for the caller to infer from an empty list.
            result = await search(sess, query, filters=filters, limit=limit)

        return {
            "retrieval": LEXICAL_ONLY if result.degraded else HYBRID,
            "arms": sorted(result.arms),
            "results": [_cite(hit) for hit in result.hits],
            "returned": len(result.hits),
        }

    @mcp.tool()
    async def get_source_metadata(source_id: int) -> dict[str, Any]:
        """Everything recorded about one source, for citing it properly."""
        async with session_ro() as sess:
            source = await sess.get(Source, source_id)
            if source is None:
                return {"error": f"no source {source_id}"}
            return {
                "source_id": source.source_id,
                "url": source.url,
                "title": source.title,
                "author": source.author,
                "publisher": source.publisher,
                "published": (
                    source.publication_date.isoformat() if source.publication_date else None
                ),
                "doi": source.doi,
                "source_tier": source.source_tier,
                "language": source.language,
                "text_available": source.text_available,
                # How it was read. A source extracted by a failed extractor is a
                # different thing from one that genuinely had no text (`P1-44`).
                "extractor": source.extractor,
                "accessed": source.accessed_at.isoformat() if source.accessed_at else None,
            }

    @mcp.tool()
    async def list_new_since(mark: int = 0, limit: int = 50) -> dict[str, Any]:
        """Passages added since a high-water mark, oldest first.

        §6.3's mark, and §11.1a's entry point: a synthesis session walks what is
        new rather than searching for it, so this path needs no query and no
        embedder. Pass the `next_mark` you get back as `mark` next time.

        The mark is a `chunk_id`. Ids are monotonic, so "everything after N" is
        exact — it cannot skip a row that arrived while you were reading, and it
        cannot return one twice.
        """
        # Built *inside* the session. ORM instances detach when it closes, and
        # every attribute read afterwards raises `DetachedInstanceError` — which
        # surfaces as "error executing tool" with nothing to say it was a
        # lifetime problem rather than a query one.
        async with session_ro() as sess:
            rows = (
                await sess.execute(
                    select(Chunk, Source)
                    .join(Source, Source.source_id == Chunk.source_id)
                    .where(Chunk.chunk_id > mark, Chunk.duplicate_of.is_(None))
                    .order_by(Chunk.chunk_id)
                    .limit(min(limit, 200))
                )
            ).all()

            passages = [
                {
                    "text": chunk.text,
                    "url": source.url,
                    "title": source.title,
                    "source_tier": source.source_tier,
                    "published": (
                        source.publication_date.isoformat() if source.publication_date else None
                    ),
                    "page_or_offset": chunk.page_or_offset,
                    "source_id": source.source_id,
                    "chunk_id": chunk.chunk_id,
                }
                for chunk, source in rows
            ]

        return {
            "passages": passages,
            "returned": len(passages),
            # The mark to resume from. Unchanged when nothing came back, so a
            # caller that keeps polling does not walk backwards.
            "next_mark": passages[-1]["chunk_id"] if passages else mark,
        }

    @mcp.tool()
    async def corpus_overview() -> dict[str, Any]:
        """How much evidence exists at all, before you ask it anything.

        §12.5's "absence is visible", as the first thing an agent can check. A
        corpus of thirty passages and one of thirty thousand support very
        different claims, and nothing else here would tell you which you are
        talking to.
        """
        async with session_ro() as sess:
            stats = await corpus_stats(sess)
        return dataclasses.asdict(stats)

    return mcp
