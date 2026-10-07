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

### Search

A corpus built by following links converges: each page links to its own neighbourhood, and
after the first few seeds the crawl was 98% link-following, fetching more of the same few sites.
§7.4 reserves a share of the seed budget for seeds that do not come from the corpus's own links;
`searchseeds` writes them (`B-51`).

- **Shapes.** The concept alone and beside its topic's name; two concepts of one topic together,
  which reaches material neither finds alone; evidence phrasings; §7.4's counter-seeds, because
  a crawl that only confirms its vocabulary walks toward consensus; news (`!news`), which
  link-following from scholarly pages never reaches; and §7.4 mechanism 5, a concept in another
  language's own words under SearXNG's `:lang` prefix, and as news in that language. Each shape
  records its §7.4 mechanism in `queue.seed_mechanism` (`P5-05`; NULL for shapes that only widen
  a topic), so yield can be read per mechanism. The counter-phrasings are mechanism 1 at *topic*
  level: they fire for every topic, since the node-level trigger needs stance (see
  [diversity seeds](#diversity-seeds)).
- **A description's facets** (`B-103`). A description written for people lists what the topic
  covers ("A, B, C and D"); each item is a subject a search can ask for. It is split on
  punctuation and "and"/"or", leading function words dropped, clauses and single short words
  left out.
- **Broad topics are searched by name.** Below a few approved terms and with no description, a
  topic is searched by its name alone. The operator runs topics broad and lets the crawl find its
  way in; the report names such topics, since a description or vocabulary would widen their
  queries, but that is an option, never a requirement.
- **A run interleaves shapes** rather than drawing in proportion to candidates: pairs vastly
  outnumber everything else, and a run of nothing but pairs would never ask for news or
  counter-evidence. Each run takes one of each shape that still has a new query, then fills the
  rest at random. The shuffle is seeded, so a run is reproducible and successive runs differ.
- **No repeats**: the generator sees every query already queued, in any status. News is the
  exception (`B-105`), asked again after three days, because it is the one shape whose answers
  change: a news query found 29 new pages on average in the three days before, against 11 for
  any other shape, and under the no-repeat rule each could be asked once, ever.
- **Queries outrank links.** Query rows are `seed_source="diversity"` (§7.4's reserved share) at
  `QUERY_PRIORITY`, above every tier, so a query is answered promptly and its results compete on
  their own tier. A search result gets `SEARCH_RESULT_BONUS` over its tier (`B-51`): it answers a
  question somebody asked about a topic, while a followed link is whatever a page carried. A
  topic with `MAX_PENDING` unanswered queries gets none that run: a worker not keeping up with
  search should not come back to a backlog of hundreds.
- **A result carries its query's topic**, not one guessed from its path: a query was written
  *for* a topic, so every result is an answer to it.

**The search backend** (`P1-34`, §6.4). SearXNG scrapes upstream engines, so individual engines
break or are rate-limited regularly, and a dead engine must never stall the queue. Three
different events: an engine unresponsive (logged; results kept, and the unresponsive count is
kept on the result because it explains a thin result set); every engine failing or nothing
matching (an empty list, so the query is *done*, since the same engines will be as broken
tomorrow); SearXNG itself unreachable (transient, so the task retries, and the health line says
the backend is missing). Queries whose engines were all throttled wait at least half an hour
and retry several times (`B-107`). A worker without `SEARXNG_URL` is degraded, not broken: it
crawls what it has but cannot widen the frontier when that runs out.

Search does not go through `Crawler.fetch`, and must not: the crawler pins requests to validated
public addresses and `netguard` refuses private ones, which is exactly wrong for an internal
service on the compose network. A search is not a fetch either: no bytes, no source, and no
`fetch_attempts` row, because the log is keyed by domain and would file SearXNG's availability
under a domain nobody crawled. A query produces queue rows and settles `done`. Its results are
candidates: §6.4 warns SearXNG returns much content-farm and SEO junk, and a search result is
the least trustworthy way a URL reaches the queue, so the prefilter is not optional for them.

<a id="diversity-seeds"></a>**Diversity seeds from the graph** (`P5-05`, §7.4). Two mechanisms
are triggered by a property of a node, so they read the graph:

- *Mechanism 2, tier imbalance.* A node whose evidence comes entirely or overwhelmingly from one
  source tier gets queries phrased to reach the tiers it lacks, in the words those tiers' own
  documents use (`!science` and `!news` send the query to the scholarly and news engines only).
  Tier is assigned mechanically at ingestion (§5.2), so this is arithmetic over provenance.
  `informal` is never a target: an unaimed crawl already finds most of it. A node needs at least
  three distinct sources: one is single-tier by construction, two sharing a tier is what two
  draws often do, and firing on every one-source node of a young graph would mean "search every
  node", not "correct an imbalance".
- *Mechanism 4, distant walks.* A few nodes far from where the crawl has been are searched by
  name. Far is either measure, so a graph with no vectors still has isolated nodes and a dense
  graph still has outliers: connected to nothing (zero edges, not "one or fewer", which on a
  young graph of single extraction edges made four nodes in five "far" and the walk a uniform
  draw), or among those most distant from the centroid of the recently embedded chunks.
- *Mechanism 1 at node level is not here.* §7.4 triggers it when every *source* on a node argues
  the same way, and a source's position is a model's output (§8) that no pass extracts yet.
  `edges.stance` is one model's reading of one relation, and counting it as the sources' stance
  would make a counter-seed from an extraction artefact. `stance_counter` is the hook, and
  `graph_inputs` passes no stances until a field exists.

A node's evidence is every chunk that justifies something about it (edges at either end,
attribute values, observations, a note's own chunks), counted over *distinct sources* excluding
copies: ten chunks of one report are one voice, and so is its mirror. Nodes with the most
sources go first (the more evidence behind an imbalance, the less likely chance), ties broken by
the seeded shuffle. A node's queries are filed under its own active label, else the active topic
most of its sources carry; with neither, under none, because a guessed topic would lend its
weight and mislabel what is found. The graph seeds run in `seedsearch`'s pass, sharing its
timetable, its no-repeat set and its backlog rule, with per-run caps so the graph's share stays
fixed as it grows. A separate job was rejected: two passes on two timetables could each repeat
what the other queued in between.

<a id="other-languages"></a>**Other-language seeds** (`B-52`, §7.4 mechanism 5, "not optional").
The comparison set is largely non-Anglophone, and a corpus found only through English queries is
a corpus of the English-language literature. A language prefix on English words was measured to
return English pages; the query needs the other language's *words*. The worker may not call a
model (§2.1), so `translate` takes them from Wikipedia's interlanguage links: written by people
who speak the language, looked up rather than generated, and a phrase with no article has no
translation. Cleaning is conservative: a parenthesised qualifier is dropped; a title identical to
the English one is not a translation (a borrowed term finds English pages); a title naming a
broader subject is kept, as a word people search with. The lookup is polite by construction: one
request at a time, spaced, a User-Agent with a contact (Wikimedia's API policy asks for it), and
a bounded number of phrases per run.

### The prefilter

Every link on every page is a candidate, and most are not worth a request (`P1-06`, §6.4). The
novelty gate catches duplicates afterwards, but a duplicate never fetched costs nothing, while
one fetched costs a request, bandwidth, an extraction and a rate-limiter slot. The gates run
cheapest first; the database checks come last, by which time a batch of 500 links is usually
40.

- **Normalisation stays conservative.** Two spellings of one URL are two rows, two fetches and
  two sources, invisibly. But anything that changes *which resource* is requested trades
  duplicates for missing pages, and a missing page is invisible in a way a duplicate is not. So
  the fragment and tracking parameters go, the host is lowercased and a default port dropped, and
  a trailing slash stays, since `/a` and `/a/` are different resources in principle. The `www.`
  prefix is removed with `removeprefix`, never `lstrip("www.")`, which strips characters.
- **Shape**: scheme, host and an extension allowlist; a `.jpg` link would buy a
  `content_type_rejected` at the cost of a real request.
- **Blocklist**: policy-blocked domains and the seeded never-follow list (social platforms and
  link shorteners appear on every official page and lead nowhere useful). Matched by suffix,
  because `registrable_domain` keeps subdomains (rightly: tiering tells them apart), and an
  exact match would block the apex and wave its mobile subdomain through. Read once per worker,
  as config (§13.1).
- <a id="identifier-hosts"></a>**Identifier hosts are routed, not dropped** (`B-23`). A `doi.org`
  URL is a DOI in a URL's clothes: fetched, it redirects to a publisher's paywall, consent wall
  or landing stub (on the first real corpus, over half of such rows had no extractable text),
  while the citation channel was already queueing the same identifiers as `doi` tasks the
  resolver turns into open-access copies. The verdict carries them separately (`Verdict.dois`)
  and the frontier queues them as `doi` tasks first.
- **Site furniture** (`B-23`, `B-42`). Path segments meaning "this page is about the website"
  (contact, help, privacy, login). A shape, not a topic list: one real run indexed a single
  site's help section as a tenth of a day's fetches. Matched as whole segments, so `/about/`
  matches and `/about-the-programme` does not. Multi-word phrases match only when a segment's
  *entire* word sequence is one, which caught `/privacy-policy`, `/terms-of-use` and
  `/contact-us/` (all of which reached a real corpus) while `/terms-of-reference-for-…` stays a
  document.
- **Already seen**: one query against `queue` and one against `sources` per batch. Any status
  counts: a failed URL has `next_attempt_at`, and re-crawl scheduling is a separate decision from
  frontier expansion, or every page's links would resurrect the corpus.
- **Refused by a cached robots.txt** (`B-90`). Stops a search engine returning the same refusing
  site's pages every day, each taking a claim to be refused. Nothing is fetched here: an origin
  never visited is asked at fetch time. It is the crawler's own reading (same parser, same
  per-domain user agent, skipped for a domain whose policy does not respect robots.txt), and
  only a file that was read counts: `missing` permits everything and `unreachable` is the
  absence of an answer. Any error keeps every URL, inside a savepoint.

<a id="queueing"></a>**Queueing.** `enqueue` does no filtering, which belongs to the prefilter
and needs more than the queue table. It does record how a domain first became known (`P4-12`),
there rather than at each call site because it must not be forgettable: a domain with no
provenance can never auto-approve. Recording is not deciding; `seed_allowed` is set only for an
operator's own seed, which is consent.

### Followed links

Frontier expansion (`P1-06`, §6.1) is what makes the crawl a crawl rather than a fetcher that
drains its seed list once. It runs in the fetch pass because the link list lives only in memory:
storing it on the source row would cost ~40 KB per page, and the right home for a URL worth
fetching is a queue row. A link inherits the topic of the page that linked to it: the only
signal without a model, usually right, and §10's steering acts on topics, so untopiced rows
would be beyond its reach.

- **Identifiers first, as `doi` tasks** ([identifier hosts](#identifier-hosts)).
- **Priority by tier** (§5.2): an official link outranks a blog without anyone curating a seed
  list; `P2-20` adds that a page whose kind rots fast is worth fetching sooner.
- **Declared English versions** (`B-57`, the operator's preference) are queued ahead of the
  page's other links (`ENGLISH_ALTERNATE_BONUS`); the original is kept, and once both are stored
  `worker.docdupes` hides the original behind its English version.

<a id="host-scores"></a>**Host scores** (`B-48`). The frontier used to queue every link a page
carried, each inheriting the linking page's topic and ranked by the linked domain's tier. On a
real crawl a handful of on-topic seeds turned into a crawl of whatever large institutional sites
they linked to (statute libraries, hospital pages, staff pages), each labelled with a topic and
ranked at the top because an academic domain ranks as scholarly. Measured by content (`P2-21`),
94% of what had been fetched was about none of the topics. A host whose examined pages are almost
never on a topic is a host whose *next* page is unlikely to be either:

- *Off-topic* (enough pages examined, under `OFFTOPIC_SHARE` on a topic): links to it are not
  queued, and its pages' links are not followed. On the crawl that set the threshold, the sites
  that dominated the drift sat at 0–1%, general university hosts at 0–5%, and useful scholarly
  and research hosts at 18–46%. A government host is down-ranked instead, never dropped: official
  sources are the ones most often served as landing pages that label poorly, and missing one is
  worse than a few pages too many.
- *Unknown*: explored up to `EXPLORE_PENDING` queued links. It was 50; a 12-hour run showed
  thousands of unjudged hosts at 50 each is a breadth-first crawl of every large site the crawl
  brushes against, long before labelling can judge them (`B-61`). Ten is enough to learn what a
  host is about.
- *Unknown on an off-topic site* (`B-113`): a subdomain nobody has judged, whose registrable
  domain is off-topic across its judged siblings. A site that serves every office or blog from
  its own subdomain would otherwise be explored ten links at a time per subdomain; a loop run
  found dozens of such siblings taking a third of followed-link fetches. It is queued at
  `DOWNRANKED_PRIORITY`, capped as unknown, never dropped (unrelated organisations can share a
  registrable domain), and its own verdict replaces the site's once it has one.
- *Any host*: never more than `MAX_PENDING` queued links. Diversity is a property of the queue.
- *On-topic hosts* keep their tier priority at or above `FULL_SHARE`, scaled down below it: a
  repository of everything or a preprint server's all-field listings is not off-topic, but one
  page in twenty does not rank beside a research centre's one in four.
- *Proven hosts* (`B-115`, at or above `FULL_SHARE`) get a bonus larger than any tier, so every
  proven link ranks above every unjudged one. Tier ranks authority, not whether the next page is
  worth fetching: over three loop runs a proven host's next page was on a topic about half the
  time and an unjudged host's one time in seven, yet proven hosts in a low tier queued below
  unjudged government links and took a thirtieth of fetches. Search keeps its reserved claims,
  so this orders followed links among themselves and never starves discovery.

Scores are rebuilt hourly by a scheduled pass from labels another pass wrote, and read as
numbers; nothing deletes and nothing calls a model. A missed run means the loop decides on the
previous hour's numbers. The same pass blocks domains that refuse every request (`B-114`, see
[crawling.md](crawling.md#refusing-domains)).

<a id="re-ranking"></a>**Re-ranking passes** never delete; steering adjusts (§2.5).

- `requeue` (`B-48`) applies the host verdict to links queued before the loop asked: on a real
  crawl, tens of thousands of links into sites since found off-topic. It walks pending frontier
  and sitemap rows (a search result or a person's seed was chosen, not followed) in claim order,
  admits each through the loop's policy, and moves the rest to the bottom.
- `requeue_links` (`B-92`). Loop run 8 found followed links on a topic 4% of the time against
  70% for search results, and two thirds of all fetches. A link is queued before anything has
  read its page; once the page is read and found about none of the topics, what it links to is
  almost always more of the same. So its pending links (`queue.parent_source_id`, recorded since
  `B-91`) move down to the floor the host gate uses for off-topic government links. A link that
  an on-topic page also carries keeps its own row's rank. Idempotent, so hourly after `topics`.
  Since `B-150` the same pass also raises links: see [vouched hosts](#vouched-hosts).

### Vouched hosts

`B-150`, [ADR 0015](../adr/0015-followed-links-follow-the-evidence.md).
With general web search refusing this machine (`B-109`), the link graph is the cheapest
discovery left, and it carries most of what search did. Measured on the live corpus
(2026-10-07), before building:

| Link | Lands on a topic |
|---|---|
| From an on-topic page, to another host | 51% |
| From an on-topic page, same host | 44% |
| From a page about none of the topics, same host | 12% |
| From a page about none of the topics, to another host | 8% |

| On-topic hosts linking to an unjudged host | Its eventual on-topic share |
|---|---|
| none | 6% |
| one | 35% |
| two to five | 23% |

Three changes follow from it:

- **Every link is a vouch.** `link_vouches` holds one row per page and other host it links to,
  written in the fetch loop *before* the prefilter drops links already queued. That drop is
  where the second and later pages linking to a host used to leave no trace. Capped at
  `MAX_VOUCHES_PER_PAGE` hosts per page.
- **Vouched hosts.** `worker.hostscore` counts, per host, the other hosts with an on-topic page
  linking to it (`host_scores.vouched`; hosts, not pages, so one site's many pages are one
  voice). An unjudged host with `VOUCHED_MIN` of them is vouched for: its links get
  `VOUCHED_BOOST` (above unjudged, below proven) and up to `VOUCHED_PENDING` of them may wait
  before it is judged, against `EXPLORE_PENDING` for an unjudged host. A vouch outweighs an
  off-topic site's verdict on a subdomain, because it is evidence about that host; once the
  host's own pages are judged, its record decides and vouches are ignored.
- **Raising after labelling.** `requeue_links` now also raises pending links: those from a page
  labelled on a topic to `PROMOTED_PRIORITY` (the bottom of the proven band, since they land as
  often as a proven host's next page), and those into a vouched-for host to `VOUCHED_PRIORITY`
  (where a newly queued one of the lowest tier would sit). It demotes first, so a vouch can lift
  a link whose page was about nothing. Nothing is raised into a host the host gate holds down,
  a raise never lowers, and a second pass moves nothing. Search results, lookups and seeds are
  not re-ranked.

The migration seeds `link_vouches` from the first parent of every queued link, the evidence
that already existed.

### Proven by following

`B-155`, [ADR 0016](../adr/0016-proven-by-following.md). Loop run 17 spent about 900 of its
2,300 fetches on five hosts that yielded about 4%. Each had been *proven* (`B-115`) on its first
twenty-odd examined pages, and those pages had come from search results or links from on-topic
pages, which is to say they were picked for being on a topic. The host's own links are not
picked: a news site, a repository or a publisher whose search hits are on a topic mostly links
to everything else.

Backtested over runs 15–17, for every host with ten or more followed fetches in a run: of five
hosts proven only on pages a search picked, four yielded 1–5% on their followed links (one
specialist site yielded 97%); where a host had a record on pages reached by following, that
share tracked the next followed page better than its share over every page. Two hosts drifted
in a way neither predicts (a repository at 60% on followed pages yielded 1% the next run).

So `host_scores` keeps a second record, `followed_examined` and `followed_on_topic`: pages
reached by a `frontier` or `sitemap` row. A host whose followed record has `MIN_FOLLOWED`
pages is judged by it: proven at `FULL_SHARE`, thin below it. A host on a topic by every
examined page but without that record is *promising*, explored like a vouched-for host
(`VOUCHED_BOOST`, up to `VOUCHED_PENDING` waiting) until following has shown what its links
lead to. The off-topic gate still reads every examined page. On the local corpus the 32 hosts
proven under the old rule became 20 proven, 9 promising and 3 thin.

`requeue`, which applies host verdicts to links already queued, runs hourly rather than daily
(the migration moves it only from the old default), so a host's change of standing reaches its
waiting links within the hour; it takes seconds. A promising host loses nothing for good: its
links wait at a lower rank until its first followed pages are read.

### Sitemaps

A sitemap is the cheapest frontier expansion there is: the site's own list of every URL, in one
request, with no rendering (`P1-28`). `RobotsRules.sitemaps` had been parsed since `P1-04` and
thrown away.

- **XML bombs.** lxml expands internal entities by default, so twenty lines of nested entities
  allocate gigabytes before any cap notices, the gzip-bomb shape again. A byte pre-scan refuses
  any DTD *before* parsing, and `resolve_entities=False` defuses expansion for anything past it
  (verified: with lxml's defaults the same document expands). The scan covers the whole
  document, broader than XML requires, since a DTD may only precede the root; no legitimate
  sitemap contains either string, and refusing a strange one costs a URL while admitting one
  costs the process. Large documents get a generous fixed window, because comments and
  processing instructions may precede the DOCTYPE and a hostile document pads with exactly
  those. `iterparse` with `.clear()` keeps the peak to one element of a 50,000-URL file.
- **Same site only.** A hostile robots.txt can point at a sitemap that enqueues ten thousand URLs
  on someone else's domain at their tier's priority. sitemaps.org allows such cross-submission;
  this does not, because the frontier feeds a model with write tools, and the cost is a handful
  of CDN-hosted sitemaps. Matched by suffix in both directions, so an apex may list its
  subdomains and a subdomain its apex, but neither a stranger.
- **Sitemaps nest.** An index's entries become further `sitemap` tasks rather than being
  followed inline, which would fetch an unbounded tree under one lease; a urlset's become `url`
  tasks. The kinds are kept apart so XML never reaches the HTML extractor.
- **Lenient where it is safe.** Elements are matched by local name, because sitemaps get the
  namespace wrong, omit it, or use the 0.84 URL; a malformed `<loc>` is dropped and counted, not
  a reason to discard the other entries. A sitemap fetch drops the content-type allowlist (most
  are served as `text/xml`, which is not a document type, and refusing them would look like a
  network problem) and the browser; `parse_sitemap` is the real gate.
- **The prefilter runs on a urlset's entries, not an index's**: `SKIP_EXTENSIONS` drops `.gz`,
  and most large sites publish `sitemap.xml.gz`.
- **Unmatched entries wait at the bottom** (`UNMATCHED_SITEMAP_PRIORITY`, below every tier and
  negative so a future tier 0 still outranks it). Not dropped: an opaque path is not known to be
  irrelevant, and §7.4 warns that a corpus confirming only its own vocabulary is its own bias.
  They are crawled when the frontier has nothing better, which is when incidental discovery is
  worth paying for. Their seed source is `sitemap`, distinct from `frontier`, because a site's
  list is a different answer to "how did this URL get here" (§5.2) from a link someone placed.

<a id="topic-from-path"></a>**Topic from the path** (`topicmatch`, §5.6, §10). A link's inherited
topic is a fair guess; a sitemap breaks the assumption, being a site's whole index, and a bad
label spreads, since each crawled URL passes its topic on, and coverage scoring (§5.3) would show
a thin topic as covered. So each entry is matched against the gazetteer's approved terms
(`topic_labels` on every term) plus the topic names themselves, since a vocabulary sourced only
from a gazetteer that grows by harvest (`P5-02`) would leave a new topic inert. Both are rows,
so a topic or term added in Admin changes matching without a deploy; read once at start-up as
config. Unapproved terms are a model's proposal awaiting a human (§5.6), and letting one steer
the crawl would be the model writing to the frontier through the back door. Matching uses the
path only (query strings are mostly pagination, session and tracking), percent-decoded, on
whole tokens so `pub` never matches `public`. Ambiguous terms are excluded, and so are short
forms below a minimum length: a path has no context to disambiguate an acronym with (§5.5), so
an unmatched URL stays unmatched and visible.

<a id="sitemap-mining"></a>**Mining proven hosts** (`sitemapmine`, `B-116`). Nothing queued a
sitemap, so none was read until this pass. Only proven hosts: every host would be a
breadth-first crawl of the web's largest sites, while a proven host's next page was, measured,
on a topic about half the time. Entries go through the same host policy as followed links, so a
sitemap of fifty thousand URLs queues at most the host's cap. A sitemap is filed under the host's
commonest topic: a claim takes only tasks filed under its drawn topic, so the first deployment
queued a hundred untopiced sitemaps and fetched none; one left pending with no topic gets one on
the next pass. URLs come from the cached robots.txt, so the pass makes no request; a host that
advertises none gets `/sitemap.xml`. `already_queued` sees any status, so the pass is
idempotent.

### Cited papers

§6.1 lists citations beside outbound links as frontier expansion, and §6.4 says the citation
graph alone sustains a full queue for weeks. A reference is also a better signal than a link: a
claim that this work matters to that one. Only DOIs are queued: `arxiv`, `pmid` and `handle`
identifiers are extracted but each needs its own resolution route, and a guessed URL would 404,
so they stay in `sources.extra`. At most `MAX_CITATIONS_PER_PAGE` per page, in document order: a
review article cites hundreds of works, and letting one page put them all ahead of everything
waiting turns the crawl depth-first through one literature. The prefilter does not run on DOIs
(it asks whether a URL is worth a request); its `already_queued` part is applied directly.
DOIs are normalised before queueing, since the frontier used to queue `doi.org` paths in the
page's case beside the citation channel's lower-cased copy, without checking either was queued.

<a id="resolution"></a>**Resolution** (`P1-14`, §6.5) finds a *legally available* copy: Unpaywall,
OpenAlex, CORE, preprint servers, then Europe PMC and Semantic Scholar. A corpus whose promise is
checkable citations cannot rest on copies readers cannot legally follow, so shadow libraries are
excluded; Google Scholar is absent too (no API, captchas), and is already searched as a SearXNG
engine, where an index belongs. This matters because academic search returns many publisher
landing pages (an abstract, a paywall): resolved, they become the open-access copy.

- The two added providers need no credential and often hold copies the aggregators miss: Europe
  PMC mirrors full text rather than pointing at it, and Semantic Scholar indexes the repository
  PDF where Unpaywall often has only a landing page. They sit below §6.5's four because the order
  ranks how likely a provider is to be right: Unpaywall is authoritative about licence and
  version, and an open landing page still extracts. The DOI enters Europe PMC's quoted field
  query only after `normalise_doi` has refused URL or query syntax.
- The APIs are called directly, not through `Crawler.fetch`, whose allowlist would refuse JSON.
  The URLs they *return* go through the crawler, robots, rate limits and `netguard` included,
  which is what makes it safe for a hostile page to cite any DOI it likes.
- One provider failing means try the next. All answering with no copy is an answer: the paper is
  paywalled today and tomorrow, so the task is done, and a metadata-only work still participates
  in the graph (§6.5). None answering is transient and retries. A provider without its credential
  (CORE's key, Unpaywall's contact email) is skipped, not failed, and costs no pacing wait. A 404
  ("no such DOI") is an empty answer, not an outage. Which provider answered is recorded, since
  "Unpaywall found it" and "guessed from the DOI" are different confidence (§5.2 applies to how a
  URL was found).
- **Throttling is not "no copy"** (`B-67`). Resolving 75 real DOIs back to back found nothing at
  Semantic Scholar, while the same DOIs one per second returned an open-access PDF for every one:
  anonymous access allows about a request a second. A 429 folded in with connection errors would
  settle the task `done` and lose the paper. So providers are paced per process (a handful of
  lanes share one resolver); a 403 counts as throttling too, since several of these APIs answer
  403 for "over the anonymous quota"; a throttled provider is left alone for its Retry-After or a
  cooldown doubling to half an hour; and a throttled resolution retries *after* the cooldown, up
  to `THROTTLED_MAX_RETRIES`, rather than on the queue's seconds-long backoff, where three quick
  refusals used to write a paper off over somebody else's traffic. `requeue_dois
  --revive-throttled` gives back DOIs failed that way before `B-67`, by hand only, since on a
  timetable it would revive the same papers forever.

<a id="cited-paper-rank"></a>**Ranking cited papers** (`B-58`, `citedpapers`). Every `doi` row used
to sit at one priority below every link and search result, so none was claimed. Raising them all
would not have helped: many were cited by pages on hosts since found off-topic, and such a paper
is about something else. So a DOI is worth what its citing page is worth, by content
(`topic_labels`: non-empty on a topic, empty examined and about none, NULL unread) and host:

- off-topic host, or a page about nothing: the floor, claimed only when nothing else waits;
- on-topic page on a host not off-topic: the peer-reviewed tier's priority, read from the tier
  map so re-weighting tiers moves them together, and never below the unjudged rank. A cited
  paper is at least as good as a peer-reviewed page followed by link; it stays below a
  peer-reviewed *search result* and the queries, which find new ground, and above other tiers'
  search results, or a steady supply of those would starve it as the old floor did;
- unread page: a modest rank, above informal and press links, below institutional ones. The page
  was fetched for a topic, but nothing has confirmed it, and pages are usually unlabelled when
  their references are queued.

`requeue_dois` settles the rest: rows queued before the parent page was recorded find their
citing pages in `sources.extra->'citations'` (both sides normalised); provisional ranks move once
the page is labelled. A DOI cited by several pages takes the best and records it as parent, since
the first page to name a paper is often the weaker one (a listing reaches a reference before the
article that discusses it); an answered row is left alone. A row whose citing page cannot be
found keeps its priority rather than a guess. Duplicate rows of one DOI are ranked on one row so
the paper is not resolved twice at the top. A resolved copy is queued at the DOI's own rank, or
at the floor when nothing recommends it.

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
