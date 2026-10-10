# 0019. Stored PDFs are re-extracted for their tables

- **Status:** Accepted
- **Date:** 2026-10-11
- **Tasks:** `B-215` (follows `B-214`)

## Context

`B-214` keeps a PDF table's rows for documents extracted from then on. The PDFs already stored
hold their tables column by column, so a value is not beside the label that says what it
measures. The re-cut pass rebuilds a source from its stored passages, which have already lost
the table's shape, so it cannot recover it; only the raw file can. Re-extracting rewrites those
sources' passages, and the new passages queue for embedding, so the operator was asked first.

## Decision

The operator approved re-extracting stored PDFs from their raw files, provided it is the right
way to reach them. It is applied narrowly: a source's passages are replaced only when the fresh
extraction places a table, and a source that anything cites, whose file is not the one fetched,
or whose text came from OCR is left as it was. A new passage whose text an old one had keeps the
old vector.

## Consequences

- About a quarter of stored PDFs gain tables as rows; the rest are untouched.
- Old passages are superseded, not deleted, so a citation of one still resolves.
- The re-embedding is about a fifth of the passages rewritten, since most keep their vector;
  the crawl may pause behind the embedding ceiling while it drains, which is the ceiling working.
- Cited sources keep their tables column by column until they are fetched again with a changed
  file.

## Alternatives considered

- **Leaving stored PDFs alone**: tables would improve only as documents are refetched, which for
  an unchanged file is never.
- **Re-cutting every stored PDF from its file**: would also apply every cleaning change since each
  was fetched, a larger rewrite than the tables call for, and one nobody asked to review.
