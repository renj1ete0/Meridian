# Extraction and chunking

Extraction turns fetched bytes into text and metadata, and chunking cuts that text into
passages. A passage is what gets embedded, what search returns, what a model reads, and what
an edge cites as evidence, so every passage must make sense on its own. It must also carry a
location (page or character offset) that a reader can follow back into the stored file.

- **Code:** `services/worker/worker/extract/` (`html.py`, `pdf.py`, `document.py`,
  `figures.py`, `clean.py`, `chunk.py`, `dockind.py`, `errorpage.py`, `injection.py`),
  `cleancut.py`, `rawstore.py`, `ocr_queue.py`, `boilerplate.py`, `rechunk.py`, `dockind.py`,
  `retitle.py`; `packages/meridian_core/meridian_core/chunks.py`, `sources.py`,
  `boilerplate.py`, `titles.py`, `figures.py`
- **Tasks:** `P1-07`–`P1-13`, `P2-02`, `B-42`–`B-45`, `B-59`, `B-69`

## How it works

```
bytes ──► route by media type
           ├─ HTML  ── browser markdown if the browser ran, else trafilatura (precision)
           ├─ PDF   ── pdftotext, page by page; too few chars per page ─► OCR queue
           └─ Office ─ MarkItDown convert_stream, four converters only
       ──► metadata: title (cleaned), DOI and other identifiers, language, date
       ──► document kind: paper, listing, legal, news, report, profile, other
       ──► clean: drop whole lines (skip links, menus, site boilerplate, running headers)
       ──► chunk: split on paragraphs and headings; sentences, then a hard cap, as fallbacks
       ──► write: new chunks; the old ones are superseded, not deleted
```

**The raw store** keeps the fetched bytes for primary sources (official, scholarly,
institutional) under a path derived from `sha256(url)`, written atomically. Background
sources keep only text and metadata. The source row records the checksum, ETag and
Last-Modified, so the next fetch can be conditional, and an unchanged page costs a 304.

**Chunks are verbatim slices.** Cleaning returns spans to skip instead of a rewritten copy,
so a passage's offset always points into the extracted text. Paginated documents cite pages;
everything else cites character offsets. There is no overlap between chunks: splitting on
structure already keeps ideas whole, and overlap would triple the storage, embedding and
reading costs.

**Re-crawls supersede.** A changed page writes new chunks, and the old ones get a
`superseded_at` stamp in the same transaction. Edges keep citing the text they were derived
from. Everything that serves readers filters on `superseded_at IS NULL`.

## Design choices

- **MarkItDown only on bytes, through an allowlist of converters.** Its `convert()` on a
  string can fetch URLs and read local files (an SSRF). Its default registry includes
  converters that unzip archives and call external tools. `convert_stream` with four
  registered converters closes both doors (AGENTS.md invariant).
- **A bad document is returned, never raised.** It is stored as metadata-only with
  `extractor="…-failed"`, so a re-extraction pass can try again later.
- **Scans are queued for OCR, never processed inline.** A scanned PDF yields almost no text
  and would otherwise look like an empty page. The characters-per-page check makes it a
  source that visibly needs OCR.
- **Boilerplate is counted per site, from uncleaned text.** A line is furniture when it
  appears on enough of a host's pages, both as a count and as a share. Counting after cleaning
  would switch the rule off by working.
- **Cleaning is conservative.** If it would remove too large a share of a page, the page is
  kept whole and logged. A page that is mostly furniture is junk, which is the furniture
  pass's job.
- **Document kind is decided by structure** (`B-59`). A **listing** (an index, feed or search
  page) is worth only its links, and its chunks splice unrelated summaries together, so a
  listing's chunks are retired. The rules favour precision: a missed listing costs little,
  while a document wrongly called a listing loses its passages.
- **Error pages served with status 200** ("Page not found" in the site template) are caught
  by a title segment or by the opening sentences (`B-45`).
- **Titles are cleaned** (`B-69`): placeholders ("untitled", file names) and site-wide titles
  are dropped. When nothing is left, a heading-like first line is used, and recorded as
  guessed.
- **Injection screening flags, it does not delete.** Hidden imperative text (invisible to a
  reader, visible to extraction) is strong evidence; visible imperative phrasing is weak,
  because articles about injection quote it. See [source-quality.md](source-quality.md).
- **Figures are captions, not vision.** Captions and alt text are extracted with the image
  URL; PDFs get a page number and no bounding box.

## Configuration

- `allowed_content_types` and `max_page_bytes` are in the fetch policy.
- `MERIDIAN_RAW_ROOT` sets where raw files go; retention by tier is in
  `config/source_tiers.yaml` (seeded).

## Operating it

- `boilerplate` runs daily. `rechunk`, `dockind`, `retitle` and `furniture` are one-off
  passes for what was stored before a rule existed. Each reports by default, needs `--apply`
  to write, and never touches a source that anything cites.
- `pdftotext` (poppler) is required in the worker image. Without it every PDF fails loudly,
  by design.

## Failure modes and traps

- `readable()` in the API joins wrapped lines for display only, conservatively. Loosen it
  only after re-running the passage sample described in the handover.
- A URL served first as HTML and later as a PDF gets a second raw path. The retention sweep
  reports the first as orphaned.

## Tests

`tests/unit/test_chunk*.py`, `test_clean*.py`, `test_extract_html.py`,
`test_extract_pdf.py`, `test_extract_document.py`, `test_extract_figures.py`, `test_dockind*.py`, `test_errorpage.py`, `test_titles.py`,
`test_injection_screen.py`, `test_rawstore*.py`; `tests/integration/test_chunks*.py`,
`test_rechunk*.py`.
