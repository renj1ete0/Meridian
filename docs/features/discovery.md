# Discovery

Discovery decides what goes into the crawl queue, and in what order. There are five sources
of URLs: search queries, links followed from fetched pages, sitemaps of proven sites, papers
cited by fetched pages, and diversity seeds read off the graph. Each is ranked by what the
corpus already knows. The aim is that the next fetch is as likely as possible to land on a
page about one of the topics.

- **Code:** `services/worker/worker/seedsearch.py`, `search.py`, `prefilter.py`,
  `sitemaps.py`, `sitemapmine.py`, `topicmatch.py`, `resolve_doi.py`, `requeue.py`,
  `requeue_links.py`, `requeue_dois.py`, `translate.py`, `hostscore.py`;
  `packages/meridian_core/meridian_core/searchseeds.py`, `hostscores.py`,
  `citedpapers.py`, `diversity.py`, `translations.py`, `tiering.py`
- **Tasks:** `P1-06`, `P1-14`, `P1-28`, `P1-34`, `P5-05`, `B-48`, `B-51`, `B-52`, `B-58`,
  `B-90`–`B-92`, `B-103`–`B-118`

## How it works

| Source | Queued by | Seed source | How it is ranked |
|---|---|---|---|
| **Search** | `seedsearch` every 3 h | `diversity` (the query), `search` (its results) | Queries above every link; results by source tier |
| **Followed links** | the fetch loop, as each page is stored | `frontier` | Source tier, adjusted by the host's score and the parent page's labels |
| **Sitemaps** | `sitemapmine` hourly, for proven hosts | `sitemap` | Paths matching a topic's vocabulary first; the rest at the bottom |
| **Cited papers** | the fetch loop (DOIs on a page) | `doi`, `citation` | By the citing page: on-topic page high, off-topic floor, unjudged modest |
| **Diversity** | `seedsearch`, from the graph | `diversity` | Like search queries, capped per run |

**Search seeds** are built mechanically from each topic's name, description and approved
vocabulary: the concept alone, two concepts together, evidence phrasings ("… evaluation"),
counter-seeds ("criticism of …"), news (`!news`), scholarly sources (`!science`), and
other-language seeds from Wikipedia's interlanguage titles (`translate`). A query is never
repeated, except news queries, which come round again after three days.

**SearXNG** answers queries with results from several engines. An unresponsive engine is
routine and logged. Empty results mean the query is done. An unreachable SearXNG is
transient and the query retries. Queries are paced (`MERIDIAN_SEARCH_MIN_INTERVAL_S`), and
throttled queries are retried, because engines that are asked too fast stop answering
(`B-107`, `B-109`).

**The prefilter** decides whether a URL is worth a request at all. It runs the cheapest checks
first: normalise the URL (drop the fragment and tracking parameters, keep any trailing
slash); check its shape (scheme, host, extension allowlist); check the blocklist and site
furniture (privacy pages, logins); check whether it is already queued or stored; and check
any cached robots.txt that would refuse it.

**Host scores** (`B-48`) are rebuilt hourly from content labels. A host is *proven* when
enough of its examined pages are on a topic, *off-topic* when almost none are, and *unknown*
until enough pages have been read. Links to off-topic hosts are not queued (official hosts
are down-ranked instead). Unknown hosts are explored only a few links at a time, and no host
holds more than a fixed number of pending links. Proven hosts' links go first (`B-115`).

**Re-ranking after the fact.** A page is labelled an hour or more after it is fetched, but
its links and DOIs were queued at fetch time. So three passes settle them later, and only
ever downward: `requeue` (host verdicts), `requeue_links` (links of a page found off-topic,
via `queue.parent_source_id`) and `requeue_dois` (papers ranked by their best citing page).
Nothing is deleted; links wait at the bottom.

**Cited papers** are resolved to a *legally available* copy: Unpaywall, OpenAlex, CORE,
preprint servers, Europe PMC, then Semantic Scholar. A provider without a key is skipped.
If every provider answered and none had a copy, the task is done. If none answered, it
retries.

## Design choices

- **Search finds new ground; links deepen it.** Measured across loop runs, roughly 40% of
  search results are on a topic, against a few percent of followed links. Search therefore
  gets directed claim slots, and links are gated by what their host and parent page turned out
  to be.
- **Sitemaps only for proven hosts.** Mining every host's sitemap would turn into a
  breadth-first crawl of the largest sites on the web. A sitemap's entries go through the same
  host cap as followed links. Sitemap tasks are filed under the host's commonest topic,
  because a task with no topic is never claimed.
- **Topic from the path, not the parent.** A sitemap lists a whole site, so its entries get
  a topic only when their path names a vocabulary term (`topicmatch`); otherwise they get
  none. Ambiguous terms are never used.
- **Same-site sitemaps only, with XML bombs defused.** DTDs are refused before parsing and
  entity expansion is off. A hostile robots.txt cannot inject other domains' URLs.
- **No model anywhere.** Every seed is a template over database text, so discovery keeps
  working with every model offline (§2.1).

## Configuration

- Search: `SEARXNG_URL` and the search variables in
  [reference/environment.md](../reference/environment.md#search-browser-and-paper-resolution).
  SearXNG's own engine list is in `config/searxng/`.
- Topics and their descriptions: **Admin → Topic weights**; vocabulary: **Admin → Gazetteer approvals**.
- Seed languages for translations: `search_languages` in the global fetch-policy row.
- Paper resolution: `MERIDIAN_CONTACT_EMAIL` (Unpaywall, Wikimedia), `CORE_API_KEY`,
  `SEMANTIC_SCHOLAR_API_KEY`.

## Operating it

- Jobs: `seedsearch`, `sitemapmine`, `hostscore`, `requeue`, `requeue_links`,
  `requeue_dois`, `translate` ([reference/scheduled-jobs.md](../reference/scheduled-jobs.md)).
  Each has a report mode for checking what it would do.
- The question to watch is **yield by seed source**: the share of new pages from each source
  that turn out to be on a topic. Loop-run notes record how to measure it.
- To see whether engines are answering, query SearXNG from the worker container and read
  `unresponsive_engines`.

## Failure modes and traps

- **Engines throttle this client.** When they do, every web engine can refuse for a day, while
  the science and news categories keep answering. A paid search API is the lasting fix
  (`B-109`).
- **The query task type is `query`**, not `search`; `seed_source='search'` marks the
  results.
- **Some sites refuse clients with no contact address.** They end up blocked by `B-114`.
  Once `MERIDIAN_CONTACT_EMAIL` is set, unblock them in Admin.

## Tests

`tests/unit/test_prefilter*.py`, `test_searchseeds.py`, `test_sitemaps.py`,
`test_topicmatch.py`, `test_hostscores.py`, `test_cited_paper_rank.py`;
`tests/integration/test_requeue*.py`, `test_sitemapmine.py`, `test_resolve_doi*.py`.
