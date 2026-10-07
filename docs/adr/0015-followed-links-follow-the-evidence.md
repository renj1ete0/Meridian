# 0015. Followed links follow the evidence, upward as well as down

- **Status:** Accepted
- **Date:** 2026-10-07
- **Tasks:** `B-150`

## Context

Web search engines refuse this machine (`B-109`): most answer with throttling or a CAPTCHA,
and only news and scholarly engines still reply. A search API key would restore general search,
but it is a cost and a dependency, and the operator asked for discovery that does not wait on
one search method.

The crawl already follows links, and a followed link is far cheaper than a search. Measured on
the live corpus (2026-10-07), the link graph carries most of the signal search provided:

- a link from a page later found on a topic landed on an on-topic page about half the time
  (51% across hosts, 44% within one); from a page about none of the topics, about one time in
  ten. Search results had run at 40–50%;
- a host that an on-topic page on another site linked to went on to have about a quarter to a
  third of its examined pages on a topic; a host no on-topic page linked to, about one in
  twenty.

Yet re-ranking after labelling only ever moved links down (`B-92`), so the links from on-topic
pages sat among the rest, near the bottom of the queue. And the queue kept only the first page
to link anywhere: a second and third on-topic page linking to the same host were dropped as
duplicates, and with them the evidence that the host was worth learning.

## Decision

Follow the evidence in both directions, without a model and without a new search provider.

1. Record every page's links to other hosts (`link_vouches`) before any link is dropped as
   already queued.
2. A host nobody has judged is *vouched for* when an on-topic page on another host links to it.
   Its links rank above unjudged hosts' and below proven hosts', and it is explored further
   before judgement than an unjudged host.
3. Once a page is labelled on a topic, its pending links rise to the bottom of the proven band;
   pending links into a vouched-for host rise to where a newly queued one would sit.
4. A host's own record outweighs any page linking to it: nothing is raised into a host judged
   off-topic, or into an unjudged subdomain of an off-topic site unless that subdomain is
   itself vouched for.

## Consequences

- Discovery leans less on search. Search keeps its reserved claims; this reorders followed
  links among themselves.
- `link_vouches` grows by the number of distinct other hosts a page links to, capped per page;
  it cascades away with its source.
- One vouch is enough to count. The measurement supports it, but a host many unrelated
  on-topic pages link to and one linked once are treated alike; a graded boost is the obvious
  next step if the queue shows vouched hosts disappointing.
- Measured before building, not after. The next loop run should compare the on-topic share of
  fetched followed links with run 15's.
