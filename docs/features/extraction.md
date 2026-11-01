# Extraction and chunking

Extraction turns fetched bytes into text and metadata, and chunking cuts that text into
passages. A passage is what gets embedded, what search returns, what a model reads, and what
an edge cites as evidence, so every passage must make sense on its own. It must also carry a
location (page or character offset) that a reader can follow back into the stored file.

- **Code:** `services/worker/worker/extract/` (`html.py`, `pdf.py`, `document.py`,
  `pdftables.py`, `figures.py`, `clean.py`, `chunk.py`, `dockind.py`, `errorpage.py`,
  `injection.py`, `language.py`, `paywall.py`, `readable.py`), `cleancut.py`, `rawstore.py`, `ocr_queue.py`,
  `boilerplate.py`, `rechunk.py`, `retable.py`, `dockind.py`, `retitle.py`, `relanguage.py`,
  `unreadable.py`, `remeta.py`, `extract/declared.py`;
  `packages/meridian_core/meridian_core/chunks.py`, `sources.py`, `boilerplate.py`, `titles.py`,
  `figures.py`, `bibliography.py`, `stubs.py`
- **Tasks:** `P1-07`–`P1-13`, `P2-02`, `B-42`–`B-45`, `B-59`, `B-69`, `B-153`, `B-191`, `B-195`,
  `B-214`, `B-215`, `B-234`, `B-235`, `B-256`
- **Decisions:** [ADR 0019](../adr/0019-stored-pdfs-re-extracted-for-their-tables.md)

## How it works

```
bytes ──► route by media type
           ├─ HTML  ── trafilatura (precision), on the rendered HTML if the browser ran
           ├─ PDF   ── pdftotext, page by page; a -layout pass places tables;
           │          too few chars per page ─► OCR queue
           └─ Office ─ MarkItDown convert_stream, four converters only
       ──► metadata: title (cleaned), DOI and other identifiers, language, date
       ──► document kind: paper, listing, legal, news, report, profile, other
       ──► clean: drop whole lines (skip links, menus, site boilerplate, running headers)
       ──► chunk: split on paragraphs and headings; sentences, then a hard cap, as fallbacks
       ──► leave out passages that are mostly unreadable characters (an unmapped font)
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
  completed, since `publication_date` is a column citations are built from. Author, publisher
  and date are read first from what the page declares, then from trafilatura, each judged
  ([Source metadata](#source-metadata)).

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
  highest ordinary document at 5%. Garble under that share, or confined to part of a page, is judged
  passage by passage ([Unreadable passages](#unreadable-passages)).
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
  all of year, month and day are present, and `pdfinfo` is run with `-isodates`: until `B-234`
  it printed `Fri Sep 22 14:06:39 2017 UTC`, the parser expected `D:20170922…`, and no PDF in
  the corpus had a date. The parser's test fed it the format the tool never printed.

<a id="pdf-tables"></a>**Tables keep their rows** (`B-214`). Reading order writes a table column
by column: every row label, then each column's values as a list of their own, so a passage holds
a figure with nothing to say what it measures. `-layout` keeps the columns aligned but
interleaves two-column prose line by line, so it cannot replace a page. A second `-layout` pass
finds the tables instead (`worker/extract/pdftables.py`), and each becomes a Markdown pipe
table put where its cells were in the reading-order text, which the chunker already keeps whole
and cuts between rows (`B-191`) and whose header the embedder carries forward (`B-195`). The
rest of the page is untouched.

- **Finding a table.** A line split on runs of three or more spaces is a table line when it
  has three or more short cells, or a label and one value. A block is three or more table lines,
  one other line allowed inside it, mostly values or a text table of short cells; two blank
  lines end it. A page whose lines are largely two columns of running words is prose, and has
  no tables.
- **Columns are found down the whole block**, at blank bands every line shares, not by
  splitting each line, so an empty cell leaves its row's values in their columns. A band may be
  crossed by one line in five (a crowded row with one space where the others have a gap), and
  the cut is made at a column blank in every line. A lower-case line inside a block ends the
  label above it.
- **A table is placed only when it is clean.** The run of reading-order lines that is the
  table's cells must hold most of its words; few may be left elsewhere on the page (a line
  another table could hold does not count); and the words going out and coming in must match,
  both ways, within a tenth. Otherwise the page is left in reading order: a table found only in
  part would be stored twice, and one placed over another table's cells would lose those.
- **Measured** on 100 stored PDFs (1,912 non-blank pages): about a third of pages flagged, at
  about 87% precision on 60 hand-labelled pages with every two-column prose page left alone;
  on flagged pages reading order has a median 3.6 times the lines layout has. The final rule
  placed 624 tables on 364 pages; no placed page gained or lost more than 8% of its words, and
  across them 0.1% of words were lost and 0.7% read twice. Refused: mostly form-like pages,
  where each record is laid out on its own.
- **Stored documents** get their tables from `worker.retable` (`B-215`, below). The re-cut
  (`worker.rechunk`) cannot do it: it rebuilds from stored passages, which hold the table column
  by column, not from the file.
- If the `-layout` pass fails or its pages do not line up with the first pass, the document is
  extracted as before. The pass costs a second `pdftotext` run per PDF.

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

### <a id="paywalled-articles"></a>Paywalled articles (`B-256`)

A paywall is never bypassed. What a walled page shows everyone (its headline, its time and
its opening paragraphs) is still worth having: search can find the story, and a reader can
follow the link. So such a page is kept as a **stub**: one passage holding what is visible,
and a mark saying it is not the article. Code: `worker/extract/paywall.py`,
`meridian_core/stubs.py`.

**Detection is the publisher's own markup, and nothing else.** The schema.org markup publishers
write so search engines can tell a paywall from cloaking: `isAccessibleForFree: false` on the
JSON-LD item the page declares itself to be, or on one of its `hasPart` elements (the walled
section, named by a CSS selector). `false` is read as a boolean or as the string `"False"`;
one outlet writes the string. Only the page's own items count, top level, list members and
`@graph`: a walled article listed inside a page does not wall the page. Then two conditions:

- **The extracted text is under `STUB_CEILING` (4,000 characters).** A metered site that sends
  every reader the whole text has not walled it, whatever its markup says.
- **It is not a scholarly page** (its own DOI or `citation_*` meta). An abstract page is the
  paper's own summary, already read as one, and resolution (`P1-14`) looks for the open copy.

**Measured** (2026-11-01). Live, one request each, three seconds apart: 25 articles from one
general outlet's news sitemap (3 declared walled), 5 from a business daily's feed (2) and 5
from a regional outlet's feed (5, all through a `hasPart` with `"False"`). Every declared page
extracted 387 to 1,042 characters (the headline, the standfirst, the byline and one to three
paragraphs); the free articles beside them 1,079 to 10,940, so length alone cannot separate
them, and no free page was flagged. In the stored corpus, 114 of 95,940 raw HTML files carry
`isAccessibleForFree` false somewhere; the rule makes 22 of them stubs (essays, magazine
articles and an archive's journal-article pages, 0 to 3,018 characters, the longest ending
"register to continue reading"), keeps 4 whole (5,352 to 12,078 characters, served in full
behind the markup), and leaves 88 alone: 6 scholarly pages declaring it, and 82 where the
`false` sits on something other than the page's own item. Three signals were measured
and rejected: wrapper class names (`paywall`, `premium`, a metering script) were on every
page of one outlet, free or not; JSON-LD `wordCount` was declared by none of the walled pages;
and length alone overlaps, as above.

**The stub's passage** is the headline, a line `Published <time>` when the article declares an
ISO 8601 `datePublished` (kept verbatim; anything else is left out, not guessed), a blank line,
and the teaser: the extracted text less a first line repeating the headline, or, when the page
renders none, the JSON-LD `description`, else `og:description`, `twitter:description` or the
meta description. Each part is the page's own words; the passage is assembled from them, so
its offset counts into the assembled text, not into an extraction. It is one passage, never
cleaned or cut, and with less than a headline and a sentence (60 characters) there is no stub.
Its kind is read from the address and the head alone (a teaser is not text the listing rules
were measured on). `text_available` is true: the passage is searchable text.

**The mark** is `sources.extra['stub']`: `reason` (`paywall`), `rule` (`isAccessibleForFree` or
`hasPart`), `from` (`page`), `visible` (which of headline, published, teaser it holds),
`published`, and `chars_seen` (what extraction got). It is set or cleared on every fetch, in the
same transaction as the passage:

- **A later fetch that finds the whole article** (the wall came down) cuts it as any page and
  clears the mark; the stub's passage is superseded, not deleted.
- **A page first kept whole and later walled** becomes a stub even when its bytes did not change,
  so search stops offering text the page no longer shows.
- **The article handed in through the connector** (`B-264`) with the page's address as its
  `source_url` replaces the stub: the stub's passage is retired, its mark cleared, and
  `extra['stub_replaced_by']` names the source holding the article. A later fetch of the walled
  page does not put a teaser back beside it. A handed-in document is never itself a stub.

Where the mark shows: search hits, Find, Ask and the source page (`partial`, see
[search](search.md#stubs) and [the web app](web-app.md#stubs)), and every MCP tool that returns a
passage or a source ([MCP](mcp.md#what-reaches-a-model)). Synthesis never reads a stub
([the cycle](synthesis.md#the-cycle)).

**Not built:** the headline and summary from a feed or news-sitemap entry. The page itself carried
the same headline and description on every walled page measured, and carrying a feed entry's
text to the page fetch would need a column on the queue. A stub is made only by a fetch: sources
stored before this rule, most of them keeping no raw file to read the markup from, are not
re-examined.

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


**Tables are cut between rows** (`B-191`). A Markdown pipe table, which is what converted
spreadsheets and HTML tables become, has no blank lines, so the chunker read a whole table as one
paragraph, found no sentence boundary in it, and cut at the 2,000-character cap: mid-row and
mid-number ("| 1389("), leaving the next passage a run of cells with no row start. On
2026-10-10, 4.6% of all passages sat exactly at the cap, and 80% of spreadsheet passages. A
paragraph of three or more lines, most starting with `|`, is now split between rows, packed up
to the cap (rows are dense and seldom the answer, and packing to the 1,200 target nearly
doubled the passages a spreadsheet makes). Every last-resort cut, in prose too, backs off to
the last whitespace in the second half of the window, so only a run with no whitespace at all
is cut inside a token. Property tests (Hypothesis) generate tables and unbroken runs and check
the cap, the slice and the row boundaries.

Measured read-only on 26 stored sources from the six hosts with the most cut tables, rebuilding
each text from its passages and cutting it both ways:

| | passages | at the cap | cut inside a token | table passages starting at a row |
|---|---|---|---|---|
| before | 21,095 | 85% | 50% | 7% |
| after | 23,019 | 0.2% | 0% | 98% |

This applies to text chunked from now on. Stored passages keep their old cuts until a source
is re-cut (`worker.rechunk --apply`, which also re-embeds what changed); that is the operator's
call, since its first phases write even without `--apply`. Not done yet: a continuation passage
still lacks its table's header row, so a value's column cannot be read from it alone (carrying
the header into the embedding view, not the stored text, keeps the passage a slice), and
spreadsheets emitted rows of `NaN` cells. Both are handled now (`B-195`): a split table leaves
out rows whose every cell is empty or `NaN` (padding; several thousand stored passages held runs
of them), packing the rows either side apart so each passage stays a slice; and a continuation
passage is embedded with its table's header (see
[the embedding view](embedding.md#the-embedding-view)).
<a id="unreadable-passages"></a>
### Unreadable passages

A PDF font with no Unicode map hands `pdftotext` glyph numbers instead of letters (`B-235`).
In the common case each glyph sits 29 code points below its letter, so "the and" arrives as
`WKH\x03DQG`: printable capitals, digits as control characters, and the space glyph as
U+0003. Other fonts land in the Greek and Latin Extended blocks, or in private use. The
page-level check above catches a page that is a fifth or more unreadable characters; this
text is a control character every word or so, often under that, and on pages where the rest
is readable. Such passages were embedded and searched: their vectors sit near nothing in
particular, so a nonsense query's nearest neighbours were mostly these.

**The test** (`extract/readable.py`): split the passage into words at the gaps a reader sees
(spaces, line breaks, tabs, and BEL and BS, which is how a word processor's bullets and tab
leaders reach the text layer), and count the characters of every word that holds a control,
private-use or replacement character. More than half (`UNREADABLE_WORD_SHARE`), and the
passage is unreadable. By word, because the character share of such text is about a fifth
and overlaps tables of contents and bulleted lists; by word, nearly all of it is unreadable
and those are almost none. `str.isspace` is not used to find the gaps: it counts U+001C to
U+001F, which is where the shifted font puts the digits.

**Measured** on the local corpus (2026-11-01, 1,370,561 live passages, read only): 13,003
passages hold at least one such character. Over the threshold:

| words unreadable, more than | passages | sources |
|---|---|---|
| 30% | 990 | 163 |
| 40% | 776 | 119 |
| **50%** | **657** (0.048%) | **95** |
| 60% | 559 | 79 |
| 70% | 459 | 56 |

All but one are PDF passages; 552 were embedded. Read by eye, the passages between 30% and
50% are mostly readable: tables of contents, equations with symbol-font brackets, Tamil and
Bengali text with a replacement character inside some words, a paragraph followed by a
garbled one. Above 50% nearly all are glyph numbers. The misses at 50%: a three-word heading
with a private-use ligature ("In\ue207astructure is not enough"), a URL glued to a bullet
character, and a few equation fragments; most of them are short enough that the vector arm
already left them out.

- **Left out at the cut, not cleaned out as lines.** `cleancut.clean_cut` drops an
  unreadable passage after chunking and renumbers `chunk_index`; offsets still point into
  the extracted text, so every kept passage stays a slice. A line is too little text to
  judge (a bulleted heading is one word with a control in it), and a passage is what is
  embedded and searched. A passage half garbled and half a real paragraph is kept for its
  readable half. `Cut.unreadable` counts them and the "chunked" log line carries it.
- **Stored passages** are retired by `worker.unreadable`: report by default, `--apply`
  supersedes them (`superseded_at`, as a re-crawl does), so they leave search, embedding and
  every reader surface and still resolve for anything that cites them; the report counts
  those. It asks the database for candidates with `UNREADABLE_CLASS`, one character class
  that Python and Postgres read alike (a test compares the two at every boundary), and
  judges each with the same function as the cut. `--source` narrows it, `--limit` stops it
  early. `worker.rechunk --apply` also drops them, as a side effect of re-cutting, but skips
  any source that is cited.
- **Not sent to OCR.** A document whose pages are mostly glyph numbers is still caught by
  the page check and queued for OCR; one whose garble is under that keeps its readable
  passages and loses the rest. OCR would recover the text; deciding which of these
  documents deserve it is not done here.

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
4. **press_release** (`B-253`): an organisation speaking for itself. The page's JSON-LD declares
   schema.org `PressRelease`; or a dated or `og:type article` page sits under a release path
   (`press-release`, `media-release`, `news-release`, `press-statement` and the like, matched
   inside a segment, so `sponsored-press-releases` counts, whoever hosts it); or a government or
   institutional site publishes it in its own newsroom (`news`, `newsroom`, `press`, `media`,
   `media-centre`, `announcements`). An agency's news *is* its own voice.
5. **opinion** (`B-253`): JSON-LD `OpinionNewsArticle`, or a press publisher's article under an
   opinion path (`opinion`, `commentary`, `editorial`, `op-ed`, `columns`). Only an outlet's: a
   `commentary` path on an agency's site is not a column.
6. **news**: a press publisher's dated article, or a dated `og:type article` under a news path.
   A story *about* an agency on an outlet stays news; only a release path outranks the outlet.
7. **report**: a PDF or office document from an official or institutional publisher.
8. **profile**: an organisation's own pages (home, about, contact, people, programmes).
9. **other**.

The thresholds were calibrated on a live corpus against known listings on one side and abstract
pages and articles on the other (the task report has the numbers). The `dockind` pass applies
the rules to stored sources from their live chunks, with head metadata from the raw file when one
is kept. A verdict records the head signals it read (`og_type`, `schema_types` in
`extra['doc_kind']`), because a background source keeps no raw file and re-deriving its kind
would otherwise lose them: before that was recorded, re-deriving the local corpus's news moved
501 news-path articles to `other`. For a verdict older than that, the rule it recorded stands in:
the news-path rule, and the outlet rule on an undated page, could only have fired on `og:type
article`.

`--kind news` re-derives only what is recorded as news, which is how the `B-253` split reaches
stored sources. On the local corpus (2026-11-01), of 2,919 news sources: 1,703 stay news, 1,144
become press releases (1,040 from agency and institution newsrooms, 104 from release paths), 72
become opinion, none fall to `other`. No stored page declared `PressRelease` in its JSON-LD; the
path rules carry the split, and the schema rule is there for the sites that declare it.

<a id="re-chunking"></a>**Re-chunking** (`B-43`). `rechunk` re-cuts what was chunked before
cleaning existed, from the live chunks, since a background source keeps no raw file. Unpaginated
text is rebuilt by laying each chunk at its offset and filling the gaps (only ever whitespace)
with newlines, so new offsets still index the original extraction; a page's chunks are joined
with a blank line. Three phases, because the per-host set needs every page's lines first: record
`page_lines` for sources that have none (never re-recorded from chunks that may be cleaned),
rebuild the boilerplate set, then re-cut and compare. Re-cut sources get new ids, so the embedding,
novelty and labelling passes pick them up again.

<a id="re-extracting-stored-pdfs"></a>**Re-extracting stored PDFs for their tables** (`B-215`,
[ADR 0019](../adr/0019-stored-pdfs-re-extracted-for-their-tables.md)). `retable` reads each
stored PDF's raw file again, through the same extraction a fetch uses, and replaces the source's
passages only where that extraction places a table. A source no table reaches is left exactly as
it is, even when a fresh cut of it would differ for some other reason: that difference is not
this pass's to make. Also left alone: a source anything cites; a file whose checksum is not the
fetch's (the stored passages were not cut from it); a source whose text came from OCR; and a PDF
that now reads as a scan. A new passage whose text an old one had keeps that vector
(`meridian_core.chunks.carry_embeddings`), except one that starts with a table row, whose vector
can carry a header row from the passage before. A second run changes nothing.

Measured on 338 stored PDFs before the run: 92 gained tables (1,734 placed), passages went from
14,001 to 14,294, and 81% of the new passages kept their old vector, so the re-embedding is a
fifth of the passages rewritten.

### Titles

Measured on a live corpus, declared titles are wrong in three recurring ways (`B-69`): a
placeholder ("untitled", "Microsoft Word - report.docx", a bare file name, "nan"), which cites as
nothing; the site rather than the page ("Home", "Results", the site's name on every page), many
sources with one title; and nothing at all, common on PDFs. `clean_title` drops the first two and
strips a site name off "Page | Site". The fallback is the first line when it looks like a heading
and nothing else: three to twenty-five words, starting with a capital or digit, not ending in a
colon or full stop, and none of a first page's furniture (journal banners, DOIs, "Received:").
A wrong title is worse than none, which shows the address instead. An author line is not a title either (`B-220`): a PDF whose metadata title is its editor
("… (Editor)"), a bare "Name et al", or a reference-list entry ("Surname, I., Surname, I., …").
Those three shapes matched 6 of 105,909 stored titles; a looser name-list rule also caught
headlines and case names, so it is kept to these. `retitle` applies this to
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

### Source metadata

Author, publisher and date are what citations and exports are built from, so a wrong one is
worse than none (`B-234`). Measured on the local corpus (2026-11-01, kept sources), the gaps
and the errors had four mechanical causes, none of them a page that declared nothing:

- **No PDF had a date.** `pdfinfo` prints dates as `Fri Sep 22 14:06:39 2017 UTC` unless asked
  for `-isodates`; the parser expected the PDF's own `D:2017…` form and matched nothing.
- **Every HTML publisher was the address.** trafilatura's JSON names the site `source-hostname`;
  the code read `sitename`, which is never there, and fell back to `hostname` on every page.
- **A copyright line became a publication date.** trafilatura's date search, given a footer's
  "© 2026", returns 1 January 2026; a page header's clock or "last updated" stamp returns the
  fetch day. About one stored HTML date in six was one or the other.
- **trafilatura rewrites authors.** It splits on "and" and "of" and drops words: "Department of
  Health in X County" becomes "Department", "Surname, Given" becomes "Surname; Given".

**Precedence: declared, then inferred, never guessed.** `worker/extract/declared.py` reads what
the page declares about itself: Highwire `citation_*` tags, the page's own JSON-LD items (not
the site's, its organisation's or a breadcrumb's; an article beats a generic `WebPage`; `@id`
references are followed), Open Graph `article:published_time`, Dublin Core, plain
`<meta name="author">` and `og:site_name`, and microdata `datePublished` when every one on the
page agrees (a listing marks up each item). Meta tags are read from the head only, as for the
page's own DOI. In each field the first source that passes the rules wins, in the order
`citation`, `jsonld`, `article`, `dc`, `meta`, `microdata`. Only when the page declares nothing
usable does trafilatura's value stand, judged more strictly. A declared title or language
fills one trafilatura did not find and never replaces one. For a PDF, `pdfinfo`'s creation date
and author are the declared values.

**Rules** (`meridian_core/bibliography.py`), each returning the name of the rule that refused:

| Field | Rule | Refuses |
|---|---|---|
| date | `future` | after the fetch (a day of slack for time zones) |
| date | `placeholder` | before 1900, or 1970-01-01 |
| date | `year only` | 1 January from an inferred source (a declared full day stands) |
| date | `fetch day` | an inferred date within a day of a fetch: server clocks, "last updated" |
| date | `made on request` | a PDF created within a day of being fetched |
| date | `before pdf` | a PDF created before 1993 |
| author | `placeholder` | "Author", "Admin", "Microsoft Office User", "Staff Writer", a month name, … |
| author | `site credit` | "Government Websites by …", "Powered by …", a template credit |
| author | `address` / `contact` | a URL or email; a postal or telephone block |
| author | `login name` | one lower-case token ("jdoe", "first.last") |
| publisher | `platform` | the CMS ("WordPress", "Drupal", …) or the page's `generator` |
| publisher | `address` | an IP address, or a host glued to a scheme ("example.orghttps:") |

A bracketed email after a name and an office account suffix ("Surname, Given@AGENCY") are
trimmed, not refused. Rejected and kept narrow: an `og:site_name` equal to the page title
(on a sample of 99 such pages, 45 were home pages whose title is rightly the site's name).

**Measured.** The year-only rule on 60 cleared dates: 27 were copyright lines, 26 other
footer or path years, 2 a 1 January that was not the page's (a date range, a script
constant), none a real publication date. The fetch-day rule on 400 dates within a day of the
fetch: where the page declared a date, 45 of 58 agreed (and those are kept, being declared);
of the 342 that declared none, 257 had the date on the page as a server timestamp,
page-generation stamp, "as of" or "last updated" line, and 85 nowhere visible.

**Provenance** sits beside each value in `extra`, named as `title_from` and `language_from`
already were: `author_from`, `publisher_from`, `date_from` hold `citation`, `jsonld`, `article`,
`dc`, `meta`, `microdata` or `pdfinfo` (declared), or `inferred`, `trafilatura`, `host`. A value
a rule refused is kept in `extra['rejected_metadata']`; `title_from` gains the declared source
when a title came from declared metadata.

**Stored sources** get the same through `worker.remeta`, from the raw file (the PDF through
`pdfinfo`, against the source's own fetch days). A declared value replaces a stored one of
unknown or inferred provenance; a stored value already from a declared source is left; with
nothing declared, the stored value is judged (an unmarked HTML date as `inferred`) and cleared
if a rule refuses it. Nothing inferred replaces anything. Replaced and cleared values are kept
in `extra['replaced_metadata']`. A source with no raw file has only its stored values judged.

The report over the local corpus (2026-11-01, 110,037 non-junk sources, 98,147 raw files
read):

| Field | Filled before | After | What changed |
|---|---|---|---|
| date | 87,522 (79.5%) | 75,791 (68.9%) | 8,073 filled (7,501 PDFs); 6,152 inferred replaced by declared; 19,804 cleared (15,771 year only, 4,033 fetch day) |
| author | 26,879 (24.4%) | 28,448 (25.9%) | 3,205 filled; 4,886 replaced by declared (mostly mangled names restored); 1,636 cleared (989 placeholder, 459 login name, 97 site credit) |
| publisher | 91,900 (83.5%) | 93,210 (84.7%) | 38,181 addresses replaced by a declared name; 1,342 filled; 32 cleared |
| language | 94.0% | 94.0% | 63 filled |
| title | 90.7% | 90.9% | 201 filled |

The date's fill rate falls because about 20,000 of the dates it had were wrong. Declared dates a
rule refused: 37 placeholders, 37 citation dates after the fetch, 33 PDFs made on request.

Not done here: resolving dates from identifiers (an arXiv identifier gives only year and month,
which `publication_date` refuses) and from Crossref or OpenAlex, which the DOI resolver calls
but does not keep metadata from. Site-wide titles (one title on many pages of a host) are
`worker.retitle`'s territory.

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

- `boilerplate` runs daily. `rechunk`, `retable`, `dockind`, `retitle` and `furniture` are one-off
  passes for what was stored before a rule existed. Each reports by default, needs `--apply`
  to write, and never touches a source that anything cites.
- `unreadable` retires stored passages a reader could not read
  ([Unreadable passages](#unreadable-passages)); report by default, `--apply` supersedes them,
  cited or not, since a superseded passage still resolves.
- `relanguage` is a one-off pass too, reporting by default and writing with `--apply`; it
  changes only a source's language and marks labels for relabelling ([Language](#language)).
- `remeta` re-reads stored sources' author, publisher and date (and fills a missing title or
  language) from their raw files, and clears stored values a rule refuses
  ([Source metadata](#source-metadata)). Report by default, `--apply` writes; `--limit` and
  `--after` bound a run. The old value is kept in `extra['replaced_metadata']`.
- `pdftotext` (poppler) is required in the worker image. Without it every PDF fails loudly,
  by design.

## Failure modes and traps

- **A NUL character cannot be stored** (`B-158`). Postgres keeps none in `text` or `jsonb`, and a
  PDF whose text layer carried one failed the page's whole write. `meridian_core.storable`
  removes them where a source's fields and its passages are written.

- `readable()` in the API joins wrapped lines for display only, conservatively. Loosen it
  only after re-running the passage sample described in the handover.
- A URL served first as HTML and later as a PDF gets a second raw path. The retention sweep
  reports the first as orphaned.

## Tests

`tests/unit/test_chunk*.py`, `test_clean*.py`, `test_extract_html.py`,
`test_extract_pdf.py`, `test_paywall.py`, `test_pdf_tables.py`, `test_extract_document.py`, `test_extract_figures.py`, `test_dockind*.py`, `test_errorpage.py`, `test_titles.py`,
`test_bibliography.py`, `test_bibliography_titles.py`, `test_declared_metadata.py`, `test_remeta_decide.py`,
`test_injection_screen.py`, `test_rawstore*.py`, `test_pdf_garbled.py`, `test_language_detection.py`,
`test_storable.py`, `test_readable.py`; `tests/integration/test_chunks*.py`, `test_rechunk*.py`,
`test_retable.py`, `test_unreadable.py`,
`test_relanguage.py`, `test_retitle.py`, `test_remeta.py`, `test_dockind_backfill.py`, `test_boilerplate.py`,
`test_nul_bytes.py`, `test_stub_marks.py`; the fetch path's stubs in `test_worker_run.py`.
