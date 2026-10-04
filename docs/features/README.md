# Features

One document per feature. Each says what the feature does, how it works, how to configure and
operate it, how it fails, and where its code and tests are. Read them in pipeline order to
understand the system end to end.

## The pipeline

```
discover ──► crawl ──► extract & chunk ──► embed ──► judge ──► read
(search,     (queue,    (text, cleaning,     (vectors,  (duplicates,  (search, map,
 links,       fetch,     chunks, kinds)       tiers)     topics,       graph, gaps,
 sitemaps,    politeness)                                places,       ask)
 papers)                                                 quality)
                                       ▲                                  │
                                       └──── steering and synthesis ◄─────┘
```

The worker never calls a language model: discovery, crawling, extraction, embedding and
every judgement in the "judge" column are mechanical. Models enter only in synthesis and in
the Ask panel.

## Index

| Stage | Feature | What it covers |
|---|---|---|
| Discover | [discovery.md](discovery.md) | Search seeds, followed links, sitemaps, cited papers, diversity seeds, host scores |
| Crawl | [crawling.md](crawling.md) | The queue, worker lanes, politeness, robots.txt, SSRF protection, the attempt log |
| Extract | [extraction.md](extraction.md) | HTML, PDF and Office extraction, cleaning, chunking, document kinds, titles, the raw store |
| Embed | [embedding.md](embedding.md) | The embedding service, tiers and backpressure, GPU, what text is embedded |
| Judge | [duplicates.md](duplicates.md) | The novelty gate, document copies, repeated edges |
| Judge | [topics.md](topics.md) | Topic labels for sources and passages, triage of long documents, overlaps |
| Judge | [places-and-terms.md](places-and-terms.md) | Which places a source is about; the gazetteer and acronym harvest |
| Judge | [source-quality.md](source-quality.md) | Source tiers, injection screening and trust, furniture and junk, retention, ageing |
| Read | [search.md](search.md) | Hybrid search, the answer page, watched questions, corpus counts |
| Read | [map.md](map.md) | The corpus map, areas, bridges between areas, naming by field |
| Read | [knowledge-graph.md](knowledge-graph.md) | Entities and edges, resolution, writes and validation, the graph workspace, routes, notes |
| Read | [gaps.md](gaps.md) | The ranked list of what the corpus cannot answer yet |
| Read | [ask-the-graph.md](ask-the-graph.md) | Questions answered by a model from cited passages |
| Steer | [steering.md](steering.md) | Topic weights, boosts, automatic proposals, steering from the map |
| Reason | [synthesis.md](synthesis.md) | The orchestrator, model routing and providers, budgets, prompt framing |
| Access | [api-and-access.md](api-and-access.md) | Explore and Admin routes, database roles, Cloudflare Access, caching |
| Access | [mcp.md](mcp.md) | The MCP surface for external assistants, tokens, grants, read-only SQL |
| Operate | [operations.md](operations.md) | The scheduler, health, alerts and digest, the Telegram bot, liveness |
| Evaluate | [question-set.md](question-set.md) | The held-out question set and its runner |
| Interface | [web-app.md](web-app.md) | The reader and Admin surfaces, and how the web package is organised |

## Template

New feature documents follow this shape. Leave out a section that has nothing in it, rather
than writing "none".

```markdown
# Feature name

One paragraph: what it does and why it exists, for someone new to the project.

- **Code:** main modules
- **Tasks:** IDs that built or changed it
- **Decisions:** ADRs, if any

## How it works
The mechanism, in the order things happen. Diagrams welcome.

## Design choices
The non-obvious decisions and the failure each one prevents. This is where the
rationale that used to live in code comments goes.

## Configuration
Environment variables, database settings and defaults, with links to reference/.

## Operating it
Scheduled jobs, commands, what to watch, and what normal looks like.

## Failure modes and traps
What has gone wrong before, how it showed up, and how to tell.

## Tests
Where the tests are and what they guard.
```
