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

Nothing in extraction calls a language model (§2.1), and nothing it produces is trusted:
extracted text is attacker-controlled, and the injection screen looks at it before any of it
reaches a model.

### HTML

Most pages take the static path and never touch a browser; they are extracted locally by
trafilatura at `favor_precision`. Precision is deliberate: a research corpus would rather lose
a sentence of body text than gain a navigation menu, because boilerplate becomes entities,
entities become edges (§2.3), and a graph full of "Skip to main content" costs far more to
unpick than the paragraph precision lost. Bytes go to trafilatura undecoded, since it does its
own encoding detection and a page that lied in its `Content-Type` is best handled by the
library that expects lies.

**A rendered page is filtered to the same standard** (`P1-43`). The browser path used to take
Crawl4AI's `fit_markdown` as-is, on the reasoning that its `PruningContentFilter` had seen a
rendered DOM this process never had. The premise was wrong: the rendered HTML comes back in the
same response and is what `fetch.py` stores as the body. The cost was an asymmetry nobody
chose: the filter is far more permissive than trafilatura at precision, so whether a page kept
its navigation depended on whether the fetcher escalated it to a browser, a decision made on how
much visible text the static fetch found. It showed in the corpus as chunks of repeated station
lists, promo banners and footer link blocks, which also inflated the novelty gate's duplicate
count. Now trafilatura extracts the rendered HTML, and the browser payload contributes what it
is better at: metadata from the rendered DOM and links including those JavaScript inserted.
`fit_markdown` is the fallback when trafilatura finds nothing, a real case on JS-assembled pages
with no semantic structure, but not below a floor of 200 characters, where it is a cookie
banner.

**Empty is a valid answer.** A page with no usable text is stored metadata-only (§6.5), still a
citable participant in the graph; `text_available` records the difference.

Other details:

- Links are anchors only, not `iterlinks()`, which yields every favicon, stylesheet and script;
  on an official home page the assets outnumbered the documents several times, and a frontier
  fed from them fetched a tiny PNG per page.
- Figures are read from the raw HTML, because precision filtering strips `<figure>` wrappers
  (`P1-10`).
- A publication date must be a full ISO day; a partial date is discarded rather than
  completed, since `publication_date` is a column citations are built from.
- Trailing spaces before a newline are collapsed: they come from HTML indentation far more often
  than from an author, and would make chunk boundaries depend on formatting.
- The English alternate (`B-57`) is read from `<link rel="alternate" hreflang="en…">` in the
  head only (in the body it is a link like any other). Any English region counts; `x-default`
  names a fallback, not a language, and a link to the page itself is not an alternate.

<a id="identifiers"></a>**Identifiers** (`P1-14` resolves them later). DOIs, arXiv, PubMed and
handle identifiers are extracted mechanically from both the text and the links: a reference list
writes DOIs as text, while a "view on arXiv" button carries one only in an `href`, and taking
one and not the other misses a predictable population rather than a random sample. The DOI
pattern's trailing class excludes sentence-ending punctuation, because "10.1234/foo." is far
more often a DOI followed by a full stop, and a resolver handed the wrong string reports "not
found", which reads as missing rather than malformed.

A page's *own* DOI (`sources.doi`, which makes the source resolvable) is kept apart from the DOIs
it cites (pointers to other things to fetch). It is read from the head's meta tags only, since a
`citation_doi` tag halfway down a reference list describes a reference. arXiv publishes no DOI
meta tag, and the DataCite DOI it mints appears only in the body, where it reads as a citation;
so for arXiv the DOI is derived from the identifier in the URL, which is exact, since the mapping
is arXiv's own. Without that, every arXiv abstract page cited its own DOI and `sources.doi` stayed
null for a large population of papers. A page's own identifiers, in every form and version, are
removed from its citations, or every paper would cite itself and `P1-14` would spend a resolution
per paper fetching what it already has.

### PDF

The fork is §6.6's: `pdftotext`, then characters per page; native text is chunked, a scan is
queued for OCR and stays metadata-only.

- **Both branches fail differently.** A scan through a text extractor yields a few ligature
  artefacts and looks exactly like an empty page; without the check, a scanned report enters as
  "extracted, nothing found" and nobody looks again. The threshold is §6.6's "below ~100
  chars/page": body prose runs to 1,500–3,000 characters a page, so the exact value matters
  little. The text layer's few characters on a scan (a header, a page number) are dropped rather
  than stored as content.
- **Garbled text layers.** A PDF drawn with a custom font encoding and no Unicode map comes out
  as a page-length run of control characters: not blank, so it passes the scan check, and it
  embeds as noise. A page more than a set share control, private-use or replacement characters
  has no readable text. Measured over a real crawl, garbled documents sat at 60–66% and the next
  highest ordinary document at 5%.
- **OCR never runs inline**: it would stall the loop for one document. The queue row is not a
  promise; OCR passes are user-triggered because they are expensive (§6.6), and which tier a
  scan deserves (multi-column reports need the expensive one) is a judgement nothing at
  ingestion can make. What is guaranteed is that a scan is *findable*: `ocr_applied` and
  `ocr_tier` record the decision rather than leaving an absence. A re-crawl does not stack a
  second request, since the pending count is what an operator decides spending from.
- **Page numbers, not offsets.** `pdftotext` separates pages with a form feed, so the boundary is
  free and exact.
- **A missing `pdftotext` is loud.** §6.6's lesson about `markitdown-ocr` silently skipping OCR:
  a worker that quietly lost poppler would store PDFs and extract none, and the only symptom
  would be a corpus that stopped growing. So its absence raises, while anything wrong with one
  document (encrypted, truncated, really an HTML error page) is returned.
- The PDF is passed on stdin, not a temporary file, so nothing downloaded lands on disk under a
  name another process could reach. Title, author and date come from `pdfinfo` in a separate
  process (official reports set the title more often than they are given credit for, and a PDF
  without one cites as a URL); its failure does not fail the document. A date is taken only when
  all of year, month and day are present.

### Office documents

Office formats arrive far more often than expected from official and consultancy sources, so a
corpus that cannot read a .docx is missing reports.

- **Bytes only, through `convert_stream`.** MarkItDown performs I/O with the caller's
  privileges, and `convert()` on an attacker-supplied string is an SSRF and a local-file read
  under one name (`file:///etc/`, `http://169.254.169.254/`). The module is handed bytes
  `fetch.py` already retrieved through the pinned-address path (`P1-24`) and never learns a URL
  or path; `convert_local()` is out for the same reason.
- **An allowlist of converters.** The default registry carries converters that fetch URLs, shell
  out to `exiftool` on untrusted bytes, and (`ZipConverter`) extract an archive and re-dispatch
  its members. A .docx is a zip, so a hostile "document" that is really an archive reaches that
  converter by content sniffing, and a zip bomb costs a disk. MarkItDown runs magika over the
  stream and tries every converter that accepts any guess, so routing is gated on the supported
  media types before any bytes are handed over, and only four converters are registered. Plugins
  are off too: a plugin that changes behaviour without appearing in the module is exactly the
  silent failure §6.6 warns about. The extension MarkItDown routes on comes from the media type,
  never the server's filename, which would be a second, attacker-chosen way to pick a converter.
  A converter is built per document (about 15 ms of magika loading) so nothing is shared between
  threads.
- **What is left out**, each verified against a real file: `.doc` and `.xls` (no `.doc`
  converter exists, and `.xls` needs `xlrd`, not installed); EPub (needs `ebooklib`; when it is
  added the format belongs here, since converting an EPub as a zip of HTML is silent
  degradation); ZIP on purpose (twelve documents would enter as one source with one provenance
  record); and HTML, which belongs to trafilatura.
- **In a thread, with no wall-clock timeout.** Conversion is synchronous and CPU-bound, and a
  slow spreadsheet would otherwise stall every fetch lane. `asyncio.to_thread` cannot cancel a
  running thread, so a timeout would free the lane while leaking a thread from a small pool, and a
  handful of pathological documents would then stall the loop for good. `max_page_bytes` is the
  bound that can be enforced.
- **Never raises.** The worker runs unattended for weeks (§13.4). An unconvertible document comes
  back as `markitdown-failed` with no text, distinct from `markitdown` with empty text (the
  document was empty), and the converter's class name is logged for a re-extraction sweep.
- **No pages.** A .docx's page breaks are a rendering decision Word makes, not a property of the
  file, so offsets are characters; inventing pages would make citations that look right and open
  in the wrong place.
- **Metadata from `core.xml`.** MarkItDown drops title, author, date and language, so they are
  read from OOXML's core properties, through a parser that resolves no entities and touches no
  network (a default parser would read files or fetch URLs at the document's request), built per
  call because lxml parsers must not be shared between threads. OOXML permits a year or a month
  alone; anything short of a full date is discarded rather than padded.

### Figures

§6.6: "Start with captions, not vision." A caption plus a page number is already a citable claim
about what a figure shows, searchable the moment it is stored; vision (`P7-07`) and the figures
panel (`P6-14`) build on it. `image_url` keeps the way back to the image, since nothing downloads
figure images. HTML has semantics: `<figure>`/`<figcaption>` says so outright, and `<img alt>` is
a description someone wrote; marked-up figures come first, so the cap truncates the weaker
evidence. A PDF has only the "Figure 3:" convention, near-universal in the documents collected,
so its figures carry an exact page and no bbox, since inventing one would put false precision on
a citation. On a re-crawl figures are replaced wholesale, in the same transaction as the source
row and chunks: they have no stable identity across fetches, and matching them would be guesswork
with a caption attached to the wrong picture as the cost.

### Error pages

Many sites answer 200 with "Page not found" in the full site template, and the fetch path stored
it, chunked it and followed its links (`B-45`). A title is usually `<page> | <site>`, and the
error must be a whole segment ("Page not found", "404", "Error 404", "Not Found"), not a word in
one: "Rule 404. Character Evidence" and "Harmless Error" are real documents. Where a template
keeps the site's own title on every page, one of a few unmistakable sentences must appear in the
opening stretch of text, where an error template puts it; an article about broken links says it
further down, if at all.

### Cleaning

trafilatura removes most chrome, the browser path less, and what survives is the same few kinds
of line on every site: skip links, menus taken for lists, banners, a PDF's running header. Each
costs three times: it is embedded, it answers searches, and it inflates the duplicate count.

- **Whole lines, as spans.** A cleaner returns spans to skip rather than a cleaned copy, so every
  chunk stays a verbatim slice and never crosses a removed line. A short stretch between two
  removed lines becomes a short chunk rather than merging with its neighbour.
- **Conservative.** A lost line is text the corpus can no longer cite, and nothing downstream
  would notice. Each rule targets a shape the corpus measurably contains and honours a rejection
  (a content list, a table row, a sentence containing a link, a heading above prose); where they
  disagree, the rejection wins.

The thresholds were measured over every live chunk of a real crawl (a few thousand pages across a
few dozen hosts, trafilatura and browser HTML, plus PDFs); the numbers are here so a change can be
argued with.

- **Kept whole above 80% removed**, measured on visible characters. The median cleaned page lost
  about a tenth. Pages losing 60–80% were menu-heavy profile and landing pages whose real content
  (a name, a title, a sentence under a banner) is what cleaning should leave; pages losing more
  were empty search results, access-error pages and bare listings, junk for `worker.furniture`,
  which demotes a page rather than hollowing it out. One guard covers a whole paginated
  document, since a cover page is often only a running head and a number. Visible characters,
  because link targets are most of a linked line's characters: measured raw, a listing with three
  long URLs per entry looked two-thirds furniture.
- **Navigation lines**: an affordance ("Skip to content", "Back to top") only when it is the whole
  line; a lone "opens in new tab" link; a link with nothing to read (image-only, empty, an
  in-page anchor). *Not* every link-only line: a bare `[text](url)` line was the commonest shape
  measured, tens of thousands of them, and most were content (an index of statutes, preprint
  identifiers, a reference's DOI, a directory of units). Navigation arrives in runs.
- **Menus**: a run of at least five short lines (navigation menus measured at five or more,
  usually over ten; four-item link runs are common inside content), each at most three visible
  words (labels measured one to three; an index of titled documents runs to five and more), with
  nearly all of them links (runs of short lines with no links were the largest class and almost
  all content: side effects of a medicine, members of an organisation). Lines with no letters ride
  along without counting, since a pager is digits and dashes between links.
- **Debris**: a line containing an invisible character (a soft hyphen, a private-use glyph), not
  blank to `str.strip`, which embeds as noise. A rule, a lone page number or a one-letter heading
  is never touched.
- **PDF running heads**: only within three lines of a page's edge, on at least four pages and
  half the document's pages, with digits folded so "Page 3 of 20" matches "Page 4 of 20". Journal
  heads and page numbers sit on 80–100% of pages; the near miss was an institution's name at the
  foot of three pages of a five-page list, which is content. A mid-page occurrence is a citation,
  never chrome.
- **Per-site repetition** (`meridian_core.boilerplate`, rebuilt daily by `worker.boilerplate`):
  a line on at least five pages *and* 30% of a host's pages. A line on five pages of a five-page
  site is its template; on five of a thousand it is a phrase a few articles share. At these values
  every qualifying line read as template when checked by hand; at 10%, article-series text that is
  part of each article began to qualify. Lines too short (a label) or too long (a paragraph,
  which two pages share by quotation or syndication) are not considered. Counts are taken from
  *uncleaned* text: counted after cleaning, a line would vanish from the pages it was removed
  from, fall below the threshold and come back, a rule switching itself off by working. Hosts
  fold `www.` but not other subdomains, since a university's hospital and its law school share a
  registrable domain and nothing else. The table is rebuilt wholesale in one transaction, so a
  line under the threshold after a template change stops being removed.

`cleancut` is the one path from text to chunks, shared by the fetch path and `rechunk`, so a page
is never cut differently depending on when it arrived.

### Chunking

A chunk is embedded and retrieved, read by the slow loop, and cited as an edge's evidence, so it
must make sense alone: an edge whose evidence is half a sentence cannot be checked. §5.3 insists
the offset is captured at chunking, since reconstructing it later is painful or impossible.

- **Structure, not a character count.** Markdown carries paragraph and heading boundaries;
  cutting there gives whole thoughts. A paragraph over the cap (a legal recital, a table
  flattened into prose) is split at sentences, and a sentence still over the cap is cut at it,
  since nothing is left to respect and emitting it whole would overflow the embedding window.
- **No overlap.** Overlap compensates for blind splitting; splitting on paragraphs fixes the same
  problem, and overlap would duplicate text in the table, the index and every slow-loop batch.
- **Slices.** All work is in half-open spans into the original text, so merging two pieces keeps
  the document's own separator and `source[offset:offset + len(text)]` is the chunk exactly. A
  chunk rejoined with separators of its own could only be searched for, never located. Offsets
  index the extracted text, not the raw file: extraction is deterministic, so offset plus raw
  file locates the passage, while a raw-HTML offset would break when the extractor improved.
- **Runts** (a heading with no body, a stranded caption) retrieve badly and prove nothing, so they
  merge into their predecessor when adjacent, keeping offsets increasing; a runt in first
  position, usually a `# Title`, merges forwards.
- **Pages.** In a paginated document the offset field holds the page number, and a chunk never
  spans a page break: a chunk across pages 4 and 5 would send a reader to the wrong page for half
  its content. Short pages make short chunks; a citation that lands beats an ideal size.
  `chunk_index` runs across the document, unique per source.

<a id="superseded-chunks"></a>**Superseded chunks** (`P1-32`). A changed page's old chunks describe
text no longer there, and §2.4 re-derives from source rather than patching, so a content change
replaces the chunks in one transaction (a crash between supersede and insert would leave no live
chunks, reading as "never extracted"). New chunks get new ids, so the slow loop's high-water mark
(§6.3) picks them up with nothing to schedule. Old chunks used to be deleted, and
`edges.supporting_chunk_ids` is an array Postgres cannot hold a foreign key on, so every edge
citing them pointed at nothing while still *having* provenance that passed every check. Now they
are stamped `superseded_at` and kept: citations resolve, an edge keeps the text it came from, and
reclaiming uncited ones is the sweep's decision, made by a person, never by a crawl on a timer.
Only live chunks are stamped, so an older generation keeps its timestamp for the sweep's age
rule. Which tables cite chunks is derived from the models (`B-46`): a hand-written list once left
out entities, so a sweep could delete a chunk only an entity cited. Everything serving the corpus
filters on `superseded_at IS NULL`, or it would quote a document as saying what it no longer
says.

### Document kinds

`source_tier` says who published a document; the kind says what it *is* (`B-59`). It matters most
for a **listing** (an index, a feed, a search or tag page, a directory): chunked as a document, it
splices unrelated summaries with rows of author links, which are embedded, labelled and offered
to synthesis as if they said something. Rules run in order, first match wins, and the deciding
rule is recorded:

1. **paper**: the page names its own DOI or carries scholarly `citation_*` head metadata (tags a
   reference list never produces); a PDF with an Abstract heading in its front matter. First, so
   a paper's long reference list never makes it a listing.
2. **listing**, measured on the extracted text: most visible text is link text (dense inline
   linking in articles stayed well below the share, and a page with two links says nothing); or
   link rows recur evenly through the page, a summary's worth of text apart (a reference list is
   also even rows, but a citation line apart); or the URL says list, search, tag, category or
   archive and the text is link-dense.
3. **legal**: section-marked titles or provisions, or a path under legislation, regulations or a
   code. After listing, so a statute's table of contents is a listing and its text is legal.
4. **news**: a press publisher's dated article, or a dated `og:type article` under a news path.
5. **report**: a PDF or office document from an official or institutional publisher.
6. **profile**: an organisation's own pages (home, about, contact, people, programmes).
7. **other**.

The thresholds were calibrated on a live corpus against known listings on one side and abstract
pages and articles on the other (the task report has the numbers). The `dockind` pass applies
the rules to stored sources from their live chunks, with head metadata from the raw file when one
is kept (absent otherwise, as for a page that declared nothing).

<a id="re-chunking"></a>**Re-chunking** (`B-43`). `rechunk` re-cuts what was chunked before
cleaning existed, from the live chunks, since a background source keeps no raw file. Unpaginated
text is rebuilt by laying each chunk at its offset and filling the gaps (only ever whitespace)
with newlines, so new offsets still index the original extraction; a page's chunks are joined
with a blank line. Three phases, because the per-host set needs every page's lines first: record
`page_lines` for sources that have none (never re-recorded from chunks that may be cleaned),
rebuild the boilerplate set, then re-cut and compare. Re-cut sources get new ids, so the embedding,
novelty and labelling passes pick them up again.

### Titles

Measured on a live corpus, declared titles are wrong in three recurring ways (`B-69`): a
placeholder ("untitled", "Microsoft Word - report.docx", a bare file name, "nan"), which cites as
nothing; the site rather than the page ("Home", "Results", the site's name on every page), many
sources with one title; and nothing at all, common on PDFs. `clean_title` drops the first two and
strips a site name off "Page | Site". The fallback is the first line when it looks like a heading
and nothing else: three to twenty-five words, starting with a capital or digit, not ending in a
colon or full stop, and none of a first page's furniture (journal banners, DOIs, "Received:").
A wrong title is worse than none, which shows the address instead. `retitle` applies this to
stored sources, keeping a replaced title in `extra['declared_title']` and marking a guessed one in
`extra['title_from']`.

### Language

A page's language is what it declares (`<html lang>`, the PDF or Office metadata), trimmed to
the primary subtag. A quarter of the pages one crawl hour stored declared none, mostly PDFs, and
a page with no language is scored as English by the topic labeller (`B-53`), so a page in
another language without a declaration missed that correction. Since `B-153` an undeclared
language is read from the text with py3langid and marked `extra['language_from'] = 'text'`: only
from at least 200 letters of the opening, only when letters are at least 60% of its visible
characters, and only at a normalised probability of 0.9 or more, so a table or a heading stays
unknown rather than guessed. The share rule came from the first backfill: a statistics table, a
quarter letters, was read as Volapük at 0.99; the identifier is confidently wrong on such text. On 2,000 stored pages that did declare
a language it agreed with the declaration 98.5% of the time and decided on 97% (some of the
disagreements were declarations that were themselves wrong). A declared language is never
overridden. `relanguage` does the same for stored sources, from their first passages, and
re-checks its own earlier guesses, clearing one the detector no longer makes; a source whose
language moves to or from another language has its own and its passages' topic labels marked
for relabelling. On the local corpus (2026-10-07) it read 7,629 of 12,272 undeclared sources,
321 of them in another language.

### The source row

One row per URL, created on first fetch and updated on every fetch.

- **Validators** (`etag`, `last_modified`) are the whole mechanism of conditional requests (§6.4).
  Before this, the crawler asked for them, stored them nowhere, and re-downloaded every page; the
  304 path was correct, tested and unreachable. A 304 updates only `accessed_at`: a source last
  verified this morning and one last verified months ago differ to a corpus that must be trusted.
- **The checksum** of the bytes as fetched is written whether or not the raw file is kept. A 200
  with identical bytes is an unchanged page, a cheaper fact than re-extracting, and only the
  previous hash can tell.
- **`raw_file_path`** is null when the tier keeps no file: "deliberately not kept", not "missing".
- **Nothing is blanked.** `None` means "nothing new": a fetch without an ETag must not erase last
  week's, or the next request silently stops being conditional, and a page that dropped its meta
  tags must not erase the title an earlier fetch found. Some columns do overwrite, each for a
  reason: the trust state (a re-fetch is a fresh screening, `P4-14`), the extractor name (it
  describes this extraction, and keeping the old name would misattribute the text), and
  `text_available` in both directions (a page that stopped extracting has changed: a paywall, a
  redesign). Topics a source was crawled for accumulate (`P2-14`, `P2-21`), since overwriting
  would make the record depend on which crawl ran last; what it is *about* is `topic_labels`,
  which the fetch path never writes. The source tier only moves up, so a hand correction in Admin
  is not reverted by the next crawl. Media type and served URL sit in `extra`: neither was worth
  a migration alone.

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
